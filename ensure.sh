#!/usr/bin/env bash
# Start the knowledge-base site only if any part is down.
# Public checks use DoH + curl --resolve (box DNS remaps *.qqx.ai -> 198.18.0.1;
# naive curl false-530s must not trigger start.sh).
set -u

CF_FALLBACK_IPS="104.21.77.95 172.67.206.117"

public_ok() {
  local host="$1" json ips ip code cleaned=""
  json="$(curl -fsS --max-time 5 -H 'accept: application/dns-json' \
    "https://1.1.1.1/dns-query?name=${host}&type=A" 2>/dev/null)" || json=""
  ips="$(printf '%s' "$json" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(" ".join(a["data"] for a in d.get("Answer", []) if a.get("type") == 1))
except Exception:
    pass
' 2>/dev/null)" || ips=""
  for ip in $ips; do
    case "$ip" in
      198.18.*|127.*|0.*|10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*) continue ;;
      *) cleaned="${cleaned}${cleaned:+ }$ip" ;;
    esac
  done
  [ -n "$cleaned" ] || cleaned="$CF_FALLBACK_IPS"
  for ip in $cleaned; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
      --resolve "${host}:443:${ip}" "https://${host}/")" || continue
    case "$code" in 200) return 0 ;; esac
  done
  return 1
}

ok=1
pgrep -f /workspace/obsite/builder.py >/dev/null || ok=0
pgrep -f /workspace/obsite/serve.py >/dev/null || ok=0
# Match real cmdline: cloudflared tunnel --no-autoupdate --protocol http2 --no-prechecks run obsite
pgrep -f 'cloudflared tunnel .* run obsite' >/dev/null || ok=0
# Local origin must answer before we trust process list.
if [ "$ok" = 1 ]; then
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8090/)" || code=fail
  case "$code" in 200) ;; *) ok=0 ;; esac
fi
# Processes can be up while the tunnel is stale (public 530/502); check via
# real CF edge IPs, not the remapped 198.18.0.1 path.
if [ "$ok" = 1 ]; then
  public_ok darren.qqx.ai || ok=0
fi
if [ "$ok" = 0 ]; then
  bash /workspace/obsite/start.sh >/dev/null 2>&1
  echo restarted
else
  echo running
fi
