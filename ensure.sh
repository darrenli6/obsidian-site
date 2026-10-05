#!/usr/bin/env bash
# Start the knowledge-base site only if any part is down.
ok=1
pgrep -f /workspace/obsite/builder.py >/dev/null || ok=0
pgrep -f /workspace/obsite/serve.py >/dev/null || ok=0
pgrep -f "tunnel --no-autoupdate run obsite" >/dev/null || ok=0
if [ $ok = 0 ]; then bash /workspace/obsite/start.sh >/dev/null 2>&1; echo restarted; else echo running; fi
