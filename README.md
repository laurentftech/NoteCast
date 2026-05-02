# NoteCast

Turns your NotebookLM audio overviews into a personal podcast feed.

> **Disclaimer:** NoteCast uses [notebooklm-py](https://github.com/teng-lin/notebooklm-py), an **unofficial** reverse-engineered client for NotebookLM. It is not affiliated with or endorsed by Google. Use at your own risk — Google may change their API or ToS at any time.


![NoteCast web UI](docs/notecast-web.png)

<img src="docs/apple-podcast.png" width="375" alt="Apple Podcasts">

## How it works

```
NotebookLM → notebooklm-py → harvester.py → MP3 → RSS feed → Caddy (HTTPS)
```

`notecast-bridge` polls all your notebooks every day (customizable), downloads new audio artifacts, converts them to MP3, and updates an RSS feed you can subscribe to in any podcast app.

NoteCast supports two modes:
- **Single-user** (default) — no login required, one feed at `/feed.xml`
- **Multi-user** — Google sign-in for the web UI, one private feed URL per user

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
mkdir -p config
curl -o config/transformer.yaml https://raw.githubusercontent.com/laurentftech/NoteCast/main/bridge/transformer.yaml.example
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
TRANSFORMER_CONFIG=/config/transformer.yaml
```

`TRANSFORMER_CONFIG` points to the YAML file inside the container. The file itself stays outside the container at `./config/transformer.yaml`.

### 4. Authenticate with NotebookLM

Login requires a real browser window. Run it on any machine with a display — your Mac, a laptop, etc.

```bash
pip3.10 install notebooklm-py playwright
python3.10 -m playwright install chromium
notebooklm login
# A browser window opens → sign in with Google → closes automatically
```

Then push the credentials to the bridge:

```bash
# Single-user: copy directly
cp ~/.notebooklm/storage_state.json ./auth/

# Or upload over the network (local or remote)
curl -X POST http://your-server:8080/api/auth/upload \
     -F "file=@$HOME/.notebooklm/storage_state.json"
```

The bridge picks up credentials immediately — no restart needed.

> **Tip:** set `BRIDGE_API_KEY` in `.env` to protect the upload endpoint. Add `-H "X-Api-Key: yourkey"` to the curl command.

### 5. Subscribe

Your feed is live at:

```
https://podcast.yourdomain.com/feed.xml
```

Paste this URL into Overcast, Pocket Casts, Apple Podcasts, or any RSS-capable app.

---

## Multi-user setup

Multiple users each get an independent feed, episode library, and NotebookLM session. The web UI requires Google sign-in.

### 1. Create a Google OAuth client

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → **APIs & Services** → **Credentials**
2. Create an OAuth 2.0 Client ID → type **Web application**
3. Under **Authorised JavaScript origins**, add your `BASE_URL` (e.g. `https://podcast.yourdomain.com`) and `http://localhost` for local testing
4. Copy the **Client ID**

Also add your domain under **APIs & Services** → **OAuth consent screen** → **Authorised domains**.

### 2. Configure `.env`

```env
# Comma-separated nicknames (drives all per-user paths)
USERS=alice,bob

# Google email each user signs in with
USER_ALICE_EMAIL=alice@gmail.com
USER_BOB_EMAIL=bob@gmail.com

# Google OAuth client ID (enables sign-in button in the web UI)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com

# Optional: per-user webhooks (fall back to global WEBHOOK_URL if not set)
# USER_ALICE_WEBHOOK_URL=https://ntfy.sh/alice-notecast
# USER_BOB_WEBHOOK_URL=https://ntfy.sh/bob-notecast
```

### 3. Authenticate each user

Each user must authenticate separately. Run `notebooklm login` for each account, then place the credentials in the right slot:

```bash
# Copy directly on the server (simplest)
cp storage_state_alice.json ./auth/alice/storage_state.json
cp storage_state_bob.json   ./auth/bob/storage_state.json
```

Alternatively, each user can upload via the web UI: sign in with Google, open the admin panel, and use the **Re-authenticate** section. The file is saved to that user's slot automatically.

### 4. Subscribe

Each user gets a private feed URL with a secret token — find it in the admin panel or in the bridge startup logs:

```
https://podcast.yourdomain.com/feed/aBcDeFgH....xml
```

The token is unguessable and stable (regenerated only if the token file is deleted). Podcast apps use this URL directly — no OAuth needed.

---

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `BASE_URL` | yes | — | Public URL used in RSS episode links |
| `CADDY_DOMAIN` | yes | — | Domain for Caddy auto-HTTPS |
| `POLL_INTERVAL` | no | `86400` | Seconds between automatic polls |
| `RETENTION_DAYS` | no | `14` | Days before episodes are deleted |
| `BRIDGE_API_KEY` | no | *(none)* | Protects `/api/auth/upload` and `/api/poll` — requests must include `X-Api-Key: <value>` |
| `FEED_IMAGE_URL` | no | *(none)* | Cover art URL for the RSS feed (1400×1400px); auto-detected from `public/cover.jpg` if absent |
| `BRIDGE_PORT` | no | `8080` | Internal HTTP port for the bridge |
| `WEBHOOK_URL` | no | *(none)* | HTTP endpoint to POST when a new episode is downloaded (ntfy, Slack, Discord, …) |
| `WEBHOOK_HEADERS` | no | *(none)* | JSON object of headers sent with each webhook — e.g. `{"Authorization": "Bearer token"}` |
| `WEBHOOK_LINK` | no | *(none)* | URL included as `click` in ntfy notifications (e.g. Apple Podcasts deep link) |
| `TOKEN_EXPIRY_WARN_DAYS` | no | `7` | Days before token expiry to send a warning (requires `WEBHOOK_URL`) |
| `USERS` | no | *(none)* | Comma-separated user nicknames; enables multi-user mode |
| `GOOGLE_CLIENT_ID` | no | *(none)* | Google OAuth client ID; required when `USERS` is set |
| `USER_{NAME}_EMAIL` | multi | — | Google email for each user (e.g. `USER_ALICE_EMAIL`) |
| `USER_{NAME}_WEBHOOK_URL` | no | `WEBHOOK_URL` | Per-user webhook URL override |
| `USER_{NAME}_WEBHOOK_HEADERS` | no | `WEBHOOK_HEADERS` | Per-user webhook headers override |
| `USER_{NAME}_WEBHOOK_LINK` | no | `WEBHOOK_LINK` | Per-user webhook click URL override |

---

## Token expiry monitoring

The admin panel displays your NotebookLM token expiry with color-coded warnings:

- **Green** — more than 7 days remaining
- **Orange** — 2–7 days remaining
- **Red** — expires today, tomorrow, or already expired

When `WEBHOOK_URL` is configured, the bridge sends a plain-text notification when the token is within the warning threshold. Notifications are rate-limited to once per 24 hours. The request uses ntfy-compatible headers:

```
POST https://ntfy.sh/your-topic
X-Title: Token expires in 3d
X-Tags: headphones
Content-Type: text/plain

NotebookLM token expires in 3 day(s)
```

Set `TOKEN_EXPIRY_WARN_DAYS` to adjust the warning window (default: `7`). Renew by running `notebooklm login` again and re-uploading `storage_state.json`.

---

## API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/config` | — | Returns `google_client_id` and `multi_user` flag for the web UI |
| `GET` | `/api/status` | bearer / key | Bridge status: episode count, next poll, token expiry, feed URL |
| `GET` | `/api/episodes` | bearer / key | Episode list as JSON |
| `POST` | `/api/poll` | bearer / key | Trigger an immediate poll |
| `POST` | `/api/webhook/test` | bearer / key | Send a test webhook notification |
| `POST` | `/api/auth/upload` | bearer / key | Upload a new `storage_state.json` |
| `GET` | `/health` | — | Health check |
| `GET` | `/feed.xml` | — | RSS feed (single-user) |
| `GET` | `/feed/{token}.xml` | — | RSS feed (multi-user, token in URL) |

*bearer = `Authorization: Bearer <google-id-token>` (multi-user mode)*  
*key = `X-Api-Key: <value>` when `BRIDGE_API_KEY` is set*

**`/api/status` response**
```json
{
  "version": "0.11.0",
  "episodes": 42,
  "last_updated": "2026-04-28T20:00:00+00:00",
  "next_poll_in": 43200,
  "webhook_enabled": true,
  "feed_url": "https://podcast.yourdomain.com/feed/aBcDeFgH....xml",
  "token_expires_in_days": 7,
  "token_expires_at_iso": "2026-05-26T12:00:00+00:00"
}
```
`token_expires_*` fields are omitted if no credentials are loaded.

---

## File layout

**Single-user**
```
.
├── auth/
│   └── storage_state.json   # NotebookLM credentials
├── data/
│   ├── history.json          # Downloaded episodes index
│   └── .feed_token           # Secret feed token
├── public/
│   ├── index.html
│   ├── feed.xml              # RSS feed
│   └── episodes/             # MP3 files
├── docker-compose.yml
├── Caddyfile
└── .env
```

**Multi-user**
```
.
├── auth/
│   ├── alice/
│   │   └── storage_state.json
│   └── bob/
│       └── storage_state.json
├── data/
│   ├── alice/
│   │   ├── history.json
│   │   └── .feed_token
│   └── bob/
│       ├── history.json
│       └── .feed_token
├── public/
│   ├── index.html
│   ├── episodes/
│   │   ├── alice/
│   │   └── bob/
│   └── feed/
│       ├── <alice-token>.xml
│       └── <bob-token>.xml
├── docker-compose.yml
├── Caddyfile
└── .env
```

---

## Updating

```bash
cd /path/to/notecast
curl -o public/index.html https://raw.githubusercontent.com/laurentftech/NoteCast/main/public/index.html
docker compose pull
docker compose up -d
```

Episode files and credentials are untouched. The bridge container is replaced with the new image; `index.html` is updated in place.

**On Synology (Container Manager UI)**
1. *Registry* → search `ghcr.io/laurentftech/notecast` → Download latest
2. *Container* → select `notecast-bridge` → Action → Stop → Clear → Start

Or SSH into the NAS and run the commands above from the folder containing `docker-compose.yml`.

---

## Troubleshooting

**Bridge exits immediately**
```bash
docker compose logs notecast-bridge
```
Most likely: credentials missing. Follow step 4 above.

**Feed is empty**
- Check notebooks have audio overviews generated in NotebookLM
- `docker compose logs notecast-bridge` — look for "Processing new audio artifact"

**Sign-in button doesn't appear**
- Verify `GOOGLE_CLIENT_ID` is set in `.env`
- Verify the page origin is listed under Authorised JavaScript origins in Google Cloud Console

**HTTPS not working**
- Verify `CADDY_DOMAIN` matches your DNS A record
- Ports 80 and 443 must be open on your firewall
- See Caddy [documentation](https://caddyserver.com) for more details
