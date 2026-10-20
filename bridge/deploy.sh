#!/usr/bin/env bash
# Deploy the Nightcloser bridge + nightcloser to the VM. Does NOT link or start closer-bridge.service.
# Usage: nightcloser/bridge/deploy.sh   (run from anywhere; HOST defaults to your-server)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
HOST="${HOST:-your-server}"

# 1. bridge sources (never ship auth, state, or handoff log)
rsync -a --delete --exclude node_modules --exclude auth --exclude state.json --exclude handoff.log \
  "$HERE/" "$HOST:closer-bridge/"
ssh "$HOST" 'chmod 700 ~/closer-bridge && cd ~/closer-bridge && if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi'

# 2. nightcloser python package (WorkingDirectory=~)
rsync -a --exclude __pycache__ --exclude bridge "$ROOT/nightcloser/" "$HOST:booking/nightcloser/"

# 3. systemd --user units. Only closer-bot is (re)started; the bridge is never enabled or started here.
ssh "$HOST" 'mkdir -p ~/.config/systemd/user'
rsync -a "$HERE/systemd/" "$HOST:.config/systemd/user/"
ssh "$HOST" 'systemctl --user daemon-reload && systemctl --user restart closer-bot.service && sleep 2 && curl -sf -m 5 http://127.0.0.1:8096/health; echo'
echo "deployed. closer-bridge.service: not touched (owner must run link.js, then start it)."
