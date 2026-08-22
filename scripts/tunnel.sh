#!/usr/bin/env bash
# Demo deployment via Cloudflare quick tunnel.
#
# SECURITY: while the tunnel is up, /api/analyze is reachable by anyone
# holding the (unguessable, ephemeral) URL — unauthenticated uploads that
# spend model credits. Run this for the demo window ONLY and Ctrl-C it
# the moment the demo ends. The URL changes on every start, by design.
set -eu
PORT="${PORT:-7861}"
cd "$(dirname "$0")/.."
uv run uvicorn server:app --host 127.0.0.1 --port "$PORT" &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 2
exec cloudflared tunnel --url "http://127.0.0.1:$PORT"
