#!/usr/bin/env bash
set -euo pipefail

# run_astrometrics.sh (moved to scripts/)
# Wraps the Astrometrics frontend/electron startup.
# Usage:
#   ./run_astrometrics.sh start [port]        - Start in background
#   ./run_astrometrics.sh stop                - Stop background processes
#   ./run_astrometrics.sh restart [port]      - Restart background processes
#   ./run_astrometrics.sh status              - Show status
#   ./run_astrometrics.sh foreground [port]   - Run in foreground (original behavior)
#   ./run_astrometrics.sh [port]              - Defaults to foreground for backward compatibility

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$ROOT_DIR/.run_logs"
PID_DIR="$ROOT_DIR/.run_pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

VITE_PID_FILE="$PID_DIR/vite.pid"
ELECTRON_PID_FILE="$PID_DIR/electron.pid"

# Default values
CMD="foreground"
PORT=5173

# Parse arguments
if [ $# -gt 0 ]; then
    case "$1" in
        start|stop|restart|status|foreground)
            CMD="$1"
            shift
            if [ $# -gt 0 ]; then PORT="$1"; fi
            ;;
        *)
            # Backward compatibility: if first arg is a number, it's a port, default to foreground
            # If it's anything else, assume it might be a port or unknown, default to foreground
            CMD="foreground"
            PORT="$1"
            ;;
    esac
fi

# Detect IP logic
detect_lan_ip() {
  if command -v ip >/dev/null 2>&1; then
    out=$(ip route get 1.1.1.1 2>/dev/null || true)
    if [ -n "$out" ]; then
      src=$(echo "$out" | sed -n 's/.* src \([0-9.]*\).*/\1/p') || true
      if [ -n "$src" ] && [ "$src" != "127.0.0.1" ]; then
        echo "$src"; return 0
      fi
    fi
  fi
  if command -v hostname >/dev/null 2>&1; then
    ips=$(hostname -I 2>/dev/null || true)
    for ipaddr in $ips; do
      case "$ipaddr" in 127.*|::1) continue ;; esac
      echo "$ipaddr"; return 0
    done
  fi
  if command -v ip >/dev/null 2>&1; then
    ip -4 addr | awk '/inet / { sub(/\/.*$/, "", $2); if ($2 != "127.0.0.1") { print $2; exit } }'
    return 0
  fi
  echo "127.0.0.1"
}

if [ -n "${DEV_HOST:-}" ]; then
  HOST=${DEV_HOST}
elif [ "${DEV_LAN:-0}" = "1" ]; then
  HOST=$(detect_lan_ip)
else
  HOST=127.0.0.1
fi
VITE_URL="http://${HOST}:${PORT}"

kill_port_pids() {
  local pids
  pids=""
  if command -v ss >/dev/null 2>&1; then
    pids=$(ss -ltnp "sport = :${PORT}" 2>/dev/null | grep -o 'pid=[0-9]*' | sed 's/pid=//' | tr '\n' ' ') || true
  elif command -v lsof >/dev/null 2>&1; then
    pids=$(lsof -ti :"${PORT}" 2>/dev/null | tr '\n' ' ') || true
  elif command -v netstat >/dev/null 2>&1; then
    pids=$(netstat -ltnp 2>/dev/null | grep ":${PORT} " | grep -o 'pid=[0-9]*' | sed 's/pid=//' | tr '\n' ' ') || true
  fi

  if [ -n "$pids" ]; then
    echo "Killing PIDs using ${PORT}: $pids"
    for p in $pids; do
      [ -n "$p" ] && kill -9 "$p" 2>/dev/null || true
    done
  fi
}

check_and_fix_sandbox() {
  local cs_path="$ROOT_DIR/node_modules/electron/dist/chrome-sandbox"
  [ ! -f "$cs_path" ] && return 0
  owner=$(stat -c '%u' "$cs_path" 2>/dev/null || echo "")
  mode=$(stat -c '%a' "$cs_path" 2>/dev/null || echo "")
  if [ "$owner" = "0" ] && [ "$mode" = "4755" ]; then return 0; fi
  echo "Fixing chrome-sandbox permissions..."
  if sudo chown root:root "$cs_path" && sudo chmod 4755 "$cs_path"; then
    return 0
  else
    echo "Failed to fix sandbox. Using --no-sandbox."
    FORCE_NO_SANDBOX=1
    return 1
  fi
}

check_frontend_deps() {
  if [ -x "$ROOT_DIR/node_modules/.bin/vite" ]; then return 0; fi
  if command -v npm >/dev/null 2>&1; then
    echo "Installing frontend dependencies..."
    (cd "$ROOT_DIR" && npm install) || return 1
    return 0
  fi
  echo "npm not found."
  return 1
}

# --- Background execution functions ---

start_vite_bg() {
  echo "Starting Vite on ${PORT} (logs: $LOG_DIR/vite.log)..."
  local host_arg=""
  [ "${DEV_LAN:-0}" = "1" ] && host_arg="--host 0.0.0.0"
  (cd "$ROOT_DIR" && npm run dev -- --port "$PORT" $host_arg) >"$LOG_DIR/vite.log" 2>&1 &
  echo $! > "$VITE_PID_FILE"
}

start_electron_bg() {
    echo "Starting Electron (nodemon) (logs: $LOG_DIR/electron.log)..."
    local extra_args=""
    [ "${FORCE_NO_SANDBOX:-0}" = "1" ] && extra_args="--no-sandbox"

    cd "$ROOT_DIR" || return 1
    if command -v npx >/dev/null 2>&1; then
        set +e
        # We use setsid to detach or just background it.
        # Note: nodemon spawns electron.
        npx nodemon --watch main.js --watch preload.js --delay 1 --exec "ELECTRON_RENDERER_URL=${VITE_URL} SKIP_BACKEND=1 NODE_ENV=development npx electron . --remote-debugging-port=9222 ${extra_args}" >"$LOG_DIR/electron.log" 2>&1 &
        local pid=$!
        set -e
        echo "$pid" > "$ELECTRON_PID_FILE"
    else
        echo "npx not found." >&2
    fi
}

wait_for_vite() {
  echo "Waiting for Vite..."
  local tries=0
  local max=60
  while [ $tries -lt $max ]; do
    if curl -sSf --connect-timeout 1 "http://${HOST}:${PORT}/" >/dev/null 2>&1; then return 0; fi
    sleep 0.5
    tries=$((tries+1))
  done
  echo "Vite timeout."
  tail -n 20 "$LOG_DIR/vite.log"
  return 1
}

stop_all() {
  echo "Stopping Astrometrics processes..."
  if [ -f "$ELECTRON_PID_FILE" ]; then
    epid=$(cat "$ELECTRON_PID_FILE" 2>/dev/null || true)
    if [ -n "$epid" ]; then
      echo "Stopping Electron (pid $epid)..."
      kill "$epid" 2>/dev/null || true
      # Wait a bit then kill -9 if needed
      sleep 1
      kill -0 "$epid" 2>/dev/null && kill -9 "$epid" 2>/dev/null || true
      rm -f "$ELECTRON_PID_FILE"
    fi
  fi
  if [ -f "$VITE_PID_FILE" ]; then
    vpid=$(cat "$VITE_PID_FILE" 2>/dev/null || true)
    if [ -n "$vpid" ]; then
      echo "Stopping Vite (pid $vpid)..."
      kill "$vpid" 2>/dev/null || true
      rm -f "$VITE_PID_FILE"
    fi
  fi
  # Also kill any stale processes on the port just in case
  kill_port_pids
}

status_check() {
  local running=0
  if [ -f "$ELECTRON_PID_FILE" ] && kill -0 "$(cat "$ELECTRON_PID_FILE" 2>/dev/null)" 2>/dev/null; then
    echo "Electron is running (pid $(cat "$ELECTRON_PID_FILE"))."
    running=1
  else
    echo "Electron is NOT running."
  fi
  if [ -f "$VITE_PID_FILE" ] && kill -0 "$(cat "$VITE_PID_FILE" 2>/dev/null)" 2>/dev/null; then
    echo "Vite is running (pid $(cat "$VITE_PID_FILE"))."
    running=1
  else
    echo "Vite is NOT running."
  fi
  return 0
}

# --- Main Logic ---

check_frontend_deps || exit 1
check_and_fix_sandbox || true

case "$CMD" in
  start)
    # Stop existing if running
    stop_all

    start_vite_bg
    if ! wait_for_vite; then
        echo "Vite failed to start."
        stop_all
        exit 1
    fi
    start_electron_bg
    echo "Astrometrics started."
    ;;
  stop)
    stop_all
    ;;
  restart)
    stop_all
    sleep 1
    "$0" start "$PORT"
    ;;
  status)
    status_check
    ;;
  foreground)
    # Original foreground logic
    echo "Starting in foreground..."
    kill_port_pids

    # Start npm run dev:local logic inline or just use generic functions but wait?
    # To keep exact behavior, we replicate the original logic

    trap 'stop_all' EXIT INT TERM

    start_vite_bg
    if ! wait_for_vite; then
        echo "Vite failed."
        exit 1
    fi

    # Run electron in foreground here?
    # Original script ran nodemon in background and waited.
    # We will run it in background and wait for it.
    start_electron_bg

    epid=$(cat "$ELECTRON_PID_FILE")
    wait "$epid"
    ;;
esac
