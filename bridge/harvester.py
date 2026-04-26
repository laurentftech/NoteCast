import os
import json
import time
import logging
import subprocess
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from podgen import Podcast, Episode, Media
import aiohttp
from aiohttp import web

# Try to import the notebooklm client
try:
    from notebooklm import NotebookLMClient, RPCError
except ImportError:
    # Fallback for development - we'll create a mock client later if needed
    NotebookLMClient = None
    RPCError = Exception

# Configuration from environment variables with defaults
BASE_URL = os.getenv('BASE_URL')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '86400'))
RETENTION_DAYS = int(os.getenv('RETENTION_DAYS', '14'))
BRIDGE_PORT = int(os.getenv('BRIDGE_PORT', '8080'))
BRIDGE_API_KEY = os.getenv('BRIDGE_API_KEY', '')  # optional — set to restrict /auth/upload
APP_VERSION = os.getenv('APP_VERSION', 'dev')
FEED_IMAGE_URL = os.getenv('FEED_IMAGE_URL', '')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
WEBHOOK_LINK = os.getenv('WEBHOOK_LINK', '')
_webhook_headers_raw = os.getenv('WEBHOOK_HEADERS', '')
try:
    WEBHOOK_HEADERS = json.loads(_webhook_headers_raw) if _webhook_headers_raw else {}
except json.JSONDecodeError:
    WEBHOOK_HEADERS = {}
    print(f"WARNING: WEBHOOK_HEADERS is not valid JSON, ignoring")

# Paths
PUBLIC_DIR = Path('/public')
EPISODES_DIR = PUBLIC_DIR / 'episodes'
HISTORY_FILE = Path('/data/history.json')
FEED_FILE = PUBLIC_DIR / 'feed.xml'
AUTH_FILE = Path('/root/.notebooklm/storage_state.json')

# Token expiry tracking
TOKEN_EXPIRY_WARN_DAYS = int(os.getenv('TOKEN_EXPIRY_WARN_DAYS', '7'))
_token_alert_sent_at = None  # Track last alert to prevent spam

# Ensure directories exist
EPISODES_DIR.mkdir(parents=True, exist_ok=True)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_history():
    if HISTORY_FILE.is_dir():
        logger.error(f"{HISTORY_FILE} is a directory — fix on host: rm -rf bridge/history.json && echo '{{}}' > bridge/history.json")
        return {}
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def recover_history_from_disk(history: dict) -> dict:
    """Add placeholder entries for MP3s on disk not tracked in history."""
    known_filenames = {v['mp3_filename'] for v in history.values()}
    recovered = 0
    for mp3 in EPISODES_DIR.glob('*.mp3'):
        if mp3.name not in known_filenames:
            artifact_id = mp3.stem
            mtime = datetime.fromtimestamp(mp3.stat().st_mtime, tz=timezone.utc).isoformat()
            history[artifact_id] = {
                'title': f'Episode {mp3.stem[:8]}',
                'created_at': mtime,
                'mp3_filename': mp3.name,
                'notebook': '',
            }
            recovered += 1
    if recovered:
        logger.info(f"Recovered {recovered} episodes from disk into history")

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

def get_token_expiry():
    """Extract the earliest cookie expiry timestamp from storage_state.json.
    Returns: (expires_at_unix, expires_in_days, expires_at_iso) or (None, None, None)"""
    if not AUTH_FILE.exists():
        return None, None, None
    
    try:
        with open(AUTH_FILE, 'r') as f:
            storage_state = json.load(f)
        
        cookies = storage_state.get('cookies', [])
        if not cookies:
            return None, None, None
        
        # Find earliest expiry (most urgent)
        earliest_expires = min((c.get('expires') for c in cookies if c.get('expires')), default=None)
        
        if earliest_expires is None:
            return None, None, None
        
        expires_at = datetime.fromtimestamp(earliest_expires, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        days_remaining = (expires_at - now).days
        
        return int(earliest_expires), days_remaining, expires_at.isoformat()
    except Exception as e:
        logger.warning(f"Failed to extract token expiry: {e}")
        return None, None, None

def download_wav_bytes(artifact_id, wav_data):
    """Save WAV bytes to a temporary file."""
    temp_wav = Path(f'/tmp/{artifact_id}.wav')
    try:
        with open(temp_wav, 'wb') as f:
            f.write(wav_data)
        return temp_wav
    except Exception as e:
        logger.error(f"Failed to save WAV for artifact {artifact_id}: {e}")
        return None

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

async def fire_webhook(episode_title: str, notebook: str):
    if not WEBHOOK_URL:
        return
    message = f"{episode_title} — {notebook}" if notebook else episode_title
    payload = {'title': 'New NoteCast episode', 'message': message, 'tags': ['headphones']}
    if WEBHOOK_LINK:
        payload['click'] = WEBHOOK_LINK
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WEBHOOK_URL, json=payload, headers=WEBHOOK_HEADERS) as resp:
                if resp.status >= 400:
                    logger.warning(f"Webhook returned {resp.status}")
    except Exception as e:
        logger.warning(f"Webhook failed: {e}")

async def check_token_expiry_and_notify():
    """Check if token is expiring soon and send notification if needed."""
    global _token_alert_sent_at
    
    if not WEBHOOK_URL:
        return
    
    token_expires_at, days_remaining, _ = get_token_expiry()
    
    if token_expires_at is None or days_remaining is None:
        return
    
    # Only alert if within the warning threshold
    if days_remaining > TOKEN_EXPIRY_WARN_DAYS:
        return
    
    now = time.time()
    # Prevent spam: only send once per 24 hours
    if _token_alert_sent_at and (now - _token_alert_sent_at) < 86400:
        return
    
    _token_alert_sent_at = now
    
    if days_remaining < 0:
        message = "NotebookLM token has EXPIRED — please renew it"
        title = "Token expired"
    elif days_remaining == 0:
        message = "NotebookLM token expires TODAY — please renew it"
        title = "Token expires today"
    else:
        message = f"NotebookLM token expires in {days_remaining} day(s) — please renew it"
        title = f"Token expires in {days_remaining} day(s)"
    
    payload = {'title': title, 'message': message, 'tags': ['warning']}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WEBHOOK_URL, json=payload, headers=WEBHOOK_HEADERS) as resp:
                if resp.status >= 400:
                    logger.warning(f"Token expiry webhook returned {resp.status}")
                else:
                    logger.info(f"Sent token expiry notification: {days_remaining} days remaining")
    except Exception as e:
        logger.warning(f"Token expiry webhook failed: {e}")

def rebuild_feed(history):
    """Rebuild the RSS feed from the history."""
    image_url = FEED_IMAGE_URL
    if not image_url:
        for ext in ('jpg', 'jpeg', 'png'):
            if (PUBLIC_DIR / f'cover.{ext}').exists():
                image_url = f"{BASE_URL}/cover.{ext}"
                break

    podcast = Podcast(
        name="NoteCast",
        description="Personal podcast server for NotebookLM audio",
        website=BASE_URL,
        explicit=False,
        image=image_url or None,
    )

    # Sort history by created_at descending (newest first)
    sorted_history = sorted(
        history.items(),
        key=lambda x: x[1].get('created_at', ''),
        reverse=True
    )

    for artifact_id, data in sorted_history:
        episode_title = data.get('title', f"Episode {artifact_id}")
        mp3_filename = data.get('mp3_filename')
        if not mp3_filename:
            continue
        media_url = f"{BASE_URL}/episodes/{mp3_filename}"
        # Parse the created_at string to datetime
        try:
            pub_date = datetime.fromisoformat(data['created_at'].replace('Z', '+00:00'))
        except Exception:
            pub_date = datetime.now(timezone.utc)
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)

        mp3_path = EPISODES_DIR / mp3_filename
        file_size = mp3_path.stat().st_size if mp3_path.exists() else 0

        episode = Episode(
            title=episode_title,
            media=Media(media_url, file_size, type='audio/mp4'),
            publication_date=pub_date,
        )
        podcast.add_episode(episode)

    podcast.rss_file(str(FEED_FILE))
    logger.info(f"Feed rebuilt with {len(sorted_history)} episodes")

def purge_old_episodes(history):
    """Remove episodes older than RETENTION_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    to_remove = []
    for artifact_id, data in history.items():
        try:
            # Use downloaded_at for retention; fall back to file mtime, then created_at
            date_str = data.get('downloaded_at') or data.get('created_at', '')
            ts = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                to_remove.append(artifact_id)
        except Exception:
            # If we can't parse the date, we keep it (or could remove? but safer to keep)
            pass

    for artifact_id in to_remove:
        data = history.pop(artifact_id)
        mp3_filename = data.get('mp3_filename')
        if mp3_filename:
            mp3_path = EPISODES_DIR / mp3_filename
            if mp3_path.exists():
                mp3_path.unlink()
                logger.info(f"Removed old episode: {mp3_filename}")
    return history

_auth_instructions_printed = False

async def load_client():
    """Wait until credentials are available, then return an authenticated client."""
    global _auth_instructions_printed
    while True:
        try:
            logger.info("Attempting to load NotebookLM credentials from storage...")
            client = await NotebookLMClient.from_storage()
            logger.info("Successfully loaded NotebookLM credentials")
            return client
        except Exception:
            if not _auth_instructions_printed:
                logger.info("=" * 60)
                logger.info("AUTHENTICATION REQUIRED")
                logger.info("=" * 60)
                logger.info("Run these commands on your HOST machine (not in Docker):")
                logger.info("")
                logger.info("  pip install notebooklm-py playwright")
                logger.info("  python -m playwright install chromium")
                logger.info("  notebooklm login")
                logger.info("  cp ~/.notebooklm/storage_state.json ./auth/")
                logger.info("")
                logger.info("Bridge will start automatically once credentials are detected.")
                logger.info("=" * 60)
                _auth_instructions_printed = True
            await asyncio.sleep(10)


async def main_async():
    if not BASE_URL:
        logger.error("BASE_URL environment variable is required — set it in .env and restart.")
        while True:
            await asyncio.sleep(60)

    if NotebookLMClient is None:
        logger.error("notebooklm-py not installed — rebuild the container.")
        while True:
            await asyncio.sleep(60)

    history = load_history()
    recover_history_from_disk(history)
    save_history(history)
    logger.info(f"NoteCast bridge {APP_VERSION} starting")
    logger.info(f"Starting harvester with {len(history)} known artifacts")

    while True:
        try:
            logger.info("Checking for new audio artifacts")
            client = await load_client()
            async with client:
                notebooks = await client.notebooks.list()

                for notebook in notebooks:
                    notebook_id = notebook.id
                    notebook_title = getattr(notebook, 'title', '')

                    try:
                        artifacts = await client.artifacts.list(notebook_id)
                    except AttributeError:
                        logger.debug(f"No direct artifact listing for notebook {notebook_id}")
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

                        logger.info(f"Processing new audio artifact: {artifact_id} - {title}")

                        temp_path = Path(f'/tmp/{artifact_id}.mp4')
                        m4a_filename = f"{artifact_id}.m4a"
                        m4a_path = EPISODES_DIR / m4a_filename
                        try:
                            await client.artifacts.download_audio(notebook_id, str(temp_path), artifact_id=artifact_id)

                            if not remux_to_m4a(temp_path, m4a_path):
                                logger.error(f"Failed to remux audio for {artifact_id}")
                                continue

                            logger.info(f"Remuxed to M4A for {artifact_id}")
                        except RPCError as e:
                            logger.error(f"RPC error downloading artifact {artifact_id}: {e}")
                            continue
                        except Exception as e:
                            logger.error(f"Failed to process artifact {artifact_id}: {e}")
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
                        logger.info(f"Successfully processed artifact {artifact_id}")
                        save_history(history)
                        rebuild_feed(history)
                        await fire_webhook(title, notebook_title)

            history = purge_old_episodes(history)
            save_history(history)
            rebuild_feed(history)
            
            # Check token expiry and send notification if needed
            await check_token_expiry_and_notify()

        except Exception as e:
            logger.error(f"Error in main loop: {e}")

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

AUTH_STORAGE_FILE = AUTH_FILE

_last_updated: str = ''
_next_poll_at: float = 0.0
_poll_event = asyncio.Event()


async def handle_health(request):
    return web.json_response({'ok': True})


async def handle_status(request):
    history = load_history()
    now = time.time()
    token_expires_at, token_expires_in_days, token_expires_at_iso = get_token_expiry()
    
    status = {
        'version': APP_VERSION,
        'episodes': len(history),
        'last_updated': _last_updated,
        'next_poll_in': max(0, int(_next_poll_at - now)),
    }
    
    if token_expires_at is not None:
        status['token_expires_at'] = token_expires_at
        status['token_expires_in_days'] = token_expires_in_days
        status['token_expires_at_iso'] = token_expires_at_iso
    
    return web.json_response(status)


async def handle_episodes(request):
    history = load_history()
    episodes = sorted(
        [
            {
                'id': k,
                'title': v['title'],
                'notebook': v.get('notebook', ''),
                'created_at': v['created_at'],
                'url': f"{BASE_URL}/episodes/{v['mp3_filename']}",
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
    if BRIDGE_API_KEY:
        key = request.headers.get('X-Api-Key', '')
        if key != BRIDGE_API_KEY:
            return web.json_response({'ok': False, 'error': 'Unauthorized'}, status=401)
    _poll_event.set()
    return web.json_response({'ok': True})


async def handle_auth_upload(request):
    if BRIDGE_API_KEY:
        key = request.headers.get('X-Api-Key', '')
        if key != BRIDGE_API_KEY:
            return web.json_response({'ok': False, 'error': 'Unauthorized'}, status=401)

    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != 'file':
        return web.json_response({'ok': False, 'error': 'Missing field: file'}, status=400)

    AUTH_STORAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = await field.read()
    AUTH_STORAGE_FILE.write_bytes(data)
    logger.info("Auth credentials updated via API upload")
    return web.json_response({'ok': True})


async def run_http_server():
    app = web.Application()
    app.router.add_get('/health', handle_health)
    app.router.add_get('/api/status', handle_status)
    app.router.add_get('/api/episodes', handle_episodes)
    app.router.add_post('/api/poll', handle_poll)
    app.router.add_post('/auth/upload', handle_auth_upload)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', BRIDGE_PORT)
    await site.start()
    logger.info(f"HTTP server listening on port {BRIDGE_PORT}")
    await asyncio.Event().wait()  # run forever


def main():
    async def run():
        await asyncio.gather(
            run_http_server(),
            main_async(),
        )
    asyncio.run(run())


if __name__ == '__main__':
    main()