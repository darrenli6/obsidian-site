#!/usr/bin/env bash
# (Re)start the Obsidian site: builder/watcher, static server on :8090, cloudflared quick tunnel.
set -u
cd "$(dirname "$0")"
D="$PWD"; PORT="${PORT:-8090}"; CF="${CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
mkdir -p logs
for f in builder serve tunnel; do
  [ -f "logs/$f.pid" ] && kill "$(cat logs/$f.pid)" 2>/dev/null
done
pkill -f "$D/builder.py" 2>/dev/null; pkill -f "$D/serve.py" 2>/dev/null
pkill -f "cloudflared tunnel --no-autoupdate" 2>/dev/null
sleep 1
export NO_MKDOCS_2_WARNING=1
"$D/venv/bin/python" "$D/builder.py" --once >> logs/builder.log 2>&1   # ensure site exists
setsid nohup "$D/venv/bin/python" "$D/builder.py" >> logs/builder.log 2>&1 < /dev/null & echo $! > logs/builder.pid
PORT=$PORT setsid nohup "$D/venv/bin/python" "$D/serve.py" >> logs/serve.log 2>&1 < /dev/null & echo $! > logs/serve.pid
setsid nohup "$CF" tunnel --no-autoupdate run obsite >> logs/tunnel.log 2>&1 < /dev/null & echo $! > logs/tunnel.pid
URL="https://darren.qqx.ai"
echo "$URL" > logs/url.txt
echo "builder pid: $(cat logs/builder.pid)  server pid: $(cat logs/serve.pid) (port $PORT)  tunnel pid: $(cat logs/tunnel.pid)"
echo "Public URL: $URL"
