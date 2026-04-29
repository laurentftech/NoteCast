"""
RSS-to-NotebookLM audio pipeline for NoteCast.

Each configured RSS feed produces an independent output podcast feed:
  /public/episodes/{user}/{feed_name}/{id}.m4a
  /public/feed/{user}/{feed_name}.xml

Supports multi-user configuration via USERS env var.
Subscribe to https://yourserver.com/feed/{user}/{feed_name}.xml in your podcast app.
"""

import asyncio
import json
import logging
import os
import secrets
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import yaml
from podgen import Episode, Media, Podcast

from dotenv import load_dotenv
load_dotenv()

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from notebooklm import NotebookLMClient, RPCError
except ImportError:
    NotebookLMClient = None
    RPCError = Exception

# ── Config ───────────────────────────────────────────────────────────────────

CONFIG_PATH  = Path(os.getenv('TRANSFORMER_CONFIG', '/data/transformer.yaml'))
PUBLIC_DIR   = Path(os.getenv('PUBLIC_DIR', './public'))
BASE_URL     = os.getenv('BASE_URL', '').rstrip('/')
_DEFAULT_AUTH_FILE = Path('/root/.notebooklm/storage_state.json')
DATA_BASE = Path(os.getenv('DATA_BASE', '/data'))

# ── User model ─────────────────────────────────────────────────────────────

@dataclass
class User:
    name: str
    auth_file: Path
    db_file: Path
    episodes_dir: Path
    feed_dir: Path
    feed_token: str


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
        # Single-user backward compat
        token = _load_or_generate_feed_token(DATA_BASE / '.transformer_feed_token')
        return [User(
            name='default',
            auth_file=_DEFAULT_AUTH_FILE,
            db_file=DATA_BASE / 'transformer.db',
            episodes_dir=PUBLIC_DIR / 'episodes',
            feed_dir=PUBLIC_DIR / 'feed',
            feed_token=token,
        )]

    users = []
    for name in names:
        token = _load_or_generate_feed_token(DATA_BASE / f'{name}/.transformer_feed_token')
        users.append(User(
            name=name,
            auth_file=_DEFAULT_AUTH_FILE.parent / name / 'storage_state.json',
            db_file=DATA_BASE / f'{name}/transformer.db',
            episodes_dir=PUBLIC_DIR / 'episodes' / name,
            feed_dir=PUBLIC_DIR / 'feed' / name,
            feed_token=token,
        ))
    return users


USERS_CONFIG: list[User] = _build_users()
_MULTI_USER = bool(os.getenv('USERS', ''))

GENERATION_TIMEOUT = 45 * 60
MAX_RETRIES        = 1
VALID_STYLES       = {'brief', 'deep-dive', 'critique', 'debate'}
DEFAULT_STYLE      = 'deep-dive'

# Ensure user directories exist
for _u in USERS_CONFIG:
    _u.episodes_dir.mkdir(parents=True, exist_ok=True)
    _u.feed_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Database ──────────────────────────────────────────────────────────────────

def init_db(user: User):
    user.db_file.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(user.db_file)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                user_name   TEXT NOT NULL,
                feed_name   TEXT NOT NULL,
                feed_title  TEXT NOT NULL,
                episode_url TEXT NOT NULL,
                title       TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                style       TEXT NOT NULL DEFAULT 'deep-dive',
                notebook_id TEXT,
                artifact_id TEXT,
                duration    INTEGER,
                retries     INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE(user_name, episode_url)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs (user_name, status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feed   ON jobs (user_name, feed_name)")
        conn.commit()


def _conn(user: User):
    conn = sqlite3.connect(str(user.db_file))
    conn.row_factory = sqlite3.Row
    return conn


def episode_seen(user: User, episode_url: str) -> bool:
    with _conn(user) as conn:
        return conn.execute(
            "SELECT 1 FROM jobs WHERE user_name = ? AND episode_url = ?",
            (user.name, episode_url)
        ).fetchone() is not None


def create_job(user: User, feed_name: str, feed_title: str,
               episode_url: str, title: str, style: str) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _conn(user) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO jobs "
            "(id, user_name, feed_name, feed_title, episode_url, title, "
            " status, style, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (job_id, user.name, feed_name, feed_title, episode_url, title, style, now, now),
        )
        conn.commit()
    return job_id


def get_next_pending(user: User) -> dict | None:
    with _conn(user) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE user_name = ? AND status = 'pending' AND retries <= ? "
            "ORDER BY created_at LIMIT 1",
            (user.name, MAX_RETRIES),
        ).fetchone()
        return dict(row) if row else None


def update_job(user: User, job_id: str, **fields):
    fields['updated_at'] = datetime.now(timezone.utc).isoformat()
    clause = ', '.join(f"{k} = ?" for k in fields)
    with _conn(user) as conn:
        conn.execute(f"UPDATE jobs SET {clause} WHERE id = ?",
                     [*fields.values(), job_id])
        conn.commit()


def get_done_jobs(user: User, feed_name: str) -> list[dict]:
    with _conn(user) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE user_name = ? AND feed_name = ? AND status = 'done' "
            "ORDER BY created_at DESC",
            (user.name, feed_name),
        ).fetchall()
        return [dict(r) for r in rows]

# ── Config loader ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.warning(f"Config not found at {CONFIG_PATH}")
        return {'rss_feeds': [], 'poll_interval_minutes': 30, 'notebooklm': {}}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}

# ── RSS Poller ────────────────────────────────────────────────────────────────

async def fetch_episodes(session: aiohttp.ClientSession, url: str) -> tuple[str, list[dict]]:
    """Returns (feed_title, [{url, title}, ...])"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = await resp.text()
    except Exception as e:
        logger.warning(f"Feed fetch failed ({url}): {e}")
        return '', []

    feed   = feedparser.parse(text)
    f_title = getattr(feed.feed, 'title', url)
    results = []
    for entry in feed.entries:
        ep_url = None
        if getattr(entry, 'enclosures', None):
            ep_url = entry.enclosures[0].get('url')
        if not ep_url:
            ep_url = getattr(entry, 'link', None)
        if ep_url:
            results.append({
                'url':   ep_url,
                'title': getattr(entry, 'title', 'Untitled'),
            })
    return f_title, results


def _feeds_for_user(config: dict, user: User) -> list[dict]:
    feeds_cfg = config.get('rss_feeds', [])
    if isinstance(feeds_cfg, dict):
        user_feeds = feeds_cfg.get(user.name)
        if user_feeds is None and not _MULTI_USER and len(feeds_cfg) == 1:
            # Single-user convenience: allow one keyed section without forcing "default"
            user_feeds = next(iter(feeds_cfg.values()))
        return user_feeds if isinstance(user_feeds, list) else []
    return feeds_cfg if isinstance(feeds_cfg, list) else []


def _user_has_configured_feeds(config: dict, user: User) -> bool:
    for feed_cfg in _feeds_for_user(config, user):
        if not isinstance(feed_cfg, dict):
            continue
        url = feed_cfg.get('url', '').strip()
        name = feed_cfg.get('name', '').strip()
        if url and name:
            return True
    return False


async def poll_feeds(user: User, config: dict):
    nb_cfg        = config.get('notebooklm', {})
    default_style = nb_cfg.get('default_style', DEFAULT_STYLE)
    new_jobs      = 0

    async with aiohttp.ClientSession() as session:
        for feed_cfg in _feeds_for_user(config, user):
            url   = feed_cfg.get('url', '').strip()
            name  = feed_cfg.get('name', '').strip()
            style = feed_cfg.get('style', default_style)
            if style not in VALID_STYLES:
                style = default_style
            if not url or not name:
                logger.warning(f"[{user.name}] Feed missing url or name — skipping: {feed_cfg}")
                continue

            f_title, episodes = await fetch_episodes(session, url)
            # Allow config to override the feed title
            f_title = feed_cfg.get('title', f_title) or name

            for ep in episodes:
                if not episode_seen(user, ep['url']):
                    create_job(user, name, f_title, ep['url'], ep['title'], style)
                    logger.info(f"[{user.name}:{name}] Queued: {ep['title'][:70]}")
                    new_jobs += 1

    if new_jobs:
        logger.info(f"[{user.name}] Queued {new_jobs} new job(s)")

# ── NotebookLM Worker ─────────────────────────────────────────────────────────

async def wait_for_audio(client, notebook_id: str, job_id: str):
    deadline  = time.time() + GENERATION_TIMEOUT
    poll_secs = 30
    while time.time() < deadline:
        await asyncio.sleep(poll_secs)
        try:
            artifacts = await client.artifacts.list(notebook_id)
            for artifact in artifacts:
                kind = getattr(artifact, 'kind', None) or getattr(artifact, 'artifact_type', None)
                if kind and 'audio' in str(kind).lower():
                    return artifact
        except Exception as e:
            logger.warning(f"[{job_id}] Poll error: {e}")
        poll_secs = min(int(poll_secs * 1.5), 120)
    return None


async def process_job(user: User, job: dict, config: dict):
    job_id    = job['id']
    feed_name = job['feed_name']
    logger.info(f"[{user.name}:{feed_name}] Processing: {job['title'][:70]}")
    update_job(user, job_id, status='processing')

    nb_cfg       = config.get('notebooklm', {})
    instructions = nb_cfg.get('instructions', '')
    temp_path    = Path(f'/tmp/{job_id}.mp4')
    notebook_id  = None

    try:
        client = await NotebookLMClient.from_storage(str(user.auth_file))
        async with client:

            # 1. Create temporary notebook
            notebook    = await client.notebooks.create(title=f"[Transformer] {job['title'][:80]}")
            notebook_id = notebook.id
            update_job(user, job_id, notebook_id=notebook_id)
            logger.info(f"[{user.name}:{feed_name}] Notebook {notebook_id}")

            # 2. Add source URL
            await client.notebooks.add_source(notebook_id, url=job['episode_url'])
            logger.info(f"[{user.name}:{feed_name}] Source added")
            await asyncio.sleep(15)

            # 3. Generate audio
            gen_kwargs = {'style': job['style']}
            if instructions:
                gen_kwargs['instructions'] = instructions
            await client.notebooks.generate_audio(notebook_id, **gen_kwargs)
            update_job(user, job_id, status='generating')
            logger.info(f"[{user.name}:{feed_name}] Generation started (style={job['style']})")

            # 4. Poll for completion
            artifact = await wait_for_audio(client, notebook_id, job_id)
            if artifact is None:
                raise TimeoutError(f"Timed out after {GENERATION_TIMEOUT}s")

            artifact_id = artifact.id
            update_job(user, job_id, artifact_id=artifact_id)
            logger.info(f"[{user.name}:{feed_name}] Audio ready: {artifact_id}")

            # 5. Download + remux
            episodes_dir = user.episodes_dir / feed_name
            episodes_dir.mkdir(parents=True, exist_ok=True)
            await client.artifacts.download_audio(notebook_id, str(temp_path), artifact_id=artifact_id)

            m4a_filename = f"{artifact_id}.m4a"
            m4a_path     = episodes_dir / m4a_filename
            subprocess.run(
                ['ffmpeg', '-y', '-i', str(temp_path), '-c', 'copy', str(m4a_path)],
                check=True, capture_output=True,
            )
            temp_path.unlink(missing_ok=True)

            # 6. Get duration
            duration = _get_duration(m4a_path)
            update_job(user, job_id, status='done', duration=duration)
            logger.info(f"[{user.name}:{feed_name}] Saved {m4a_filename}")

            # 7. Delete notebook
            try:
                await client.notebooks.delete(notebook_id)
                logger.info(f"[{user.name}:{feed_name}] Notebook deleted")
            except Exception as e:
                logger.warning(f"[{user.name}:{feed_name}] Notebook delete failed: {e}")

            # 8. Rebuild this feed's RSS
            rebuild_feed(user, feed_name, job['feed_title'])
            logger.info(f"[{user.name}:{feed_name}] Feed rebuilt")

    except Exception as e:
        logger.error(f"[{user.name}:{feed_name}] Failed: {e}")
        err_msg = str(e)
        if isinstance(e, FileNotFoundError) or 'Storage file not found:' in err_msg:
            logger.warning(
                f"[{user.name}:{feed_name}] Auth storage missing; keeping job pending for retry"
            )
            update_job(user, job_id, status='pending')
        else:
            retries = job.get('retries', 0) + 1
            update_job(
                user,
                job_id,
                status='failed' if retries > MAX_RETRIES else 'pending',
                retries=retries,
            )
        temp_path.unlink(missing_ok=True)

# ── Feed builder ──────────────────────────────────────────────────────────────

def rebuild_feed(user: User, feed_name: str, feed_title: str):
    base_url = os.getenv('BASE_URL', '').rstrip('/') or BASE_URL
    if not base_url:
        logger.warning(f"[{user.name}] BASE_URL not set — skipping feed rebuild")
        return

    jobs = get_done_jobs(user, feed_name)
    env_multi_user = os.getenv('_MULTI_USER', '').lower() in {'1', 'true', 'yes', 'on'}
    prefix = f"{user.name}/" if (_MULTI_USER or env_multi_user) else ""
    ep_base  = f"{base_url}/episodes/{prefix}{feed_name}/"
    feed_url = f"{base_url}/feed/{prefix}{feed_name}.xml?token={user.feed_token}"

    podcast = Podcast(
        name=feed_title,
        description=f"NoteCast audio overviews — {feed_title}",
        website=feed_url,
        explicit=False,
    )

    for job in jobs:
        if not job.get('artifact_id'):
            continue
        m4a_filename = f"{job['artifact_id']}.m4a"
        m4a_path     = user.episodes_dir / feed_name / m4a_filename
        file_size    = m4a_path.stat().st_size if m4a_path.exists() else 0
        try:
            pub_date = datetime.fromisoformat(job['created_at'])
        except Exception:
            pub_date = datetime.now(timezone.utc)
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)

        podcast.add_episode(Episode(
            title=job['title'],
            media=Media(f"{ep_base}{m4a_filename}", file_size, type='audio/mp4'),
            publication_date=pub_date,
        ))

    feed_file = user.feed_dir / f'{feed_name}.xml'
    feed_file.parent.mkdir(parents=True, exist_ok=True)
    podcast.rss_file(str(feed_file))

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_duration(path: Path) -> int | None:
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True, text=True, check=True,
        )
        return int(float(result.stdout.strip()))
    except Exception:
        return None

# ── Main loop ─────────────────────────────────────────────────────────────────

async def main_async():
    if NotebookLMClient is None:
        logger.error("notebooklm-py not installed")
        return
    if feedparser is None:
        logger.error("feedparser not installed")
        return
    if not BASE_URL:
        logger.error("BASE_URL not set — required for feed generation")
        return

    # Initialize databases for all users
    for user in USERS_CONFIG:
        init_db(user)
    
    logger.info(f"RSS Transformer started with {len(USERS_CONFIG)} user(s)")

    last_poll = {}
    for user in USERS_CONFIG:
        last_poll[user.name] = 0.0

    while True:
        config = load_config()
        poll_secs = config.get('poll_interval_minutes', 30) * 60

        active_users = [u for u in USERS_CONFIG if _user_has_configured_feeds(config, u)]

        # Poll feeds only for users with configured feeds
        for user in active_users:
            if time.time() - last_poll[user.name] >= poll_secs:
                await poll_feeds(user, config)
                last_poll[user.name] = time.time()

        # Process next pending job only for users with configured feeds
        for user in active_users:
            job = get_next_pending(user)
            if job:
                await process_job(user, job, config)
                break  # Process one job at a time, then check feeds again
        else:
            await asyncio.sleep(60)


def main():
    asyncio.run(main_async())


if __name__ == '__main__':
    main()
