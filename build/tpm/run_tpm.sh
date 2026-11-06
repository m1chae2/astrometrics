#!/usr/bin/env bash
set -euo pipefail

# run_tpm.sh
# Measures the technical performance measures (TPMs) of the app: how fast the
# displays switch, how much the page blocks, how much memory and CPU the
# backend and the renderer use, and how many backend calls each display makes.
# It compares the result with build/tpm/tpm_baseline.json and prints a table.
#
# SAFETY: everything runs against a throwaway copy of the world. The script
# makes its own library folder, settings file, databases and a backend on a
# random port with the INDI simulator, exactly as ui/globalSetup.ts does for
# the UI tests. It never talks to port 5000, your real library, your real
# databases or any hardware, and it deletes its folder when it ends.
#
# Usage:
#   build/tpm/run_tpm.sh [--laps N] [--idle-seconds S] [--electron [--visible] [--ozone P]] [--idle-minutes M]
#                        [--gpu auto|intel|nvidia] [--require-power-profile NAME]
#                        [--observatory offline|simulated|replay] [--sim-exposure-seconds S]
#                        [--replay-night YYYY-MM-DD] [--replay-speed N] [--replay-window-seconds S]
#                        [--replay-preload-frames N] [--no-seal]
#                        [--pan-seconds S] [--sky-catalog-copy] [--scale N] [--real-snapshot]
#                        [--cold] [--after-reboot] [--preflight [--skip-preflight]] [--update-baseline] [--keep]
#     --laps N            laps of the display flow (default 3)
#     --idle-seconds S    idle time at the end of the Chrome flow, for the polling count (default 30)
#     --electron          also run the lap in the Electron app (needs xvfb-run) and collect its process metrics
#     --visible           with --electron: open the Electron window on your real display (real GPU), not in xvfb
#     --ozone P           with --visible: Electron's --ozone-platform-hint (auto, x11 or wayland; default auto)
#     --idle-minutes M    idle minutes before the Electron lap (default 3)
#     --gpu CHOICE        graphics card for Electron (with --electron) or the Chrome flow: auto (default), intel or nvidia.
#                         The run stops if the WebGL renderer does not show the card asked for (see README.md)
#     --require-power-profile NAME  refuse to run unless `powerprofilesctl get` prints NAME and AC power is online
#                         (never changes the profile; set it with `powerprofilesctl set performance`)
#     --observatory P     observatory profile: offline (default; no telescope connection) or simulated (a telescope that
#                         is "on": a local INDI server with simulators, a stand-in PHD2, a folder that plays the telescope
#                         computer, automatic downloads on; every piece is local, see README.md)
#                         replay: the same stand-ins, but they play the last minutes of a real night (see README.md)
#     --sim-exposure-seconds S  with --observatory simulated: seconds per simulated exposure (default 60)
#     --replay-night DATE with --observatory replay: the night to play (default 2026-10-03)
#     --replay-speed N    with --observatory replay: pack N times as much of the night into the same minutes (default 1)
#     --replay-window-seconds S  with --observatory replay: real seconds of live replay (default 300)
#     --replay-preload-frames N  with --observatory replay: earlier frames loaded before the run (default 16)
#     --no-seal           offline profile only: do not seal the run in its own loopback-only network
#                         (simulated and replay runs are always sealed; real-snapshot is never sealed)
#     --pan-seconds S     length of the traced Planetarium pan (default 6)
#     --sky-catalog-copy  copy your star-catalog caches (read only) into the throwaway folder (synthetic mode)
#     --scale N           synthetic library with N times the targets and frames per target
#     --real-snapshot     measure on copies of your real databases and a copy-on-write overlay of your real library
#                         (a trend, not a gate; see README.md)
#     --cold              drop the Linux page cache for the files the run reads (fadvise, no root) before the flow, so the
#                         run is a cold start. Tags the results cold: true and gives the run its own baseline key.
#                         On a memory-backed /tmp the throwaway folder is made in /var/tmp instead (see README.md)
#     --after-reboot      say that the computer was just restarted: records boot_age_seconds and cold_source: reboot
#                         (with --cold; without it, cold_source is fadvise). Use it for the first run after a reboot
#     --preflight         only check that no rclone upload, no database backup and no real backend (port 5000) runs;
#                         exit 1 and say what to stop. The checks also run before every --cold or --after-reboot run
#     --skip-preflight    run the --cold or --after-reboot checks as a warning only (for a function check, never for a baseline)
#     --update-baseline   write this run's values into tpm_baseline.json (a deliberate act; see README.md)
#     --keep              keep the throwaway folder afterwards (for debugging)
#   Set CHROME_PATH to the Chrome, Chromium or Brave executable if it is not in a usual place.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
TPM_DIR="$ROOT_DIR/build/tpm"
PYTHON="$ROOT_DIR/.venv/bin/python"
REAL_BACKEND_PORT=5000

LAPS=3
IDLE_SECONDS=30
RUN_ELECTRON=0
IDLE_MINUTES=3
UPDATE_BASELINE=0
KEEP=0
VISIBLE=0
OZONE=auto
PAN_SECONDS=6
SKY_CATALOG_COPY=0
SCALE=1
REAL_SNAPSHOT=0
GPU=auto
REQUIRED_POWER_PROFILE=""
COLD=0
AFTER_REBOOT=0
PREFLIGHT_ONLY=0
SKIP_PREFLIGHT=0
MODE=synthetic
#: The observatory profile: offline (no telescope connection at all) or simulated (local simulators only).
OBSERVATORY_PROFILE=offline
SIM_EXPOSURE_SECONDS=60
REPLAY_NIGHT=2026-10-03
REPLAY_SPEED=1
REPLAY_WINDOW_SECONDS=300
REPLAY_PRELOAD_FRAMES=16
#: 1 seals the run in its own network namespace with only loopback (see README.md).
SEAL=1
ORIGINAL_ARGUMENTS=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --laps) LAPS="$2"; shift 2 ;;
    --idle-seconds) IDLE_SECONDS="$2"; shift 2 ;;
    --electron) RUN_ELECTRON=1; shift ;;
    --idle-minutes) IDLE_MINUTES="$2"; shift 2 ;;
    --update-baseline) UPDATE_BASELINE=1; shift ;;
    --keep) KEEP=1; shift ;;
    --visible) VISIBLE=1; shift ;;
    --ozone) OZONE="$2"; shift 2 ;;
    --gpu) GPU="$2"; shift 2 ;;
    --require-power-profile) REQUIRED_POWER_PROFILE="$2"; shift 2 ;;
    --pan-seconds) PAN_SECONDS="$2"; shift 2 ;;
    --sky-catalog-copy) SKY_CATALOG_COPY=1; shift ;;
    --scale) SCALE="$2"; shift 2 ;;
    --observatory) OBSERVATORY_PROFILE="$2"; shift 2 ;;
    --sim-exposure-seconds) SIM_EXPOSURE_SECONDS="$2"; shift 2 ;;
    --replay-night) REPLAY_NIGHT="$2"; shift 2 ;;
    --replay-speed) REPLAY_SPEED="$2"; shift 2 ;;
    --replay-window-seconds) REPLAY_WINDOW_SECONDS="$2"; shift 2 ;;
    --replay-preload-frames) REPLAY_PRELOAD_FRAMES="$2"; shift 2 ;;
    --no-seal) SEAL=0; shift ;;
    --cold) COLD=1; shift ;;
    --after-reboot) AFTER_REBOOT=1; shift ;;
    --preflight) PREFLIGHT_ONLY=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --real-snapshot) REAL_SNAPSHOT=1; MODE=real-snapshot; shift ;;
    -h|--help) sed -n 3,65p "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ "$VISIBLE" = "1" ] && [ "$RUN_ELECTRON" != "1" ]; then echo "ERROR: --visible needs --electron." >&2; exit 2; fi
case "$GPU" in auto|intel|nvidia) ;; *) echo "ERROR: --gpu must be auto, intel or nvidia." >&2; exit 2 ;; esac
if [ "$GPU" = "nvidia" ] && ! command -v nvidia-smi >/dev/null; then echo "ERROR: --gpu nvidia needs nvidia-smi." >&2; exit 1; fi
case "$OBSERVATORY_PROFILE" in offline|simulated|replay) ;; *) echo "ERROR: --observatory must be offline, simulated or replay." >&2; exit 2 ;; esac
#: 1 when the telescope is a set of local stand-ins (the simulated and the replay profile).
LIVE=0
[ "$OBSERVATORY_PROFILE" = "offline" ] || LIVE=1
if [ "$LIVE" = "1" ]; then
  if [ "$REAL_SNAPSHOT" = "1" ]; then echo "ERROR: --observatory $OBSERVATORY_PROFILE applies to the synthetic library only." >&2; exit 2; fi
  if [ "$SEAL" != "1" ]; then echo "ERROR: --no-seal is allowed only with the offline profile." >&2; exit 2; fi
  for tool in indiserver indi_simulator_telescope indi_simulator_ccd indi_simulator_focus indi_simulator_wheel indi_simulator_guide indi_setprop indi_getprop rsync ss setsid; do
    command -v "$tool" >/dev/null || { echo "ERROR: --observatory $OBSERVATORY_PROFILE needs $tool." >&2; exit 1; }
  done
fi
# A real-snapshot run has its own mounts and its own bwrap lock; it is not sealed.
[ "$REAL_SNAPSHOT" = "1" ] && SEAL=0
if [ "$REAL_SNAPSHOT" = "1" ] && [ "$SCALE" != "1" ]; then echo "ERROR: --scale applies to the synthetic library only." >&2; exit 2; fi
if [ "$REAL_SNAPSHOT" = "1" ]; then
  for tool in fuse-overlayfs bwrap fusermount3 mountpoint; do
    command -v "$tool" >/dev/null || { echo "ERROR: --real-snapshot needs $tool." >&2; exit 1; }
  done
fi
[ -x "$PYTHON" ] || { echo "ERROR: $PYTHON not found. Run build/linux/setup_venv.sh first." >&2; exit 1; }

# The quiet-computer check. It must run before the seal: the sealed run has its own process list and network, so it
# could not see an upload, a backup or the real backend. It stops nothing; it only says what you should stop.
if [ -z "${TPM_SEALED:-}" ] && { [ "$PREFLIGHT_ONLY" = "1" ] || [ "$COLD" = "1" ] || [ "$AFTER_REBOOT" = "1" ]; }; then
  if [ "$SKIP_PREFLIGHT" = "1" ] && [ "$PREFLIGHT_ONLY" != "1" ]; then
    "$PYTHON" "$TPM_DIR/preflight.py" --port "$REAL_BACKEND_PORT" || echo "WARNING: --skip-preflight: going on although the computer is not quiet; do not use this run as a baseline." >&2
  else
    "$PYTHON" "$TPM_DIR/preflight.py" --port "$REAL_BACKEND_PORT" || exit 1
  fi
fi
[ "$PREFLIGHT_ONLY" = "1" ] && exit 0

# A cold run needs a throwaway folder on a disk. On a memory-backed /tmp (tmpfs) the files live in the page cache,
# so nothing can be dropped from it; use /var/tmp (a disk) then. An existing TMPDIR is left alone.
if [ "$COLD" = "1" ] && [ -z "${TMPDIR:-}" ] && [ "$("$PYTHON" "$TPM_DIR/cold_cache.py" filesystem /tmp)" = "tmpfs" ]; then
  if [ -d /var/tmp ] && [ -w /var/tmp ] && [ "$("$PYTHON" "$TPM_DIR/cold_cache.py" filesystem /var/tmp)" != "tmpfs" ]; then
    export TMPDIR=/var/tmp
    echo "== /tmp is memory-backed: the throwaway folder goes to /var/tmp, so --cold can drop its files from the cache"
  else
    echo "WARNING: /tmp is memory-backed and /var/tmp is not usable; the throwaway files cannot be dropped from the cache." >&2
  fi
fi
BOOT_AGE_SECONDS="$("$PYTHON" "$TPM_DIR/cold_cache.py" boot-age)"

# The seal: run the whole script again in a new network namespace that has only a loopback interface, so nothing
# in the run (backend, INDI server, stand-in PHD2, drivers, UI server, browser, Electron) can reach any network and
# nothing from outside can reach it. Everything that talks over 127.0.0.1 is a child of this script, so it is
# inside too. --unshare-pid makes every process of the run end when the script ends. The whole computer is bound
# read only; only the throwaway folder's parent (TMPDIR, /var/tmp for --cold), the results folder, Vite's cache
# folders and the display sockets (X11, Wayland) are writable, so a visible window works and the run cannot change your files.
if [ "$SEAL" = "1" ] && [ -z "${TPM_SEALED:-}" ]; then
  command -v bwrap >/dev/null || { echo "ERROR: sealing the run needs bwrap (bubblewrap)." >&2; exit 1; }
  echo "== Sealing the run in its own loopback-only network (bwrap --unshare-net, read-only computer)"
  mkdir -p "$TPM_DIR/results"
  SEAL_BINDS=(--bind "${TMPDIR:-/tmp}" "${TMPDIR:-/tmp}" --bind "$TPM_DIR/results" "$TPM_DIR/results")
  # Vite writes its bundled config and cache under node_modules; nothing else in the repository is writable.
  mkdir -p "$ROOT_DIR/node_modules/.vite-temp" "$ROOT_DIR/node_modules/.vite"
  SEAL_BINDS+=(--bind "$ROOT_DIR/node_modules/.vite-temp" "$ROOT_DIR/node_modules/.vite-temp"
    --bind "$ROOT_DIR/node_modules/.vite" "$ROOT_DIR/node_modules/.vite")
  [ -d /tmp/.X11-unix ] && SEAL_BINDS+=(--bind /tmp/.X11-unix /tmp/.X11-unix)
  [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "$XDG_RUNTIME_DIR" ] && SEAL_BINDS+=(--bind "$XDG_RUNTIME_DIR" "$XDG_RUNTIME_DIR")
  exec env TPM_SEALED=1 bwrap --ro-bind / / --dev-bind /dev /dev --proc /proc "${SEAL_BINDS[@]}" \
    --unshare-net --unshare-pid --die-with-parent "$0" "${ORIGINAL_ARGUMENTS[@]}"
fi
if [ -n "${TPM_SEALED:-}" ]; then
  "$PYTHON" "$TPM_DIR/seal_guard.py" sealed || exit 1
fi

if [ -n "$REQUIRED_POWER_PROFILE" ]; then
  "$PYTHON" "$TPM_DIR/setup_fingerprint.py" check-power "$REQUIRED_POWER_PROFILE" || exit 1
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RESULTS_DIR="$TPM_DIR/results/$TIMESTAMP"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/astrometrics-tpm-XXXXXX")"
case "$SANDBOX" in
  "${TMPDIR:-/tmp}"/astrometrics-tpm-*) ;;
  *) echo "ERROR: unexpected sandbox path $SANDBOX" >&2; exit 1 ;;
esac
mkdir -p "$RESULTS_DIR"

BACKEND_PID=""
PREVIEW_PID=""
SAMPLER_PID=""
NVIDIA_SAMPLER_PID=""
# The simulated observatory (empty in the offline profile): one program that starts the INDI server, the
# stand-in PHD2 and the driver through the supervisor, and ends them all when it gets SIGTERM.
OBSERVATORY_PID=""
# Under bwrap the backend is a child that outlives bwrap itself, so it is stopped by its own id.
BACKEND_CHILD_PID=""
OVERLAY_MNT=""

# Stops every process this script started and removes the throwaway folder.
cleanup() {
  local status=$?
  for pid in "$NVIDIA_SAMPLER_PID" "$SAMPLER_PID" "$PREVIEW_PID" "$BACKEND_CHILD_PID" "$BACKEND_PID" "$OBSERVATORY_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  sleep 1
  # If the observatory program was killed, its supervisors notice and end the stand-ins on their own.
  for pid in "$PREVIEW_PID" "$BACKEND_CHILD_PID" "$BACKEND_PID" "$OBSERVATORY_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  if [ -f "$SANDBOX/backend.log" ]; then
    cp "$SANDBOX/backend.log" "$RESULTS_DIR/backend.log" 2>/dev/null || true
  fi
  # The overlay must be unmounted before anything is deleted, always.
  local still_mounted=0
  if [ -n "$OVERLAY_MNT" ]; then
    for _ in 1 2 3 4 5; do
      grep -q " $OVERLAY_MNT " /proc/mounts || break
      fusermount3 -u "$OVERLAY_MNT" 2>/dev/null || fusermount -u "$OVERLAY_MNT" 2>/dev/null || true
      sleep 1
    done
    if grep -q " $OVERLAY_MNT " /proc/mounts; then
      echo "WARNING: a plain unmount failed (something still uses the overlay); forcing a lazy unmount." >&2
      fusermount3 -uz "$OVERLAY_MNT" 2>/dev/null || true
      sleep 1
      status=1
    fi
    if grep -q " $OVERLAY_MNT " /proc/mounts; then
      still_mounted=1
      echo "ERROR: the overlay is STILL MOUNTED at $OVERLAY_MNT. Unmount it by hand (fusermount3 -u) before deleting anything." >&2
      status=1
    else
      echo "Overlay unmounted (checked in /proc/mounts)."
    fi
  fi
  if [ "$still_mounted" = "1" ]; then
    echo "Throwaway folder NOT deleted: $SANDBOX" >&2
  elif [ "$KEEP" = "1" ]; then
    echo "Throwaway folder kept: $SANDBOX"
  else
    rm -rf "${SANDBOX:?}"
  fi
  exit "$status"
}
trap cleanup EXIT

free_port() {
  "$PYTHON" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

BACKEND_PORT="$(free_port)"
UI_PORT="$(free_port)"
INDI_PORT=""
PHD2_PORT=""
if [ "$LIVE" = "1" ]; then
  INDI_PORT="$(free_port)"
  PHD2_PORT="$(free_port)"
fi
# 4400 is a real PHD2's port and 7624 is INDI's usual port: no stand-in uses them either.
for port in "$BACKEND_PORT" "$UI_PORT" "$INDI_PORT" "$PHD2_PORT"; do
  case "$port" in
    "$REAL_BACKEND_PORT"|4400|7624) echo "ERROR: refusing to use port $port (a real service's port)." >&2; exit 1 ;;
  esac
done
STANDIN_HOME="$SANDBOX/standin/home"

# These steps read your real files (read only) and so run before the script points XDG folders at the throwaway folder.
if [ "$REAL_SNAPSHOT" = "1" ]; then
  echo "== Real snapshot: copying the databases and catalogs (read only) into the throwaway folder"
  "$PYTHON" "$TPM_DIR/real_snapshot.py" prepare "$SANDBOX"
  CONFIG_PATH="$SANDBOX/astrometrics.config.toml"
else
  CONFIG_PATH="$("$PYTHON" "$TPM_DIR/write_sandbox_config.py" "$SANDBOX" --profile "$OBSERVATORY_PROFILE" \
    ${INDI_PORT:+--indi-port "$INDI_PORT"} ${PHD2_PORT:+--phd2-port "$PHD2_PORT"})"
  if [ "$OBSERVATORY_PROFILE" = "replay" ]; then
    # Reads the night's real logs and frames (read only) and stages what the run needs inside the throwaway
    # folder. This runs before the XDG folders point at the throwaway folder, because it reads the real ones.
    echo "== Staging the replay of the night $REPLAY_NIGHT (real files are only read)"
    REPLAY_STAGE_STARTED="$(date +%s.%N)"
    "$PYTHON" -m wayfindinglib.simulation.night_replay stage "$SANDBOX" --night "$REPLAY_NIGHT" --speed "$REPLAY_SPEED" \
      --window-seconds "$REPLAY_WINDOW_SECONDS" --preload-frames "$REPLAY_PRELOAD_FRAMES" || exit 1
    REPLAY_FRAMES_IN_WINDOW="$("$PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["window_frames"]))' "$SANDBOX/replay/plan.json")"
  fi
  if [ "$SKY_CATALOG_COPY" = "1" ]; then
    echo "== Copying the star-catalog caches (read only) into the throwaway folder"
    "$PYTHON" "$TPM_DIR/copy_sky_catalog.py" "$SANDBOX"
  fi
fi
case "$CONFIG_PATH" in
  "$SANDBOX"/*) ;;
  *) echo "ERROR: settings file $CONFIG_PATH is outside the throwaway folder." >&2; exit 1 ;;
esac

# The stand-in profiles check their settings (hosts, ports, folders) when the observatory program starts,
# before any stand-in runs.

# The environment that keeps the backend, the seeder and the sampler inside the throwaway folder.
export ASTROMETRICS_CONFIG="$CONFIG_PATH"
export ASTROMETRICS_CONFIG_PATH="$CONFIG_PATH"
# ASTROMETRICS_TESTING makes the backend use its built-in simulator, no INDI, and no automatic downloads. The
# simulated profile must run the real INDI, PHD2 and download code against the stand-ins, so it leaves it unset.
if [ "$LIVE" = "1" ]; then
  unset ASTROMETRICS_TESTING
else
  export ASTROMETRICS_TESTING=1
fi
export XDG_DATA_HOME="$SANDBOX/xdg_data"
export XDG_CACHE_HOME="$SANDBOX/xdg_cache"
export XDG_STATE_HOME="$SANDBOX/xdg_state"
export XDG_CONFIG_HOME="$SANDBOX/xdg_config"
mkdir -p "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME" "$XDG_CONFIG_HOME"

cd "$ROOT_DIR"
BACKEND_COMMAND=("$PYTHON" -m backend.main_backend)
if [ "$REAL_SNAPSHOT" = "1" ]; then
  REAL_LIBRARY="$("$PYTHON" "$TPM_DIR/real_snapshot.py" get "$SANDBOX" real_library)"
  REAL_STACKS="$("$PYTHON" "$TPM_DIR/real_snapshot.py" get "$SANDBOX" real_stacks)"
  case "$REAL_LIBRARY" in /*) ;; *) echo "ERROR: the real library path must be absolute." >&2; exit 1 ;; esac
  case "$REAL_STACKS" in "$REAL_LIBRARY"|"$REAL_LIBRARY"/*) ;; *) echo "ERROR: the stacks folder is outside the library; the overlay would miss it." >&2; exit 1 ;; esac
  [ -d "$REAL_LIBRARY" ] || { echo "ERROR: $REAL_LIBRARY is not a folder." >&2; exit 1; }
  mkdir -p "$SANDBOX/overlay/upper" "$SANDBOX/overlay/work" "$SANDBOX/overlay/mnt"
  echo "== Mounting a copy-on-write overlay of $REAL_LIBRARY (reads: real files; writes: $SANDBOX/overlay/upper)"
  # The one overlay code, shared with the app's simulated observatory.
  "$PYTHON" -m wayfindinglib.simulation.library_copy mount --lower "$REAL_LIBRARY" --upper "$SANDBOX/overlay/upper" \
    --work "$SANDBOX/overlay/work" --mountpoint "$SANDBOX/overlay/mnt" || { echo "ERROR: the overlay did not mount." >&2; exit 1; }
  OVERLAY_MNT="$SANDBOX/overlay/mnt"
  grep -q " $OVERLAY_MNT " /proc/mounts || { echo "ERROR: the overlay did not mount." >&2; exit 1; }
  # The second lock: the backend runs in a mount namespace where the whole computer is read only, only the
  # throwaway folder is writable, and the overlay is bound over the real library path (the databases hold
  # absolute paths, so the backend then reads and writes the overlay and never the real library).
  bwrap --ro-bind / / --dev-bind /dev /dev --proc /proc --tmpfs /tmp --bind "$SANDBOX" "$SANDBOX" \
    --bind "$OVERLAY_MNT" "$REAL_LIBRARY" true || { echo "ERROR: bwrap does not work here; refusing the real snapshot." >&2; exit 1; }
  BACKEND_COMMAND=(bwrap --ro-bind / / --dev-bind /dev /dev --proc /proc --tmpfs /tmp --bind "$SANDBOX" "$SANDBOX"
    --bind "$OVERLAY_MNT" "$REAL_LIBRARY" --die-with-parent "${BACKEND_COMMAND[@]}")
  export PYTHONDONTWRITEBYTECODE=1
else
  echo "== Seeding the throwaway library"
  TPM_SCALE="$SCALE" "$PYTHON" "$TPM_DIR/seed_library.py" "$SANDBOX"
  if [ "$OBSERVATORY_PROFILE" = "replay" ]; then
    # The night's targets, with coordinates, as the user would have them before the night.
    "$PYTHON" "$TPM_DIR/replay_preload.py" targets "$SANDBOX" || exit 1
  fi
fi

# The stand-ins write their statistics and logs under $SANDBOX/logs; the results get a copy at the end.
SIM_STATS="$SANDBOX/logs/driver_stats.json"
PHD2_STATS="$SANDBOX/logs/phd2_stats.json"
OBSERVATORY_PORTS_FILE="$SANDBOX/observatory_ports.json"
if [ "$LIVE" = "1" ]; then
  echo "== Starting the $OBSERVATORY_PROFILE observatory: INDI simulators on port $INDI_PORT, stand-in PHD2 on port $PHD2_PORT"
  OBSERVATORY_ARGUMENTS=(--kind "$OBSERVATORY_PROFILE" --root "$SANDBOX" --indi-port "$INDI_PORT" --phd2-port "$PHD2_PORT"
    --ports-file "$OBSERVATORY_PORTS_FILE" --exposure-seconds "$SIM_EXPOSURE_SECONDS")
  # The replay was staged above, and it starts when the flow starts (below), not when the stand-ins listen.
  [ "$OBSERVATORY_PROFILE" = "replay" ] && OBSERVATORY_ARGUMENTS+=(--replay-night "$REPLAY_NIGHT" --replay-staged --manual-replay-start)
  "$PYTHON" -m wayfindinglib.simulation.observatory start "${OBSERVATORY_ARGUMENTS[@]}" > "$SANDBOX/observatory.log" 2>&1 &
  OBSERVATORY_PID=$!
  for _ in $(seq 1 90); do
    [ -f "$OBSERVATORY_PORTS_FILE" ] && break
    kill -0 "$OBSERVATORY_PID" 2>/dev/null || { echo "ERROR: the observatory did not start:" >&2; cat "$SANDBOX/observatory.log" >&2; exit 1; }
    sleep 0.5
  done
  [ -f "$OBSERVATORY_PORTS_FILE" ] || { echo "ERROR: the observatory did not start listening." >&2; exit 1; }
  # The observatory program put the ssh stand-in in $SANDBOX/standin/bin; the app's transfer code reaches it by PATH.
  BACKEND_COMMAND=(env "PATH=$SANDBOX/standin/bin:$PATH" "ASTROMETRICS_STANDIN_HOME=$STANDIN_HOME" "${BACKEND_COMMAND[@]}")
fi

if [ "$OBSERVATORY_PROFILE" = "offline" ]; then
  # The offline telescope host never answers. A refusing ssh stand-in comes first on the backend's PATH, so the
  # run never starts the real ssh (which fails inside the sealed network on the system's ssh configuration).
  mkdir -p "$SANDBOX/standin/bin"
  ln -sf "$ROOT_DIR/wayfindinglib/simulation/standin_ssh.sh" "$SANDBOX/standin/bin/ssh"
  BACKEND_COMMAND=(env "PATH=$SANDBOX/standin/bin:$PATH" "ASTROMETRICS_STANDIN_SSH_MODE=refuse" "${BACKEND_COMMAND[@]}")
fi

echo "== Starting the throwaway backend on port $BACKEND_PORT (mode: $MODE, observatory profile: $OBSERVATORY_PROFILE)"
ASTROMETRICS_PORT="$BACKEND_PORT" "${BACKEND_COMMAND[@]}" > "$SANDBOX/backend.log" 2>&1 &
BACKEND_PID=$!
for _ in $(seq 1 120); do
  if curl -fs -m 2 "http://127.0.0.1:$BACKEND_PORT/api/ready" 2>/dev/null | grep -q '"ready":true'; then
    READY=1
    break
  fi
  kill -0 "$BACKEND_PID" 2>/dev/null || { echo "ERROR: the backend stopped; see $SANDBOX/backend.log" >&2; tail -20 "$SANDBOX/backend.log" >&2; exit 1; }
  sleep 1
done
[ "${READY:-0}" = "1" ] || { echo "ERROR: the backend did not become ready." >&2; exit 1; }

echo "== Building the production UI"
npx vite build --outDir "$SANDBOX/dist" --emptyOutDir > "$SANDBOX/ui_build.log" 2>&1 || { tail -20 "$SANDBOX/ui_build.log" >&2; exit 1; }

echo "== Serving the UI on port $UI_PORT"
TPM_UI_DIST="$SANDBOX/dist" TPM_UI_PORT="$UI_PORT" TPM_BACKEND_ORIGIN="http://127.0.0.1:$BACKEND_PORT" \
  node "$ROOT_DIR/node_modules/vite/bin/vite.js" preview --config "$TPM_DIR/vite.tpm.config.mjs" > "$SANDBOX/preview.log" 2>&1 &
PREVIEW_PID=$!
for _ in $(seq 1 30); do
  curl -fs -m 2 "http://127.0.0.1:$UI_PORT/api/ready" 2>/dev/null | grep -q '"ready":true' && break
  sleep 1
done

# Under bwrap the process that runs the backend is a child of the one started; sample that child.
SAMPLE_PID="$BACKEND_PID"
if [ "$REAL_SNAPSHOT" = "1" ]; then
  for candidate in $(pgrep -a -f "backend.main_backend" | awk '{print $1}'); do
    if tr '\0' '\n' < "/proc/$candidate/environ" 2>/dev/null | grep -qx "ASTROMETRICS_PORT=$BACKEND_PORT" \
      && tr '\0' ' ' < "/proc/$candidate/cmdline" | grep -q "^[^ ]*python"; then
      SAMPLE_PID="$candidate"
      BACKEND_CHILD_PID="$candidate"
    fi
  done
fi

if [ "$LIVE" = "1" ]; then
  # Give the backend a few seconds to open its INDI and PHD2 connections, then check where it connects.
  sleep 8
  "$PYTHON" "$TPM_DIR/seal_guard.py" connections --pid "$SAMPLE_PID" || exit 1
fi

if [ "$OBSERVATORY_PROFILE" = "replay" ]; then
  # Setup, timed apart from the run: the app downloads, indexes and measures the earlier frames of the night.
  echo "== Loading the end of the night: the app ingests the earlier frames (setup, not timed as a TPM)"
  for _ in $(seq 1 60); do [ -f "$SANDBOX/replay/ready" ] && break; sleep 1; done
  [ -f "$SANDBOX/replay/ready" ] || { echo "ERROR: the replay driver did not connect the simulators." >&2; exit 1; }
  "$PYTHON" "$TPM_DIR/replay_preload.py" ingest "$SANDBOX" --backend-port "$BACKEND_PORT" --out "$SANDBOX/replay/ingest.json" || exit 1
  REPLAY_SETUP_SECONDS="$(awk -v a="$REPLAY_STAGE_STARTED" -v b="$(date +%s.%N)" 'BEGIN { printf "%.1f", b - a }')"
  echo "Setup took $REPLAY_SETUP_SECONDS s (staging, building and serving the UI, and ingesting the earlier frames)."
fi

COLD_ARGUMENT=()
if [ "$COLD" = "1" ]; then
  echo "== Cold start: dropping the files the run reads from the page cache (boot age ${BOOT_AGE_SECONDS} s)"
  COLD_COMMAND=("$PYTHON" "$TPM_DIR/cold_cache.py" drop --sandbox "$SANDBOX" --repo "$ROOT_DIR" --out "$RESULTS_DIR/cold-cache.json")
  [ "$REAL_SNAPSHOT" = "1" ] && COLD_COMMAND+=(--real-stacks "$REAL_STACKS")
  [ -n "${CHROME_PATH:-}" ] && [ -e "$CHROME_PATH" ] && COLD_COMMAND+=(--browser "$CHROME_PATH")
  [ "$AFTER_REBOOT" = "1" ] && COLD_COMMAND+=(--after-reboot)
  "${COLD_COMMAND[@]}"
  COLD_ARGUMENT=(--cold --cold-report "$RESULTS_DIR/cold-cache.json")
fi
[ "$AFTER_REBOOT" = "1" ] && COLD_ARGUMENT+=(--after-reboot)

echo "== Recording the setup (CPU, power, displays, graphics cards)"
"$PYTHON" "$TPM_DIR/setup_fingerprint.py" collect --out "$RESULTS_DIR/setup-fingerprint.json"
if command -v nvidia-smi >/dev/null; then
  "$PYTHON" "$TPM_DIR/setup_fingerprint.py" nvidia-sample --out "$RESULTS_DIR/nvidia-samples.jsonl" &
  NVIDIA_SAMPLER_PID=$!
fi

echo "== Sampling the backend process"
"$PYTHON" "$TPM_DIR/rpc_timings.py" sample --pid "$SAMPLE_PID" --port "$BACKEND_PORT" --out "$RESULTS_DIR/backend-samples.jsonl" &
SAMPLER_PID=$!

# With --electron the chosen card applies to the Electron window; the Chrome flow then stays on auto.
CHROME_GPU="$GPU"
[ "$RUN_ELECTRON" = "1" ] && CHROME_GPU=auto
if [ "$OBSERVATORY_PROFILE" = "replay" ]; then
  # The timed run starts here: the replayed window begins 3 s from now, as the flow starts.
  "$PYTHON" -c 'import time; print(time.time() + 3.0)' > "$SANDBOX/replay/start"
fi
echo "== Running the flow in Chrome ($LAPS laps, gpu: $CHROME_GPU)"
node "$TPM_DIR/flow.mjs" --ui-url "http://127.0.0.1:$UI_PORT" --backend-url "http://127.0.0.1:$BACKEND_PORT" \
  --out "$RESULTS_DIR" --laps "$LAPS" --idle-seconds "$IDLE_SECONDS" --pan-seconds "$PAN_SECONDS" --gpu "$CHROME_GPU"

ELECTRON_ARGUMENT=()
if [ "$RUN_ELECTRON" = "1" ]; then
  if [ "$VISIBLE" = "1" ]; then
    echo "NOTICE: an Electron window will open on your real display for this run (real GPU). Leave it alone until it closes."
  fi
  echo "== Running the lap in Electron ($IDLE_MINUTES idle minutes first)"
  node "$TPM_DIR/electron_run.mjs" --sandbox "$SANDBOX" --ui-url "http://127.0.0.1:$UI_PORT" \
    --backend-url "http://127.0.0.1:$BACKEND_PORT" --out "$RESULTS_DIR" --idle-minutes "$IDLE_MINUTES" --laps "$LAPS" \
    --pan-seconds "$PAN_SECONDS" --visible "$VISIBLE" --ozone "$OZONE" --gpu "$GPU"
  ELECTRON_ARGUMENT=(--electron "$RESULTS_DIR/electron-metrics.jsonl")
fi

OBSERVATORY_ARGUMENT=()
if [ "$LIVE" = "1" ]; then
  WAIT_STATUS=0
  if [ "$OBSERVATORY_PROFILE" = "replay" ]; then
    echo "== Waiting for the replay window to finish and its $REPLAY_FRAMES_IN_WINDOW frames to reach the library"
    "$PYTHON" "$TPM_DIR/observatory_metrics.py" wait --sandbox "$SANDBOX" --sim-stats "$SIM_STATS" \
      --frames "$REPLAY_FRAMES_IN_WINDOW" --require-finished --timeout "$(awk -v w="$REPLAY_WINDOW_SECONDS" 'BEGIN { print w + 240 }')" || WAIT_STATUS=1
  else
    echo "== Waiting for a simulated frame to reach the library (exposure $SIM_EXPOSURE_SECONDS s)"
    "$PYTHON" "$TPM_DIR/observatory_metrics.py" wait --sandbox "$SANDBOX" --sim-stats "$SIM_STATS" \
      --timeout "$((SIM_EXPOSURE_SECONDS + 150))" || WAIT_STATUS=1
  fi
  # The last look at the backend's connections, after all the traffic of the run.
  "$PYTHON" "$TPM_DIR/seal_guard.py" connections --pid "$SAMPLE_PID" || exit 1
  [ "$WAIT_STATUS" = "0" ] || exit 1
fi

kill "$SAMPLER_PID" 2>/dev/null || true
wait "$SAMPLER_PID" 2>/dev/null || true
SAMPLER_PID=""
if [ -n "$NVIDIA_SAMPLER_PID" ]; then
  kill "$NVIDIA_SAMPLER_PID" 2>/dev/null || true
  wait "$NVIDIA_SAMPLER_PID" 2>/dev/null || true
  NVIDIA_SAMPLER_PID=""
fi
# Stop the backend now, so the last writes are done before the data sizes and the overlay writes are read.
for pid in "$BACKEND_CHILD_PID" "$BACKEND_PID"; do
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
done
for _ in $(seq 1 20); do
  kill -0 "${BACKEND_CHILD_PID:-$BACKEND_PID}" 2>/dev/null || break
  sleep 0.5
done
for pid in "$BACKEND_CHILD_PID" "$BACKEND_PID"; do
  [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null || true
done
cp "$SANDBOX/backend.log" "$RESULTS_DIR/backend.log"
if [ "$LIVE" = "1" ]; then
  # Stop the simulators, so their statistics files are final, then reduce them.
  "$PYTHON" -m wayfindinglib.simulation.observatory stop --root "$SANDBOX" || true
  OBSERVATORY_PID=""
  cp "$SIM_STATS" "$RESULTS_DIR/observatory-sim-stats.json" 2>/dev/null || true
  cp "$PHD2_STATS" "$RESULTS_DIR/observatory-phd2-stats.json" 2>/dev/null || true
  REPLAY_ARGUMENT=()
  if [ "$OBSERVATORY_PROFILE" = "replay" ]; then
    "$PYTHON" -m wayfindinglib.simulation.night_replay report "$SANDBOX" --ingest "$SANDBOX/replay/ingest.json" \
      --setup-seconds "$REPLAY_SETUP_SECONDS" --out "$RESULTS_DIR/replay-summary.json"
    cp "$SANDBOX/replay/plan.json" "$RESULTS_DIR/replay-plan.json"
    REPLAY_ARGUMENT=(--replay-summary "$RESULTS_DIR/replay-summary.json")
  fi
  "$PYTHON" "$TPM_DIR/observatory_metrics.py" summarize --sandbox "$SANDBOX" --sim-stats "$SIM_STATS" \
    --phd2-stats "$PHD2_STATS" --out "$RESULTS_DIR/observatory-metrics.json" "${REPLAY_ARGUMENT[@]}"
  cp "$SANDBOX/logs/indiserver.log" "$SANDBOX/logs/mock_phd2.log" "$SANDBOX/logs/driver.log" "$RESULTS_DIR/" 2>/dev/null || true
  [ -f "$RESULTS_DIR/driver.log" ] && mv "$RESULTS_DIR/driver.log" "$RESULTS_DIR/exposure_driver.log"
  OBSERVATORY_ARGUMENT=(--observatory-metrics "$RESULTS_DIR/observatory-metrics.json")
fi
FRAMES_ROOT="$SANDBOX/library/frames"
EXTRA_COMPILE_ARGUMENTS=()
if [ "$REAL_SNAPSHOT" = "1" ]; then
  FRAMES_ROOT="$OVERLAY_MNT"
  "$PYTHON" "$TPM_DIR/real_snapshot.py" overlay-report "$SANDBOX/overlay/upper" "$RESULTS_DIR/overlay-writes.json"
  EXTRA_COMPILE_ARGUMENTS+=(--overlay-writes "$RESULTS_DIR/overlay-writes.json")
fi
"$PYTHON" "$TPM_DIR/data_size.py" --library-dir "$SANDBOX/library" --wayfinding-db "$SANDBOX/wayfinding/wayfinding.db" \
  --frames-root "$FRAMES_ROOT" --catalog-db "$SANDBOX/xdg_cache/astrometrics/catalogs/catalog_cache.db" --out "$RESULTS_DIR/data-size.json"
"$PYTHON" "$TPM_DIR/rpc_timings.py" summarize --flow "$RESULTS_DIR/flow-metrics.json" \
  --samples "$RESULTS_DIR/backend-samples.jsonl" --log "$RESULTS_DIR/backend.log" --out "$RESULTS_DIR/backend-metrics.json"

COMPILE_ARGUMENTS=("$RESULTS_DIR" "${ELECTRON_ARGUMENT[@]}" --mode "$MODE" --observatory-profile "$OBSERVATORY_PROFILE"
  --data-size "$RESULTS_DIR/data-size.json" --setup "$RESULTS_DIR/setup-fingerprint.json" --gpu-requested "$GPU" \
  --boot-age "$BOOT_AGE_SECONDS" "${COLD_ARGUMENT[@]}" "${OBSERVATORY_ARGUMENT[@]}" "${EXTRA_COMPILE_ARGUMENTS[@]}")
[ "$UPDATE_BASELINE" = "1" ] && COMPILE_ARGUMENTS+=(--update-baseline)
echo "== TPMs (results in $RESULTS_DIR)"
set +e
"$PYTHON" "$TPM_DIR/compile_tpm.py" "${COMPILE_ARGUMENTS[@]}"
COMPILE_STATUS=$?
set -e
echo "Flow report: $RESULTS_DIR/flow-report.html"
exit "$COMPILE_STATUS"
