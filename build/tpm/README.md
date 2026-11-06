# Technical performance measures (TPMs)

This folder measures how fast the Astrometrics app is and how many computer resources it uses. One run gives a list of numbers, the technical performance measures (TPMs). A run compares each number with a saved baseline, so you can see when a change makes the app slower or heavier.

Status of the limits: Design estimate. The baseline comes from one run on one workstation, with headless Chrome and software graphics. The limits are starting points and have no validation-register entry. A baseline is only valid for the setup it was taken on (see "Setup and comparability").

## Safety rule

A run never touches your real app. It builds a throwaway world in a temporary folder:

- its own image library, wayfinding library, settings file, databases and cache folders, all inside that folder;
- its own backend on a random port, with the INDI simulator (a fake telescope and camera) and no real hardware (in the `simulated` and `replay` profiles, a local INDI server with simulators, see "The simulated observatory" and "The replay observatory");
- its own copy of the UI, served on another random port;
- its own loopback-only network (see "The network seal"): nothing in the run can reach the network, and nothing outside can reach the run.

The scripts stop before starting if the backend port is 5000 (the real backend's port), if a simulator port is 4400, 5000 or 7624 (the ports of a real PHD2, the real backend and the usual INDI server), if the backend is not on this computer, or if any library path points outside the throwaway folder or into `~/Astrometrics` or `~/.local/state/astrometrics`. The flow clicks only controls that select or show data. It fails the run if the page sends an RPC call (a request to the backend) that looks like a hardware command. The folder is deleted at the end.

`ui/globalSetup.ts` starts its test backend the same way. `write_sandbox_config.py` repeats its settings file, with a few made-up equipment entries added.

## Two modes

| Mode | Data | Compared with the baseline? |
| --- | --- | --- |
| `synthetic` (the default) | A small fake library that `seed_library.py` builds. The same every run. | Yes. It is the regression gate. |
| `real-snapshot` | Copies of your real databases, plus a copy-on-write overlay of your real library. | No. Every TPM gets the status `trend`, and the run can never update the baseline. |

Use the synthetic mode to catch a change that made the app slower. Use the real-snapshot mode to see how the app behaves on your real amount of data. Each `tpm.json` records its `mode`, its `observatory_profile` and a `data_size` block, so results are compared only within one mode and profile.

The observatory profile says how the throwaway backend is connected to a telescope. `--observatory offline` (the default) names this computer as the telescope host and a port nothing listens on, turns automatic downloads off and sets PHD2 (the guiding program) to local on port 1, where nothing listens (so a real PHD2 on this computer, port 4400, is never touched). The backend never contacts your telescope computer, INDI or PHD2. `--observatory simulated` gives the backend a telescope that is "on", made only of local stand-ins (see "The simulated observatory"). `--observatory replay` uses the same stand-ins, but they play the last minutes of a real night, 2026-10-03, after the app has already downloaded the rest of that night (see "The replay observatory"). The baseline stores its profile and is used only for a run with the same profile, because the setup key includes it.

## Run it

Install the Node packages once (`npm install`) and have a Chrome, Chromium or Brave browser. Set `CHROME_PATH` if the script cannot find one.

```bash
build/tpm/run_tpm.sh                    # Chrome flow, 3 laps, 30 s idle
build/tpm/run_tpm.sh --laps 5           # more laps
build/tpm/run_tpm.sh --electron         # also measure the Electron app (see below)
build/tpm/run_tpm.sh --electron --visible --gpu intel --laps 3   # real window on the Intel graphics (leave the window alone)
build/tpm/run_tpm.sh --electron --visible --gpu nvidia --laps 3  # the same on the NVIDIA card
build/tpm/run_tpm.sh --gpu nvidia       # Chrome flow on the NVIDIA card (invisible)
build/tpm/run_tpm.sh --require-power-profile performance   # refuse unless that profile and AC power are active
build/tpm/run_tpm.sh --cold             # drop the file cache first: a cold start (see "Cold start")
build/tpm/run_tpm.sh --preflight        # only check that no upload, backup or real backend is running
build/tpm/run_tpm.sh --update-baseline  # save this run as the new baseline
build/tpm/run_tpm.sh --observatory simulated   # a telescope that is "on", all local simulators (see below)
build/tpm/run_tpm.sh --observatory simulated --sim-exposure-seconds 20   # shorter simulated exposures
build/tpm/run_tpm.sh --observatory replay    # the last minutes of the night of 2026-10-03, after an end-of-night preload
build/tpm/run_tpm.sh --observatory replay --replay-speed 2   # twice as much night in the same minutes
build/tpm/run_tpm.sh --scale 5          # synthetic library with 5 times the targets and frames
build/tpm/run_tpm.sh --sky-catalog-copy # synthetic library, but with your star-catalog caches (see below)
build/tpm/run_tpm.sh --real-snapshot    # your real data, read only (see below)
build/tpm/scaling.py                    # table of key TPMs against data size (use .venv/bin/python)
```

The run takes about two minutes. It writes `build/tpm/results/<timestamp>/` (not tracked by git):

| File | Content |
| --- | --- |
| `tpm.json` | One entry per TPM: name, catalog id, unit, value, `n` (number of samples), status and limit report. |
| `flow-report.html` and `flow-report.json` | The Lighthouse user-flow report: one step for the load and one for each display and lap. |
| `flow-metrics.json` | The raw page measurements: every click time, every RPC call, heap sizes. |
| `backend-metrics.json`, `backend-samples.jsonl` | Backend CPU seconds, peak memory and per-method RPC times. |
| `observatory-metrics.json`, `observatory-sim-stats.json`, `observatory-phd2-stats.json`, `indiserver.log`, `mock_phd2.log`, `exposure_driver.log` | Only with `--observatory simulated` or `replay`: what the simulated observatory did (see below). The replay driver's log is also called `exposure_driver.log`. |
| `replay-plan.json`, `replay-summary.json` | Only with `--observatory replay`: the plan (window, frames, every planned guide event and log line) and the facts that also go into `info.replay` of `tpm.json`. |
| `backend.log` | The throwaway backend's log. |
| `electron-metrics.jsonl` | Electron process samples, only with `--electron`. |
| `cold-cache.json` | Only with `--cold`: the files and bytes dropped from the page cache, the time it took and the share of a sample still in memory before and after, per group. |
| `setup-fingerprint.json`, `nvidia-samples.jsonl` | The setup facts taken at the start, and the NVIDIA card's state every 2 seconds (only if `nvidia-smi` exists). |

The script prints a table of every TPM with its status and exits with 1 if any TPM is breached.

## Setup and comparability

The same code gives different numbers on a different setup, so each run records the setup and is compared only with a baseline from the same setup.

What the run records (in the `info.setup` block of `tpm.json`, and in the table header):

| Field | Where it comes from |
| --- | --- |
| CPU model, physical and logical cores, CPU governor | `/proc/cpuinfo`, `lscpu`, `/sys/devices/system/cpu` |
| RAM total, kernel | `/proc/meminfo`, `uname` |
| AC or battery, power profile | `/sys/class/power_supply`, `powerprofilesctl get` |
| Connected displays with resolution and refresh rate, docked yes or no | `xrandr` through XWayland, and `/sys/class/drm/*/status` (docked means an output that is not the laptop screen is connected) |
| GPUs present, PRIME mode, session type | `lspci`, `prime-select query`, `XDG_SESSION_TYPE` |
| GPU requested (`--gpu`), GPU used, WebGL renderer string | the WebGL renderer of the profiled page (the Electron window's own WebGL when `--electron` ran, otherwise the Chrome flow's); `info.gpu` holds requested, used, renderer and source. In Electron `electron_gpu` (`app.getGPUInfo`, the last report) is shown next to it. |
| NVIDIA state during the run | `nvidia-smi` every 2 seconds: peak and mean utilization and memory, power states seen |
| Mode, observatory profile | as before |

Why these matter: the dock changes which screens the Intel graphics must drive, and a 120 Hz screen changes how many frames the page draws. The power profile and the CPU governor set how fast the CPU runs. The graphics card (Intel, NVIDIA or software) changes the Planetarium numbers the most. On battery the computer is slower still. Reading `nvidia-smi` can keep the NVIDIA card awake, so a run with `nvidia-smi` installed does not show a fully idle card.

**The setup key.** A short hash (`setup_key`) of the CPU model, the displays with their resolutions, AC state, power profile, the kind of graphics card used (`nvidia`, `intel`, `amd` or `software`, from the WebGL renderer of the profiled window), the mode, the observatory profile and (only for a `--cold` run) the cold flag. Refresh rate, RAM and kernel are recorded but do not change the key. `tpm_baseline.json` holds one baseline per key under `setups`.

- A run whose key has a baseline is compared with it.
- Any other run prints "DIFFERENT SETUP: no baseline for this setup key, not compared", and every TPM gets the status `unmatched` (a real-snapshot run still gets `trend`). Run `--update-baseline` to start the baseline for the new setup. It writes only that key's entry and leaves the others alone.
- The first file had one baseline and no keys. It was filed under the key of this workstation's setup (AC, balanced, docked with two screens, headless Chrome, software graphics). A file in the old format is still read and compared as before, with a note in the header.

**Choosing the graphics card.** `--gpu auto|intel|nvidia`. With `--electron` it applies to the Electron window (and the Chrome flow stays on `auto`); without it, it applies to the Chrome flow.

- `auto`: today's behavior. The card is only recorded.
- `nvidia`: sets the PRIME render offload variables (`__NV_PRIME_RENDER_OFFLOAD=1`, `__GLX_VENDOR_LIBRARY_NAME=nvidia`, `__VK_LAYER_NV_optimus=NVIDIA_only`) for the launched process and starts it with `--ozone-platform=x11 --use-gl=angle` plus an ANGLE backend. That is XWayland with ANGLE on the system graphics driver. The Chrome (Brave) flow uses `--use-angle=gl-egl` (the system OpenGL ES driver). Electron uses `--use-angle=gl` (desktop OpenGL through GLX), because Electron's own Chromium cannot find an EGL configuration on the NVIDIA card and quietly draws on Intel. Both choices come from `electron/gpu_choice.js`, the one place the app launcher and this harness share. (ANGLE's Vulkan backend gave no WebGL here, and without these switches headless Chrome uses software SwiftShader.)
- `intel`: removes those variables, so the Intel graphics (the default card on this laptop) is used, with the same Chromium switches.
- After the page loads, the WebGL renderer string must name the card asked for. If it does not (for example Intel when you asked for NVIDIA), the run fails with a `GPU CHECK FAILED` message instead of measuring the wrong card. An invisible Electron run (`xvfb-run`) has no graphics card, so `--gpu intel` or `--gpu nvidia` fails there; use `--visible`. A trial with headless Brave on this computer gave the Intel UHD 630 for `intel` and the GeForce GTX 1650 for `nvidia`. The visible Electron path (`--electron --visible --gpu nvidia`, Electron variant of the switches with PRIME offload) was verified on this computer: the Electron window's WebGL reported the GeForce GTX 1650. The run still checks the renderer every time. `flow.mjs` and `electron_run.mjs` import `electron/gpu_choice.js` directly.

**Requiring a power profile.** `--require-power-profile <name>` refuses to start unless `powerprofilesctl get` prints that name and an AC adapter is online. It never changes the profile. To set it yourself: `powerprofilesctl set performance`.

**Recommended fixed setup** for baselines: on AC power, power profile `performance`, docked with the same displays every time, the lid state the same, no other heavy program running, and one run per graphics choice. Take the baseline with `build/tpm/run_tpm.sh --require-power-profile performance --update-baseline`, and a second baseline with `--electron --visible --gpu intel` and another with `--gpu nvidia`.

## The real-snapshot mode

`run_tpm.sh --real-snapshot` copies and mounts, in this order:

1. **Databases.** `astrometrics.db`, `astrometrics_log.db` and `wayfinding.db` are copied with SQLite's online backup call. The source is opened read only (`file:...?mode=ro`), so it is safe while your backend runs. `calibration_frames.json` and the deep-star catalog are copied too. No other database is copied; the backend does not need them.
2. **Catalogs.** `~/.cache/astrometrics/catalogs` (the Gaia star cache and the Gaia spectra cache) is copied into the throwaway cache folder. The run refuses to copy more than 5 GB.
3. **Settings.** The throwaway settings file keeps your equipment sections (cameras, optics, location). It drops every section that names a host, a key or a folder (the telescope host, the remote transfer, the guiding location, the online solver key) and uses the offline settings instead.
4. **Library.** Your image library (hundreds of GB) is not copied. `fuse-overlayfs` mounts a copy-on-write overlay inside the throwaway folder: the lower layer is your real library, the upper layer is in the throwaway folder. A read comes from your real file. A write goes to the upper layer only.
5. **Second lock.** The databases hold absolute paths into your library, so the backend runs under `bwrap` (a sandbox tool): the whole computer is read only for it, only the throwaway folder is writable, and the overlay is bound over the real library path. The backend then sees the overlay at the path it expects and can never reach the real files.
6. **Cleanup.** The overlay is always unmounted when the run ends, even after a failure, and the script checks `/proc/mounts`. If the overlay is still mounted, the script keeps the throwaway folder and says so, because deleting through a mount could reach real files.

After the run, `overlay-writes.json` lists everything the backend wrote into the upper layer, with sizes. An empty list means the session did not write to the library. A file in the list is code that writes during what should be a read-only session. The flow still stops the run if the page sends a hardware-looking RPC call.

What can never be written: your real library, your real databases, your settings file, your state and cache folders. What the run reads from them: the files listed above, read only.

The first real-snapshot numbers are slow mostly because the databases are large and the library lives on a 316 GB tree. Some flow steps cannot find the seeded names (for example the night `2026-10-12`); those steps are left out of the numbers.

### The scaling table

`.venv/bin/python build/tpm/scaling.py [results folder] [--csv file]` prints one row per run: mode, profile, data size (targets, frames, stars, nights, guiding samples, database and library size in GB), and the key TPMs. Compare rows of the same mode and profile. To get a controlled curve with fake data, run the synthetic mode with `--scale 1`, `2`, `5`, `10`: the targets and the frames per target grow by that factor (stars do not). Use `--csv` to plot a TPM against a data-size column.

The `data_size` block in `tpm.json` holds the counts the table uses.

## What the run does

1. `seed_library.py` fills the library with fixed fake content (a fixed random seed, so every run is the same): three targets (M 31, M 13 and M 81) with light frames, an imaging stack and a preview picture; calibration frames; the star Vega with a spectrum and a light curve; and one observing night with guiding samples and a guiding run.
2. The script builds the production UI and serves it with `vite preview`. The preview server forwards the backend's routes to the throwaway backend, because the backend accepts a browser connection only from its own app origin.
3. `flow.mjs` loads the app and then does one lap, several times. A lap visits Image Viewer (select a target), Image Processing (select a target, open the Analysis and Data tabs), Astronomy Manager (select a star), Planetarium (select a target, pan, zoom, change the time) and Observatory Manager (select a target, open the seeded night, go back to Live). Lighthouse records one navigation step for the load and one timespan step per display and lap.

   **A different target every visit.** The app shares one selected target between displays, so a display often opens with the target the flow is about to click, and that click would measure nothing. On each display the flow reads which row is selected and clicks a seeded row that is not (M 31, M 13 or M 81; on Astronomy Manager Vega or Altair). The timing ends when that row shows as selected, the next frame has painted and no backend call has been waiting for 150 ms, so it covers the display's real reload. The seed has three targets and two stars for this. A display that has no such row in its list is left out of the select-target number for that visit.

   **Prototype displays.** Observation Manager and Command Console are prototype displays, so the flow does not visit them and no TPM covers them.
4. The flow idles on Image Viewer and counts the RPC calls the page makes on its own.
5. `rpc_timings.py` and `compile_tpm.py` reduce the raw files to the TPM list and compare it with `tpm_baseline.json`.

## The TPMs in plain words

| TPM | What it means | Lower or higher is better |
| --- | --- | --- |
| `time_to_app_ready` | Time from starting the page load until the first display shows its target list and no backend call is waiting. | Lower |
| `time_to_app_ready_cold` | The same time as `time_to_app_ready`, held to a cold-start limit. Every run measures it; only a `--cold` run compares it with the limit (see "Cold start"). Limit TBR, proposed 2026-11-06. | Lower |
| `first_display_open[<display>]` | Time of the first visit to each display in lap 1, the same click-to-settled time as `display_switch_median[<display>]` but for the first visit only. Cold-start limit (proposed 2026-11-06). | Lower |
| `first_rpc[<rpc>]` | Latency of the first call of the night review, the target list and the stack summary in the run. The first query on real data reads database pages that may not be in memory. Cold-start limit (proposed 2026-11-06). | Lower |
| `display_switch_median`, `display_switch_p95`, `display_switch_median[<display>]` | Time from clicking a display in the mode menu until the display has painted and its backend calls have finished. The median is the middle value; p95 is the value 95 out of 100 switches stay below. | Lower |
| `interaction_latency_select_target`, `_open_night`, `_change_tab` | The same time for selecting a target, opening an observing night and changing a tab. | Lower |
| `total_blocking_time[<display>]` | Total time the page's main thread was blocked by tasks longer than 50 ms while the display was used (the Lighthouse measure). A blocked page cannot answer clicks. | Lower |
| `inp_worst` | Interaction to Next Paint: the longest time from a click to the next paint, over all steps. | Lower |
| `cls_load`, `cls_worst_display_step` | Cumulative Layout Shift: how much the page jumps around, as a score. | Lower |
| `script_transfer_size_load`, `request_count_load` | Size of the JavaScript the page loads at start, and the number of requests. | Lower |
| `planetarium_fps_zooming` | Frames drawn per second while the zoom is animating, in the traced zoom (12 wheel steps each way over `--zoom-seconds`, default 4). The sky map draws only while something changes, so the gaps between wheel steps are not counted. Software graphics make it low. | Higher |
| `zoom_frame_ms_p50`, `zoom_frame_ms_p95` | Time between two drawn frames during the zoom animation only (frames closer than 100 ms): the middle value and the value 95 out of 100 frames stay below. | Lower |
| `zoom_slow_frames` | How many of those intervals are longer than 20 ms (1.2 refreshes of a 60 Hz screen; an exact 16.7 ms limit flags about two frames in five on a healthy run because of vsync timing jitter). | Lower |
| `zoom_tick_latency_ms_p50`, `_p95` | For each wheel step: the time from the wheel input event to the first frame drawn after it. | Lower |
| `zoom_settle_ms_p50`, `_p95` | For each wheel step: the time from the step to the last frame of its easing, when the sky is still. Steps whose easing runs into the next step are left out. | Lower |
| `planetarium_idle_draws_per_second` | Frames the browser draws per second in the 3 s idle window at the end of the traced zoom, when nothing moves. A sky map that draws on demand should draw about 0. Limit TBD (not set yet). | Lower |
| `zoom_long_tasks` | Main-thread tasks longer than 50 ms in the zoom window. | Lower |
| `console_errors`, `failed_requests`, `error_toasts`, `desktop_notifications`, `backend_error_log_lines` | The error counts (see "Error TPMs"). Each limit is 0. | Lower |
| `zoom_main_thread_ms_per_frame`, `zoom_gpu_ms_p50` | How long the page's main thread (per frame) and the graphics process (middle value) were busy during the traced zoom. | Lower |
| `frames_auto_downloaded`, `guide_events_received`, `frame_to_library_seconds`, `guide_log_lines_pulled` | Only with `--observatory simulated` or `replay`. Frames the app copied into the library by itself, guide steps the stand-in PHD2 sent to the app, the middle time from a frame appearing in the stand-in picture folder to its copy in the library, and lines of guide log the log sync fetched. | Higher, higher, lower, higher |
| `ekos_log_lines_pulled` | Only with `--observatory replay`. Lines of the Ekos analyze log (the log Ekos writes about captures, focus and guiding) that the log sync fetched. The log grows during the replayed window. | Higher |
| `fps_hw` | The same frame rate, kept only when WebGL drew on a real graphics card (the `info` block says which). Empty on software graphics. | Higher |
| `pan_frame_ms_p50`, `pan_frame_ms_p95` | Time between two drawn frames in the traced Planetarium pan: the middle value and the value 95 out of 100 frames stay below. | Lower |
| `star_project_ms_p50` | Middle time of the JavaScript that turns the stars' sky positions into screen positions and uploads them to the graphics card, once per frame. | Lower |
| `overlay_draw_ms_p50` | Middle time of the 2D drawing of the grid, labels, outlines and other layers, once per frame. | Lower |
| `gpu_ms_p50` | Middle time the graphics process was busy during one frame. If it is close to the frame time, the graphics card is the limit. If it is small, the page's own JavaScript is the limit. | Lower |
| `planetarium_fps_panning` | Frames the browser draws per second while the Planetarium sky is dragged. The run uses software graphics, so the value is much lower than on a real graphics card. | Higher |
| `js_heap_after_last_lap` | JavaScript memory in use after the last lap and a forced garbage collection (cleanup of unused memory). | Lower |
| `js_heap_growth_per_lap` | How much that memory grows from the first lap to the last, per lap. A value that stays above zero hints at a memory leak. | Lower |
| `electron_<main,renderer,gpu>_peak_memory_mb`, `_mean_cpu_percent` | Memory and CPU of the Electron processes, from the file `electron/perf_metrics.js` writes. | Lower |
| `backend_peak_rss`, `backend_cpu_time` | The backend's largest resident memory and the CPU seconds it used during the run. | Lower |
| `rpc_count_load`, `rpc_count_per_visit[<display>]` | Backend calls at load, and per visit to each display. | Lower |
| `rpc_p95[history:night_quality]`, `rpc_p95[target:get_targets]`, `rpc_p95[processing:stack_summary]` | Round-trip time of the night review, the target list and the stack summary. | Lower |
| `idle_rpc_per_minute` | Backend calls per minute the page makes while nobody uses it. | Lower |

The backend does not log how long each RPC call takes, so the per-method times are the page's round trips (the time from sending a request to receiving the reply). They include the preview server's forwarding, which adds a small, steady amount.

`budgets.json` is a Lighthouse budget file (total blocking time, layout shift, script size and request count). Lighthouse shows it in the load step of the report. The values are design estimates.

## TPM catalog

`tpm_catalog.json` defines every TPM. It holds no measured values. Three files play three roles:

| File | Role |
| --- | --- |
| `tpm_catalog.json` | The definition: what the TPM is, why a user cares, its unit, how it is measured, and its limit. Also the displays and actions the flow visits. |
| `tpm_baseline.json` | The baseline: what one setup measured once. Used for the watch and breach check. |
| `<results>/tpm.json` | The result of one run: the value, the baseline status and the limit report. |

Each catalog entry has an `id`, a `title`, a `why` (the user need in one sentence), a `unit`, `better` (lower or higher), a `scope` (per run, per display, per RPC method, or only for the simulated observatory), the `method` and the `statistic`, a `threshold`, an `objective`, a `threshold_status`, the `source` of the limit and the `reference_setup` the limit was judged on. The `name` is the flat name that `tpm.json` and the baseline use, with `{display}`, `{rpc}` or `{process}` where the TPM comes once per display, method or process. A limit that depends on the run is written as `{"factor": 0.25, "of": "exposure_seconds"}`.

The threshold is the worst value you accept. The objective is the value you aim for.

| Status | Meaning |
| --- | --- |
| TBD | No limit yet. |
| TBR | A proposed limit. The table shows how much of it the run uses, but it never passes or fails a run. |
| Confirmed | A limit you accepted. A value beyond it makes `compile_tpm.py` exit with 1. |

To confirm a limit, edit its `threshold_status` to `Confirmed` in `tpm_catalog.json` and put the reason in `source`. Only Confirmed limits can fail a run, in addition to the baseline check above. A confirmed entry also has a `confirmed` note (date, who, source). On 2026-11-06 the user confirmed ten limits: `inp_worst`, `interaction_latency_select_target`, `cls_load`, `cls_worst_display_step`, `planetarium_fps_panning`, `planetarium_fps_zooming`, `fps_hw`, `pan_frame_ms_p95`, `zoom_frame_ms_p95` and `display_switch_p95`. The same day the five error TPMs were added as a user requirement, each with a limit of 0 (see "Error TPMs"). A limit of 0 is beyond the moment the value is 1.

`tpm.json` gives each TPM its catalog `id` as `tpm`, plus `statistic`, the scope fields (`display`, `rpc`, `process`) and a `limit` block with `threshold`, `objective`, `threshold_status` and `used_percent` (the share of the threshold the value uses; over 100 means beyond it). The table prints the same.

Limits proposed so far come from the Nielsen response-time limits (0.1 s feels instant, 1 s keeps the flow of thought, 10 s is the attention limit), the Google Core Web Vitals (Interaction to Next Paint, layout shift), the 60 Hz laptop panel (frame rate) and the memory of the workstation. All are TBR. Some entries say `not_measured_yet`: they have a proposed limit, but no code produces the number.

`budgets.json` is the Lighthouse budget file. Its values are listed in the catalog under `lighthouse_budgets`, and `test/test_tpm_catalog.py` fails if the two differ. Edit both together.

`flow.mjs` reads the displays and their actions from the catalog. Observation Manager and Command Console are listed as excluded prototype displays. A test fails if the catalog and the app's displays (`ui/App.tsx`) differ.

## The Planetarium pan and zoom profile

After the laps, the flow switches to the Planetarium and drags the sky around for `--pan-seconds` (default 6) while it records a Chrome DevTools trace. It then zooms the sky in and back out with the mouse wheel (the sky map listens to the wheel on its canvas, see `useSkyMapInput.ts`) in the same trace. `planetarium-profile.json` holds the pan numbers at the top and the zoom numbers under `zoom`; they are reported separately, with their own marks in the trace.

**The sky map draws on demand.** It asks for a frame only while something changes (a drag, or the zoom easing toward its target in `zoomEasing.ts`) and draws nothing while the picture is still. The flow's wheel steps come about every 167 ms, so the time between two drawn frames across a step gap says nothing about speed: it is idle time, not a slow frame. The zoom is therefore measured like this (`planetarium_profile.mjs`):

- The flow puts a `tpm:wheel_tick` mark in the trace at every wheel input event (stamped with the event's own timestamp).
- *Tick latency*: from the mark to the first `DrawFrame` after it.
- *Settle time*: from the mark to the last `DrawFrame` of its easing. Drawn frames closer than 100 ms (`ACTIVE_GAP_MS`) are one stretch of animation; a longer gap is the renderer being idle. A step whose animation runs into the next step has no settle time of its own and is counted in `ticks.overlapped`.
- *Active frame times*: the p50 and p95 of the intervals inside the stretches only, the number of intervals over 20 ms (`zoom_slow_frames`) and the frame rate inside the stretches (`planetarium_fps_zooming`). `active.idleGapCount` counts the idle gaps that were left out.
- *Long tasks*: main-thread tasks over 50 ms in the zoom window (`zoom_long_tasks`).
- The flow no longer counts animation frames during the zoom. A counting loop asks the page for a frame every refresh, which the sky map would not draw by itself.

**The zoom ticks and the idle window.** The traced zoom sends its 12 wheel steps each way in bursts of 4, with a 0.6 s pause after each burst. The easing of a burst's last step then finishes before the next step, so some steps have a settle time (a continuous run of steps gave none, because every step's easing ran into the next). After the last step the flow waits 0.8 s for the easing to settle, ends the zoom window, and then leaves the page alone for 3 s (`tpm:idle_start` to `tpm:idle_end` in the trace). `planetarium_idle_draws_per_second` is the number of drawn frames in that window divided by its length. The first function check on 2026-11-06 (NVIDIA, Chrome flow) gave 0 draws in 3.0 s and settle times of about 340 ms from 6 of 24 steps. The pauses make the zoom window about 7 seconds longer than `--zoom-seconds`; the smoothness numbers (frame times, slow frames, tick latency) still count only the frames inside the stretches of animation.

If the app draws every frame anyway (no idle gap at all, as in the first function check of 2026-11-06, which drew at 60 fps through the whole trace including the 300 ms pause), the stretches merge, almost every step counts as overlapped and the settle time cannot be told from draws. Then `active.idleGapCount` is 0 and the settle TPMs have few or no samples; fixing that needs the app to stop drawing when nothing changes, or to mark the end of the easing.

The old all-interval numbers are still in the profile as `frameMsP50` and `frameMsP95`. The pan is a continuous drag (a mouse move every 16 ms), so its draws are continuous and its frame-time TPMs keep using the plain intervals; `active.idleGapCount` in the pan profile shows whether any idle gap slipped in. It saves `planetarium-trace.json` (open it in the Chrome Performance panel or at `ui.perfetto.dev`) and `planetarium-profile.json`. The profile holds:

- the frame count and rate, and the time between frames (a frame is one drawn compositor frame);
- the page's main-thread busy time per frame, split into star projection and upload, 2D overlay drawing, and everything else;
- the graphics process's busy time per frame;
- the WebGL renderer name.

The star and overlay times come from `performance.measure` marks in `CelestialSkyMap.tsx` and `StarFieldRenderer.ts`. They record only while the flow sets `window.__ASTROMETRICS_PERF__` for the pan. Otherwise each mark is one property check. The trace cannot run during a Lighthouse step, so the pan runs between steps, and tracing slows the page a little. The untraced `fps_hw` comes from the ordinary pan inside the laps.

Every `tpm.json` has an `info` block with `webgl` (the vendor and renderer strings the page reports, and whether they look like a real graphics card), `gpu` (the graphics card requested, the card used, the renderer and where it came from: the Electron window's WebGL when `--electron` ran, otherwise the Chrome flow's) and, with `--electron`, `electron_gpu` (what the Electron main process says about the GPU and its features, last report). The setup key's graphics field comes from `info.gpu`. A renderer name with `SwiftShader` or `llvmpipe` means software graphics. The TPMs added with the on-demand zoom measures have no baseline yet (status `new`). The profile comes from the Electron window when `--electron` ran, otherwise from the Chrome flow.

## Error TPMs

The user's rule is "no errors in the web browser console" and no error notifications at start-up. Five TPMs count what breaks it. Each is a count with a Confirmed limit of 0 (confirmed as a user requirement on 2026-11-06), so one error fails the run. When a limit is breached, `compile_tpm.py` prints every offending message under `OFFENDERS for <TPM>`, and `tpm.json` keeps them in `info.errors`.

| TPM | What it counts | Where it comes from |
| --- | --- | --- |
| `console_errors` | Console messages of level error and uncaught page errors, with the message text and its source URL. | Puppeteer's `console` and `pageerror` events (`error_watch.mjs`) on the Chrome flow and on the Electron window. For Electron a second connection (`electron_run.mjs`, `electron-startup-errors.json`) also watches the app's start-up and idle time, so the errors of the first load are counted, not only those of the flow. |
| `failed_requests` | Responses with status 400 or more, and requests that failed, with the URL and status. | Puppeteer's `response` and `requestfailed` events. A request the browser cancelled (`net::ERR_ABORTED`) is listed apart as `aborted_requests` and does not count. The flow itself aborts no request. |
| `error_toasts` | In-app toasts of kind error or warning, with text and source. | A listener for the `astrometrics:toast` window event (`ui/common/utils/emitToast.ts`) that the flow installs before the app starts. |
| `desktop_notifications` | Native notifications the page asked Electron to show, with title and body. | `electron/perf_metrics.js` listens next to the app's own `show-notification` handler when `ASTROMETRICS_PERF_METRICS` is set. It counts the requests before the app's cooldown and its checks. Only an Electron run measures it; a plain browser cannot send one, so a Chrome-only run leaves it empty. |
| `backend_error_log_lines` | Lines at ERROR or CRITICAL level in the throwaway backend's `backend.log`, from the backend's start to its end, and the web server's own `ERROR:` lines. | `backend.log` in the results folder. Warnings and the lines of a traceback after its first line are not counted. |

A failed image load shows up twice, as a failed request and as the console error the browser prints for it. Counts add up over the Chrome flow, the Electron flow and the Electron start-up, so a run with `--electron` can show the same fault more than once. The limit is 0, so only the list matters. Toasts that the app shows before the flow attaches to the Electron window are not seen as toasts (an error toast also writes a console error, which the start-up watcher does see).

## Visible GPU profile

The Chrome flow and the xvfb Electron run use software graphics. To measure on the real graphics card, run:

```bash
build/tpm/run_tpm.sh --electron --visible --idle-minutes 0 --laps 1
build/tpm/run_tpm.sh --electron --visible --gpu intel --laps 3    # force the Intel graphics
build/tpm/run_tpm.sh --electron --visible --gpu nvidia --laps 3   # force the NVIDIA card
```

An Electron window opens on your real desktop for the run, so leave it alone until it closes. It uses its own user-data folder, lock and settings inside the throwaway folder, so it cannot touch your real app. It keeps `DISPLAY` and `WAYLAND_DISPLAY` from your session and passes `--ozone-platform-hint=auto`. Read the `webgl_electron_window` line in the printed header: if it still says software, try `--ozone x11` (XWayland) or `--ozone wayland`, or choose the card with `--gpu` (which replaces `--ozone`; see "Setup and comparability").

## Star density (`--sky-catalog-copy`)

The fake library has one star, but the real Planetarium draws stars from the Gaia cache (about 1.2 million stars). `--sky-catalog-copy` copies `catalog_cache.db`, the Gaia spectra cache and the deep-star catalog into the throwaway folder (read only at the source; the run prints the size and refuses over 5 GB), so the throwaway backend serves the same star density. `--real-snapshot` includes this copy.

## Electron

`run_tpm.sh --electron` starts the Electron app in `xvfb-run` (an invisible screen; `--visible` uses your real display instead) in development mode, pointed at the throwaway backend and the preview server. It sets `ASTROMETRICS_PERF_METRICS` to a file. When this variable is set, the main process also writes a `gpu_report` line (`app.getGPUFeatureStatus()` and `app.getGPUInfo('basic')`) at start-up and again 20 seconds later (the first one comes before the window has rendered and can show software features; `info.electron_gpu` is the last one, and the card that counts is the one the Electron window's WebGL reports), writes one `desktop_notification` line (title and body) each time the page sends the `show-notification` request, and samples `app.getAppMetrics()` every 2 seconds and appends one JSON line per process (time, process type, process id, CPU percent, working set and peak working set). The app does nothing when the variable is not set. The script lets the app idle for `--idle-minutes`, then runs the same flow in its window over the Chrome DevTools port.

Electron's own folder, lock and desktop settings point inside the throwaway folder, so a running copy of the real app is not affected. The runner launches Electron itself with a DevTools port and drives it with the same Puppeteer code as the Chrome flow, instead of Playwright's `_electron` launcher, so one flow serves both. It needs `xvfb-run` (package `xvfb`).

## Cold start (`--cold`, `--after-reboot`)

A cold start is a start where the files the app needs are not in the computer's memory and must come from the disk. A real start after a reboot is cold. A run right after another run is warm, because the first run left everything in the Linux page cache (the memory the kernel uses to keep recently read files).

`--cold` makes the run start cold without root rights and without any system setting. Just before the flow starts (the backend runs, the UI is built and served), `cold_cache.py` asks the kernel to forget the cached pages of every file the app reads, one file at a time (`os.posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)`). Files in the throwaway folder are flushed to disk first, because pages that are not yet on disk cannot be dropped. Nothing is written to any file it reads.

| Group | Files |
| --- | --- |
| `sandbox_databases`, `sandbox_library` | The throwaway databases and the other files in the throwaway library. With `--real-snapshot`: the database copies. |
| `catalog_caches` | The star-catalog caches (with `--sky-catalog-copy`, or the copies of `--real-snapshot`). |
| `ui_build` | The built UI that the harness serves. |
| `electron` | `node_modules/electron/dist` (the Electron program and its resources). |
| `python_site_packages`, `python_stdlib` | Every installed package of the virtual environment, and the Python standard library. |
| `repo_packages` | `astrometricslib`, `wayfindinglib` and `backend`. |
| `browser` | The program named by `CHROME_PATH`. A program in a shared folder such as `/usr/bin` is taken alone; one in its own folder (for example `/opt/brave.com/brave`) brings the folder. |
| `real_library` | Only with `--real-snapshot`: the preview pictures and the FITS files with `stack` in the name under the real stacks folder. At most 20,000 files and 4 GB; the raw frames and the rest of the library are not touched. Files are opened for reading only. |

The drop is checked. With `fincore` (from util-linux) or `vmtouch`, the script measures how much of a sample of each group (up to 40 files: the largest and evenly spaced ones) is in memory before and after, and prints a table. `cold-cache.json` in the results folder holds it, and `info.cold_cache` of `tpm.json` repeats it. Three things keep the "after" value above zero:

- Pages that a running program has mapped cannot be dropped. The backend and, if your real app runs, its Python and Electron processes hold their libraries.
- `fincore` cannot look at files that are not yours (the kernel refuses), so the system's Python files and some browser files show `-`. The drop is still asked for them.
- On a memory-backed file system (tmpfs) the page cache is the storage, so nothing can be dropped. On this computer `/tmp` is tmpfs, so with `--cold` the throwaway folder goes to `/var/tmp` (a disk) when `TMPDIR` is not set. If you set `TMPDIR` yourself, it is used as it is. The report lists a group on a memory file system under `ineffective_groups`.

The drop takes about 3.5 seconds for 49,000 files and 2 GB (the virtual environment's `site-packages` is 44,000 of them and takes 2 seconds).

`--after-reboot` only records facts. It stores `boot_age_seconds` (from `/proc/uptime`) and sets `cold_source` to `reboot`; a `--cold` run without it has `cold_source: fadvise`. With `--cold` the drop still happens in a run after a reboot (it also clears what the run itself read while setting up). Use `--after-reboot` only for the first run after a reboot; the run prints the boot age so you can check. The two sources have the same setup key, so you can compare a drop-based cold run with the true cold baseline.

**What changes in the results.** Every `tpm.json` has `cold` (true or false) and `cold_source` at the top, and every TPM has `cold`. The `cold` flag is part of the setup key (a warm run's key is unchanged), so a cold run has its own baseline and is never compared with a warm one.

**The cold-start TPMs.** `time_to_app_ready_cold`, `first_display_open[<display>]` and `first_rpc[<rpc>]` are measured in every run from the first lap, but only a `--cold` run holds them to their limits (`cold_only` in the catalog). The limits are TBR, a proposal of 2026-11-06 for you to set: app ready 10 s (objective 5 s), the first open of each display 3 s (1 s), and the first call of each of the three tracked methods 5 s (1 s). The seeded library is small, so the first function check had values far below them (app ready 1.2 s, first opens 20 to 430 ms, first calls 25 to 430 ms); the real-snapshot mode is where the cold read of big databases shows.

## How to take a baseline

Take a baseline when the computer is as quiet and as repeatable as it can be. The steps, in order:

1. Commit your work, so the baseline belongs to a known version of the code.
2. Restart the computer.
3. Plug in the charger. Set the power profile to balanced (`powerprofilesctl set balanced`). Use the laptop screen only: no dock, no external screen, the lid open.
4. Do not start the Astrometrics app. Close Brave, KStars and Siril.
5. Wait about 2 minutes, so the start-up work of the desktop finishes.
6. Check the computer is quiet: `build/tpm/run_tpm.sh --preflight`. It refuses (exit code 1) and says what to stop if an `rclone copy` or `rclone sync` upload, a `backup_library_databases` run or the real backend (port 5000) is running. It stops nothing. The `--cold` and `--after-reboot` runs make the same check first. `--skip-preflight` turns the refusal into a warning; use it for a function check only, never for a baseline.

Then run these, one after the other. Each line is a separate run with its own results folder. Add `--update-baseline` to a line only when you decide to keep its values (see "Update the baseline on purpose"). Add `CHROME_PATH=/usr/bin/brave-browser` in front if the script cannot find your browser, and `--require-power-profile balanced` to make the run refuse on a wrong power profile.

```bash
# 1. The true cold baseline: the first run after the reboot, NVIDIA, Chrome flow.
build/tpm/run_tpm.sh --after-reboot --cold --gpu nvidia --sky-catalog-copy --laps 3

# 2. Warm runs, one per graphics card: NVIDIA first, then Intel.
build/tpm/run_tpm.sh --gpu nvidia --sky-catalog-copy --laps 3
build/tpm/run_tpm.sh --gpu intel --sky-catalog-copy --laps 3

# 3. The other observatory profiles (offline is the default and was run above).
build/tpm/run_tpm.sh --observatory simulated --gpu nvidia
build/tpm/run_tpm.sh --observatory replay --gpu nvidia

# 4. Your real data, read only (a trend, never a baseline).
build/tpm/run_tpm.sh --real-snapshot --cold --gpu nvidia

# 5. The visible Electron window on the real graphics card (leave the window alone).
build/tpm/run_tpm.sh --electron --visible --gpu nvidia --laps 3
build/tpm/run_tpm.sh --electron --visible --gpu intel --laps 3
```

Why this order: the first run after the reboot is the only one that is truly cold, so it goes first and is the only run that gets `--after-reboot`. Later `--cold` runs drop the cache by `fadvise` and can be compared with it. The cold run on NVIDIA has its own key; the warm NVIDIA and Intel runs have their own keys too (the graphics card is part of the key). A real-snapshot run is never compared and never updates the baseline.

## Update the baseline on purpose

The baseline file `tpm_baseline.json` holds one block per setup key (see "Setup and comparability"), and each block has one entry per TPM: the baseline value, a watch ratio (default 1.25), a breach ratio (default 1.5) and a slack, a small absolute allowance in the TPM's unit so that tiny values do not trip the limits on noise. A value past `baseline * watch_ratio + slack` is on **watch**; past `baseline * breach_ratio + slack` it is **breached**. For `planetarium_fps_panning` higher is better, so the ratios divide instead.

The baseline never changes by itself. To accept a new normal (for example after a deliberate feature that costs memory):

1. Run `build/tpm/run_tpm.sh` twice and confirm the numbers agree.
2. Say in the pull request why the number moved.
3. Run `build/tpm/run_tpm.sh --update-baseline` (on the setup you want the baseline for) and commit the new `tpm_baseline.json`.

To change one limit only, edit its `watch_ratio`, `breach_ratio` or `slack` in the file.

## The replay observatory

`--observatory replay` plays a real night through the same stand-ins as the simulated profile. The simulated profile is invented traffic (one fake exposure after another, a looped guide log). The replay profile is the traffic of an actual night: real frames, real guide steps with their dithers and lost stars, and a real Ekos log that grows.

**One fixed night.** The night is 2026-10-03 (`--replay-night` can name another one). It is the night the guide log fixture was cut from. The run reads these files, and only reads them:

| What | Where it comes from |
| --- | --- |
| Light frames | Your image library's `lights` folder, found by file name from the capture lines of the Ekos log. Their headers give the time, exposure and where the mount pointed. |
| Ekos analyze log | `ekos-<date>T*.analyze` in the wayfinding `ekos_logs` folder. |
| Guide log | `guide_log-<date>T*.txt` in the same folder. |

The paths come from your real settings file (read only). Every file is copied into the throwaway folder, never linked, so nothing the run does can reach a real file.

**Short runs, an end-of-night start.** A real night lasts hours and a test must not. So the run does not replay the night from the start. It starts from the state at the end of the night and plays only the last minutes:

1. *Window.* `wayfindinglib/simulation/night_replay.py` picks the busiest window late in the night (the last 40% of the guide log). A window scores 3 points for each frame that arrives, 4 for each settle after a dither and 1 for each lost-star row (at most 4). The default window is 300 seconds. For 2026-10-03 it is 2026-10-04 08:48:36 to 08:53:36 UTC: three frames of NGC 7331 (115 to 117), a dither with its settle, 85 guide steps. Run `.venv/bin/python build/tpm/night_replay.py choose` to see the choice, or pass `--window-start` to `night_replay.py stage` to try another.
2. *Preload (setup).* Before the timed run, the frames that came before the window are loaded as if the app had already downloaded them. The run does not write database rows by hand. It puts a spread of 16 earlier frames (`--replay-preload-frames`) into the stand-in telescope computer's picture folder, adds the two targets (M 57 and NGC 7331) to the throwaway library with their coordinates, and asks the running throwaway backend to download each target with its `telescope:sync` method, the call behind the "Download Frames" button. The backend copies, sorts, indexes and measures the frames with its own code. The stand-in guide log and Ekos log are cut at the window's start, so the log pulls start from a log of the right length (6,880 and 8,814 lines for the default window). All of this is setup. It took about 2 minutes on the first runs (staging, building and serving the UI, and a 96 second ingest), almost all of it the quality measurement of the 16 frames. It is timed apart (`info.replay.setup_seconds`) and no TPM includes it. Only a subset is loaded: all 175 earlier frames would take about 15 to 30 minutes.
3. *Live window.* When setup ends, the run writes a start time 3 seconds ahead and the flow starts. The window then plays at the original spacing, divided by `--replay-speed` (default 1; at 2 the window covers 600 seconds of night in the same 300 seconds):
   - *Frames.* Each frame is copied into the stand-in picture folder at the moment the real camera saved it. A few seconds earlier the INDI simulator mount is pointed at the frame's coordinates and the simulated camera runs a short exposure. That only drives the camera status ("Exposing", then "Idle") that makes the app download. The simulated camera's own picture goes to a scratch folder and is not used. The frame is a real file, so the download, ingest and quality measurement work on real pixels.
   - *Guide events.* The stand-in PHD2 (`wayfindinglib/simulation/mock_phd2.py`) sends the window's guide steps, settle events and lost-star events (a guide row marked `DROP` becomes PHD2's `StarLost` event) at their real times, against the same start time as the frames.
   - *Logs.* The window's lines of the guide log and the Ekos log are appended to the stand-in files one at a time. The app's log pulls (after the first frame and then at most every 5 minutes) fetch the growing files. The pull interval is a constant in the app (`GUIDE_LOG_MINIMUM_INTERVAL_SECONDS`, not a setting). The first pull happens after the first downloaded frame, so every run has at least one. This harness does not change it.
4. *End.* The run waits until the driver has played the whole window and every window frame is in the library, then compiles the TPMs.

**Clocks.** The Ekos log counts seconds from a local start time and names the zone (MDT). The guide log has local times without a zone. The frame headers are UTC. The planner uses the log's zone name and reports `clock_check_seconds`: the median gap between the log's "frame saved" time and the header's end of exposure. A few seconds is normal; 3,600 would mean a wrong zone.

**What goes into `tpm.json`.** `info.replay` holds the night, the window (start, end, night seconds, real seconds), the speed, the frame counts (night total, before the window, preloaded, in the window, with the names), the guide events by type, the log line counts before and in the window, `clock_check_seconds`, `setup_seconds` (staging, building and serving the UI, and the ingest) and `ingest_seconds`.

**Approximations.** The app measures only the preloaded subset, not the whole night. The simulated camera runs a 6 second stand-in exposure per frame, not the real 120 seconds. The guide events are replayed from the log, so values the log does not hold (such as PHD2's `AvgDist`) are filled with zero. The replay's live guiding samples carry the time of the replay, while the pulled logs carry the date of the real night.

## The network seal

The simulated and replay profiles (and the offline profile, unless you pass `--no-seal`) run inside their own network. `run_tpm.sh` starts itself again under `bwrap --unshare-net --unshare-pid` (bubblewrap, the same tool the real-snapshot mode uses). The new network namespace has only a loopback interface. A network namespace has its own loopback, so everything that talks over 127.0.0.1 must be inside it, and it is: the script starts the backend, the INDI server and its simulators, the stand-in PHD2, the drivers, the UI server, the browser, Electron and `xvfb-run` as its own children. Nothing in the run can connect out, and nothing outside can connect in (your real PHD2, INDI server, StellarMate and backend are not reachable).

- The whole computer is bound read only (`bwrap --ro-bind / /`). Only the throwaway folder's parent (`TMPDIR`, which is `/var/tmp` for `--cold`), `build/tpm/results` and the display sockets (X11 and Wayland) are writable. `--unshare-pid` makes every process of the run end when the script ends.
- `python build/tpm/seal_guard.py sealed` checks the seal at the start: the only interface in `/proc/net/dev` is `lo`, and a connection to 1.1.1.1 and 8.8.8.8 fails.
- The older connection guard (the settings check and the `ss` check of the backend's connections) still runs as a second check.
- Because nothing has a route out, anything the app asks of the internet (for example an online lookup) fails at once. The numbers of a sealed offline run can differ a little from an older, unsealed baseline for that reason.
- A window on your display still works. The Wayland socket and the X11 socket are files, and they are reachable from the new namespace. `xrandr` ran from inside the seal during testing. A visible Electron window (`--electron --visible`) was not opened as part of testing the seal.
- The real-snapshot mode is not sealed. It has its own mounts and its own bwrap lock for the backend, and bubblewrap inside bubblewrap is not worth the risk for a mode that already cannot write to your library.
- To look inside a running sealed run from another terminal, use `nsenter --preserve-credentials -U -n -t <pid of a process in the run> <command>`.

## The PHD2 contract test

The app reads PHD2 only through its JSON event stream and the guide log. The stand-in PHD2 (`wayfindinglib/simulation/mock_phd2.py`) is only useful while it sends what real PHD2 sends, so it is tested against a recording of the real one.

**The recording.** `record_phd2_contract.py` runs the user's own PHD2 build (`~/.local/bin/phd2`, under `xvfb-run`) against the INDI simulators (telescope and guide camera), all inside the same loopback-only seal as the TPM run (`bwrap --unshare-net`). PHD2 gets a throwaway `HOME` with its own settings file and an INDI camera and mount profile; the user's `~/.PHDGuidingV2` is never read or written. The script drives PHD2 through its JSON-RPC (connect, loop, find star, calibrate, guide, one dither, stop) and keeps `wayfindinglib/simulation/test/fixtures/phd2_contract_events.jsonl` (every event; the first line names the PHD2 version, now 2.6.14dev1 commit 264a64d, the date and the script) and `wayfindinglib/simulation/test/fixtures/phd2_contract_guide_log.txt` (the guide log PHD2 wrote). The INDI camera simulator draws stars by calling the `gsc` catalog program, which is not installed, so the script puts a small fake `gsc` (a fixed lattice of made-up stars) on indiserver's path only. A run takes about 3 minutes. After a PHD2 upgrade, run `.venv/bin/python build/tpm/record_phd2_contract.py` again, then `.venv/bin/pytest wayfindinglib/simulation/test/test_phd2_contract.py -q -s`.

**The test** checks that: (a) every event type the app consumes (`GuideStep`) is in the recording and in the stand-in's output; (b) the fields the stand-in sends exist in real PHD2 with the same JSON types, the fields the app reads are always sent, and duration and direction come together and only for a pulsed axis (extra real fields are printed as information); (c) the app's guide log parser reads the stand-in's log and the real log the same way; (d) the events around a dither come in the same order (`GuidingDithered`, `SettleBegin`, guide steps, `SettleDone`); and the app's `GuideStep` parser accepts every step of both.

**What is not covered.** `StarLost` is not in the recording (the run never lost its star), so its shape is still from PHD2's documentation. Real PHD2 also sends `HFD`, `AvgDist` and `Settling` events, and `TotalFrames` and `DroppedFrames` in `SettleDone`, which the stand-in leaves out because the app does not read them.

## The simulated observatory

**Shared code.** The stand-ins live in `wayfindinglib/simulation/` (see its README), not in this folder. The app uses the same code for its **Simulated sky** and **Replay a night** observatory sources (Settings, Observatory). `run_tpm.sh` starts every stand-in through one program, `python -m wayfindinglib.simulation.observatory start` (which uses `SimulatedObservatory` and the supervisor, as the app does), and stops them with `... observatory stop`. It uses the same settings text (`wayfindinglib/simulation/settings.py`), the same `standin_ssh.sh` and `.night_replay` for the replay staging. The sealed network namespace, the port choice in `run_tpm.sh` and the seeded library are the harness's own.

`--observatory simulated` runs the same flow with a telescope that is "on": live status, a guiding stream, automatic download of new frames, frame analysis and the guide log pulls all run through the app's real code. Every piece is a local stand-in inside the throwaway folder, on a random free port:

| Piece | What it is | Settings that point at it |
| --- | --- | --- |
| INDI server | `indiserver` with `indi_simulator_telescope`, `_ccd`, `_focus`, `_wheel` and `_guide`. | `[Observatory.Telescope] hostname = 127.0.0.1`, `indi_port` |
| Stand-in PHD2 | `wayfindinglib/simulation/mock_phd2.py`. It plays PHD2's event server and replays the guide steps and settle events of a real guide log at the pace of the log. | `[Observatory.Guiding] phd2_location = local`, `phd2_port` |
| Stand-in telescope computer | The folder `standin/home` in the throwaway folder. Frames are in `Pictures/M 13/Light`, guide logs in `.local/share/kstars/guidelogs`. | `[Observatory.Telescope] remote_pictures_path` |
| Stand-in ssh | `wayfindinglib/simulation/standin_ssh.sh`, put first on the backend's `PATH`. | none (the app still runs `ssh` and `rsync -e ssh`) |
| Exposure driver | `wayfindinglib/simulation/exposure_driver.py`. It connects the simulators, points the mount at M 13 (a seeded target), takes exposures one after another (`--sim-exposure-seconds`, default 60) and writes a growing guide log. | none |

**Transfer without ssh.** The app copies frames and logs with one rsync runner (`StellarMateInterface._run_rsync`) that starts `ssh` and `rsync -e ssh` against the telescope host. The simulation keeps that code and changes only what `ssh` is. The backend's `PATH` starts with a folder whose `ssh` is `standin_ssh.sh`. It drops the ssh options and the host, and runs the command on this computer with `HOME` set to the stand-in folder. It accepts only the host `127.0.0.1` or `localhost`, refuses port forwards, and does nothing without `ASTROMETRICS_STANDIN_HOME`. There is no second transfer code and no sshd.

**The offline profile has a refusing ssh.** The offline profile puts the same script first on the backend's `PATH` with `ASTROMETRICS_STANDIN_SSH_MODE=refuse`. It prints `ssh: connect to host localhost port 22: Connection refused` and exits 255 for any command, so the offline run never starts the real ssh (which fails inside the sealed network on the system's ssh configuration).

**Automatic download needs the real INDI path.** `ASTROMETRICS_TESTING` makes the backend use its built-in simulator and switches automatic downloads off. The simulated profile leaves it unset, so the backend uses the real INDI client (in its own process), the real PHD2 client and the automatic download service. The offline profile still sets it.

**The guide log.** `wayfindinglib/simulation/guide_log_sample.txt` is the first 620 lines of an Ekos internal guider log written on 2026-10-03 (about 20 minutes of guiding, with calibration and one dither). It was copied from the app's own `ekos_logs` folder; the original was only read. The replay is the same every run. Only the dates in the headers are set to the time of the replay, so the app files the samples under the current night. A gap between two guide sections is cut to 10 s so a short run is not silent.

**Safety.**

- Before anything starts, `observatory start` checks the settings file (`wayfindinglib.simulation.observatory_guard`) and refuses it unless the telescope host is `127.0.0.1` or `localhost` (and resolves only to this computer), the INDI and PHD2 ports are the ports the script started, PHD2 is `local`, the picture folder is inside the stand-in folder, every library folder (image library, frames, stacks, wayfinding) is inside the throwaway folder and none is one of your real folders, and no IPv4 address other than 127.x appears anywhere in the file (so the real StellarMate's address is refused).
- The run refuses the ports 5000, 4400 and 7624 for every stand-in.
- After the backend has started, and again after the flow, `python build/tpm/seal_guard.py connections` lists the TCP connections of the backend and its child processes (`ss -tnp`). Any connection to an address beyond this computer stops the run with a message.
- `indiserver` listens on all network interfaces; it has no option for loopback only. It runs for the length of the run and serves only simulators.
- The user's library folders, databases and the real StellarMate are never touched. The script ends all stand-ins when it ends.

**After the flow** the script waits (at most the exposure time plus 150 s) until the first simulated frame has reached the library, so even a short run shows the download working. It fails if none arrives. `observatory_metrics.py` then writes `observatory-metrics.json`. `frame_to_library_seconds` is the inode change time of the library copy minus the write time of the source file.

The unit tests for these pieces run with `.venv/bin/pytest build/tpm/test`.

For exact behavior, read the scripts in this folder.
