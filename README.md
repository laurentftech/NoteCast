# NoteCast

Turns your NotebookLM audio overviews into a personal podcast feed.

> **Disclaimer:** NoteCast uses [notebooklm-py](https://github.com/teng-lin/notebooklm-py), an **unofficial** reverse-engineered client for NotebookLM. It is not affiliated with or endorsed by Google. Use at your own risk — Google may change their API or ToS at any time.

## How it works

```
NotebookLM → notebooklm-py → harvester.py → MP3 → RSS feed → Caddy (HTTPS)
```

`notecast-bridge` polls all your notebooks every 5 minutes, downloads new audio artifacts, converts them to MP3, and updates an RSS feed you can subscribe to in any podcast app.

---

## Setup

### 1. Prerequisites

- Docker + Docker Compose
- A domain pointing to your server (for HTTPS)

### 2. Get the files

```bash
curl -O https://raw.githubusercontent.com/laurentftech/NoteCast/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/laurentftech/NoteCast/main/Caddyfile
curl -O https://raw.githubusercontent.com/laurentftech/NoteCast/main/.env.example
mkdir -p auth data public/episodes
```

The bridge image (`ghcr.io/laurentftech/notecast:latest`) is pulled automatically.

> **Build from source:** clone the repo and add a `docker-compose.override.yml`:
> ```yaml
> services:
>   notecast-bridge:
>     build: ./bridge
>     image: notecast-bridge
> ```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
BASE_URL=https://podcast.yourdomain.com   # public URL of this server
CADDY_DOMAIN=podcast.yourdomain.com       # same host, no protocol
```

### 4. Authenticate with NotebookLM

Login requires a real browser window (Google OAuth). Run it on any machine that has a display — your Mac, a laptop, etc.

```bash
# One-time setup on your Mac
pip3.10 install notebooklm-py playwright
python3.10 -m playwright install chromium
notebooklm login
# A browser window opens → sign in with Google → closes automatically
```

Then push the credentials to the bridge (works locally or remotely over the network):

```bash
# If running locally
cp ~/.notebooklm/storage_state.json ./auth/

# If running on Synology or remote server
curl -X POST http://your-synology:8080/auth/upload \
     -F "file=@$HOME/.notebooklm/storage_state.json"
```

The bridge picks up the credentials immediately — no restart needed.

> **Tip:** set `BRIDGE_API_KEY` in `.env` to protect the upload endpoint. Then add `-H "X-Api-Key: yourkey"` to the curl command.

> **Tip:** back up `./auth/storage_state.json`. If lost, repeat this step.

### 5. Subscribe

Your feed is live at:

```
https://podcast.yourdomain.com/feed.xml
```

Paste this URL into Overcast, Pocket Casts, Apple Podcasts, or any RSS-capable app.

---

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `BASE_URL` | yes | — | Public URL used in RSS episode links |
| `CADDY_DOMAIN` | yes | — | Domain for Caddy auto-HTTPS |
| `POLL_INTERVAL` | no | `300` | Seconds between polls |
| `RETENTION_DAYS` | no | `14` | Days before episodes are deleted |

---

## File layout

```
.
├── auth/                  # Persisted NotebookLM credentials (gitignored)
├── bridge/
│   ├── harvester.py       # Poll → download → convert → RSS
│   ├── requirements.txt
│   └── Dockerfile
├── public/
│   ├── feed.xml           # Generated RSS feed
│   └── episodes/          # Converted MP3 files
├── example/
│   └── app.py             # Optional REST API wrapper (reference)
├── docker-compose.yml
├── Caddyfile
└── .env                   # Your local config (not committed)
```

---

## Troubleshooting

**Bridge exits immediately**
```bash
docker compose logs notecast-bridge
```
Most likely: credentials missing. Follow step 3 above.

**Feed is empty**
- Check notebooks have audio overviews generated in NotebookLM
- `docker compose logs notecast-bridge` — look for "Processing new audio artifact"

**HTTPS not working**
- Verify `CADDY_DOMAIN` matches your DNS A record
- Ports 80 and 443 must be open on your firewall
