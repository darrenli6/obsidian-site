#!/usr/bin/env bash
# Start the knowledge-base site only if any part is down.
ok=1
pgrep -f /workspace/obsite/builder.py >/dev/null || ok=0
pgrep -f /workspace/obsite/serve.py >/dev/null || ok=0
# Match real cmdline: cloudflared tunnel --no-autoupdate --protocol http2 --no-prechecks run obsite
pgrep -f 'cloudflared tunnel .* run obsite' >/dev/null || ok=0
# Processes can be up while the tunnel is stale (public 530/502); treat that as down too.
if [ $ok = 1 ]; then code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 https://darren.qqx.ai); case "$code" in 200) ;; *) ok=0 ;; esac; fi
if [ $ok = 0 ]; then bash /workspace/obsite/start.sh >/dev/null 2>&1; echo restarted; else echo running; fi
