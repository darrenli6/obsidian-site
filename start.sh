#!/usr/bin/env bash
# (Re)start the Obsidian site: builder/watcher, static server on :8090, named Cloudflare tunnel.
set -u
cd "$(dirname "$0")"
D="$PWD"; PORT="${PORT:-8090}"; CF="${CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
mkdir -p logs

# Box DNS remaps Cloudflare edge hosts to 198.18.0.1, which breaks tunnel port 7844.
# Pin real Cloudflare edge IPs so cloudflared can connect over HTTP/2.
if ! grep -q 'region1.v2.argotunnel.com' /etc/hosts 2>/dev/null || grep -q '198.18.0.1.*argotunnel' /etc/hosts 2>/dev/null; then
  true
fi
if ! getent hosts region1.v2.argotunnel.com 2>/dev/null | grep -q '198.41.'; then
  sudo sed -i '/argotunnel.com/d' /etc/hosts 2>/dev/null || true
  printf '\n# cloudflared edge bypass fake DNS\n198.41.192.7 region1.v2.argotunnel.com\n198.41.200.43 region2.v2.argotunnel.com\n' | sudo tee -a /etc/hosts >/dev/null
fi

for f in builder serve tunnel; do
  [ -f "logs/$f.pid" ] && kill "$(cat logs/$f.pid)" 2>/dev/null
done
pkill -f "$D/builder.py" 2>/dev/null; pkill -f "$D/serve.py" 2>/dev/null
pkill -f "cloudflared tunnel --no-autoupdate" 2>/dev/null
sleep 1
export NO_MKDOCS_2_WARNING=1
"$D/venv/bin/python" "$D/builder.py" --once >> logs/builder.log 2>&1
setsid nohup "$D/venv/bin/python" "$D/builder.py" >> logs/builder.log 2>&1 < /dev/null & echo $! > logs/builder.pid
PORT=$PORT setsid nohup "$D/venv/bin/python" "$D/serve.py" >> logs/serve.log 2>&1 < /dev/null & echo $! > logs/serve.pid
# HTTP/2: QUIC/UDP to Cloudflare edge is often blocked on this box.
setsid nohup "$CF" tunnel --no-autoupdate --protocol http2 --no-prechecks run obsite >> logs/tunnel.log 2>&1 < /dev/null & echo $! > logs/tunnel.pid
URL="https://darren.qqx.ai"
echo "$URL" > logs/url.txt
echo "builder pid: $(cat logs/builder.pid)  server pid: $(cat logs/serve.pid) (port $PORT)  tunnel pid: $(cat logs/tunnel.pid)"
echo "Public URL: $URL"
