# NoteCast

Turns your NotebookLM audio overviews into a personal podcast feed.

> **Disclaimer:** NoteCast uses [notebooklm-py](https://github.com/teng-lin/notebooklm-py), an **unofficial** reverse-engineered client for NotebookLM. It is not affiliated with or endorsed by Google. Use at your own risk — Google may change their API or ToS at any time.


![NoteCast web UI](docs/notecast-web.png)

<img src="docs/apple-podcast.png" width="375" alt="Apple Podcasts">

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
curl -o public/index.html https://raw.githubusercontent.com/laurentftech/NoteCast/main/public/index.html
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
| `POLL_INTERVAL` | no | `86400` | Seconds between automatic polls |
| `RETENTION_DAYS` | no | `14` | Days before episodes are deleted |
| `BRIDGE_API_KEY` | no | *(none)* | If set, protects `/auth/upload` and `/api/poll` — requests must include `X-Api-Key: <value>` |
| `FEED_IMAGE_URL` | no | *(none)* | Cover art URL for the RSS feed (1400×1400px recommended); auto-detected from `public/cover.jpg` if absent |
| `BRIDGE_PORT` | no | `8080` | Internal HTTP port for the bridge |
| `WEBHOOK_URL` | no | *(none)* | HTTP endpoint to POST when a new episode is downloaded (ntfy, Slack, Discord, …) |
| `WEBHOOK_HEADERS` | no | *(none)* | JSON object of headers sent with each webhook request — e.g. `{"Authorization": "Bearer token"}` |
| `WEBHOOK_LINK` | no | *(none)* | URL included as `click` field in ntfy notifications (e.g. Apple Podcasts deep link) |

---

## API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/status` | — | Bridge status: episode count, last poll, next poll countdown |
| `GET` | `/api/episodes` | — | Episode list as JSON |
| `POST` | `/api/poll` | key | Trigger an immediate poll (skips the wait) |
| `POST` | `/auth/upload` | key | Upload a new `storage_state.json` |
| `GET` | `/health` | — | Health check |
| `GET` | `/feed.xml` | — | RSS feed |

*key = requires `X-Api-Key` header when `BRIDGE_API_KEY` is set*

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
│   ├── index.html         # Web UI
│   ├── feed.xml           # Generated RSS feed
│   └── episodes/          # Converted MP3 files
├── example/
│   └── app.py             # Optional REST API wrapper (reference)
├── docker-compose.yml
├── Caddyfile
└── .env                   # Your local config (not committed)
```

---

## Updating

```bash
cd /path/to/notecast   # folder containing docker-compose.yml
curl -o public/index.html https://raw.githubusercontent.com/laurentftech/NoteCast/main/public/index.html
docker compose pull
docker compose up -d
```

Episode files and credentials are untouched. The bridge container is replaced with the new image; `index.html` is updated in place (Caddy serves it directly from disk).

**On Synology (Container Manager UI)**
1. *Registry* → search `ghcr.io/laurentftech/notecast` → Download latest
2. *Container* → select `notecast-bridge` → Action → Stop → Clear → Start

Or SSH into the NAS and run the two commands above from the folder where your `docker-compose.yml` lives.

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
