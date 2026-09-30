#!/usr/bin/env bash
set -euo pipefail

# run_astrometrics.sh (moved to scripts/)
# Wraps the Astrometrics backend + frontend/electron startup.
#
# The backend is launched first, in the background, WITHOUT waiting for it to
# answer, and Electron is started as soon as Vite is up. That way the splash
# screen appears within a couple of seconds and stays up while the backend
# starts and loads its star catalog (Electron polls the backend's /api/ready
# route before opening the main window). Running run_backend.sh start first
# would defeat that: it blocks until the backend answers, so Electron -- and
# its splash -- could not start until the wait was already over.
#
# A backend that is already running is left alone. `stop` and `restart` also
# stop the backend; `foreground` stops it on exit only if it launched it.
# Set DEV_LAN=1 to bind the backend to the LAN too, matching Vite.
#
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
BACKEND_PID_FILE="$PID_DIR/backend.pid"
BACKEND_LAUNCHER="$ROOT_DIR/build/linux/run_backend.sh"
BACKEND_URL="http://127.0.0.1:5000/"
# Set to 1 when this invocation launched the backend, so `foreground` knows
# whether it is responsible for stopping it on exit.
BACKEND_LAUNCHED_HERE=0

# Default values
CMD="foreground"
PORT=5173
OPEN_FILE=""
APP_MODE=""
# Electron's own remote-debugging port (fixed, unrelated to Vite's $PORT).
# stop_frontend force-frees this too, since a previous Electron instance
# that hasn't fully exited yet holding it is what causes a fresh launch to
# fail with "bind() failed: Address already in use".
ELECTRON_DEBUG_PORT=9222

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        start|stop|restart|status|foreground)
            CMD="$1"
            shift
            if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then
                PORT="$1"
                shift
            fi
            ;;
        --mode=*)
            APP_MODE="${1#*=}"
            shift
            ;;
        --mode)
            shift
            if [ $# -gt 0 ]; then
                APP_MODE="$1"
                shift
            fi
            ;;
        --lan)
            export DEV_LAN=1
            shift
            ;;
        *)
            if [[ "$1" =~ ^[0-9]+$ ]]; then
                PORT="$1"
            else
                OPEN_FILE="$(realpath "$1" 2>/dev/null || echo "$1")"
            fi
            shift
            ;;
    esac
done

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
if [ -n "$APP_MODE" ]; then
  encoded_mode=$(echo "$APP_MODE" | sed 's/ /%20/g')
  VITE_URL="${VITE_URL}/?mode=${encoded_mode}"
fi

# Force-frees whatever is bound to a port, regardless of whether we have a
# PID file for it. Defaults to $PORT (Vite) when called with no argument,
# matching every call site that existed before this took a parameter.
kill_port_pids() {
  local port="${1:-$PORT}"
  local pids
  pids=""
  if command -v ss >/dev/null 2>&1; then
    pids=$(ss -ltnp "sport = :${port}" 2>/dev/null | grep -o 'pid=[0-9]*' | sed 's/pid=//' | tr '\n' ' ') || true
  elif command -v lsof >/dev/null 2>&1; then
    pids=$(lsof -ti :"${port}" 2>/dev/null | tr '\n' ' ') || true
  elif command -v netstat >/dev/null 2>&1; then
    pids=$(netstat -ltnp 2>/dev/null | grep ":${port} " | grep -o 'pid=[0-9]*' | sed 's/pid=//' | tr '\n' ' ') || true
  fi

  if [ -n "$pids" ]; then
    echo "Killing PIDs using ${port}: $pids"
    for p in $pids; do
      [ -n "$p" ] && kill -9 "$p" 2>/dev/null || true
    done
  fi
}

# ---------------------------------------------------------------------------
# wait_for_pid_exit: polls until a PID is no longer running, or
# max_wait_seconds elapses. Used so stop_frontend only reports a process
# stopped once it actually has, instead of guessing with a fixed sleep --
# restarting immediately after only sending a kill signal can otherwise
# race a fresh launch against a previous instance that hasn't fully torn
# down yet (e.g. Electron's fixed remote-debugging port failing to bind
# because the old instance still held it).
# ---------------------------------------------------------------------------
wait_for_pid_exit() {
  local pid="$1"
  local max_wait_seconds="${2:-5}"
  local max_ticks=$((max_wait_seconds * 2))
  local ticks=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$ticks" -ge "$max_ticks" ]; then
      return 1
    fi
    sleep 0.5
    ticks=$((ticks + 1))
  done
  return 0
}

# ---------------------------------------------------------------------------
# check_and_fix_sandbox: checks whether chrome-sandbox has root setuid
# permissions. If non-interactive sudo succeeds, it fixes permissions;
# otherwise it safely sets FORCE_NO_SANDBOX=1 without blocking or prompting.
# ---------------------------------------------------------------------------
check_and_fix_sandbox() {
  local cs_path="$ROOT_DIR/node_modules/electron/dist/chrome-sandbox"
  [ ! -f "$cs_path" ] && return 0
  owner=$(stat -c '%u' "$cs_path" 2>/dev/null || echo "")
  mode=$(stat -c '%a' "$cs_path" 2>/dev/null || echo "")
  if [ "$owner" = "0" ] && [ "$mode" = "4755" ]; then return 0; fi
  if sudo -n chown root:root "$cs_path" 2>/dev/null && sudo -n chmod 4755 "$cs_path" 2>/dev/null; then
    return 0
  else
    FORCE_NO_SANDBOX=1
    return 1
  fi
}

# ---------------------------------------------------------------------------
# check_frontend_deps: verifies that vite is available or runs npm install.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# ensure_prerequisites: runs frontend dependency and sandbox checks prior
# to starting the application services.
# ---------------------------------------------------------------------------
ensure_prerequisites() {
  check_frontend_deps || exit 1
  check_and_fix_sandbox || true
}

# --- Background execution functions ---

backend_is_running() {
  if [ -f "$BACKEND_PID_FILE" ] && kill -0 "$(cat "$BACKEND_PID_FILE" 2>/dev/null)" 2>/dev/null; then
    return 0
  fi
  curl -s -o /dev/null --max-time 3 "$BACKEND_URL"
}

# Launches the backend and returns immediately (it keeps starting up in the
# background). Leaves an already-running backend alone.
launch_backend_bg() {
  if backend_is_running; then
    echo "Backend already running; not starting another."
    return 0
  fi
  local launcher_args=()
  [ "${DEV_LAN:-0}" = "1" ] && launcher_args+=(--lan)
  echo "Launching backend (logs: $LOG_DIR/backend.log)..."
  # Explicit `|| return 1`: callers use this inside `if !`, where `set -e` is
  # suppressed, so a failed launch would otherwise be silently ignored.
  "$BACKEND_LAUNCHER" ${launcher_args[@]+"${launcher_args[@]}"} launch || return 1
  BACKEND_LAUNCHED_HERE=1
}

# Stops the backend whenever explicit stop/restart is requested.
stop_backend() {
  "$BACKEND_LAUNCHER" stop
}

# Stops the backend only if this invocation launched it, so exiting or failing
# never takes down a backend the user started separately.
stop_backend_if_launched_here() {
  if [ "$BACKEND_LAUNCHED_HERE" = "1" ]; then
    stop_backend
  fi
}

start_vite_bg() {
  echo "Starting Vite on ${PORT} (logs: $LOG_DIR/vite.log)..."
  local host_arg=""
  [ "${DEV_LAN:-0}" = "1" ] && host_arg="--host 0.0.0.0"
  (cd "$ROOT_DIR" && npm run dev -- --port "$PORT" $host_arg) >"$LOG_DIR/vite.log" 2>&1 &
  echo $! > "$VITE_PID_FILE"
}

# ---------------------------------------------------------------------------
# start_electron_bg: launches Electron against the active Vite URL.
# If WATCH=1 is set, wraps execution with nodemon to restart on main/preload
# changes. Otherwise launches Electron directly so that window closure
# triggers immediate application shutdown.
# ---------------------------------------------------------------------------
start_electron_bg() {
    local extra_args="--ozone-platform-hint=auto --enable-features=WaylandWindowDecorations"
    [ "${FORCE_NO_SANDBOX:-0}" = "1" ] && extra_args+=" --no-sandbox"
    local file_arg=""
    [ -n "${OPEN_FILE:-}" ] && file_arg="\"${OPEN_FILE}\""

    cd "$ROOT_DIR" || return 1
    if command -v npx >/dev/null 2>&1; then
        set +e
        if [ "${WATCH:-0}" = "1" ]; then
            echo "Starting Electron (nodemon watch) (logs: $LOG_DIR/electron.log)..."
            npx nodemon --watch main.js --watch preload.js --delay 1 --exec "ELECTRON_RENDERER_URL=${VITE_URL} SKIP_BACKEND=1 NODE_ENV=development npx electron . --remote-debugging-port=${ELECTRON_DEBUG_PORT} ${extra_args} ${file_arg}" >"$LOG_DIR/electron.log" 2>&1 &
        else
            echo "Starting Electron (logs: $LOG_DIR/electron.log)..."
            ELECTRON_RENDERER_URL="${VITE_URL}" SKIP_BACKEND=1 NODE_ENV=development npx electron . --remote-debugging-port=${ELECTRON_DEBUG_PORT} ${extra_args} ${file_arg} >"$LOG_DIR/electron.log" 2>&1 &
        fi
        local pid=$!
        set -e
        echo "$pid" > "$ELECTRON_PID_FILE"
    else
        echo "npx not found." >&2
    fi
}

# wait_for_backend_alive: confirms the backend answers HTTP before Electron is
# launched. Electron runs with SKIP_BACKEND=1, so with no backend behind it the
# app would sit on the splash screen. Catalog warm-up is covered by the splash
# itself, so this only waits for the server to start listening.
wait_for_backend_alive() {
  echo "Waiting for backend to answer..."
  local tries=0
  local max=120
  while [ $tries -lt $max ]; do
    if curl -s -o /dev/null --max-time 2 "$BACKEND_URL"; then return 0; fi
    sleep 0.5
    tries=$((tries+1))
  done
  echo "Backend is not answering at $BACKEND_URL."
  tail -n 20 "$LOG_DIR/backend.log" 2>/dev/null
  return 1
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

stop_frontend() {
  echo "Stopping Astrometrics frontend processes..."
  if [ -f "$ELECTRON_PID_FILE" ]; then
    epid=$(cat "$ELECTRON_PID_FILE" 2>/dev/null || true)
    if [ -n "$epid" ]; then
      echo "Stopping Electron (pid $epid)..."
      kill "$epid" 2>/dev/null || true
      # A heavy Electron app (GPU process, tray, possibly several windows
      # each running their own shutdown cleanup) can take longer than a
      # token sleep to actually exit -- wait for real, then escalate.
      if ! wait_for_pid_exit "$epid" 5; then
        echo "Electron did not exit in time; sending SIGKILL..."
        kill -9 "$epid" 2>/dev/null || true
        wait_for_pid_exit "$epid" 3 || true
      fi
      rm -f "$ELECTRON_PID_FILE"
    fi
  fi
  if [ -f "$VITE_PID_FILE" ]; then
    vpid=$(cat "$VITE_PID_FILE" 2>/dev/null || true)
    if [ -n "$vpid" ]; then
      echo "Stopping Vite (pid $vpid)..."
      kill "$vpid" 2>/dev/null || true
      wait_for_pid_exit "$vpid" 3 || true
      rm -f "$VITE_PID_FILE"
    fi
  fi
  # Force-free both ports regardless of whether the PID waits above
  # succeeded -- catches orphaned processes never tracked by a PID file,
  # and anything that still didn't release its port in time. Electron's
  # debug port matters here too: a fresh launch failing to bind it (because
  # the previous instance still held it) is what caused the original
  # "partially started" bug this function is now guarding against.
  kill_port_pids "$PORT"
  kill_port_pids "$ELECTRON_DEBUG_PORT"
}

stop_all() {
  stop_frontend
  stop_backend
}

stop_what_this_run_started() {
  stop_frontend
  stop_backend_if_launched_here
}

status_check() {
  local running=0
  local epid=""
  if [ -f "$ELECTRON_PID_FILE" ] && kill -0 "$(cat "$ELECTRON_PID_FILE" 2>/dev/null)" 2>/dev/null; then
    epid="$(cat "$ELECTRON_PID_FILE")"
  else
    epid=$(pgrep -f "electron .*astrometrics|electron \." | head -n 1 || true)
  fi

  if [ -n "$epid" ] && kill -0 "$epid" 2>/dev/null; then
    echo "Electron is running (pid $epid)."
    running=1
  else
    echo "Electron is NOT running."
  fi

  local vpid=""
  if [ -f "$VITE_PID_FILE" ] && kill -0 "$(cat "$VITE_PID_FILE" 2>/dev/null)" 2>/dev/null; then
    vpid="$(cat "$VITE_PID_FILE")"
  else
    vpid=$(pgrep -f "vite --port ${PORT}|vite" | head -n 1 || true)
  fi

  if [ -n "$vpid" ] || curl -s -o /dev/null --max-time 1 "http://127.0.0.1:${PORT}"; then
    if [ -n "$vpid" ]; then
      echo "Vite is running (pid $vpid, port ${PORT})."
    else
      echo "Vite is running (port ${PORT})."
    fi
    running=1
  else
    echo "Vite is NOT running."
  fi

  if backend_is_running; then
    echo "Backend is running."
    running=1
  else
    echo "Backend is NOT running."
  fi
  if [ -x "$ASSISTANT_LAUNCHER" ]; then
    "$ASSISTANT_LAUNCHER" status
  fi
  return 0
}

# --- Main Logic ---

case "$CMD" in
  start)
    ensure_prerequisites
    # Stop a previous frontend, but leave any running backend alone
    stop_frontend

    # Backend first: its launcher purges the shared log dir, which must happen
    # before Vite starts writing vite.log there.
    if ! launch_backend_bg; then
        echo "Backend failed to launch."
        exit 1
    fi
    start_vite_bg
    if ! wait_for_vite; then
        echo "Vite failed to start."
        stop_what_this_run_started
        exit 1
    fi
    if ! wait_for_backend_alive; then
        echo "Backend is not running; not launching Electron."
        stop_what_this_run_started
        exit 1
    fi
    start_electron_bg
    echo "Astrometrics started. The splash screen stays up until the backend is ready."
    ;;
  stop)
    stop_all
    ;;
  restart)
    # No extra sleep needed here: stop_all (via stop_frontend's
    # wait_for_pid_exit calls and force-freed ports, and run_backend.sh's
    # own wait+SIGKILL+port-cleanup sequence) only returns once everything
    # is confirmed stopped, not just signaled.
    stop_all
    "$0" start "$PORT"
    ;;
  status)
    status_check
    ;;
  foreground)
    ensure_prerequisites
    echo "Starting in foreground..."
    kill_port_pids

    trap 'stop_all' EXIT INT TERM

    if ! launch_backend_bg; then
        echo "Backend failed to launch."
        exit 1
    fi
    start_vite_bg
    if ! wait_for_vite; then
        echo "Vite failed."
        exit 1
    fi

    if ! wait_for_backend_alive; then
        echo "Backend is not running; not launching Electron."
        exit 1
    fi
    start_electron_bg

    epid=$(cat "$ELECTRON_PID_FILE")
    wait "$epid"
    ;;
esac
