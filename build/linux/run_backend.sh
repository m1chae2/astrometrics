#!/usr/bin/env bash
set -euo pipefail

# run_backend.sh (moved to scripts/)
# Simple helper to start/stop/check the Python backend for Astrometrics.
#
# On a fresh clone this script will automatically create .venv and install
# all backend dependencies (via setup_venv.sh) if the virtual environment
# does not yet exist.  No manual setup step is required.
#
# Usage:
#   ./run_backend.sh start             - start backend in background (writes PID to .run_pids/backend.pid)
#   ./run_backend.sh foreground        - run backend in foreground (logs to stdout)
#   ./run_backend.sh stop              - stop backgrounded backend
#   ./run_backend.sh status            - show status
#   ./run_backend.sh install           - install latest wheel (or editable) into the selected Python
#                                        environment (does NOT start the backend)
#   ./run_backend.sh --lan start       - start backend and bind to 0.0.0.0 so it's reachable on the LAN
#   ./run_backend.sh help|--help|-h    - print this help text

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$ROOT_DIR/.run_logs"
PID_DIR="$ROOT_DIR/.run_pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

PID_FILE="$PID_DIR/backend.pid"
LOG_FILE="$LOG_DIR/backend.log"

# ---------------------------------------------------------------------------
# ensure_venv: create and populate .venv if it does not already exist.
# Delegates to setup_venv.sh which is located alongside this script.
# ---------------------------------------------------------------------------
ensure_venv() {
  if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    return 0  # venv already exists and is functional
  fi

  SETUP_SCRIPT="$ROOT_DIR/build/linux/setup_venv.sh"
  if [ ! -f "$SETUP_SCRIPT" ]; then
    echo "ERROR: setup_venv.sh not found at $SETUP_SCRIPT" >&2
    exit 1
  fi

  echo "No virtual environment found. Running setup_venv.sh to create one..."
  bash "$SETUP_SCRIPT"
}

# Auto-bootstrap the virtual environment on a fresh clone.
ensure_venv

PYTHON=""
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  echo "No Python interpreter found. Install python3 or create a .venv." >&2
  exit 1
fi

## This script runs the backend package from the repository. The package
## module lives in `backend/` so point the module to `backend.main_backend`.
## If you prefer installing and running the wheel from `dist/`, we can add
## an optional install step — currently this script runs the in-repo module.
BACKEND_MODULE="backend.main_backend"

# Default bind host; can be overridden by passing --lan on the command line which
# sets the bind host to 0.0.0.0 so the server is reachable on the local network.
BIND_HOST="127.0.0.1"

# Parse global flags (only --lan supported currently). Remove recognized
# flags from the positional parameters so the case statement sees only the
# subcommand.
NEW_ARGS=()
for a in "$@"; do
  case "$a" in
    --lan)
      BIND_HOST="0.0.0.0"
      ;;
    --help|-h|help)
      NEW_ARGS+=(help)
      ;;
    *)
      NEW_ARGS+=("$a")
      ;;
  esac
done
# Replace positional parameters with filtered args
set -- "${NEW_ARGS[@]:-}"

start_backend() {

  if [ -f "$PID_FILE" ]; then
    if kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1; then
      echo "Backend already running (pid $(cat "$PID_FILE")). Stopping it first..."
      stop_backend
    else
      echo "Stale pidfile found, removing."; rm -f "$PID_FILE"
    fi
  fi

  # Purge old logs to ensure process panel starts clean
  echo "Purging old log files..."
  rm -rf "$LOG_DIR"/* || true
  rm -rf "$ROOT_DIR/backend/logs"/* || true

  echo "Starting backend (module: $BACKEND_MODULE) binding to $BIND_HOST..."
  # Pass the bind host via environment variable that the Python entrypoint reads
  # Force PYTHONPATH to include ROOT_DIR so we use local source instead of installed packages
  export PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}"
  # -u: unbuffered stdout/stderr. Without it, Python fully block-buffers
  # output once it's not a TTY (redirected to $LOG_FILE), so print()
  # diagnostics -- including the INDI driver's "No indiserver running on
  # host:port" -- can sit unflushed indefinitely, making the log look
  # silent even while something is actively failing.
  ASTROMETRICS_BIND_HOST="$BIND_HOST" nohup "$PYTHON" -u -m "$BACKEND_MODULE" >"$LOG_FILE" 2>&1 &
  backend_pid=$!
  echo "$backend_pid" > "$PID_FILE"

  # Health Check Loop
  echo "Waiting for backend to become healthy..."
  for i in {1..120}; do
      if curl -s "http://$BIND_HOST:5000/" >/dev/null; then
          echo -e "\033[0;32mBackend started successfully (pid $backend_pid).\033[0m"
          echo "Logs: $LOG_FILE"
          echo "Listening on http://$BIND_HOST:5000"
          return 0
      fi

      # Check if process died while waiting
      if ! kill -0 "$backend_pid" >/dev/null 2>&1; then
          echo -e "\033[0;31mERROR: Backend process died during startup.\033[0m"
          echo "Last 20 lines of log ($LOG_FILE):"
          echo "---------------------------------------------------"
          tail -n 20 "$LOG_FILE"
          echo "---------------------------------------------------"
          rm -f "$PID_FILE"
          return 1
      fi
      sleep 0.5
  done

  # Timed out
  echo -e "\033[0;31mERROR: Backend process is alive but timed out answering health check (http://$BIND_HOST:5000/).\033[0m"
  echo "It might be hanging or stuck. Check logs:"
  echo "$LOG_FILE"
  return 1
}

install_into_env() {
  # Install the project into the selected Python environment.
  # Prefer a wheel from dist/ if available, otherwise fall back to an editable install.
  echo "Installing project into environment using $PYTHON..."
  if compgen -G "$ROOT_DIR/dist/*.whl" > /dev/null; then
    echo "Found wheel(s) in $ROOT_DIR/dist/; installing..."
    # shellcheck disable=SC2086
    "$PYTHON" -m pip install --upgrade "$ROOT_DIR/dist/"*.whl
  else
    echo "No wheel found in $ROOT_DIR/dist/; installing editable from source..."
    "$PYTHON" -m pip install -e "$ROOT_DIR/backend"
  fi


}

start_foreground() {
  echo "Starting backend in foreground (Ctrl+C to exit)..."
  ASTROMETRICS_BIND_HOST="$BIND_HOST" exec "$PYTHON" -u -m "$BACKEND_MODULE"
}

stop_backend() {
  if [ ! -f "$PID_FILE" ]; then
    echo "No pidfile found at $PID_FILE; backend not running?"; return 0
  fi
  pid=$(cat "$PID_FILE")
  if kill -0 "$pid" >/dev/null 2>&1; then
    echo "Stopping backend pid $pid..."
    kill "$pid"
    sleep 1
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "Backend did not exit; sending SIGKILL..."
      kill -9 "$pid" || true
    fi
    rm -f "$PID_FILE"
    echo "Stopped."
  else
    echo "Process $pid not running; removing stale pidfile."; rm -f "$PID_FILE"
  fi
}

status_backend() {
  if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "Backend running (pid $pid). Logs: $LOG_FILE"
      return 0
    else
      echo "Stale pidfile found (pid $pid not running)."
      return 1
    fi
  else
    echo "Backend not running (no $PID_FILE)."
  fi
}

case ${1:-start} in
  start)
    start_backend
    ;;
  install)
    install_into_env
    ;;
  foreground)
    start_foreground
    ;;
  help|--help|-h)
    # Print a helpful multi-line description of commands
    cat <<'EOF'
Usage: ./scripts/run_backend.sh [OPTIONS] <command>

Commands:
  start               Start backend in background (writes PID to .run_pids/backend.pid)
  foreground          Run backend in foreground (logs to stdout)
  stop                Stop backgrounded backend (uses PID file)
  restart             Stop and then start the backend
  status              Show backend status and log location
  install             Install the latest wheel from dist/ (or do editable install) into the selected Python environment

Global OPTIONS:
  --lan               Bind the server to 0.0.0.0 so it is reachable from other machines on the LAN. Use as:
                      ./scripts/run_backend.sh --lan start
  -h, --help, help    Show this help message

Notes:
  - The script prefers an existing .venv (./.venv/bin/python) for installs and runtime.
  - When --lan is used, the script sets ASTROMETRICS_BIND_HOST=0.0.0.0 for the backend process.
EOF
    ;;
  stop)
    stop_backend
    ;;
  status)
    status_backend
    ;;
  restart)
    stop_backend
    sleep 1
    start_backend
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|foreground|install}"
    exit 2
    ;;
esac
