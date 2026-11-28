# Linux scripts

Helper scripts for building, running and checking Astrometrics on Linux.

## run_backend.sh

Starts, stops and checks the Python backend. Run it with `help` to see every command.

### The launcher

`run_backend.sh` always starts `python -m simulator.launch`, in `start`, `launch`, `restart` and `foreground`. The launcher reads the Observatory source setting, builds the simulated world when you chose Simulated, runs the backend as its child and removes the world when the backend ends (see `simulator/README.md`). The backend has no world code of its own. Started any other way (`python -m backend.main_backend`, or by Electron without `SKIP_BACKEND`, as in the packaged app), it runs on the real telescope and refuses to change its source.

The pid file `.run_pids/backend.pid` holds the launcher's pid, so `run_astrometrics.sh` finds a dead launcher the same way it finds a dead backend. `stop` sends SIGTERM to the launcher, which stops the backend, the stand-ins and the library copy before it exits. The script waits up to 60 seconds for that and up to 3 minutes for the first answer after a start, because the launcher builds the world first. The file `.run_pids/backend.launcher` records that the pid file holds a launcher. Switching Real and Simulated in the app makes the backend exit with code 75, and the launcher starts the next session.

### dump

```bash
build/linux/run_backend.sh dump
```

Use this when the backend stops answering. The backend writes the Python stack of every thread to its log file, and the script prints those new lines. The stacks show what each thread is waiting on. It needs no `sudo`.

If the backend is not running, the script says so and exits with an error.

### Memory limit

`start`, `launch` and `restart` run the backend inside a systemd user scope with a memory limit. Siril and other child processes start in the same scope, so the limit covers them. If the backend grows past the hard limit, the kernel's out-of-memory killer stops a process in that scope instead of letting the whole desktop run out of memory. This happened on 2026-09-25, when the backend grew to 15 to 22 GB and the desktop session was killed.

| Limit | Default | What happens above it |
| --- | --- | --- |
| `MemoryHigh` | 80% of RAM | The kernel slows the backend down and reclaims memory first. Nothing is killed. |
| `MemoryMax` | 90% of RAM | The kernel kills a process in the scope. |

The largest real use on record is 26.7 GB plus 2.1 GB of swap, seen in the restack of 2026-10-11 on this 32 GB machine. That is 82% of the RAM. The limit sits above that peak and leaves about 3 GB for the desktop. The 15 to 22 GB growth was a bug, since fixed, and is not a size to plan for.

To change a limit, set an environment variable before you start the backend. Values are systemd sizes such as `24G`. The value `off` turns the limit off.

```bash
ASTROMETRICS_BACKEND_MEMORY_MAX=24G build/linux/run_backend.sh restart
ASTROMETRICS_BACKEND_MEMORY_MAX=off build/linux/run_backend.sh restart
```

Without a systemd user session, the backend starts with no limit and the script prints one line saying so. `foreground` has no limit.

To see whether the kernel killed the backend, run `build/linux/run_backend.sh status`. After a kill it prints "The kernel's out-of-memory killer stopped a process of the backend" with the scope name. The details are in the user journal: `journalctl --user -u <scope name>`. The scope name is kept in `.run_pids/backend.scope`. A kill of a child such as Siril, not the backend itself, shows up as a failed stack and the same journal line.

The helper functions are in `backend_memory_limit.sh`.

### One group for the whole app

`run_astrometrics.sh` starts the backend, Vite and Electron in separate systemd scopes, and all three scopes sit in one group, `astrometrics.slice`. They are still separate processes, so you can restart one without the others. The group makes them easy to see and to clean up:

```bash
systemd-cgls --user-unit astrometrics.slice
build/linux/run_astrometrics.sh status
```

`status` lists everything in the group. `stop` and `restart` stop each piece in turn and then stop anything still left in the group, so no helper process outlives `stop`. Without a systemd user session the pieces start as before and the group does not exist.

The group holds only what these scripts start. The MCP servers belong to the Claude Code session that started them, and each one exits when that session closes.

## run_astrometrics.sh: start order

`start` launches the backend in the background without waiting for it, starts Vite, and starts Electron as soon as Vite answers. The splash screen therefore appears within about a second, while the backend is still starting. The splash polls the backend's `/api/ready` route and stays up until the backend has finished its warm-up. A refused connection during that time is normal and is not logged as an error. If the backend dies while starting (its process id in `.run_pids/backend.pid` no longer exists), or nothing answers for two minutes, Electron shows a "backend not running" dialog with Retry and Quit. Electron is started from `node_modules/.bin/electron` directly, because `npx` adds a quarter of a second.

When the saved observatory source is a simulator, the backend first copies the databases (about 1.5 GB). This copy, not Python imports, was most of the backend's start-up time (about 12 of 13 seconds). It now runs the four copies side by side. Importing the backend alone takes about 2 seconds.

## run_astrometrics.sh: graphics card

On a laptop with an Intel and an NVIDIA card, `start` picks the card for the Electron window before Electron starts, because Chromium cannot switch cards while it runs. The choice comes from the environment variable `ASTROMETRICS_GPU`, then the `gpu` key in `[Frontend]` of `astrometrics.config.toml` (read only), then `auto`. The values are `auto`, `intel` and `nvidia`. `auto` means NVIDIA on AC power with the NVIDIA render offload driver present, and Intel on battery. Plugging in or unplugging while the app runs does not switch cards until the next start.

The launcher calls `node electron/gpu_choice.js shell-plan`, which prints the environment and switches to use (`node electron/gpu_choice.js decide` prints the decision as JSON). The same file serves the TPM harness (`build/tpm/gpu_choice.mjs` re-exports it). NVIDIA uses X11 through XWayland, ANGLE with desktop OpenGL (`--use-angle=gl`; the EGL route quietly lands on Intel in Electron) and the PRIME offload variables. Intel keeps the app's normal start. At start-up `electron/gpu_startup_check.js` logs the renderer Electron really got, warns when it is not the requested card, and restarts the app once on Intel if NVIDIA failed.

## Tray menu

`electron/tray_menu.js` gives the tray icon a native menu (status lines, workspaces, Park, Open App, Quit). The desktop draws it under the icon, which GNOME on Wayland needs because an app cannot place its own window there. Status comes from the `telescope:status` RPC every five seconds.

## Presence (is the user looking at the app)

`electron/presence.js` decides whether the user is `present` or `away`. It is used only to decide desktop notifications. Polling, charts and background work do not change when the user is away.

The user is present when the main window has focus and is not minimized, the screen is not locked, the computer is not asleep, and the keyboard or mouse was touched in the last 120 seconds (`IDLE_THRESHOLD_SECONDS`). The idle time is checked every 10 seconds. Window focus is the main signal, because a covered window can still report that it is visible. On Wayland and XWayland only the app's own window focus is known, which is enough.

The page reads the answer through `window.astrometrics.app.getPresence()` and `onPresenceChange()`. A notification marked `notify: true` goes to the desktop only when the user is away. It stays until dismissed, and the main process closes it when the user returns. In a plain browser the page falls back to focus and visibility. See `ui/common/utils/README.md`.

## setup_siril_gpu.sh: Siril's tools on the graphics card

`setup_siril_gpu.sh` makes the Flatpak Siril's GPU tools use an NVIDIA graphics card (GPU): GraXpert, StarNet2, and the Python scripts Siril runs, such as GraXpert-AI and Cosmic Clarity. Each tool needs several pieces in place. The script puts each piece in place from pinned versions, so the setup is the same on every machine. It changes only files in your home folder, so run it as your own user, not with `sudo`.

```bash
build/linux/setup_siril_gpu.sh --check     # report each piece, change nothing
build/linux/setup_siril_gpu.sh --dry-run   # report, then print what a run would do
build/linux/setup_siril_gpu.sh             # set up what is missing
```

The script handles seven parts, in this order. `--parts` picks some of them, for example `--parts=wrappers,config`.

1. `overrides`: the Flatpak permission overrides for `org.siril.Siril`. The script shares `~/cuda_libs` read-only, hides `~/.astropy`, allows all devices, and sets `LD_LIBRARY_PATH`, `TMPDIR` and `TF_FORCE_GPU_ALLOW_GROWTH`. It adds only the settings that are missing, with one `flatpak override --user` command. A setting that is already there with another value is reported as a conflict and left alone. The script never removes a setting. It also notes settings that have no effect, such as folders under `/usr`, which Flatpak never shares.
2. `cuda-libs`: `~/cuda_libs`, the CUDA 12 runtime, cuBLAS, cuFFT, cuRAND, NVRTC and cuDNN 9. CUDA is NVIDIA's toolkit for running programs on the graphics card. Siril's Python and StarNet2 use these files.
3. `starnet-ort`: `~/.local/share/StarNetGPU/ort-gpu-1.26`, the GPU build of ONNX Runtime 1.26 for StarNet2. ONNX Runtime is the library that runs the AI models.
4. `graxpert-cudnn`: `~/.local/share/GraXpert/cudnn13`, cuDNN built for CUDA 13, for GraXpert 4. The script also checks for the system CUDA 13 runtime and cuBLAS in `/usr/local/cuda`. They come from NVIDIA's package repository and need `sudo`, so it only reports them.
5. `siril-python`: the GPU build of ONNX Runtime and the packages it needs, at exact versions, in Siril's own Python environment. The script runs that Python inside the sandbox with `flatpak run --command=...`. It also reports a CPU-only `onnxruntime` package, which hides the GPU build.
6. `wrappers`: `graxpert-gpu.sh` and `starnet-gpu.sh`, made from the templates in `siril_gpu/templates/`. Inside the sandbox each wrapper starts its program on the host with `flatpak-spawn --host`. `--graxpert` and `--starnet` name the programs when they are not in `~/Applications`.
7. `config`: the wrapper paths. The script writes `graxpert_executable` in `astrometrics.config.toml`, and `starnet_exe` and `graxpert_path` in Siril's own settings file, only where they are empty. It skips Siril's settings while Siril is open, because Siril rewrites them when it closes. It keeps the old file as `.bak-<time>`.

**Pins and checks.** `siril_gpu/siril_gpu_pins.py` lists every pinned piece. Each library comes from one pinned Python wheel (a package file) on PyPI. `pip download` fetches the wheel into a work folder in Siril's cache on disk, not into the RAM-backed `/tmp`. The script checks each file it takes out of the wheel against the SHA-256 checksum and size in the wheel's own `RECORD` file. It refuses a file that does not match, and the file it would have replaced stays in place. To move to a newer version, change the wheel's version and every checksum and size together.

**Hand-built copies.** Some working copies of libraries were copied by hand from other sources: cuBLAS from the Ollama CUDA 12 bundle, cuRAND from the CUDA 13 toolkit, and the ONNX Runtime CUDA library from a wheel for another Python version. The pins list their checksums. `--check` reports them as `accepted`, and a run leaves them in place. `--force` replaces them, and any other file that differs from its pin, and keeps the old file as `.bak-<time>`.

**No GPU, no Flatpak Siril.** Without an NVIDIA card with a working driver (`nvidia-smi -L`), or without the Flatpak Siril, the script says so and exits without error. Siril, GraXpert and StarNet2 still work on the processor. Siril must have been opened once, and must have run one script, before Siril's Python environment and settings file exist. Until then the script skips those parts and says why.

**Exit status.** The script exits with 0 when every part is ready, set up or skipped. It exits with 1 when a part failed during a run, such as a refused checksum, after it has tried the other parts.

Windows needs none of this. There is no Flatpak sandbox on Windows, so Siril's normal installer needs no overrides or wrappers, and GraXpert uses the graphics card through DirectML.

The tests are in `siril_gpu/test/`. They run the program in a temporary home folder with stand-in `flatpak` and `nvidia-smi` commands. Run them with `.venv/bin/pytest build/linux/siril_gpu/test -q`. For exact behavior, read the code.
