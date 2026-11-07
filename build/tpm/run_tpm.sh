#!/usr/bin/env bash
set -euo pipefail

# run_tpm.sh
# Measures the technical performance measures (TPMs) of the app: how fast the
# displays switch, how much the page blocks, how much memory and CPU the
# backend and the renderer use, and how many backend calls each display makes.
# It compares the result with build/tpm/tpm_baseline.json and prints a table.
#
# SAFETY: everything runs against a throwaway copy of the world. By default (--library simulation) the
# copy is the one the app's SIMULATED button makes: copies of your databases and a copy-on-write overlay of your
# real library (read only: every write lands in the throwaway folder), plus simulated telescope, focuser, camera
# and PHD2 on loopback ports. With --library synthetic the script makes its own made-up library, settings file
# and databases instead, as ui/globalSetup.ts does for the UI tests. Either way the backend runs on a random port
# inside a network of its own with only a loopback interface, never talks to port 5000, your real
# databases or any hardware, and the script deletes its folder when it ends.
#
# Usage:
#   build/tpm/run_tpm.sh [--library simulation|synthetic] [--laps N] [--idle-seconds S] [--browser brave|chrome [--electron]] [--headless] [--ozone P] [--idle-minutes M]
#                        [--gpu auto|intel|nvidia] [--require-power-profile NAME]
#                        [--observatory simulated|offline|replay] [--sim-exposure-seconds S]
#                        [--replay-night YYYY-MM-DD] [--replay-speed N] [--replay-window-seconds S]
#                        [--replay-preload-frames N] [--no-seal]
#                        [--pan-seconds S] [--sky-catalog-copy] [--scale N] [--real-snapshot]
#                        [--cold] [--after-reboot] [--preflight [--skip-preflight]] [--update-baseline] [--keep]
#                        [--processing TARGET [--processing-camera CAMERA] [--processing-steps LIST] [--processing-dry-run]]
#     --laps N            laps of the display flow (default 3)
#     --idle-seconds S    idle time at the end of the flow, for the polling count (default 30)
#     The flow runs in the real Electron app by default, on your real display (the real graphics card), attached over a
#     loopback DevTools port, with page throttling off. Its process metrics are collected too.
#     --browser NAME      run the flow in a browser instead: brave or chrome (for the web and phone client and the
#                         navigation-only Lighthouse audits). The setup key names the target, so baselines never mix
#     --electron          with --browser: also run a second lap in the Electron app. Without --browser it changes nothing
#     --headless          run Electron in xvfb-run instead of on your display: WAYLAND_DISPLAY is unset and Electron is forced
#                         to the X11 backend, so no window opens and the page uses software graphics. For quick smoke
#                         checks only. The setup key names it electron-headless, so it is never a baseline
#     --visible           the default for Electron: the window opens on your real display without taking focus
#     --ozone P           Electron's --ozone-platform-hint on the real display (auto, x11 or wayland; default auto)
#     --idle-minutes M    idle minutes before the Electron flow starts (default 3; 0 skips the wait)
#     --gpu CHOICE        graphics card for Electron or the browser flow: auto (default), intel or nvidia.
#                         The run stops if the WebGL renderer does not show the card asked for (see README.md)
#     --require-power-profile NAME  refuse to run unless `powerprofilesctl get` prints NAME and AC power is online
#                         (never changes the profile; set it with `powerprofilesctl set performance`)
#     --library MODE     simulation (the default): a read-only copy of your real library (your databases, targets,
#                         frames, stacks and catalogs, shown through a copy-on-write overlay) with the simulated
#                         observatory. The same world as the app's SIMULATED button. The only mode that is compared
#                         with a baseline. synthetic: a small made-up library, for quick, headless and CI checks;
#                         never a baseline (every TPM is a trend)
#     --observatory P     observatory profile: simulated (the default with --library simulation: a telescope that
#                         is "on": a local INDI server with simulators, a stand-in PHD2, a folder that plays the telescope
#                         computer, automatic downloads on; every piece is local, see README.md)
#                         offline (the default with --library synthetic; no telescope connection)
#                         replay: the same stand-ins as simulated, but they play the last minutes of a real night (see
#                         README.md). Offline and replay need --library synthetic
#     --sim-exposure-seconds S  with --observatory simulated: seconds per simulated exposure (default 60)
#     --replay-night DATE with --observatory replay: the night to play (default 2026-10-03)
#     --replay-speed N    with --observatory replay: pack N times as much of the night into the same minutes (default 1)
#     --replay-window-seconds S  with --observatory replay: real seconds of live replay (default 300)
#     --replay-preload-frames N  with --observatory replay: earlier frames loaded before the run (default 16)
#     --no-seal           offline profile only: do not seal the run in its own loopback-only network
#                         (simulation, simulated and replay runs are always sealed; real-snapshot is never sealed)
#     --pan-seconds S     length of the traced Planetarium pan (default 6)
#     --sky-catalog-copy  copy your star-catalog caches (read only) into the throwaway folder (--library synthetic)
#     --scale N           synthetic library with N times the targets and frames per target (--library synthetic)
#     --real-snapshot     the older offline copy of your real library, not sealed (a trend, not a gate; used by
#                         --processing on real data; see README.md). --library simulation replaces it for the flow
#     --cold              drop the Linux page cache for the files the run reads (fadvise, no root) after the library copy
#                         is made and before the flow, so the
#                         run is a cold start. Tags the results cold: true and gives the run its own baseline key.
#                         On a memory-backed /tmp the throwaway folder is made in /var/tmp instead (see README.md)
#     --after-reboot      say that the computer was just restarted: records boot_age_seconds and cold_source: reboot
#                         (with --cold; without it, cold_source is fadvise). Use it for the first run after a reboot
#     --preflight         only check that no rclone upload, no database backup and no real backend (port 5000) runs;
#                         exit 1 and say what to stop. The checks also run before every --cold or --after-reboot run
#     --skip-preflight    run the --cold or --after-reboot checks as a warning only (for a function check, never for a baseline)
#     --update-baseline   write this run's values into tpm_baseline.json (a deliberate act; see README.md)
#     --keep              keep the throwaway folder afterwards (for debugging)
#     --processing TARGET run the app's own stack job and analysis for one target through the RPC calls of the Image
#                         Processing display, and time every stage with its CPU, memory, disk and NVIDIA use. There is no
#                         browser flow. Takes a heavy-work slot (build/agent_tools/common.sh). With --real-snapshot it
#                         works on your real data through the overlay, under a write lock; see README.md "Processing runs"
#     --processing-camera CAMERA  only the frames of this camera are stacked (default "ZWO ASI 533MM Pro")
#     --processing-steps LIST     analysis steps after the stack, comma separated: photometry, frame_measurements,
#                         stack_comparison (default: all three)
#     --processing-dry-run        find the target's frames and check the files; start no job
#   Set CHROME_PATH to the Chrome, Chromium or Brave executable if it is not in a usual place.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
TPM_DIR="$ROOT_DIR/build/tpm"
PYTHON="$ROOT_DIR/.venv/bin/python"
REAL_BACKEND_PORT=5000

LAPS=3
IDLE_SECONDS=30
RUN_ELECTRON=0
#: What the display flow runs in: electron (the real app, the default), brave or chrome (--browser).
FLOW_TARGET=electron
IDLE_MINUTES=3
UPDATE_BASELINE=0
KEEP=0
VISIBLE=0
#: --visible and --headless as typed (Electron runs on the real display unless --headless).
VISIBLE_FLAG=0
HEADLESS=0
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
#: The library mode: simulation (default), synthetic or real-snapshot. It is the "mode" of the setup key.
LIBRARY=""
MODE=""
#: The observatory profile: simulated (local simulators only) or offline (no telescope connection at all). Empty
#: until the library mode has chosen its default.
OBSERVATORY_PROFILE=""
SIM_EXPOSURE_SECONDS=60
REPLAY_NIGHT=2026-10-03
REPLAY_SPEED=1
REPLAY_WINDOW_SECONDS=300
REPLAY_PRELOAD_FRAMES=16
#: 1 seals the run in its own network namespace with only loopback (see README.md).
SEAL=1
#: The processing scenario (empty for the display flow): the target to stack and analyze.
PROCESSING_TARGET=""
PROCESSING_CAMERA="ZWO ASI 533MM Pro"
PROCESSING_STEPS="photometry,frame_measurements,stack_comparison"
PROCESSING_DRY_RUN=0
ORIGINAL_ARGUMENTS=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --laps) LAPS="$2"; shift 2 ;;
    --idle-seconds) IDLE_SECONDS="$2"; shift 2 ;;
    --electron) RUN_ELECTRON=1; shift ;;
    --browser) FLOW_TARGET="$2"; shift 2 ;;
    --idle-minutes) IDLE_MINUTES="$2"; shift 2 ;;
    --update-baseline) UPDATE_BASELINE=1; shift ;;
    --keep) KEEP=1; shift ;;
    --visible) VISIBLE_FLAG=1; shift ;;
    --headless) HEADLESS=1; shift ;;
    --ozone) OZONE="$2"; shift 2 ;;
    --gpu) GPU="$2"; shift 2 ;;
    --require-power-profile) REQUIRED_POWER_PROFILE="$2"; shift 2 ;;
    --pan-seconds) PAN_SECONDS="$2"; shift 2 ;;
    --sky-catalog-copy) SKY_CATALOG_COPY=1; shift ;;
    --scale) SCALE="$2"; shift 2 ;;
    --library) LIBRARY="$2"; shift 2 ;;
    --observatory) OBSERVATORY_PROFILE="$2"; shift 2 ;;
    --sim-exposure-seconds) SIM_EXPOSURE_SECONDS="$2"; shift 2 ;;
    --replay-night) REPLAY_NIGHT="$2"; shift 2 ;;
    --replay-speed) REPLAY_SPEED="$2"; shift 2 ;;
    --replay-window-seconds) REPLAY_WINDOW_SECONDS="$2"; shift 2 ;;
    --replay-preload-frames) REPLAY_PRELOAD_FRAMES="$2"; shift 2 ;;
    --no-seal) SEAL=0; shift ;;
    --processing) PROCESSING_TARGET="$2"; shift 2 ;;
    --processing-camera) PROCESSING_CAMERA="$2"; shift 2 ;;
    --processing-steps) PROCESSING_STEPS="$2"; shift 2 ;;
    --processing-dry-run) PROCESSING_DRY_RUN=1; shift ;;
    --cold) COLD=1; shift ;;
    --after-reboot) AFTER_REBOOT=1; shift ;;
    --preflight) PREFLIGHT_ONLY=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --real-snapshot) REAL_SNAPSHOT=1; shift ;;
    -h|--help) sed -n 3,86p "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# The library mode. The simulation mode (a read-only copy of your real library and the simulated observatory) is
# the default. A processing run and --real-snapshot bring their own library, so they never default to it.
case "$LIBRARY" in ""|simulation|synthetic) ;; *) echo "ERROR: --library must be simulation or synthetic." >&2; exit 2 ;; esac
if [ "$REAL_SNAPSHOT" = "1" ]; then
  [ -z "$LIBRARY" ] || { echo "ERROR: --real-snapshot and --library exclude each other." >&2; exit 2; }
  LIBRARY=real-snapshot
elif [ -n "$PROCESSING_TARGET" ]; then
  [ "$LIBRARY" != "simulation" ] || { echo "ERROR: --processing works on the synthetic library or with --real-snapshot, not with --library simulation." >&2; exit 2; }
  LIBRARY=synthetic
elif [ -z "$LIBRARY" ]; then
  LIBRARY=simulation
fi
MODE="$LIBRARY"
# A simulation-mode run uses the app's simulated observatory, so its header should show the SIMULATOR chip
# (flow.mjs checks). Off for now: the harness builds the simulator world itself, and the backend has no way yet to
# learn that it runs one, so it reports the real telescope and shows no chip. Turn this on with the simulator
# layer's "launch identity" (documentation/plans/SIMULATOR_FROM_REAL_DATA_PLAN.md).
EXPECT_SIMULATOR_CHIP=0
if [ -z "$OBSERVATORY_PROFILE" ]; then
  if [ "$LIBRARY" = "simulation" ]; then OBSERVATORY_PROFILE=simulated; else OBSERVATORY_PROFILE=offline; fi
fi
if [ "$LIBRARY" = "simulation" ]; then
  [ "$OBSERVATORY_PROFILE" = "simulated" ] || { echo "ERROR: --observatory $OBSERVATORY_PROFILE needs --library synthetic (the simulation library runs the simulated observatory)." >&2; exit 2; }
  [ "$SCALE" = "1" ] || { echo "ERROR: --scale needs --library synthetic." >&2; exit 2; }
  [ "$SKY_CATALOG_COPY" = "0" ] || { echo "ERROR: --sky-catalog-copy needs --library synthetic (the simulation library holds your catalogs already)." >&2; exit 2; }
fi
case "$FLOW_TARGET" in electron|brave|chrome) ;; *) echo "ERROR: --browser must be brave or chrome." >&2; exit 2 ;; esac
#: 1 when a second lap runs in Electron after a browser flow.
EXTRA_ELECTRON_LAP=0
[ "$FLOW_TARGET" != "electron" ] && [ "$RUN_ELECTRON" = "1" ] && EXTRA_ELECTRON_LAP=1
#: 1 when any part of the run uses the Electron app.
USES_ELECTRON=0
[ "$FLOW_TARGET" = "electron" ] || [ "$EXTRA_ELECTRON_LAP" = "1" ] && USES_ELECTRON=1
if { [ "$VISIBLE_FLAG" = "1" ] || [ "$HEADLESS" = "1" ]; } && [ "$USES_ELECTRON" != "1" ]; then echo "ERROR: --visible and --headless need Electron (the default target, or --browser with --electron)." >&2; exit 2; fi
if [ "$VISIBLE_FLAG" = "1" ] && [ "$HEADLESS" = "1" ]; then echo "ERROR: --visible and --headless exclude each other." >&2; exit 2; fi
# Electron draws on the real display (the real graphics card) unless --headless asks for xvfb.
[ "$USES_ELECTRON" = "1" ] && [ "$HEADLESS" != "1" ] && VISIBLE=1
# The setup key names the target; a headless (software graphics) run never shares a baseline with a real-display run.
KEY_TARGET="$FLOW_TARGET"
[ "$FLOW_TARGET" = "electron" ] && [ "$HEADLESS" = "1" ] && KEY_TARGET=electron-headless
case "$GPU" in auto|intel|nvidia) ;; *) echo "ERROR: --gpu must be auto, intel or nvidia." >&2; exit 2 ;; esac
if [ "$GPU" = "nvidia" ] && ! command -v nvidia-smi >/dev/null; then echo "ERROR: --gpu nvidia needs nvidia-smi." >&2; exit 1; fi
case "$OBSERVATORY_PROFILE" in offline|simulated|replay) ;; *) echo "ERROR: --observatory must be offline, simulated or replay." >&2; exit 2 ;; esac
#: 1 when the telescope is a set of local stand-ins (the simulated and the replay profile).
LIVE=0
[ "$OBSERVATORY_PROFILE" = "offline" ] || LIVE=1
if [ "$LIVE" = "1" ]; then
  if [ "$REAL_SNAPSHOT" = "1" ]; then echo "ERROR: --real-snapshot is offline only; for your library with the simulated observatory use the default --library simulation." >&2; exit 2; fi
  if [ "$SEAL" != "1" ]; then echo "ERROR: --no-seal is allowed only with the offline profile." >&2; exit 2; fi
  for tool in indiserver indi_simulator_telescope indi_simulator_ccd indi_simulator_focus indi_simulator_wheel indi_simulator_guide indi_setprop indi_getprop rsync ss setsid; do
    command -v "$tool" >/dev/null || { echo "ERROR: --observatory $OBSERVATORY_PROFILE needs $tool." >&2; exit 1; }
  done
fi
if [ "$LIBRARY" = "simulation" ]; then
  for tool in fuse-overlayfs fusermount3 mountpoint bwrap; do
    command -v "$tool" >/dev/null || { echo "ERROR: --library simulation needs $tool." >&2; exit 1; }
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
if [ -z "$PROCESSING_TARGET" ] && [ "$PROCESSING_DRY_RUN" = "1" ]; then echo "ERROR: --processing-dry-run needs --processing." >&2; exit 2; fi
if [ -n "$PROCESSING_TARGET" ]; then
  # The processing scenario has no browser, no Electron and no stand-in observatory. It cannot be sealed: Siril runs
  # in a Flatpak sandbox, which needs its own user namespace, and the system refuses one inside bwrap's.
  [ "$RUN_ELECTRON" = "0" ] && [ "$FLOW_TARGET" = "electron" ] && [ "$LIVE" = "0" ] && [ "$COLD" = "0" ] && [ "$SCALE" = "1" ] || {
    echo "ERROR: --processing cannot be combined with --electron, --browser, --observatory simulated/replay, --cold or --scale." >&2; exit 2; }
  SEAL=0
  command -v flock >/dev/null || { echo "ERROR: --processing needs flock (the heavy-work slots)." >&2; exit 1; }
  # Siril runs in a Flatpak sandbox, which has its own /tmp and /var/tmp and shares the home folder. The throwaway
  # folder must be in the home folder, or Siril cannot open the pipes the app makes in it.
  export TMPDIR="${TMPDIR:-$HOME/.cache/astrometrics-tpm-runs}"
  mkdir -p "$TMPDIR"
  # Take one of the shared heavy-work slots, the way build/agent_tools/check.sh does, then run this script again in it.
  if [ -z "${TPM_SLOT_HELD:-}" ]; then
    # shellcheck source=../agent_tools/common.sh
    source "$ROOT_DIR/build/agent_tools/common.sh" "$(basename "$ROOT_DIR")"
    export TPM_SLOT_HELD=1
    heavy "$0" "${ORIGINAL_ARGUMENTS[@]}"
    exit $?
  fi
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
# The simulation library mounts an overlay in the throwaway folder, and the system lets fusermount3 mount only under
# the home folder, /tmp, /mnt, /media and /run/user, so that mode uses a folder in the home folder instead.
if [ "$COLD" = "1" ] && [ -z "${TMPDIR:-}" ] && [ "$("$PYTHON" "$TPM_DIR/cold_cache.py" filesystem /tmp)" = "tmpfs" ]; then
  if [ "$LIBRARY" = "simulation" ] && [ "$("$PYTHON" "$TPM_DIR/cold_cache.py" filesystem "$HOME")" != "tmpfs" ]; then
    export TMPDIR="$HOME/.cache/astrometrics-tpm-runs"
    mkdir -p "$TMPDIR"
    echo "== /tmp is memory-backed: the throwaway folder goes to $TMPDIR (a disk where the overlay can mount), so --cold can drop its files from the cache"
  elif [ "$LIBRARY" != "simulation" ] && [ -d /var/tmp ] && [ -w /var/tmp ] && [ "$("$PYTHON" "$TPM_DIR/cold_cache.py" filesystem /var/tmp)" != "tmpfs" ]; then
    export TMPDIR=/var/tmp
    echo "== /tmp is memory-backed: the throwaway folder goes to /var/tmp, so --cold can drop its files from the cache"
  else
    echo "WARNING: /tmp is memory-backed and /var/tmp is not usable; the throwaway files cannot be dropped from the cache." >&2
  fi
fi
BOOT_AGE_SECONDS="$("$PYTHON" "$TPM_DIR/cold_cache.py" boot-age)"

#: Prints a free loopback port.
free_port() {
  "$PYTHON" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

#: Fills SEAL_BINDS with the sealed run's /tmp. Xvfb (headless Electron) must write its lock file in /tmp, so when the
#: throwaway folder is elsewhere (--cold in the home folder or /var/tmp) the read-only /tmp is replaced by an empty
#: private one, with the host's display sockets and lock files showing through, so xvfb-run still avoids a host display.
seal_tmp_binds() {
  case "${TMPDIR:-/tmp}" in
    /tmp|/tmp/*) SEAL_BINDS+=(--bind "${TMPDIR:-/tmp}" "${TMPDIR:-/tmp}") ;;
    *)
      SEAL_BINDS+=(--tmpfs /tmp --bind "$TMPDIR" "$TMPDIR")
      local lock_file
      for lock_file in /tmp/.X*-lock; do [ -e "$lock_file" ] && SEAL_BINDS+=(--ro-bind "$lock_file" "$lock_file"); done
      ;;
  esac
}

# The simulation library is made BEFORE the seal. Its overlay of your real library is a FUSE mount, which only an
# ordinary process can make (the network seal is a user namespace, where the system refuses it). The sealed run
# then reaches the mount, because a bind of the folder that holds it carries the mount along (README.md). So in this
# mode the outer script owns the throwaway folder: it makes the copy, runs the sealed script as a child, checks that
# your real library did not change, and unmounts the overlay and deletes the folder at the end.
if [ "$LIBRARY" = "simulation" ] && [ -z "${TPM_SEALED:-}" ]; then
  command -v bwrap >/dev/null || { echo "ERROR: sealing the run needs bwrap (bubblewrap)." >&2; exit 1; }
  OUTER_TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
  OUTER_RESULTS_DIR="$TPM_DIR/results/$OUTER_TIMESTAMP"
  OUTER_SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/astrometrics-tpm-XXXXXX")"
  case "$OUTER_SANDBOX" in
    "${TMPDIR:-/tmp}"/astrometrics-tpm-*) ;;
    *) echo "ERROR: unexpected sandbox path $OUTER_SANDBOX" >&2; exit 1 ;;
  esac
  mkdir -p "$OUTER_RESULTS_DIR"
  OUTER_BWRAP_PID=""
  # Ends the sealed run, unmounts the overlay (the copy's own teardown) and only then deletes the folder.
  outer_cleanup() {
    local status=$?
    if [ -n "$OUTER_BWRAP_PID" ] && kill -0 "$OUTER_BWRAP_PID" 2>/dev/null; then
      kill "$OUTER_BWRAP_PID" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$OUTER_BWRAP_PID" 2>/dev/null || break; sleep 1; done
      kill -9 "$OUTER_BWRAP_PID" 2>/dev/null || true
    fi
    [ -f "$OUTER_SANDBOX/backend.log" ] && [ ! -f "$OUTER_RESULTS_DIR/backend.log" ] && cp "$OUTER_SANDBOX/backend.log" "$OUTER_RESULTS_DIR/backend.log" 2>/dev/null
    "$PYTHON" "$TPM_DIR/simulation_library.py" teardown "$OUTER_SANDBOX" || status=1
    if grep -q " $OUTER_SANDBOX/" /proc/mounts; then
      echo "ERROR: something is STILL MOUNTED under $OUTER_SANDBOX. Unmount it by hand (fusermount3 -u) before deleting anything." >&2
      echo "Throwaway folder NOT deleted: $OUTER_SANDBOX" >&2
      status=1
    elif [ "$KEEP" = "1" ]; then
      echo "Throwaway folder kept: $OUTER_SANDBOX"
    else
      rm -rf "${OUTER_SANDBOX:?}"
    fi
    exit "$status"
  }
  trap outer_cleanup EXIT
  trap 'exit 143' TERM INT HUP
  OUTER_INDI_PORT="$(free_port)"
  OUTER_PHD2_PORT="$(free_port)"
  echo "== Real library: remembering its files (size and change time) to prove afterwards that the run did not touch it"
  "$PYTHON" "$TPM_DIR/real_snapshot.py" fingerprint "$OUTER_SANDBOX" "$OUTER_SANDBOX/real-fingerprint-before.json"
  echo "== Simulation library: the app's own copy of your databases and a copy-on-write overlay of your library (real files are only read)"
  "$PYTHON" "$TPM_DIR/simulation_library.py" prepare "$OUTER_SANDBOX" --indi-port "$OUTER_INDI_PORT" --phd2-port "$OUTER_PHD2_PORT" || exit 1
  mountpoint -q "$OUTER_SANDBOX/library/frames" || echo "NOTICE: no overlay is mounted (the real library has no frames folder); the run has databases only."
  echo "== Sealing the run in its own loopback-only network (bwrap --unshare-net, read-only computer; the overlay is bound in with the folder that holds it)"
  SEAL_BINDS=()
  seal_tmp_binds
  SEAL_BINDS+=(--bind "$TPM_DIR/results" "$TPM_DIR/results")
  mkdir -p "$ROOT_DIR/node_modules/.vite-temp" "$ROOT_DIR/node_modules/.vite"
  SEAL_BINDS+=(--bind "$ROOT_DIR/node_modules/.vite-temp" "$ROOT_DIR/node_modules/.vite-temp"
    --bind "$ROOT_DIR/node_modules/.vite" "$ROOT_DIR/node_modules/.vite")
  [ -d /tmp/.X11-unix ] && SEAL_BINDS+=(--bind /tmp/.X11-unix /tmp/.X11-unix)
  [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "$XDG_RUNTIME_DIR" ] && SEAL_BINDS+=(--bind "$XDG_RUNTIME_DIR" "$XDG_RUNTIME_DIR")
  env TPM_SEALED=1 TPM_SANDBOX="$OUTER_SANDBOX" TPM_RESULTS_DIR="$OUTER_RESULTS_DIR" TPM_INDI_PORT="$OUTER_INDI_PORT" \
    TPM_PHD2_PORT="$OUTER_PHD2_PORT" bwrap --ro-bind / / --dev-bind /dev /dev --proc /proc "${SEAL_BINDS[@]}" \
    --unshare-net --unshare-pid --die-with-parent "$0" "${ORIGINAL_ARGUMENTS[@]}" &
  OUTER_BWRAP_PID=$!
  SEALED_STATUS=0
  wait "$OUTER_BWRAP_PID" || SEALED_STATUS=$?
  OUTER_BWRAP_PID=""
  REAL_CHECK_OK=1
  "$PYTHON" "$TPM_DIR/real_snapshot.py" fingerprint "$OUTER_SANDBOX" "$OUTER_SANDBOX/real-fingerprint-after.json" || REAL_CHECK_OK=0
  if [ "$REAL_CHECK_OK" = "1" ]; then
    "$PYTHON" "$TPM_DIR/real_snapshot.py" verify "$OUTER_SANDBOX/real-fingerprint-before.json" "$OUTER_SANDBOX/real-fingerprint-after.json" \
      "$OUTER_RESULTS_DIR/real-library-check.json" || REAL_CHECK_OK=0
  fi
  if [ "$REAL_CHECK_OK" = "0" ]; then
    echo "ERROR: THE RUN FAILED THE REAL-LIBRARY CHECK. Something in your real library changed (or could not be checked) while the run went on. The run itself could not write there (it is read only), so this may be your own app or an upload, but it is not proved. See $OUTER_RESULTS_DIR/real-library-check.json." >&2
    [ "$SEALED_STATUS" = "0" ] && SEALED_STATUS=1
  fi
  exit "$SEALED_STATUS"
fi

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
  SEAL_BINDS=()
  seal_tmp_binds
  SEAL_BINDS+=(--bind "$TPM_DIR/results" "$TPM_DIR/results")
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

if [ -n "${TPM_SANDBOX:-}" ]; then
  # The simulation library: the outer script made the throwaway folder and the copy, and deletes the folder.
  SANDBOX="$TPM_SANDBOX"
  RESULTS_DIR="$TPM_RESULTS_DIR"
  OUTER_OWNS_SANDBOX=1
else
  TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
  RESULTS_DIR="$TPM_DIR/results/$TIMESTAMP"
  SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/astrometrics-tpm-XXXXXX")"
  OUTER_OWNS_SANDBOX=0
fi
case "$SANDBOX" in
  "${TMPDIR:-/tmp}"/astrometrics-tpm-*) ;;
  *) echo "ERROR: unexpected sandbox path $SANDBOX" >&2; exit 1 ;;
esac
mkdir -p "$RESULTS_DIR"
# The overlay mountpoint is under the home folder (the AppArmor profile of fusermount3 lets a user mount only under
# the home folder, /mnt, /media, /tmp and /run/user, and the throwaway folder may be in /var/tmp).
MOUNT_PARENT="${HOME}/.cache/astrometrics-tpm-mounts"
OVERLAY_MOUNT_DIR="$MOUNT_PARENT/$(basename "$SANDBOX")"

BACKEND_PID=""
PREVIEW_PID=""
SAMPLER_PID=""
RESOURCE_SAMPLER_PID=""
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
  for pid in "$NVIDIA_SAMPLER_PID" "$RESOURCE_SAMPLER_PID" "$SAMPLER_PID" "$PREVIEW_PID" "$BACKEND_CHILD_PID" "$BACKEND_PID" "$OBSERVATORY_PID"; do
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
      rmdir "$OVERLAY_MNT" 2>/dev/null || true
    fi
  fi
  if [ "$OUTER_OWNS_SANDBOX" = "1" ]; then
    : # The outer script unmounts the overlay and deletes the folder (it never deletes through the mount).
  elif [ "$still_mounted" = "1" ]; then
    echo "Throwaway folder NOT deleted: $SANDBOX" >&2
  elif [ "$KEEP" = "1" ]; then
    echo "Throwaway folder kept: $SANDBOX"
  else
    rm -rf "${SANDBOX:?}"
  fi
  exit "$status"
}
trap cleanup EXIT

BACKEND_PORT="$(free_port)"
UI_PORT="$(free_port)"
INDI_PORT=""
PHD2_PORT=""
if [ "$LIVE" = "1" ]; then
  # The simulation library chose its ports before the seal (its settings file names them).
  INDI_PORT="${TPM_INDI_PORT:-$(free_port)}"
  PHD2_PORT="${TPM_PHD2_PORT:-$(free_port)}"
fi
# 4400 is a real PHD2's port and 7624 is INDI's usual port: no stand-in uses them either.
for port in "$BACKEND_PORT" "$UI_PORT" "$INDI_PORT" "$PHD2_PORT"; do
  case "$port" in
    "$REAL_BACKEND_PORT"|4400|7624) echo "ERROR: refusing to use port $port (a real service's port)." >&2; exit 1 ;;
  esac
done
STANDIN_HOME="$SANDBOX/standin/home"

# These steps read your real files (read only) and so run before the script points XDG folders at the throwaway folder.
if [ "$LIBRARY" = "simulation" ]; then
  # Made before the seal by simulation_library.py (the app's own library copy); nothing to copy here.
  CONFIG_PATH="$SANDBOX/astrometrics.config.toml"
  [ -f "$CONFIG_PATH" ] || { echo "ERROR: the simulation library's settings file is missing." >&2; exit 1; }
  mountpoint -q "$SANDBOX/library/frames" && echo "== Simulation library: the overlay of your library is mounted at $SANDBOX/library/frames (seen through the seal)"
elif [ "$REAL_SNAPSHOT" = "1" ]; then
  echo "== Real snapshot: copying the databases and catalogs (read only) into the throwaway folder"
  if [ -n "$PROCESSING_TARGET" ]; then
    # Siril cannot run inside the bwrap lock, so the database copies and the settings name the overlay's mountpoint
    # instead of the real library (see README.md "Processing runs").
    "$PYTHON" "$TPM_DIR/real_snapshot.py" prepare "$SANDBOX" --mountpoint "$OVERLAY_MOUNT_DIR"
  else
    "$PYTHON" "$TPM_DIR/real_snapshot.py" prepare "$SANDBOX"
  fi
  CONFIG_PATH="$SANDBOX/astrometrics.config.toml"
  [ -z "$PROCESSING_TARGET" ] || "$PYTHON" "$TPM_DIR/processing_settings.py" "$SANDBOX" --mode real-snapshot
else
  CONFIG_PATH="$("$PYTHON" "$TPM_DIR/write_sandbox_config.py" "$SANDBOX" --profile "$OBSERVATORY_PROFILE" \
    ${INDI_PORT:+--indi-port "$INDI_PORT"} ${PHD2_PORT:+--phd2-port "$PHD2_PORT"})"
  [ -z "$PROCESSING_TARGET" ] || "$PYTHON" "$TPM_DIR/processing_settings.py" "$SANDBOX" --mode synthetic
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

# The deep-star (Gaia) catalog: without it the Planetarium has almost no stars to draw, so the sky-map TPMs would measure an
# empty sky. Copy the real file (read only, SQLite backup) into the throwaway library. A processing run has no Planetarium.
if [ -z "$PROCESSING_TARGET" ] && [ "$LIBRARY" != "simulation" ]; then
  "$PYTHON" "$TPM_DIR/copy_deep_catalog.py" "$SANDBOX"
fi

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
if [ "$LIBRARY" = "simulation" ]; then
  # Nothing to seed: the library is the copy. The backend runs straight in the seal (the real library is read only
  # there, and the overlay takes every write).
  export PYTHONDONTWRITEBYTECODE=1
elif [ "$REAL_SNAPSHOT" = "1" ]; then
  REAL_LIBRARY="$("$PYTHON" "$TPM_DIR/real_snapshot.py" get "$SANDBOX" real_library)"
  REAL_STACKS="$("$PYTHON" "$TPM_DIR/real_snapshot.py" get "$SANDBOX" real_stacks)"
  case "$REAL_LIBRARY" in /*) ;; *) echo "ERROR: the real library path must be absolute." >&2; exit 1 ;; esac
  case "$REAL_STACKS" in "$REAL_LIBRARY"|"$REAL_LIBRARY"/*) ;; *) echo "ERROR: the stacks folder is outside the library; the overlay would miss it." >&2; exit 1 ;; esac
  [ -d "$REAL_LIBRARY" ] || { echo "ERROR: $REAL_LIBRARY is not a folder." >&2; exit 1; }
  # The mountpoint is under the home folder (see above). The upper and work folders have no such limit.
  mkdir -p "$MOUNT_PARENT" "$SANDBOX/overlay/upper" "$SANDBOX/overlay/work"
  mkdir -p "$OVERLAY_MOUNT_DIR"
  echo "== Mounting a copy-on-write overlay of $REAL_LIBRARY (reads: real files; writes: $SANDBOX/overlay/upper)"
  # The one overlay code, shared with the app's simulated observatory.
  "$PYTHON" -m wayfindinglib.simulation.library_copy mount --lower "$REAL_LIBRARY" --upper "$SANDBOX/overlay/upper" \
    --work "$SANDBOX/overlay/work" --mountpoint "$OVERLAY_MOUNT_DIR" || { echo "ERROR: the overlay did not mount." >&2; exit 1; }
  OVERLAY_MNT="$OVERLAY_MOUNT_DIR"
  grep -q " $OVERLAY_MNT " /proc/mounts || { echo "ERROR: the overlay did not mount." >&2; exit 1; }
  # The second lock: the backend runs in a mount namespace where the whole computer is read only, only the
  # throwaway folder is writable, and the overlay is bound over the real library path (the databases hold
  # absolute paths, so the backend then reads and writes the overlay and never the real library).
  if [ -n "$PROCESSING_TARGET" ]; then
    # No bwrap lock here: Siril runs in a Flatpak sandbox, which needs a user namespace of its own, and the system
    # refuses one inside bwrap's (Landlock is no way out either: it forbids the mounts Flatpak makes). The protection
    # is instead: every path in the database copies and the settings names the overlay's mountpoint, a guard refuses
    # the run if any stored path still points outside the throwaway folder and the overlay, and the real library's
    # files are compared before and after the run.
    "$PYTHON" "$TPM_DIR/real_snapshot.py" guard "$SANDBOX" || { echo "ERROR: the database copies still hold paths outside the throwaway folder and the overlay; refusing." >&2; exit 1; }
    "$PYTHON" "$TPM_DIR/real_snapshot.py" fingerprint "$SANDBOX" "$SANDBOX/real-fingerprint-before.json"
  else
    bwrap --ro-bind / / --dev-bind /dev /dev --proc /proc --tmpfs /tmp --bind "$SANDBOX" "$SANDBOX" \
      --bind "$OVERLAY_MNT" "$REAL_LIBRARY" true || { echo "ERROR: bwrap does not work here; refusing the real snapshot." >&2; exit 1; }
    BACKEND_COMMAND=(bwrap --ro-bind / / --dev-bind /dev /dev --proc /proc --tmpfs /tmp --bind "$SANDBOX" "$SANDBOX"
      --bind "$OVERLAY_MNT" "$REAL_LIBRARY" --die-with-parent "${BACKEND_COMMAND[@]}")
  fi
  export PYTHONDONTWRITEBYTECODE=1
else
  echo "== Seeding the throwaway library"
  TPM_SCALE="$SCALE" TPM_STACKABLE_TARGET="$PROCESSING_TARGET" "$PYTHON" "$TPM_DIR/seed_library.py" "$SANDBOX"
  if [ "$OBSERVATORY_PROFILE" = "replay" ]; then
    # The night's targets, with coordinates, as the user would have them before the night.
    "$PYTHON" "$TPM_DIR/replay_preload.py" targets "$SANDBOX" || exit 1
  fi
fi

if [ -n "$PROCESSING_TARGET" ]; then
  export PYTHONDONTWRITEBYTECODE=1
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
  OBSERVATORY_START=("$PYTHON" -m wayfindinglib.simulation.observatory start)
  # In a world that already holds the library copy, only the stand-ins are started (the app's start would prepare the
  # world again and unmount the overlay).
  [ "$LIBRARY" = "simulation" ] && OBSERVATORY_START=("$PYTHON" "$TPM_DIR/simulation_library.py" start)
  "${OBSERVATORY_START[@]}" "${OBSERVATORY_ARGUMENTS[@]}" > "$SANDBOX/observatory.log" 2>&1 &
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

if [ -z "$PROCESSING_TARGET" ]; then
  echo "== Building the production UI"
  npx vite build --outDir "$SANDBOX/dist" --emptyOutDir > "$SANDBOX/ui_build.log" 2>&1 || { tail -20 "$SANDBOX/ui_build.log" >&2; exit 1; }
fi
# The real Electron app loads the built UI itself (from app://), so the preview server is only for a browser.
if [ -z "$PROCESSING_TARGET" ] && [ "$FLOW_TARGET" != "electron" ] || [ "$EXTRA_ELECTRON_LAP" = "1" ]; then
  echo "== Serving the UI on port $UI_PORT"
  TPM_UI_DIST="$SANDBOX/dist" TPM_UI_PORT="$UI_PORT" TPM_BACKEND_ORIGIN="http://127.0.0.1:$BACKEND_PORT" \
    node "$ROOT_DIR/node_modules/vite/bin/vite.js" preview --config "$TPM_DIR/vite.tpm.config.mjs" > "$SANDBOX/preview.log" 2>&1 &
  PREVIEW_PID=$!
  for _ in $(seq 1 30); do
    curl -fs -m 2 "http://127.0.0.1:$UI_PORT/api/ready" 2>/dev/null | grep -q '"ready":true' && break
    sleep 1
  done
fi

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
  [ "$LIBRARY" = "simulation" ] && COLD_COMMAND+=(--real-stacks "$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["real_stacks"])' "$SANDBOX/library_copy.json")")
  [ -n "${CHROME_PATH:-}" ] && [ -e "$CHROME_PATH" ] && COLD_COMMAND+=(--browser "$CHROME_PATH")
  [ "$AFTER_REBOOT" = "1" ] && COLD_COMMAND+=(--after-reboot)
  "${COLD_COMMAND[@]}"
  COLD_ARGUMENT=(--cold --cold-report "$RESULTS_DIR/cold-cache.json")
fi
[ "$AFTER_REBOOT" = "1" ] && COLD_ARGUMENT+=(--after-reboot)

echo "== Recording the setup (CPU, power, displays, graphics cards)"
if [ -n "$PROCESSING_TARGET" ]; then
  echo "   (and the versions of Siril, GraXpert and Cosmic Clarity)"
  "$PYTHON" "$TPM_DIR/setup_fingerprint.py" collect --out "$RESULTS_DIR/setup-fingerprint.json" --config "$CONFIG_PATH"
else
  LIBRARY_RECORD_ARGUMENT=()
  [ "$LIBRARY" = "simulation" ] && LIBRARY_RECORD_ARGUMENT=(--library-record "$SANDBOX/library_record.json")
  "$PYTHON" "$TPM_DIR/setup_fingerprint.py" collect --out "$RESULTS_DIR/setup-fingerprint.json" \
    --deep-catalog "$SANDBOX/deep_catalog.json" "${LIBRARY_RECORD_ARGUMENT[@]}"
fi
if command -v nvidia-smi >/dev/null; then
  "$PYTHON" "$TPM_DIR/setup_fingerprint.py" nvidia-sample --out "$RESULTS_DIR/nvidia-samples.jsonl" &
  NVIDIA_SAMPLER_PID=$!
fi

echo "== Sampling the backend process and the programs under it"
"$PYTHON" "$TPM_DIR/rpc_timings.py" sample --pid "$SAMPLE_PID" --port "$BACKEND_PORT" --out "$RESULTS_DIR/backend-samples.jsonl" &
SAMPLER_PID=$!
# CPU, memory and disk of the backend's whole process tree (and, when they start, Electron and the browser).
"$PYTHON" "$TPM_DIR/resource_usage.py" sample --backend-pid "$SAMPLE_PID" --backend-port "$BACKEND_PORT" \
  --watch-dir "$RESULTS_DIR" --out "$RESULTS_DIR/resource-samples.jsonl" &
RESOURCE_SAMPLER_PID=$!

ELECTRON_ARGUMENT=()
PROCESSING_STATUS=0
if [ -n "$PROCESSING_TARGET" ]; then
  PROCESSING_ARGUMENTS=(--backend-port "$BACKEND_PORT" --backend-pid "$SAMPLE_PID" --target "$PROCESSING_TARGET"
    --camera "$PROCESSING_CAMERA" --out "$RESULTS_DIR" --nvidia-samples "$RESULTS_DIR/nvidia-samples.jsonl"
    --analysis-steps "$PROCESSING_STEPS")
  [ "$PROCESSING_DRY_RUN" = "1" ] && PROCESSING_ARGUMENTS+=(--dry-run)
  echo "== Processing run: $PROCESSING_TARGET, camera $PROCESSING_CAMERA (mode: $MODE)"
  "$PYTHON" "$TPM_DIR/processing_run.py" "${PROCESSING_ARGUMENTS[@]}" || PROCESSING_STATUS=$?
else
  # The flow uses the chosen card in every target. (The browser flow used to stay on auto, which is software
  # graphics in headless Chrome, so planetarium_fps_panning came out near 32 fps while the Electron window ran at 60.)
  FLOW_GPU="$GPU"
  if [ "$OBSERVATORY_PROFILE" = "replay" ]; then
    # The timed run starts here: the replayed window begins 3 s from now, as the flow starts.
    "$PYTHON" -c 'import time; print(time.time() + 3.0)' > "$SANDBOX/replay/start"
  fi
  if [ "$FLOW_TARGET" = "electron" ]; then
    # The default: the real Electron app, started with a loopback DevTools port; flow.mjs attaches to it.
    if [ "$VISIBLE" = "1" ]; then
      echo "NOTICE: an Electron window will open on your real display for this run (real GPU, without taking focus). Leave it alone until it closes."
    fi
    echo "== Running the flow in the Electron app ($LAPS laps, gpu: $FLOW_GPU, $IDLE_MINUTES idle minutes first)"
    node "$TPM_DIR/electron_run.mjs" --sandbox "$SANDBOX" --ui-url "http://127.0.0.1:$UI_PORT" \
      --backend-url "http://127.0.0.1:$BACKEND_PORT" --out "$RESULTS_DIR" --flow-out "$RESULTS_DIR" \
      --idle-minutes "$IDLE_MINUTES" --laps "$LAPS" --idle-seconds "$IDLE_SECONDS" \
      --pan-seconds "$PAN_SECONDS" --ui-dist "$SANDBOX/dist" --visible "$VISIBLE" --ozone "$OZONE" --gpu "$GPU" \
      --expect-simulator-chip "$EXPECT_SIMULATOR_CHIP"
    ELECTRON_ARGUMENT=(--electron "$RESULTS_DIR/electron-metrics.jsonl")
  else
    echo "== Running the flow in $FLOW_TARGET ($LAPS laps, gpu: $FLOW_GPU)"
    node "$TPM_DIR/flow.mjs" --ui-url "http://127.0.0.1:$UI_PORT" --backend-url "http://127.0.0.1:$BACKEND_PORT" \
      --out "$RESULTS_DIR" --laps "$LAPS" --idle-seconds "$IDLE_SECONDS" --pan-seconds "$PAN_SECONDS" --gpu "$FLOW_GPU" \
      --browser "$FLOW_TARGET" --expect-simulator-chip "$EXPECT_SIMULATOR_CHIP"

    if [ "$EXTRA_ELECTRON_LAP" = "1" ]; then
      if [ "$VISIBLE" = "1" ]; then
        echo "NOTICE: an Electron window will open on your real display for this run (real GPU, without taking focus). Leave it alone until it closes."
      fi
      echo "== Running the lap in Electron ($IDLE_MINUTES idle minutes first)"
      node "$TPM_DIR/electron_run.mjs" --sandbox "$SANDBOX" --ui-url "http://127.0.0.1:$UI_PORT" \
        --backend-url "http://127.0.0.1:$BACKEND_PORT" --out "$RESULTS_DIR" --idle-minutes "$IDLE_MINUTES" --laps "$LAPS" \
        --pan-seconds "$PAN_SECONDS" --ui-dist "$SANDBOX/dist" --visible "$VISIBLE" --ozone "$OZONE" --gpu "$GPU" \
        --expect-simulator-chip "$EXPECT_SIMULATOR_CHIP"
      ELECTRON_ARGUMENT=(--electron "$RESULTS_DIR/electron-metrics.jsonl")
    fi
  fi
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
kill "$RESOURCE_SAMPLER_PID" 2>/dev/null || true
wait "$RESOURCE_SAMPLER_PID" 2>/dev/null || true
RESOURCE_SAMPLER_PID=""
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
if [ "$LIBRARY" = "simulation" ]; then
  "$PYTHON" "$TPM_DIR/real_snapshot.py" overlay-report "$SANDBOX/overlay/upper" "$RESULTS_DIR/overlay-writes.json"
  EXTRA_COMPILE_ARGUMENTS+=(--overlay-writes "$RESULTS_DIR/overlay-writes.json")
  cp "$SANDBOX/library_copy.json" "$SANDBOX/library_record.json" "$RESULTS_DIR/" 2>/dev/null || true
elif [ "$REAL_SNAPSHOT" = "1" ]; then
  FRAMES_ROOT="$OVERLAY_MNT"
  "$PYTHON" "$TPM_DIR/real_snapshot.py" overlay-report "$SANDBOX/overlay/upper" "$RESULTS_DIR/overlay-writes.json"
  EXTRA_COMPILE_ARGUMENTS+=(--overlay-writes "$RESULTS_DIR/overlay-writes.json")
  if [ -n "$PROCESSING_TARGET" ]; then
    # Prove the real library was not touched: compare its files (size and change time) with the start of the run.
    "$PYTHON" "$TPM_DIR/real_snapshot.py" fingerprint "$SANDBOX" "$SANDBOX/real-fingerprint-after.json"
    "$PYTHON" "$TPM_DIR/real_snapshot.py" verify "$SANDBOX/real-fingerprint-before.json" "$SANDBOX/real-fingerprint-after.json" \
      "$RESULTS_DIR/real-library-check.json" || { echo "ERROR: the real library changed during the run. See $RESULTS_DIR/real-library-check.json." >&2; PROCESSING_STATUS=1; }
  fi
fi
if [ "$PROCESSING_DRY_RUN" = "1" ]; then
  echo "Dry run finished (no job was started; no TPMs compiled). Results folder: $RESULTS_DIR"
  exit "$PROCESSING_STATUS"
fi
"$PYTHON" "$TPM_DIR/data_size.py" --library-dir "$SANDBOX/library" --wayfinding-db "$SANDBOX/wayfinding/wayfinding.db" \
  --frames-root "$FRAMES_ROOT" --catalog-db "$SANDBOX/xdg_cache/astrometrics/catalogs/catalog_cache.db" --out "$RESULTS_DIR/data-size.json"
RPC_FLOW_ARGUMENT=(--flow "$RESULTS_DIR/flow-metrics.json")
[ -z "$PROCESSING_TARGET" ] || RPC_FLOW_ARGUMENT=()
"$PYTHON" "$TPM_DIR/rpc_timings.py" summarize "${RPC_FLOW_ARGUMENT[@]}" \
  --samples "$RESULTS_DIR/backend-samples.jsonl" --log "$RESULTS_DIR/backend.log" --out "$RESULTS_DIR/backend-metrics.json"

[ -n "$PROCESSING_TARGET" ] || EXTRA_COMPILE_ARGUMENTS+=(--flow-target "$KEY_TARGET")
[ -z "$PROCESSING_TARGET" ] || EXTRA_COMPILE_ARGUMENTS+=(--scenario processing --processing-metrics "$RESULTS_DIR/processing-metrics.json")
COMPILE_ARGUMENTS=("$RESULTS_DIR" "${ELECTRON_ARGUMENT[@]}" --mode "$MODE" --observatory-profile "$OBSERVATORY_PROFILE"
  --data-size "$RESULTS_DIR/data-size.json" --setup "$RESULTS_DIR/setup-fingerprint.json" --gpu-requested "$GPU" \
  --boot-age "$BOOT_AGE_SECONDS" "${COLD_ARGUMENT[@]}" "${OBSERVATORY_ARGUMENT[@]}" "${EXTRA_COMPILE_ARGUMENTS[@]}")
[ "$UPDATE_BASELINE" = "1" ] && COMPILE_ARGUMENTS+=(--update-baseline)
echo "== TPMs (results in $RESULTS_DIR)"
set +e
"$PYTHON" "$TPM_DIR/compile_tpm.py" "${COMPILE_ARGUMENTS[@]}"
COMPILE_STATUS=$?
set -e
[ -n "$PROCESSING_TARGET" ] || echo "Flow report: $RESULTS_DIR/flow-report.html"
[ "$COMPILE_STATUS" != "0" ] || COMPILE_STATUS="$PROCESSING_STATUS"
exit "$COMPILE_STATUS"
