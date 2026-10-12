#!/usr/bin/env bash
# Lightweight Cloudflare tunnel watchdog for darren.qqx.ai / mediaoverview.qqx.ai.
#
# Box DNS remaps those hostnames to 198.18.0.1 (sandbox egress). Naive curl often
# false-fails (530) while the tunnel is healthy — do NOT restart on that path.
# Health model:
#   (a) local origins http://127.0.0.1:8090/ and :3000/ must be 200
#   (b) public HTTPS via DoH (1.1.1.1) + curl --resolve to real CF edge IPs
# Treat public as down only when the matching local origin is up AND (b) fails.
# Then run ensure.sh (fallback start.sh). Quiet when healthy. Does not touch .env.
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$DIR/logs"
LOG="$LOG_DIR/watchdog.log"
PIDFILE="$LOG_DIR/watchdog.pid"
INTERVAL="${WATCHDOG_INTERVAL:-120}"
CURL_MAX="${WATCHDOG_CURL_MAX:-15}"
# Fallback Cloudflare anycast for these zones when DoH is unavailable.
CF_FALLBACK_IPS="${WATCHDOG_CF_IPS:-104.21.77.95 172.67.206.117}"

LOCAL_ORIGIN_DARREN="${WATCHDOG_LOCAL_DARREN:-http://127.0.0.1:8090/}"
LOCAL_ORIGIN_MEDIA="${WATCHDOG_LOCAL_MEDIA:-http://127.0.0.1:3000/}"
PUBLIC_HOST_DARREN="${WATCHDOG_HOST_DARREN:-darren.qqx.ai}"
PUBLIC_HOST_MEDIA="${WATCHDOG_HOST_MEDIA:-mediaoverview.qqx.ai}"

mkdir -p "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" >>"$LOG"
}

is_running() {
  local pid
  [ -f "$PIDFILE" ] || return 1
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [ -n "${pid:-}" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  # Confirm it's our watchdog, not a recycled PID.
  if [ -r "/proc/$pid/cmdline" ]; then
    tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q 'tunnel-watchdog\.sh' || return 1
  fi
  return 0
}

stop_watchdog() {
  if ! is_running; then
    rm -f "$PIDFILE"
    echo "watchdog not running"
    return 0
  fi
  local pid
  pid="$(cat "$PIDFILE")"
  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.2
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
  echo "stopped watchdog pid $pid"
}

# HTTP status for a local origin; prints code or "fail".
check_local() {
  local url="$1" code
  code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time "$CURL_MAX" "$url" 2>/dev/null)" || {
    echo "fail"
    return 1
  }
  if [ "$code" = "200" ]; then
    echo "$code"
    return 0
  fi
  echo "$code"
  return 1
}

# Real Cloudflare A records via DoH; never trust box getent (198.18.0.1).
cf_ips_for() {
  local host="$1" json ips
  json="$(curl -fsS --max-time 5 \
    -H 'accept: application/dns-json' \
    "https://1.1.1.1/dns-query?name=${host}&type=A" 2>/dev/null)" || json=""
  ips="$(printf '%s' "$json" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(" ".join(a["data"] for a in d.get("Answer", []) if a.get("type") == 1))
except Exception:
    pass
' 2>/dev/null)" || ips=""
  # Drop fake/sandbox remap and other non-edge answers if DoH is poisoned.
  local cleaned="" ip
  for ip in $ips; do
    case "$ip" in
      198.18.*|127.*|0.*|10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*) continue ;;
      *) cleaned="${cleaned}${cleaned:+ }$ip" ;;
    esac
  done
  if [ -z "$cleaned" ]; then
    cleaned="$CF_FALLBACK_IPS"
  fi
  printf '%s\n' "$cleaned"
}

# Public HTTPS via curl --resolve to a real CF edge IP. Prints 200 or fail.
check_public_resolve() {
  local host="$1" path="${2:-/}" ip code
  for ip in $(cf_ips_for "$host"); do
    code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time "$CURL_MAX" \
      --resolve "${host}:443:${ip}" "https://${host}${path}" 2>/dev/null)" || continue
    if [ "$code" = "200" ]; then
      echo "$code"
      return 0
    fi
  done
  echo "fail"
  return 1
}

recover() {
  local reason="$1"
  log "FAIL $reason — running ensure.sh"
  if bash "$DIR/ensure.sh" >>"$LOG" 2>&1; then
    log "ensure.sh finished"
  else
    log "ensure.sh failed (exit $?) — falling back to start.sh"
    bash "$DIR/start.sh" >>"$LOG" 2>&1 || log "start.sh also failed (exit $?)"
  fi
}

loop() {
  echo $$ >"$PIDFILE"
  log "watchdog started pid=$$ interval=${INTERVAL}s (local+DoH/--resolve; ignore fake 198.18.0.1)"
  trap 'rm -f "$PIDFILE"; log "watchdog stopped pid=$$"; exit 0' TERM INT HUP

  while true; do
    bad=""
    local_darren="$(check_local "$LOCAL_ORIGIN_DARREN")" || true
    local_media="$(check_local "$LOCAL_ORIGIN_MEDIA")" || true

    if [ "$local_darren" != "200" ]; then
      bad="${bad}${bad:+; }local :8090 -> ${local_darren:-fail}"
    fi
    # :3000 down is logged but does not alone restart the Cloudflare tunnel
    # (ensure/start.sh only manages obsites + cloudflared, not Next.js).
    if [ "$local_media" != "200" ]; then
      if [ "${WATCHDOG_DEBUG:-0}" = "1" ]; then
        log "note local :3000 -> ${local_media:-fail} (not restarting tunnel for this alone)"
      fi
    fi

    # Public: only blame tunnel when the matching local origin is healthy
    # and the --resolve/DoH path fails. Never use naive/fake-IP curl.
    if [ "$local_darren" = "200" ]; then
      pub_darren="$(check_public_resolve "$PUBLIC_HOST_DARREN" /)" || true
      if [ "$pub_darren" != "200" ]; then
        bad="${bad}${bad:+; }https://${PUBLIC_HOST_DARREN}/ (resolve) -> ${pub_darren:-fail}"
      fi
    fi
    if [ "$local_media" = "200" ]; then
      pub_media="$(check_public_resolve "$PUBLIC_HOST_MEDIA" /)" || true
      if [ "$pub_media" != "200" ]; then
        bad="${bad}${bad:+; }https://${PUBLIC_HOST_MEDIA}/ (resolve) -> ${pub_media:-fail}"
      fi
    fi

    if [ -n "$bad" ]; then
      recover "$bad"
    elif [ "${WATCHDOG_DEBUG:-0}" = "1" ]; then
      log "ok local8090=${local_darren} local3000=${local_media} public(resolve)=200"
    fi
    sleep "$INTERVAL"
  done
}

usage() {
  cat <<'USAGE'
Usage: tunnel-watchdog.sh [--start|--stop|--status|--foreground|--check-once]
  --start       Start in background via setsid/nohup (default if no args)
  --stop        Stop the running watchdog (pidfile)
  --status      Print running/not-running and pid
  --foreground  Run the check loop in this shell (for debugging)
  --check-once  Run one health cycle to stdout (no recover); exit 0 if healthy

Env:
  WATCHDOG_INTERVAL  Seconds between checks (default 120)
  WATCHDOG_CURL_MAX  curl --max-time (default 15)
  WATCHDOG_DEBUG=1   Log one-line ok / :3000 notes
  WATCHDOG_CF_IPS    Fallback CF edge IPs if DoH fails
USAGE
}

check_once() {
  local local_darren local_media pub_darren pub_media rc=0
  local_darren="$(check_local "$LOCAL_ORIGIN_DARREN")" || true
  local_media="$(check_local "$LOCAL_ORIGIN_MEDIA")" || true
  echo "local :8090 -> ${local_darren:-fail}"
  echo "local :3000 -> ${local_media:-fail}"
  if [ "$local_darren" = "200" ]; then
    pub_darren="$(check_public_resolve "$PUBLIC_HOST_DARREN" /)" || true
    echo "public ${PUBLIC_HOST_DARREN} (DoH/--resolve) -> ${pub_darren:-fail}"
    [ "$pub_darren" = "200" ] || rc=1
  else
    echo "public ${PUBLIC_HOST_DARREN} skipped (local down)"
    rc=1
  fi
  if [ "$local_media" = "200" ]; then
    pub_media="$(check_public_resolve "$PUBLIC_HOST_MEDIA" /)" || true
    echo "public ${PUBLIC_HOST_MEDIA} (DoH/--resolve) -> ${pub_media:-fail}"
    [ "$pub_media" = "200" ] || rc=1
  else
    echo "public ${PUBLIC_HOST_MEDIA} skipped (local down)"
  fi
  [ "$local_darren" = "200" ] || rc=1
  if [ "$rc" -eq 0 ]; then
    echo "healthy (would NOT restart)"
  else
    echo "unhealthy (would recover via ensure.sh)"
  fi
  return "$rc"
}

case "${1:---start}" in
  --help|-h) usage; exit 0 ;;
  --stop) stop_watchdog; exit 0 ;;
  --check-once) check_once; exit $? ;;
  --status)
    if is_running; then
      echo "running pid=$(cat "$PIDFILE")"
      exit 0
    fi
    echo "not running"
    exit 1
    ;;
  --foreground) loop ;;
  --start|"")
    if is_running; then
      echo "already running pid=$(cat "$PIDFILE")"
      exit 0
    fi
    rm -f "$PIDFILE"
    setsid nohup bash "$DIR/tunnel-watchdog.sh" --foreground \
      >>"$LOG" 2>&1 < /dev/null &
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      [ -f "$PIDFILE" ] && is_running && break
      sleep 0.1
    done
    if is_running; then
      echo "started pid=$(cat "$PIDFILE")"
      exit 0
    fi
    echo "failed to start (see $LOG)" >&2
    exit 1
    ;;
  *)
    echo "unknown option: $1" >&2
    usage >&2
    exit 2
    ;;
esac
