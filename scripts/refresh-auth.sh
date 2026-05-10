#!/usr/bin/env bash
# Refresh NoteCast NotebookLM credentials from a local browser session.
#
# Usage:
#   ./scripts/refresh-auth.sh --url https://notecast.example.com [options]
#
# Options:
#   --url      <url>      NoteCast base URL (required)
#   --browser  <name>     Browser to extract cookies from (default: chrome)
#                         Supported: chrome, firefox, safari, edge, chromium, brave
#   --token    <token>    Feed token for auth (required in multi-user mode;
#                         found in your RSS feed URL: /feed/<token>.xml)
#   --profile  <name>     notebooklm-py profile name (optional)

set -euo pipefail

BROWSER="chrome"
NOTECAST_URL=""
FEED_TOKEN=""
PROFILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)     NOTECAST_URL="$2"; shift 2 ;;
    --browser) BROWSER="$2";      shift 2 ;;
    --token)   FEED_TOKEN="$2";   shift 2 ;;
    --profile) PROFILE="$2";      shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "$NOTECAST_URL" ]]; then
  echo "Error: --url is required"
  exit 1
fi

NOTECAST_URL="${NOTECAST_URL%/}"

# Step 1: extract cookies from browser
echo "Extracting cookies from $BROWSER..."
if [[ -n "$PROFILE" ]]; then
  notebooklm --profile "$PROFILE" login --browser-cookies "$BROWSER"
else
  notebooklm login --browser-cookies "$BROWSER"
fi

PROFILE_NAME="${PROFILE:-default}"
STORAGE_STATE="$HOME/.notebooklm/profiles/$PROFILE_NAME/storage_state.json"
if [[ ! -f "$STORAGE_STATE" ]]; then
  echo "Error: $STORAGE_STATE not found after login"
  exit 1
fi

# Step 2: upload to NoteCast
echo "Uploading to $NOTECAST_URL ..."
AUTH_HEADER=""
if [[ -n "$FEED_TOKEN" ]]; then
  AUTH_HEADER="-H \"Authorization: Bearer $FEED_TOKEN\""
fi

HTTP_STATUS=$(curl -s -o /tmp/notecast-upload-response.json -w "%{http_code}" \
  ${FEED_TOKEN:+-H "Authorization: Bearer $FEED_TOKEN"} \
  -F "file=@$STORAGE_STATE" \
  "$NOTECAST_URL/api/auth/upload")

BODY=$(cat /tmp/notecast-upload-response.json)
rm -f /tmp/notecast-upload-response.json

if [[ "$HTTP_STATUS" == "200" ]]; then
  echo "✓ Credentials updated successfully"
else
  echo "Error: server returned HTTP $HTTP_STATUS"
  echo "$BODY"
  exit 1
fi
