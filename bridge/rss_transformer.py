"""
RSS-to-NotebookLM audio pipeline for NoteCast.

Polls configured RSS feeds, generates NotebookLM audio overviews,
deposits m4a files into the NoteCast episode library, then triggers
feed rebuild via the bridge HTTP API.

Runs as a separate process alongside harvester.py. Never modifies
harvester.py or any existing NoteCast component.
"""

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import yaml

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from notebooklm import NotebookLMClient, RPCError
except ImportError:
    NotebookLMClient = None
    RPCError = Exception

# ── Config ──────────────────────────────────────────────────────────────────

CONFIG_PATH   = Path(os.getenv('TRANSFORMER_CONFIG', '/data/transformer.yaml'))
DB_PATH       = Path(os.getenv('TRANSFORMER_DB',     '/data/transformer.db'))
PUBLIC_DIR    = Path('/public')
DATA_DIR      = Path('/data')
BRIDGE_URL    = os.getenv('BRIDGE_URL', 'http://localhost:8080')
BRIDGE_API_KEY = os.getenv('BRIDGE_API_KEY', '')

EPISODES_DIR  = PUBLIC_DIR / 'episodes'
HISTORY_FILE  = DATA_DIR / 'history.json'

GENERATION_TIMEOUT = 45 * 60   # 45 min
MAX_RETRIES        = 1
VALID_STYLES       = {'brief', 'deep-dive', 'critique', 'debate'}
DEFAULT_STYLE      = 'deep-dive'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Database ─────────────────────────────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                rss_url     TEXT NOT NULL,
                episode_url TEXT NOT NULL UNIQUE,
                title       TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                style       TEXT NOT NULL DEFAULT 'deep-dive',
                notebook_id TEXT,
                artifact_id TEXT,
                retries     INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs (status, created_at)")
        conn.commit()


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def episode_seen(episode_url: str) -> bool:
    with _conn() as conn:
        return conn.execute(
            "SELECT 1 FROM jobs WHERE episode_url = ?", (episode_url,)
        ).fetchone() is not None


def create_job(rss_url: str, episode_url: str, title: str, style: str) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO jobs "
            "(id, rss_url, episode_url, title, status, style, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (job_id, rss_url, episode_url, title, style, now, now),
        )
        conn.commit()
    return job_id


def get_next_pending() -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'pending' AND retries <= ? "
            "ORDER BY created_at LIMIT 1",
            (MAX_RETRIES,),
        ).fetchone()
        return dict(row) if row else None


def update_job(job_id: str, **fields):
    fields['updated_at'] = datetime.now(timezone.utc).isoformat()
    clause = ', '.join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE jobs SET {clause} WHERE id = ?",
                     [*fields.values(), job_id])
        conn.commit()

# ── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.warning(f"Config not found at {CONFIG_PATH} — no feeds to poll")
        return {'rss_feeds': [], 'poll_interval_minutes': 30, 'notebooklm': {}}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}

# ── RSS Poller ────────────────────────────────────────────────────────────────

async def fetch_episodes(session: aiohttp.ClientSession, url: str) -> list[dict]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = await resp.text()
    except Exception as e:
        logger.warning(f"Feed fetch failed ({url}): {e}")
        return []

    feed = feedparser.parse(text)
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
    return results


async def poll_feeds(config: dict):
    nb_cfg        = config.get('notebooklm', {})
    default_style = nb_cfg.get('default_style', DEFAULT_STYLE)
    feeds         = config.get('rss_feeds', [])
    new_jobs      = 0

    async with aiohttp.ClientSession() as session:
        for feed_cfg in feeds:
            url   = feed_cfg.get('url', '').strip()
            style = feed_cfg.get('style', default_style)
            if style not in VALID_STYLES:
                style = default_style
            if not url:
                continue
            for ep in await fetch_episodes(session, url):
                if not episode_seen(ep['url']):
                    create_job(url, ep['url'], ep['title'], style)
                    logger.info(f"Queued: {ep['title'][:70]}")
                    new_jobs += 1

    if new_jobs:
        logger.info(f"Queued {new_jobs} new job(s)")

# ── NotebookLM Worker ─────────────────────────────────────────────────────────

async def wait_for_audio(client, notebook_id: str, job_id: str):
    """Poll until audio artifact is ready. Returns artifact or None on timeout."""
    deadline     = time.time() + GENERATION_TIMEOUT
    poll_secs    = 30
    while time.time() < deadline:
        await asyncio.sleep(poll_secs)
        try:
            artifacts = await client.artifacts.list(notebook_id)
            for artifact in artifacts:
                kind = getattr(artifact, 'kind', None) or getattr(artifact, 'artifact_type', None)
                if kind and 'audio' in str(kind).lower():
                    return artifact
        except Exception as e:
            logger.warning(f"[{job_id}] Artifact poll error: {e}")
        poll_secs = min(int(poll_secs * 1.5), 120)
    return None


async def process_job(job: dict, config: dict):
    job_id = job['id']
    logger.info(f"[{job_id}] Start: {job['title'][:70]}")
    update_job(job_id, status='processing')

    nb_cfg       = config.get('notebooklm', {})
    instructions = nb_cfg.get('instructions', '')
    notebook_id  = None
    temp_path    = Path(f'/tmp/{job_id}.mp4')

    try:
        client = await NotebookLMClient.from_storage()
        async with client:

            # 1. Create temporary notebook
            notebook    = await client.notebooks.create(title=f"[Transformer] {job['title'][:80]}")
            notebook_id = notebook.id
            update_job(job_id, notebook_id=notebook_id)
            logger.info(f"[{job_id}] Notebook {notebook_id}")

            # 2. Add episode URL as source
            await client.notebooks.add_source(notebook_id, url=job['episode_url'])
            logger.info(f"[{job_id}] Source added")

            # 3. Brief pause for indexing
            await asyncio.sleep(15)

            # 4. Trigger audio generation
            gen_kwargs = {'style': job['style']}
            if instructions:
                gen_kwargs['instructions'] = instructions
            await client.notebooks.generate_audio(notebook_id, **gen_kwargs)
            update_job(job_id, status='generating')
            logger.info(f"[{job_id}] Generation started (style={job['style']})")

            # 5. Poll until done
            artifact = await wait_for_audio(client, notebook_id, job_id)
            if artifact is None:
                raise TimeoutError(f"Timed out after {GENERATION_TIMEOUT}s")

            artifact_id = artifact.id
            update_job(job_id, artifact_id=artifact_id)
            logger.info(f"[{job_id}] Audio ready: {artifact_id}")

            # 6. Download
            EPISODES_DIR.mkdir(parents=True, exist_ok=True)
            await client.artifacts.download_audio(notebook_id, str(temp_path), artifact_id=artifact_id)

            m4a_filename = f"{artifact_id}.m4a"
            m4a_path     = EPISODES_DIR / m4a_filename
            subprocess.run(
                ['ffmpeg', '-y', '-i', str(temp_path), '-c', 'copy', str(m4a_path)],
                check=True, capture_output=True,
            )
            temp_path.unlink(missing_ok=True)
            logger.info(f"[{job_id}] Saved {m4a_filename}")

            # 7. Register in NoteCast history
            _register_episode(artifact_id, job['title'], m4a_filename)

            # 8. Trigger feed rebuild
            await _ping_poll()

            # 9. Delete notebook
            try:
                await client.notebooks.delete(notebook_id)
                logger.info(f"[{job_id}] Notebook deleted")
            except Exception as e:
                logger.warning(f"[{job_id}] Notebook delete failed: {e}")

            update_job(job_id, status='done')
            logger.info(f"[{job_id}] Done")

    except Exception as e:
        logger.error(f"[{job_id}] Failed: {e}")
        retries = job.get('retries', 0) + 1
        update_job(
            job_id,
            status='failed' if retries > MAX_RETRIES else 'pending',
            retries=retries,
        )
        temp_path.unlink(missing_ok=True)

# ── NoteCast integration ──────────────────────────────────────────────────────

def _register_episode(artifact_id: str, title: str, m4a_filename: str):
    history = {}
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    now = datetime.now(timezone.utc).isoformat()
    history[artifact_id] = {
        'title':         title,
        'created_at':    now,
        'downloaded_at': now,
        'mp3_filename':  m4a_filename,
        'notebook':      'RSS Transformer',
        'duration':      None,
    }
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    logger.info(f"Registered {artifact_id} in history.json")


async def _ping_poll():
    """Trigger bridge feed rebuild without importing harvester."""
    headers = {}
    if BRIDGE_API_KEY:
        headers['X-Api-Key'] = BRIDGE_API_KEY
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BRIDGE_URL}/api/poll",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                logger.info(f"Poll triggered: {resp.status}")
    except Exception as e:
        logger.warning(f"Poll ping failed (feed will rebuild on next harvest): {e}")

# ── Main loop ─────────────────────────────────────────────────────────────────

async def main_async():
    if NotebookLMClient is None:
        logger.error("notebooklm-py not installed")
        return
    if feedparser is None:
        logger.error("feedparser not installed — add it to requirements.txt")
        return

    init_db()
    logger.info("RSS Transformer started")

    last_poll = 0.0
    while True:
        config       = load_config()
        poll_secs    = config.get('poll_interval_minutes', 30) * 60

        if time.time() - last_poll >= poll_secs:
            await poll_feeds(config)
            last_poll = time.time()

        job = get_next_pending()
        if job:
            await process_job(job, config)
        else:
            await asyncio.sleep(60)


def main():
    asyncio.run(main_async())


if __name__ == '__main__':
    main()
