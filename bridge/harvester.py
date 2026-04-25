import os
import json
import time
import logging
import subprocess
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from podgen import Podcast, Episode, Media
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
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '300'))
RETENTION_DAYS = int(os.getenv('RETENTION_DAYS', '14'))
BRIDGE_PORT = int(os.getenv('BRIDGE_PORT', '8080'))
BRIDGE_API_KEY = os.getenv('BRIDGE_API_KEY', '')  # optional — set to restrict /auth/upload
FEED_IMAGE_URL = os.getenv('FEED_IMAGE_URL', '')

# Paths
PUBLIC_DIR = Path('/public')
EPISODES_DIR = PUBLIC_DIR / 'episodes'
HISTORY_FILE = Path('/app/history.json')
FEED_FILE = PUBLIC_DIR / 'feed.xml'

# Ensure directories exist
EPISODES_DIR.mkdir(parents=True, exist_ok=True)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

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

def convert_to_mp3(wav_path, mp3_path):
    """Convert WAV to MP3 using ffmpeg."""
    try:
        # ffmpeg -i input.wav -ac 1 -ab 96k -af loudnorm output.mp3
        cmd = [
            'ffmpeg',
            '-i', str(wav_path),
            '-ac', '1',
            '-ab', '96k',
            '-af', 'loudnorm',
            str(mp3_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed: {e.stderr.decode()}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during conversion: {e}")
        return None

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
            media=Media(media_url, file_size, type='audio/mpeg'),
            publication_date=pub_date,
        )
        podcast.add_episode(episode)

    podcast.rss_file(str(FEED_FILE))
    logger.info(f"Feed rebuilt with {len(sorted_history)} episodes")

def purge_old_episodes(history):
    """Remove episodes older than RETENTION_DAYS."""
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    to_remove = []
    for artifact_id, data in history.items():
        try:
            created = datetime.fromisoformat(data['created_at'].replace('Z', '+00:00'))
            if created < cutoff:
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
                        mp3_filename = f"{artifact_id}.mp3"
                        mp3_path = EPISODES_DIR / mp3_filename
                        try:
                            await client.artifacts.download_audio(notebook_id, str(temp_path), artifact_id=artifact_id)

                            if not convert_to_mp3(temp_path, mp3_path):
                                logger.error(f"Failed to convert audio to MP3 for {artifact_id}")
                                continue

                            logger.info(f"Successfully converted and saved MP3 for {artifact_id}")
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
                            'mp3_filename': mp3_filename,
                            'notebook': notebook_title,
                        }
                        logger.info(f"Successfully processed artifact {artifact_id}")
                        save_history(history)
                        rebuild_feed(history)

            history = purge_old_episodes(history)
            save_history(history)
            rebuild_feed(history)

        except Exception as e:
            logger.error(f"Error in main loop: {e}")

        global _last_updated, _next_poll_at
        _last_updated = datetime.now(timezone.utc).isoformat()
        _next_poll_at = time.time() + POLL_INTERVAL
        logger.info(f"Sleeping for {POLL_INTERVAL} seconds")
        await asyncio.sleep(POLL_INTERVAL)

AUTH_STORAGE_FILE = Path('/root/.notebooklm/storage_state.json')

_last_updated: str = ''
_next_poll_at: float = 0.0


async def handle_health(request):
    return web.json_response({'ok': True})


async def handle_status(request):
    history = load_history()
    now = time.time()
    return web.json_response({
        'episodes': len(history),
        'last_updated': _last_updated,
        'next_poll_in': max(0, int(_next_poll_at - now)),
    })


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
            }
            for k, v in history.items()
        ],
        key=lambda x: x['created_at'],
        reverse=True,
    )
    return web.json_response(episodes)


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