import os
import json
import time
import secrets
import shutil
import logging
import subprocess
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from podgen import Podcast, Episode, Media
import aiohttp
from aiohttp import web

try:
    from notebooklm import NotebookLMClient, RPCError
except ImportError:
    NotebookLMClient = None
    RPCError = Exception

# ── Configuration ──────────────────────────────────────────────────────────

BASE_URL = os.getenv('BASE_URL')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '86400'))
RETENTION_DAYS = int(os.getenv('RETENTION_DAYS', '14'))
BRIDGE_PORT = int(os.getenv('BRIDGE_PORT', '8080'))
BRIDGE_API_KEY = os.getenv('BRIDGE_API_KEY', '')
APP_VERSION = os.getenv('APP_VERSION', 'dev')
FEED_IMAGE_URL = os.getenv('FEED_IMAGE_URL', '')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
WEBHOOK_LINK = os.getenv('WEBHOOK_LINK', '')
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
TOKEN_EXPIRY_WARN_DAYS = int(os.getenv('TOKEN_EXPIRY_WARN_DAYS', '7'))

_wh_raw = os.getenv('WEBHOOK_HEADERS', '')
try:
    WEBHOOK_HEADERS: dict = json.loads(_wh_raw) if _wh_raw else {}
except json.JSONDecodeError:
    WEBHOOK_HEADERS = {}
    print("WARNING: WEBHOOK_HEADERS is not valid JSON, ignoring")

PUBLIC_DIR = Path('/public')
_DEFAULT_AUTH_FILE = Path('/root/.notebooklm/storage_state.json')

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── User model ─────────────────────────────────────────────────────────────

@dataclass
class User:
    name: str
    email: str
    auth_file: Path
    history_file: Path
    episodes_dir: Path
    feed_file: Path
    feed_token: str
    webhook_url: str = ''
    webhook_headers: dict = None
    webhook_link: str = ''

    def __post_init__(self):
        if self.webhook_headers is None:
            self.webhook_headers = {}


def _parse_webhook_headers(raw: str) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("WEBHOOK_HEADERS is not valid JSON, ignoring")
        return {}


def _load_or_generate_feed_token(token_file: Path) -> str:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    if token_file.exists():
        return token_file.read_text().strip()
    token = secrets.token_urlsafe(24)
    token_file.write_text(token)
    return token


def _build_users() -> list[User]:
    names_raw = os.getenv('USERS', '')
    names = [n.strip() for n in names_raw.split(',') if n.strip()]

    if not names:
        # Single-user backward compat — existing paths, no auth required
        token = _load_or_generate_feed_token(Path('/data/.feed_token'))
        return [User(
            name='default',
            email='',
            auth_file=_DEFAULT_AUTH_FILE,
            history_file=Path('/data/history.json'),
            episodes_dir=PUBLIC_DIR / 'episodes',
            feed_file=PUBLIC_DIR / 'feed.xml',
            feed_token=token,
            webhook_url=WEBHOOK_URL,
            webhook_headers=WEBHOOK_HEADERS,
            webhook_link=WEBHOOK_LINK,
        )]

    users = []
    for name in names:
        key = name.upper()
        email = os.getenv(f'USER_{key}_EMAIL', '')
        token = _load_or_generate_feed_token(Path(f'/data/{name}/.feed_token'))
        # Per-user webhook, fallback to global
        wh_url = os.getenv(f'USER_{key}_WEBHOOK_URL', WEBHOOK_URL)
        wh_headers = _parse_webhook_headers(
            os.getenv(f'USER_{key}_WEBHOOK_HEADERS', os.getenv('WEBHOOK_HEADERS', ''))
        )
        wh_link = os.getenv(f'USER_{key}_WEBHOOK_LINK', WEBHOOK_LINK)
        users.append(User(
            name=name,
            email=email,
            auth_file=_DEFAULT_AUTH_FILE.parent / name / 'storage_state.json',
            history_file=Path(f'/data/{name}/history.json'),
            episodes_dir=PUBLIC_DIR / 'episodes' / name,
            feed_file=PUBLIC_DIR / 'feed' / f'{token}.xml',
            feed_token=token,
            webhook_url=wh_url,
            webhook_headers=wh_headers,
            webhook_link=wh_link,
        ))
    return users


USERS_CONFIG: list[User] = _build_users()
EMAIL_TO_USER: dict[str, User] = {u.email: u for u in USERS_CONFIG if u.email}
_MULTI_USER = bool(os.getenv('USERS', ''))

# Ensure episode and feed dirs exist
for _u in USERS_CONFIG:
    _u.episodes_dir.mkdir(parents=True, exist_ok=True)
if _MULTI_USER:
    (PUBLIC_DIR / 'feed').mkdir(parents=True, exist_ok=True)

# ── Google token validation ────────────────────────────────────────────────

_token_cache: dict[str, tuple[str, float]] = {}  # token → (email, exp)


async def validate_google_token(token: str) -> str | None:
    """Validate a Google ID token. Returns email or None."""
    if not GOOGLE_CLIENT_ID:
        return None
    cached = _token_cache.get(token)
    if cached:
        email, exp = cached
        if time.time() < exp - 30:
            return email
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'https://oauth2.googleapis.com/tokeninfo',
                params={'id_token': token},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json()
    except Exception:
        return None
    if data.get('aud') != GOOGLE_CLIENT_ID:
        return None
    email = data.get('email')
    exp = float(data.get('exp', 0))
    if email and exp > time.time():
        _token_cache[token] = (email, exp)
    return email


async def get_request_user(request) -> User | None:
    """Return authenticated User for this request, or None."""
    if not GOOGLE_CLIENT_ID:
        return USERS_CONFIG[0]
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    email = await validate_google_token(auth[7:])
    if not email:
        return None
    return EMAIL_TO_USER.get(email)


# ── History ────────────────────────────────────────────────────────────────

def load_history(history_file: Path) -> dict:
    if history_file.is_dir():
        logger.error(f"{history_file} is a directory")
        return {}
    if history_file.exists():
        try:
            with open(history_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_history(history: dict, history_file: Path):
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, 'w') as f:
        json.dump(history, f, indent=2)


def recover_history_from_disk(history: dict, episodes_dir: Path):
    known_filenames = {v['mp3_filename'] for v in history.values()}
    recovered = 0
    for f in [*episodes_dir.glob('*.m4a'), *episodes_dir.glob('*.mp3')]:
        if f.name not in known_filenames:
            artifact_id = f.stem
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
            history[artifact_id] = {
                'title': f'Episode {f.stem[:8]}',
                'created_at': mtime,
                'mp3_filename': f.name,
                'notebook': '',
            }
            recovered += 1
    if recovered:
        logger.info(f"Recovered {recovered} episodes from disk into history")


# ── Token expiry ───────────────────────────────────────────────────────────

def get_token_expiry(auth_file: Path):
    """Returns (expires_at_unix, expires_in_days, expires_at_iso) or (None, None, None)."""
    if not auth_file.exists():
        return None, None, None
    try:
        with open(auth_file, 'r') as f:
            storage_state = json.load(f)
        cookies = storage_state.get('cookies', [])
        if not cookies:
            return None, None, None
        earliest_expires = min((c.get('expires') for c in cookies if c.get('expires')), default=None)
        if earliest_expires is None:
            return None, None, None
        expires_at = datetime.fromtimestamp(earliest_expires, tz=timezone.utc)
        days_remaining = (expires_at - datetime.now(timezone.utc)).days
        return int(earliest_expires), days_remaining, expires_at.isoformat()
    except Exception as e:
        logger.warning(f"Failed to extract token expiry: {e}")
        return None, None, None


_token_alert_sent: dict[str, float] = {}  # user_name → last sent timestamp


async def check_token_expiry_and_notify(user: User):
    if not user.webhook_url:
        return
    _, days_remaining, _ = get_token_expiry(user.auth_file)
    if days_remaining is None or days_remaining > TOKEN_EXPIRY_WARN_DAYS:
        return
    now = time.time()
    if _token_alert_sent.get(user.name, 0) + 86400 > now:
        return
    _token_alert_sent[user.name] = now
    prefix = f"[{user.name}] " if _MULTI_USER else ""
    if days_remaining < 0:
        title, message = "Token expired", f"{prefix}NotebookLM token has EXPIRED — please renew"
    elif days_remaining == 0:
        title, message = "Token expires today", f"{prefix}NotebookLM token expires TODAY"
    else:
        title = f"Token expires in {days_remaining}d"
        message = f"{prefix}NotebookLM token expires in {days_remaining} day(s)"
    await _post_webhook(user, title, message)
    logger.info(f"[{user.name}] Sent token expiry notification: {days_remaining} days remaining")


# ── Audio processing ───────────────────────────────────────────────────────

def remux_to_m4a(src_path, m4a_path):
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-i', str(src_path), '-c', 'copy', str(m4a_path)],
            check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg remux failed: {e.stderr.decode()}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during remux: {e}")
        return None


def get_duration(path):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True, text=True, check=True,
        )
        return int(float(result.stdout.strip()))
    except Exception:
        return None


# ── Webhook ────────────────────────────────────────────────────────────────

async def _post_webhook(user: 'User', title: str, message: str):
    headers = {'X-Title': title, 'X-Tags': 'headphones', **user.webhook_headers}
    if user.webhook_link:
        headers['X-Click'] = user.webhook_link
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(user.webhook_url, data=message.encode(), headers=headers) as resp:
                if resp.status >= 400:
                    logger.warning(f"[{user.name}] Webhook returned {resp.status}")
    except Exception as e:
        logger.warning(f"[{user.name}] Webhook failed: {e}")


async def fire_webhook(user: 'User', episode_title: str, notebook: str):
    if not user.webhook_url:
        return
    message = f"{episode_title} — {notebook}" if notebook else episode_title
    await _post_webhook(user, 'New NoteCast episode', message)


# ── Feed ───────────────────────────────────────────────────────────────────

def rebuild_feed(history: dict, user: User):
    image_url = FEED_IMAGE_URL
    if not image_url:
        for ext in ('jpg', 'jpeg', 'png'):
            if (PUBLIC_DIR / f'cover.{ext}').exists():
                image_url = f"{BASE_URL}/cover.{ext}"
                break

    feed_name = f"NoteCast — {user.name}" if _MULTI_USER else "NoteCast"
    podcast = Podcast(
        name=feed_name,
        description="Personal podcast server for NotebookLM audio",
        website=BASE_URL,
        explicit=False,
        image=image_url or None,
    )

    sorted_history = sorted(history.items(), key=lambda x: x[1].get('created_at', ''), reverse=True)

    for artifact_id, data in sorted_history:
        episode_title = data.get('title', f"Episode {artifact_id}")
        mp3_filename = data.get('mp3_filename')
        if not mp3_filename:
            continue
        ep_base = f"{BASE_URL}/episodes/{user.name}/" if _MULTI_USER else f"{BASE_URL}/episodes/"
        media_url = f"{ep_base}{mp3_filename}"
        try:
            pub_date = datetime.fromisoformat(data['created_at'].replace('Z', '+00:00'))
        except Exception:
            pub_date = datetime.now(timezone.utc)
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)
        mp3_path = user.episodes_dir / mp3_filename
        file_size = mp3_path.stat().st_size if mp3_path.exists() else 0
        podcast.add_episode(Episode(
            title=episode_title,
            media=Media(media_url, file_size, type='audio/mp4'),
            publication_date=pub_date,
        ))

    user.feed_file.parent.mkdir(parents=True, exist_ok=True)
    podcast.rss_file(str(user.feed_file))
    logger.info(f"[{user.name}] Feed rebuilt with {len(sorted_history)} episodes")


def purge_old_episodes(history: dict, episodes_dir: Path) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    to_remove = []
    for artifact_id, data in history.items():
        try:
            date_str = data.get('downloaded_at') or data.get('created_at', '')
            ts = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                to_remove.append(artifact_id)
        except Exception:
            pass
    for artifact_id in to_remove:
        data = history.pop(artifact_id)
        mp3_filename = data.get('mp3_filename')
        if mp3_filename:
            mp3_path = episodes_dir / mp3_filename
            if mp3_path.exists():
                mp3_path.unlink()
                logger.info(f"Removed old episode: {mp3_filename}")
    return history


# ── Harvester ──────────────────────────────────────────────────────────────

_auth_printed: set[str] = set()


async def load_client_for_user(user: User):
    """Load NotebookLM client for this user. Returns None if credentials missing."""
    # In multi-user mode, copy user auth to the default Playwright location
    if _MULTI_USER and user.auth_file != _DEFAULT_AUTH_FILE:
        if not user.auth_file.exists():
            if user.name not in _auth_printed:
                logger.info("=" * 60)
                logger.info(f"AUTHENTICATION REQUIRED for user: {user.name}")
                logger.info(f"  cp ~/.notebooklm/storage_state.json ./auth/{user.name}/")
                logger.info("=" * 60)
                _auth_printed.add(user.name)
            return None
        _DEFAULT_AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(user.auth_file, _DEFAULT_AUTH_FILE)

    try:
        client = await NotebookLMClient.from_storage()
        logger.info(f"[{user.name}] Loaded NotebookLM credentials")
        return client
    except Exception:
        if user.name not in _auth_printed:
            logger.info("=" * 60)
            logger.info(f"AUTHENTICATION REQUIRED for user: {user.name}")
            logger.info("  notebooklm login")
            logger.info(f"  cp ~/.notebooklm/storage_state.json ./auth/{user.name if _MULTI_USER else ''}")
            logger.info("=" * 60)
            _auth_printed.add(user.name)
        return None


async def harvest_user(user: User):
    """Poll NotebookLM and download new audio artifacts for one user."""
    history = load_history(user.history_file)
    recover_history_from_disk(history, user.episodes_dir)
    save_history(history, user.history_file)

    client = await load_client_for_user(user)
    if client is None:
        return

    try:
        async with client:
            notebooks = await client.notebooks.list()
            for notebook in notebooks:
                notebook_id = notebook.id
                notebook_title = getattr(notebook, 'title', '')
                try:
                    artifacts = await client.artifacts.list(notebook_id)
                except AttributeError:
                    continue
                for artifact in artifacts:
                    artifact_id = getattr(artifact, 'id', None)
                    if not artifact_id or artifact_id in history:
                        continue
                    artifact_kind = getattr(artifact, 'kind', None) or getattr(artifact, 'artifact_type', None)
                    if artifact_kind and 'audio' not in str(artifact_kind).lower():
                        continue
                    title = getattr(artifact, 'title', f"Artifact {artifact_id}")
                    _created = getattr(artifact, 'created_at', None)
                    if isinstance(_created, datetime):
                        created_at = _created.isoformat()
                    elif _created:
                        created_at = str(_created)
                    else:
                        created_at = datetime.now().isoformat()

                    logger.info(f"[{user.name}] Processing: {artifact_id} - {title}")
                    temp_path = Path(f'/tmp/{artifact_id}.mp4')
                    m4a_filename = f"{artifact_id}.m4a"
                    m4a_path = user.episodes_dir / m4a_filename
                    try:
                        await client.artifacts.download_audio(notebook_id, str(temp_path), artifact_id=artifact_id)
                        if not remux_to_m4a(temp_path, m4a_path):
                            logger.error(f"[{user.name}] Remux failed for {artifact_id}")
                            continue
                    except RPCError as e:
                        logger.error(f"[{user.name}] RPC error for {artifact_id}: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"[{user.name}] Failed to process {artifact_id}: {e}")
                        continue
                    finally:
                        temp_path.unlink(missing_ok=True)

                    history[artifact_id] = {
                        'title': title,
                        'created_at': created_at,
                        'downloaded_at': datetime.now(timezone.utc).isoformat(),
                        'mp3_filename': m4a_filename,
                        'notebook': notebook_title,
                        'duration': get_duration(m4a_path),
                    }
                    logger.info(f"[{user.name}] Processed {artifact_id}")
                    save_history(history, user.history_file)
                    rebuild_feed(history, user)
                    await fire_webhook(user, title, notebook_title)

        history = purge_old_episodes(history, user.episodes_dir)
        save_history(history, user.history_file)
        rebuild_feed(history, user)
        await check_token_expiry_and_notify(user)

    except Exception as e:
        logger.error(f"[{user.name}] Error in harvest: {e}")


_last_updated: str = ''
_next_poll_at: float = 0.0
_poll_event = asyncio.Event()


async def main_async():
    if not BASE_URL:
        logger.error("BASE_URL is required — set it in .env and restart.")
        while True:
            await asyncio.sleep(60)
    if NotebookLMClient is None:
        logger.error("notebooklm-py not installed — rebuild the container.")
        while True:
            await asyncio.sleep(60)

    logger.info("=" * 40)
    logger.info(f"  NoteCast bridge {APP_VERSION}")
    if _MULTI_USER:
        for u in USERS_CONFIG:
            logger.info(f"  {u.name} ({u.email}) → {BASE_URL}/feed/{u.feed_token}.xml")
    else:
        u = USERS_CONFIG[0]
        logger.info(f"  Feed: {BASE_URL}/feed.xml")
    logger.info("=" * 40)

    while True:
        logger.info("Checking for new audio artifacts")
        for user in USERS_CONFIG:
            await harvest_user(user)

        global _last_updated, _next_poll_at
        _last_updated = datetime.now(timezone.utc).isoformat()
        _next_poll_at = time.time() + POLL_INTERVAL
        logger.info(f"Sleeping for {POLL_INTERVAL} seconds")
        _poll_event.clear()
        try:
            await asyncio.wait_for(_poll_event.wait(), timeout=POLL_INTERVAL)
            logger.info("Poll triggered manually")
        except asyncio.TimeoutError:
            pass


# ── HTTP handlers ──────────────────────────────────────────────────────────

async def handle_health(request):
    return web.json_response({'ok': True})


async def handle_config(request):
    """Public: returns client-side config. No auth needed."""
    return web.json_response({
        'google_client_id': GOOGLE_CLIENT_ID or None,
        'multi_user': _MULTI_USER,
    })


async def handle_status(request):
    user = await get_request_user(request)
    if user is None:
        return web.json_response({'ok': False, 'error': 'Unauthorized'}, status=401)
    history = load_history(user.history_file)
    now = time.time()
    _, token_expires_in_days, token_expires_at_iso = get_token_expiry(user.auth_file)
    feed_url = (f"{BASE_URL}/feed/{user.feed_token}.xml" if _MULTI_USER
                else f"{BASE_URL}/feed.xml")
    status = {
        'version': APP_VERSION,
        'episodes': len(history),
        'last_updated': _last_updated,
        'next_poll_in': max(0, int(_next_poll_at - now)),
        'webhook_enabled': bool(user.webhook_url),
        'feed_url': feed_url,
    }
    if token_expires_in_days is not None:
        status['token_expires_in_days'] = token_expires_in_days
        status['token_expires_at_iso'] = token_expires_at_iso
    return web.json_response(status)


async def handle_episodes(request):
    user = await get_request_user(request)
    if user is None:
        return web.json_response({'ok': False, 'error': 'Unauthorized'}, status=401)
    history = load_history(user.history_file)
    ep_base = f"{BASE_URL}/episodes/{user.name}/" if _MULTI_USER else f"{BASE_URL}/episodes/"
    episodes = sorted(
        [
            {
                'id': k,
                'title': v['title'],
                'notebook': v.get('notebook', ''),
                'created_at': v['created_at'],
                'url': f"{ep_base}{v['mp3_filename']}",
                'filename': v['mp3_filename'],
                'duration': v.get('duration'),
            }
            for k, v in history.items()
        ],
        key=lambda x: x['created_at'],
        reverse=True,
    )
    return web.json_response(episodes)


async def handle_poll(request):
    user = await get_request_user(request)
    if user is None:
        if BRIDGE_API_KEY and request.headers.get('X-Api-Key') == BRIDGE_API_KEY:
            pass  # API key auth allowed for poll
        else:
            return web.json_response({'ok': False, 'error': 'Unauthorized'}, status=401)
    _poll_event.set()
    return web.json_response({'ok': True})


async def handle_webhook_test(request):
    user = await get_request_user(request)
    if user is None:
        return web.json_response({'ok': False, 'error': 'Unauthorized'}, status=401)
    if not user.webhook_url:
        return web.json_response({'ok': False, 'error': 'WEBHOOK_URL not configured'}, status=400)
    await _post_webhook(user, 'NoteCast test', 'Webhook is working correctly')
    return web.json_response({'ok': True})


async def handle_auth_upload(request):
    user = await get_request_user(request)
    if user is None:
        if BRIDGE_API_KEY and request.headers.get('X-Api-Key') == BRIDGE_API_KEY:
            user = USERS_CONFIG[0]
        else:
            return web.json_response({'ok': False, 'error': 'Unauthorized'}, status=401)

    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != 'file':
        return web.json_response({'ok': False, 'error': 'Missing field: file'}, status=400)
    user.auth_file.parent.mkdir(parents=True, exist_ok=True)
    data = await field.read()
    user.auth_file.write_bytes(data)
    logger.info(f"[{user.name}] Auth credentials updated via API upload")
    return web.json_response({'ok': True})


async def run_http_server():
    app = web.Application()
    app.router.add_get('/health', handle_health)
    app.router.add_get('/api/config', handle_config)
    app.router.add_get('/api/status', handle_status)
    app.router.add_get('/api/episodes', handle_episodes)
    app.router.add_post('/api/poll', handle_poll)
    app.router.add_post('/api/webhook/test', handle_webhook_test)
    app.router.add_post('/auth/upload', handle_auth_upload)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', BRIDGE_PORT)
    await site.start()
    logger.info(f"HTTP server listening on port {BRIDGE_PORT}")
    await asyncio.Event().wait()


def main():
    async def run():
        await asyncio.gather(run_http_server(), main_async())
    asyncio.run(run())


if __name__ == '__main__':
    main()
