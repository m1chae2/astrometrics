# Technical performance measures (TPMs)

This folder measures how fast the Astrometrics app is and how many computer resources it uses. One run gives a list of numbers, the technical performance measures (TPMs). A run compares each number with a saved baseline, so you can see when a change makes the app slower or heavier.

Status of the limits: Design estimate. The baseline comes from one run on one workstation, with headless Chrome and software graphics (before the real Electron app became the default target; Electron baselines start fresh, see "Setup and comparability"). The limits are starting points and have no validation-register entry. A baseline is only valid for the setup it was taken on (see "Setup and comparability").

## Safety rule

A run never touches your real app. It builds a throwaway world in a temporary folder:

- its own image library, wayfinding library, settings file, databases and cache folders, all inside that folder;
- its own backend on a random port, with the INDI simulator (a fake telescope and camera) and no real hardware (in the `simulated` and `replay` profiles, a local INDI server with simulators, see "The simulated observatory" and "The replay observatory");
- its own copy of the UI, served on another random port;
- its own loopback-only network (see "The network seal"): nothing in the run can reach the network, and nothing outside can reach the run.

A processing run on real data uses another second lock (see "Processing runs"). The scripts stop before starting if the backend port is 5000 (the real backend's port), if a simulator port is 4400, 5000 or 7624 (the ports of a real PHD2, the real backend and the usual INDI server), if the backend is not on this computer, or if any library path points outside the throwaway folder or into `~/Astrometrics` or `~/.local/state/astrometrics`. The flow clicks only controls that select or show data. It fails the run if the page sends an RPC call (a request to the backend) that looks like a hardware command. The folder is deleted at the end.

`ui/globalSetup.ts` starts its test backend the same way. `write_sandbox_config.py` repeats its settings file, with a few made-up equipment entries added.

## Library modes

| Mode | Data | Compared with the baseline? |
| --- | --- | --- |
| `simulation` (the default, `--library simulation`) | A read-only copy of your real library (your databases, targets, frames, stacks and catalogs) and the simulated observatory. The same world as the app's SIMULATED button. See "The simulation library". | Yes. It is the regression gate. |
| `synthetic` (`--library synthetic`) | A small fake library that `seed_library.py` builds. The same every run. For quick, headless and CI checks. | No. Every TPM gets the status `trend`, and the run can never update the baseline. |
| `real-snapshot` (`--real-snapshot`) | The older offline copy of your real databases and an overlay of your real library, not sealed. Kept for `--processing` on real data. | No. Same as synthetic. |

A fourth kind of run, `--processing <target>` (see "Processing runs"), measures the stack and analysis jobs instead of the display flow. It uses the synthetic library or `--real-snapshot`, never `--library simulation`.

Use the default mode to catch a change that made the app slower on your real amount of data. Use the synthetic mode when you want a fast check that needs none of your files. `--observatory offline`, `--observatory replay`, `--scale` and `--sky-catalog-copy` belong to the synthetic library, and the script refuses them with the default. Each `tpm.json` records its `mode`, its `observatory_profile` and a `data_size` block, so results are compared only within one mode and profile. The mode is part of the setup key, so the three modes never share a baseline. Baselines taken before this change were synthetic; take a new one with `--update-baseline`.

The observatory profile says how the throwaway backend is connected to a telescope. `--observatory simulated` is the default with the simulation library. `--observatory offline` (the default with the synthetic library) names this computer as the telescope host and a port nothing listens on, turns automatic downloads off and sets PHD2 (the guiding program) to local on port 1, where nothing listens (so a real PHD2 on this computer, port 4400, is never touched). The backend never contacts your telescope computer, INDI or PHD2. `--observatory simulated` gives the backend a telescope that is "on", made only of local stand-ins (see "The simulated observatory"). `--observatory replay` uses the same stand-ins, but they play the last minutes of a real night, 2026-10-03, after the app has already downloaded the rest of that night (see "The replay observatory"). The baseline stores its profile and is used only for a run with the same profile, because the setup key includes it.

## What the flow runs in

The default target is the real Electron app, the one users run. `run_tpm.sh` builds the production UI, starts Electron with its own user-data folder, `ASTROMETRICS_UI_DIST` (so the window loads the UI from the private `app://` address, as the packaged app does) and a DevTools port on 127.0.0.1 only. `flow.mjs` attaches to the window with `puppeteer.connect` and runs the same displays and actions as before. `--browser brave|chrome` keeps the old flow, which launches a headless browser against the preview server. Use it for the web and phone client, and for the Lighthouse audits that need a normal page navigation.

**What is measured where.**

| Measurement | Electron (default) | Browser (`--browser`) |
| --- | --- | --- |
| Display switch and click times, time to ready, JavaScript heap, RPC calls per display, idle polling | Yes (the flow's own timing) | Yes |
| Interaction to Next Paint and layout shift | Per Lighthouse timespan step, as in a browser; the whole-page layout shift also comes from a `PerformanceObserver` | Yes |
| Total blocking time per step | Yes (Lighthouse timespans work on an attached page) | Yes |
| Planetarium frame rate and the DevTools trace of the pan and zoom | Yes | Yes |
| Console errors, failed requests, error toasts | Yes, from the flow and from the app's start-up and idle time | Yes, from the flow only |
| Lighthouse navigation audit of the page load (the "Load app" navigation step: its resource summary and request count) | No. The page is `app://`, and Lighthouse navigates only to `http(s)`, so the load is a timespan around a plain navigation | Yes |
| Electron process memory and CPU, native notifications, the main process's GPU report | Yes | Only with `--electron` |

Lighthouse audits that rest on a navigation (for example the resource summary and the request count of the load) are therefore browser-only; the rest of the Lighthouse report still comes from the timespan steps.

**No throttling.** `electron_run.mjs` sets `ASTROMETRICS_TPM_HARNESS=1`. Only then `electron/harness_mode.js` turns off `backgroundThrottling` for the app's windows and shows the main window without taking focus. It also passes `--disable-renderer-backgrounding`, `--disable-backgrounding-occluded-windows` and `--disable-background-timer-throttling` to Chromium. A normal start of the app does none of this.

**The window.** The reference setup is the laptop panel alone, so a baseline run renders on the real panel with the real graphics card. The default run opens the Electron window on your real display with `showInactive()`, so it does not take the keyboard focus. Leave the window alone until it closes. It is not moved off screen or put behind other windows: a covered or off-screen window can be skipped by the window system's compositor, and then the frames you measure are not the frames a user sees. `--visible` is accepted and does nothing (it is the default).

**`--headless`.** For quick smoke checks only (for example by an AI agent). Electron runs inside `xvfb-run`. `WAYLAND_DISPLAY` is removed and `--ozone-platform=x11` is forced, because Electron otherwise picks its Wayland backend from `WAYLAND_DISPLAY`, ignores xvfb's `DISPLAY` and opens the window on your real desktop (this happened in a first trial). The page then uses software graphics. The invisible screen and the window are 1920 x 1080 (`ASTROMETRICS_TPM_WINDOW_SIZE`, read by `electron/harness_mode.js`): at the app's own 1280 x 800 the header's session chip covers the display menu button while a camera exposes, and the flow could not switch displays. The setup key says `electron-headless`, so these results never share a baseline with a real-display run. Either way the WebGL renderer string is recorded in the setup record (`gpu_renderer` in `tpm.json`), so a result says what drew it.

**The graphics card.** `--gpu auto|intel|nvidia` applies to Electron exactly as it does for the app (the launch plan comes from `electron/gpu_choice.js`), and the run stops if the window's WebGL renderer does not name the card asked for.

## The Planetarium's stars

An empty sky would make the sky-map frame-rate and frame-time TPMs meaningless, so every flow run (not a processing run) copies your deep-star (Gaia) catalog into the throwaway library before the backend starts (`copy_deep_catalog.py`). It finds the library folder in your real settings file (read only), opens the real `catalogs/deep_star_catalog.db` read only, and copies it with the SQLite backup call, so a write-ahead log cannot be half copied. Nothing is linked or opened in place. If the file is missing, the run prints one line and goes on with the stars the seeded library has. The star count and file size go into the setup record (`deep_catalog` in `setup-fingerprint.json` and `tpm.json`), so a baseline says what load it ran with. They are not part of the setup key. The bright stars come from the bundled Hipparcos extract; the flow records how many stars each Planetarium source call returned (`planetariumSources` in `flow-metrics.json`) and saves `planetarium-sky.png`, a screenshot of the sky map.

## Run it

Install the Node packages once (`npm install`). The flow runs in the real Electron app by default, in a window on your real display. Only `--headless` needs `xvfb-run` (package `xvfb`). For a browser run (`--browser`) have a Brave, Chrome or Chromium browser; set `CHROME_PATH` if the script cannot find one.

```bash
build/tpm/run_tpm.sh                    # Electron app in a window on your display, 3 laps, 3 idle minutes first, 30 s idle at the end
build/tpm/run_tpm.sh --laps 5           # more laps
build/tpm/run_tpm.sh --headless --laps 1 --idle-minutes 0 --idle-seconds 5   # a quick smoke check, no window (never a baseline)
build/tpm/run_tpm.sh --browser brave    # the browser flow instead (Brave; or --browser chrome)
build/tpm/run_tpm.sh --browser brave --electron   # the browser flow, then a second lap in Electron
build/tpm/run_tpm.sh --gpu intel --laps 3   # real Electron window on the Intel graphics (leave the window alone)
build/tpm/run_tpm.sh --gpu nvidia --laps 3  # the same on the NVIDIA card
build/tpm/run_tpm.sh --browser chrome --gpu nvidia    # browser flow on the NVIDIA card (invisible)
build/tpm/run_tpm.sh --require-power-profile performance   # refuse unless that profile and AC power are active
build/tpm/run_tpm.sh --cold             # drop the file cache first: a cold start (see "Cold start")
build/tpm/run_tpm.sh --preflight        # only check that no upload, backup or real backend is running
build/tpm/run_tpm.sh --update-baseline  # save this run as the new baseline
build/tpm/run_tpm.sh                    # the default: a read-only copy of your real library and the simulated observatory
build/tpm/run_tpm.sh --library synthetic   # the made-up library: quick, headless and CI checks, never a baseline
build/tpm/run_tpm.sh --sim-exposure-seconds 20   # shorter simulated exposures
build/tpm/run_tpm.sh --library synthetic --observatory replay    # the last minutes of the night of 2026-10-03, after an end-of-night preload
build/tpm/run_tpm.sh --library synthetic --observatory replay --replay-speed 2   # twice as much night in the same minutes
build/tpm/run_tpm.sh --library synthetic --scale 5   # synthetic library with 5 times the targets and frames
build/tpm/run_tpm.sh --library synthetic --sky-catalog-copy   # synthetic library, but with your star-catalog caches (see below)
build/tpm/run_tpm.sh --real-snapshot    # the older offline copy of your real data (see below)
build/tpm/scaling.py                    # table of key TPMs against data size (use .venv/bin/python)
build/tpm/run_tpm.sh --processing "M 31"   # a stack and analysis run instead of the display flow (see "Processing runs")
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
| `resource-samples.jsonl` | Every second: CPU, memory and disk bytes of the backend, of every program under it (grouped), and of Electron and the browser when they run (see "Resource TPMs"). |
| `processing-metrics.json`, `processing-plan.json`, `processing-job.log`, `real-library-check.json` | Only with `--processing`: the stack job's stages and the analysis steps with their resource use, the frames that went in, the stack job's log, and (real data) the before-and-after check of the real library. |
| `backend.log` | The throwaway backend's log. |
| `electron-output.log` | Everything Electron printed (stdout and stderr), and a line when its process exits. Read it first when the run says Electron did not open its DevTools port. |
| `electron-metrics.jsonl` | Electron process samples, when the run used Electron (the default target, or `--browser` with `--electron`). |
| `electron-startup-errors.json` | Console errors, failed requests and toasts seen from Electron's start-up through its idle time. |
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
| GPU requested (`--gpu`), GPU used, WebGL renderer string | the WebGL renderer of the profiled page (the Electron window's own WebGL when the flow ran in Electron, otherwise the browser flow's); `info.gpu` holds requested, used, renderer and source. In Electron `electron_gpu` (`app.getGPUInfo`, the last report) is shown next to it. |
| NVIDIA state during the run | `nvidia-smi` every 2 seconds: peak and mean utilization and memory, power states seen |
| External tool versions | Only with `--processing`: Siril and GraXpert from `--version`, Cosmic Clarity by file size and date (see "Processing runs") |
| Mode, observatory profile | as before |

Why these matter: the dock changes which screens the Intel graphics must drive, and a 120 Hz screen changes how many frames the page draws. The power profile and the CPU governor set how fast the CPU runs. The graphics card (Intel, NVIDIA or software) changes the Planetarium numbers the most. On battery the computer is slower still. Reading `nvidia-smi` can keep the NVIDIA card awake, so a run with `nvidia-smi` installed does not show a fully idle card.

**The setup key.** A short hash (`setup_key`) of the CPU model, the displays with their resolutions, AC state, power profile, the kind of graphics card used (`nvidia`, `intel`, `amd` or `software`, from the WebGL renderer of the profiled window), the mode, the observatory profile, the flow target (`electron`, `electron-headless`, `brave` or `chrome`) and (only for a `--cold` run) the cold flag. Refresh rate, RAM and kernel are recorded but do not change the key. `tpm_baseline.json` holds one baseline per key under `setups`. Because the flow target is in the key, an Electron baseline never mixes with a browser one, and baselines taken before the target was added (which have no target in their key) do not match any run now: take new ones. The processing scenario has no page, so its key (which holds the tool versions) does not include a target and means what it did.

- A run whose key has a baseline is compared with it.
- Any other run prints "DIFFERENT SETUP: no baseline for this setup key, not compared", and every TPM gets the status `unmatched` (a synthetic or real-snapshot run gets `trend`, because only a simulation run is compared). Run `--update-baseline` to start the baseline for the new setup. It writes only that key's entry and leaves the others alone.
- The first file had one baseline and no keys. It was filed under the key of this workstation's setup (AC, balanced, docked with two screens, headless Chrome, software graphics). A file in the old format is still read and compared as before, with a note in the header.

**Choosing the graphics card.** `--gpu auto|intel|nvidia`. It applies to the Electron window and to the browser flow. The Electron window loads the built UI from the private `app://` address, as the real app does; the browser flow uses the preview server over HTTP.

- `auto`: today's behavior. The card is only recorded.
- `nvidia`: sets the PRIME render offload variables (`__NV_PRIME_RENDER_OFFLOAD=1`, `__GLX_VENDOR_LIBRARY_NAME=nvidia`, `__VK_LAYER_NV_optimus=NVIDIA_only`) for the launched process and starts it with `--ozone-platform=x11 --use-gl=angle` plus an ANGLE backend. That is XWayland with ANGLE on the system graphics driver. The Chrome (Brave) flow uses `--use-angle=gl-egl` (the system OpenGL ES driver). Electron uses `--use-angle=gl` (desktop OpenGL through GLX), because Electron's own Chromium cannot find an EGL configuration on the NVIDIA card and quietly draws on Intel. Both choices come from `electron/gpu_choice.js`, the one place the app launcher and this harness share. (ANGLE's Vulkan backend gave no WebGL here, and without these switches headless Chrome uses software SwiftShader.)
- `intel`: removes those variables, so the Intel graphics (the default card on this laptop) is used, with the same Chromium switches.
- After the page loads, the WebGL renderer string must name the card asked for. If it does not (for example Intel when you asked for NVIDIA), the run fails with a `GPU CHECK FAILED` message instead of measuring the wrong card. A `--headless` Electron run (`xvfb-run`) has no graphics card, so `--gpu intel` or `--gpu nvidia` fails there; use the default real-display run. A trial with headless Brave on this computer gave the Intel UHD 630 for `intel` and the GeForce GTX 1650 for `nvidia`. The visible Electron path (`--gpu nvidia` on the real display, Electron variant of the switches with PRIME offload) was verified on this computer: the Electron window's WebGL reported the GeForce GTX 1650. The run still checks the renderer every time. `flow.mjs` and `electron_run.mjs` import `electron/gpu_choice.js` directly.

**Requiring a power profile.** `--require-power-profile <name>` refuses to start unless `powerprofilesctl get` prints that name and an AC adapter is online. It never changes the profile. To set it yourself: `powerprofilesctl set performance`.

**Recommended fixed setup** for baselines: on AC power, power profile `performance`, docked with the same displays every time, the lid state the same, no other heavy program running, and one run per graphics choice. Take the baseline with `build/tpm/run_tpm.sh --require-power-profile performance --update-baseline`, and a second baseline with `--gpu intel` and another with `--gpu nvidia`.

## The simulation library

`catalog_failures` (TBD, no threshold) counts the Planetarium's `planetarium:get_catalog_sources` calls whose reply lists a driver in `failedCatalogs`. `flow.mjs` records `failedCatalogs` for each call under `planetariumSources`. A call to `planetarium:get_sources` has no such field and is not counted. With no call that carries the field, the TPM is not measured.

`run_tpm.sh` (the default, `--library simulation`) uses the app's own code for the library part. It is the world the app's SIMULATED button builds (`simulator/library_copy.py` and `observatory.py`; `simulator/launch.py` calls them; the harness builds the world itself and starts the backend directly, so the backend cannot change its source in a TPM run). `simulation_library.py prepare` does, in order:

1. **Databases.** `astrometrics.db`, `astrometrics_log.db`, `wayfinding.db` and the deep-star catalog are copied with SQLite's backup call, and the paths inside that name your real frames and stacks folders are rewritten to the mount point. Your real settings are copied except the library, telescope, guiding, transfer and solver sections. The harness then copies the deep-star catalog a second time with the backup call alone and replaces the app's copy. A catalog download that runs for hours can write the file while the app's code copies it as a plain file; the harness never does that. The catalog's star count grows while a download runs, so counts differ between runs.
2. **Overlay.** `fuse-overlayfs` mounts your library (frames, stacks, previews) read only below, with the upper layer in the throwaway folder, at `<throwaway>/library/frames`.
3. **Checks.** The app's guard refuses the world if any setting points beyond this computer or at a real folder.

Your star-catalog caches under `~/.cache` are not copied (the app's simulated world does not copy them either), so the sky map draws the bundled bright stars and the deep-star catalog. The deep-star catalog is only what you have downloaded so far.

**Why the copy is made outside the seal.** The overlay is a FUSE mount, and only an ordinary process can make it. So the outer `run_tpm.sh` makes the throwaway folder and the copy, then starts the sealed script as a child. The seal is `bwrap --unshare-net` as before. `bwrap` binds the folder that holds the throwaway folder, and the bind carries the mount along. The sealed backend then reads and writes the overlay as a normal folder. The real library is also bound read only in the seal (the whole computer is), so even a stray write to a real path fails. This was checked on this computer: a file read and a file written through the overlay from inside `bwrap --unshare-net --unshare-pid`, with `lo` as the only interface, and the write landed in the upper layer. The outer script unmounts the overlay (with the copy's own `teardown`) and deletes the folder only after that; it never deletes through a mount. If you kill the outer script with `kill -9`, the overlay stays mounted: run `fusermount3 -u <folder>/library/frames`.

**Where the folder is.** The system lets `fusermount3` mount only under your home folder, `/tmp`, `/mnt`, `/media` and `/run/user`. With `--cold` and a memory-backed `/tmp`, this mode uses `~/.cache/astrometrics-tpm-runs` (a disk) instead of `/var/tmp`. Then the seal replaces its read-only `/tmp` with an empty private one (the host's display sockets and lock files show through), because Xvfb (headless Electron) must write a lock file in `/tmp`; without that, `--cold --headless` ended with "Electron did not open its DevTools port".

**Proof that the real library is untouched.** The outer script lists every file of the real library (size and change time) before and after, and writes `real-library-check.json` into the results. Any difference fails the run: it prints an error and exits with 1, even if the flow itself passed. The one exception is a change to a database's `-shm` file (your own app touches it just by being open), which is listed as information only. The run itself cannot write there, so a difference most likely comes from your own app or an upload, but the run does not claim that. `overlay-writes.json` lists what the backend wrote into the overlay, so the real library is not in it.

**Setup record.** The setup record (`setup-fingerprint.json`, `info.setup`) gets a `library` block (copy time, database sizes, targets, measured frames, deep-catalog stars) and the `deep_catalog` block. They are facts about the data, not part of the setup key. `info.data_size` also counts light frames in the overlay.

**Cold runs.** `--cold` drops the page cache after the copy is made and before the flow, so the freshly copied databases are cold too. The walk that picks the files to drop skips mount points, so it never enters the overlay; the real stacks and previews are dropped through `--real-stacks` as before.

**Stand-ins.** `simulation_library.py start` starts the INDI simulators, the stand-in PHD2 and the exposure driver in the world that is already built. The app's own `start` command prepares the world again, and that would unmount the overlay.

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
| `backend_tree_cpu_time`, `backend_tree_peak_rss` | CPU seconds and peak resident memory of the backend and every program under it (pool workers, the INDI worker, Siril, GraXpert, the denoise tools). Unlike `backend_cpu_time` and `backend_peak_rss`, which count the backend process alone. See "Resource TPMs". | Lower |
| `disk_read_mb[<group>]`, `disk_write_mb[<group>]` | Megabytes the kernel read from and wrote to storage for a process group over the run (`/proc/<pid>/io`). Groups: `backend`, `backend_workers`, `indi`, `siril`, `graxpert`, `denoise_tools`, `backend_other`, and `electron` and `browser` when they run. | Lower |
| `disk_read_mb_per_lap[<group>]`, `disk_write_mb_per_lap[<group>]` | The same for one lap of the display flow (the median over the laps). | Lower |
| `nvidia_util_percent_mean`, `nvidia_util_percent_peak`, `nvidia_memory_mb_peak` | How busy the NVIDIA card was, and the memory in use on the whole card (the desktop included), from `nvidia-smi` every 2 seconds. Empty without an NVIDIA card. | Lower |
| `processing_total_seconds`, `processing_stage_seconds[<stage>]`, `analysis_seconds[<step>]` | Only with `--processing`. Wall time of the stack plus the analysis, of each stacking stage and of each analysis step (see "Processing runs"). | Lower |
| `processing_stage_cpu_seconds`, `_peak_rss_mb`, `_disk_read_mb`, `_disk_write_mb`, `_nvidia_util_percent_mean`, `_nvidia_memory_mb_peak` (each `[<stage>]`), `analysis_cpu_seconds[<step>]`, `analysis_peak_rss_mb[<step>]` | Only with `--processing`. What the backend's process tree and the NVIDIA card used in that stage or step. | Lower |
| `processing_frame_count`, `processing_failed_jobs` | Only with `--processing`. The number of frames stacked (the input size) and the number of jobs that failed or timed out. | information |
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

Every `tpm.json` has an `info` block with `webgl` (the vendor and renderer strings the page reports, and whether they look like a real graphics card), `gpu` (the graphics card requested, the card used, the renderer and where it came from: the Electron window's WebGL when the flow ran in Electron, otherwise the browser flow's) and, when Electron ran, `electron_gpu` (what the Electron main process says about the GPU and its features, last report). The setup key's graphics field comes from `info.gpu`. A renderer name with `SwiftShader` or `llvmpipe` means software graphics. The TPMs added with the on-demand zoom measures have no baseline yet (status `new`). The profile comes from the Electron window when Electron ran, otherwise from the browser flow.

## Error TPMs

The user's rule is "no errors in the web browser console" and no error notifications at start-up. Five TPMs count what breaks it. A sixth, `simulator_chip_missing`, is described at the end of this section. Each is a count with a Confirmed limit of 0 (confirmed as a user requirement on 2026-11-06), so one error fails the run. When a limit is breached, `compile_tpm.py` prints every offending message under `OFFENDERS for <TPM>`, and `tpm.json` keeps them in `info.errors`.

| TPM | What it counts | Where it comes from |
| --- | --- | --- |
| `console_errors` | Console messages of level error and uncaught page errors, with the message text and its source URL. | Puppeteer's `console` and `pageerror` events (`error_watch.mjs`) on the Chrome flow and on the Electron window. For Electron a second connection (`electron_run.mjs`, `electron-startup-errors.json`) also watches the app's start-up and idle time, so the errors of the first load are counted, not only those of the flow. |
| `failed_requests` | Responses with status 400 or more, and requests that failed, with the URL and status. | Puppeteer's `response` and `requestfailed` events. A request the browser cancelled (`net::ERR_ABORTED`) is listed apart as `aborted_requests` and does not count. The flow itself aborts no request. |
| `error_toasts` | In-app toasts of kind error or warning, with text and source. | A listener for the `astrometrics:toast` window event (`ui/common/utils/emitToast.ts`) that the flow installs before the app starts. |
| `desktop_notifications` | Native notifications the page asked Electron to show, with title and body. | `electron/perf_metrics.js` listens next to the app's own `show-notification` handler when `ASTROMETRICS_PERF_METRICS` is set. It counts the requests before the app's cooldown and its checks. Only an Electron run measures it; a plain browser cannot send one, so a Chrome-only run leaves it empty. |
| `backend_error_log_lines` | Lines at ERROR or CRITICAL level in the throwaway backend's `backend.log`, from the backend's start to its end, and the web server's own `ERROR:` lines. | `backend.log` in the results folder. Warnings and the lines of a traceback after its first line are not counted. |

`simulator_chip_missing` guards the user's rule that a simulated night is always visibly marked (confirmed as a user requirement on 2026-11-07). In a simulation-mode run (`--library simulation`, which runs the app's simulated observatory), `flow.mjs` waits up to 120 seconds after the app is ready for the header's `.header__source-chip` element (the chip says `SIMULATOR STARTING`, then `SIMULATOR`) and records what it saw in the `sourceChip` block of `flow-metrics.json`. The TPM counts the flows that did not show the chip, and its Confirmed limit is 0. A run on the real telescope, or on the synthetic library with the offline profile, has no chip to look for: the flow records `expected: false` and the TPM is not produced. `run_tpm.sh` passes `--expect-simulator-chip 1` for `--library simulation` and `0` in every other mode. The harness builds the simulation world with the simulator layer, which writes the launch identity (a `[Launch]` section with `label = "SIMULATOR"`, `offline = true` and `disposable_library = true`) into the settings file the backend starts with. The backend reports it as `launch` in `observatory:get_source`, and the header chip is drawn from its label. Until the UI reads `launch.label` (the UI part of phase L1 in `documentation/plans/SIMULATOR_FROM_REAL_DATA_PLAN.md`), the chip still shows only for the app's own simulator source, so a harness run, which pins its own world, counts this TPM as breached until then.

A failed image load shows up twice, as a failed request and as the console error the browser prints for it. Counts add up over the Chrome flow, the Electron flow and the Electron start-up, so a run with a browser flow and `--electron` can show the same fault more than once. The limit is 0, so only the list matters. Toasts that the app shows before the flow attaches to the Electron window are not seen as toasts (an error toast also writes a console error, which the start-up watcher does see).

## Visible GPU profile

The browser flow and the `--headless` Electron run use software graphics. To measure on the real graphics card, run:

```bash
build/tpm/run_tpm.sh --idle-minutes 0 --laps 1
build/tpm/run_tpm.sh --gpu intel --laps 3    # force the Intel graphics
build/tpm/run_tpm.sh --gpu nvidia --laps 3   # force the NVIDIA card
```

An Electron window opens on your real desktop for the run, so leave it alone until it closes. It uses its own user-data folder, lock and settings inside the throwaway folder, so it cannot touch your real app. It keeps `DISPLAY` and `WAYLAND_DISPLAY` from your session and passes `--ozone-platform-hint=auto`. Read the `webgl_electron_window` line in the printed header: if it still says software, try `--ozone x11` (XWayland) or `--ozone wayland`, or choose the card with `--gpu` (which replaces `--ozone`; see "Setup and comparability").

## Star density (`--sky-catalog-copy`)

The fake library has one star, but the real Planetarium draws stars from the Gaia cache (about 1.2 million stars). `--sky-catalog-copy` copies `catalog_cache.db`, the Gaia spectra cache and the deep-star catalog into the throwaway folder (read only at the source; the run prints the size and refuses over 5 GB), so the throwaway backend serves the same star density. `--real-snapshot` includes this copy.

## Electron

`run_tpm.sh` (Electron is the default target) starts the Electron app on your real display (`--headless` uses `xvfb-run` instead) in development mode, pointed at the throwaway backend, with the built UI served from `app://`. It sets `ASTROMETRICS_PERF_METRICS` to a file. When this variable is set, the main process also writes a `gpu_report` line (`app.getGPUFeatureStatus()` and `app.getGPUInfo('basic')`) at start-up and again 20 seconds later (the first one comes before the window has rendered and can show software features; `info.electron_gpu` is the last one, and the card that counts is the one the Electron window's WebGL reports), writes one `desktop_notification` line (title and body) each time the page sends the `show-notification` request, and samples `app.getAppMetrics()` every 2 seconds and appends one JSON line per process (time, process type, process id, CPU percent, working set and peak working set). The app does nothing when the variable is not set. The script lets the app idle for `--idle-minutes`, then runs the flow in its window over the Chrome DevTools port. With `--browser` and `--electron` this is a second lap whose files go to `electron-flow/`; as the default target its files go straight into the results folder.

Electron's own folder, lock and desktop settings point inside the throwaway folder, so a running copy of the real app is not affected. The runner launches Electron itself with a DevTools port and drives it with the same Puppeteer code as the Chrome flow, instead of Playwright's `_electron` launcher, so one flow serves both. `--headless` needs `xvfb-run` (package `xvfb`).

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
build/tpm/run_tpm.sh --gpu nvidia --laps 3
build/tpm/run_tpm.sh --gpu intel --laps 3
```

Why this order: the first run after the reboot is the only one that is truly cold, so it goes first and is the only run that gets `--after-reboot`. Later `--cold` runs drop the cache by `fadvise` and can be compared with it. The cold run on NVIDIA has its own key; the warm NVIDIA and Intel runs have their own keys too (the graphics card is part of the key). A real-snapshot run is never compared and never updates the baseline.

## Update the baseline on purpose

The baseline file `tpm_baseline.json` holds one block per setup key (see "Setup and comparability"), and each block has one entry per TPM: the baseline value, a watch ratio (default 1.25), a breach ratio (default 1.5) and a slack, a small absolute allowance in the TPM's unit so that tiny values do not trip the limits on noise. A value past `baseline * watch_ratio + slack` is on **watch**; past `baseline * breach_ratio + slack` it is **breached**. For `planetarium_fps_panning` higher is better, so the ratios divide instead.

The baseline never changes by itself. To accept a new normal (for example after a deliberate feature that costs memory):

1. Run `build/tpm/run_tpm.sh` twice and confirm the numbers agree.
2. Say in the pull request why the number moved.
3. Run `build/tpm/run_tpm.sh --update-baseline` (on the setup you want the baseline for) and commit the new `tpm_baseline.json`.

To change one limit only, edit its `watch_ratio`, `breach_ratio` or `slack` in the file.

## Resource TPMs

Every run measures what the backend and the programs it starts cost, not only the backend process. `resource_usage.py` reads `/proc` once a second (twice a second in a processing run) and follows the whole process tree under the backend. When they start, Electron (`electron.pid`) and the Chrome flow's browser (`browser.pid`) are followed too, as groups of their own.

| Read from | What it gives |
| --- | --- |
| `/proc/<pid>/io` | `read_bytes` and `write_bytes`: bytes the kernel read from and wrote to storage for the process. These are the disk TPMs. `rchar` and `wchar` (bytes passed to read and write calls, file cache and pipes included) are kept in `info.resources` for information only. |
| `/proc/<pid>/stat` | CPU time (`utime` plus `stime`), and the CPU time of the process's finished, collected children (`cutime` plus `cstime`). The start time tells a reused process number from the old one. |
| `/proc/<pid>/status` | Resident memory now (`VmRSS`) and its peak (`VmHWM`). |

**Groups.** A process is sorted into a group by its name and command line: `siril` (also the Flatpak and sandbox processes under it), `graxpert`, `denoise_tools` (Cosmic Clarity, StarNet), `indi`, `backend_workers` (pool workers, which are forks of the backend or `multiprocessing` processes), `backend_other` and `backend` (the root). A process the rules do not know takes its parent's group.

**Children that end between two samples.** The disk and CPU counters grow only. A ledger keeps the last values seen for every process, so a finished child stays in its group's sum. The tree CPU time uses the kernel's own total instead: a parent's `cutime` and `cstime` already include its collected children, so the tree's CPU time is the sum, over the live processes, of their own time plus their collected children's time. Nothing a child did is lost or counted twice, even if it ended between samples. A child handed to `init` (its parent ended first) is followed while it lives and added from the ledger when it ends.

**What can be missed.** The disk bytes a process moves after its last sample and before it ends are lost, so the disk numbers of a very short program (a Siril call of a second) are a lower bound. The peak memory is the largest sum over the samples: a peak shorter than the sampling interval can be missed (`info.resources.trees.backend.peak_sum_mb`, the sum of each process's own peak, is an upper bound). The numbers describe the kernel's accounting of storage requests, not the drive: a read answered from the file cache is not counted in `read_bytes`.

**NVIDIA.** The `nvidia_*` TPMs reduce `nvidia-samples.jsonl` (utilization and memory from `nvidia-smi`, every 2 seconds). The memory is what the whole card has in use, so a desktop that draws on the card adds to it.

**Intel graphics.** The load of the Intel graphics is not measured. Reading it needs `intel_gpu_top` or the `perf` counters of the i915 driver, and both need elevated permissions (root, or a changed `perf_event_paranoid` or capabilities). The harness makes no change to the system, so there is no TPM for it. The Intel card's use shows only indirectly, in the frame-rate TPMs of a run with `--gpu intel`.

## Processing runs

`run_tpm.sh --processing <target>` measures the heavy work of the app instead of the screens. It asks the throwaway backend, through the same RPC calls the Image Processing display uses, to stack a target's frames and then to analyze it, waits for each job, and records the time, CPU, memory, disk use and NVIDIA use of every stage. There is no browser and no Electron.

```bash
# The fixed comparison scenario: M 31, ZWO ASI 533MM Pro frames only.
build/tpm/run_tpm.sh --processing "M 31" --processing-camera "ZWO ASI 533MM Pro"          # a small made-up library, a few minutes
build/tpm/run_tpm.sh --real-snapshot --processing "M 31" --processing-camera "ZWO ASI 533MM Pro"   # your real frames, through the overlay
build/tpm/run_tpm.sh --real-snapshot --processing "M 31" --processing-dry-run              # find the frames and check the files, start no job
```

**When to run it.** Before merging a change to stacking, quality measurement or analysis, and once a week on the real data to see the trend. Use the same target and camera every time, so the numbers can be compared (the number of frames and a hash of their paths are in `processing-plan.json` and `info.processing.plan`). `--processing-steps photometry,frame_measurements,stack_comparison` chooses the analysis steps (all three by default); `--processing-camera` chooses the camera.

**What it runs.**

1. `target:get_files` lists the target's frames. Frames of the chosen camera are kept (spectroscopy frames are left out) and sorted by path. The script stops with exit code 3 if there are none or a file is missing.
2. `processing:stack` (the Process button) starts the stack job. The script polls `processing:get_job` every half second.
3. The job log (`processing:job_log_tail`) holds a line `Stage N of 7: ...` at the start of each stage, with a time to the millisecond. A stage runs to the next stage's line; the last one to the end of the job. The stages are `choose_frames`, `screen_frames`, `prepare`, `stacking`, `summarize`, `quality` and `preview`. A skipped stage gives no TPM.
4. The analysis steps then run one after another, each as a button of the display does: `photometry` (`analysis:analyze_image` on the new stack, the Analyze Target button), `frame_measurements` (`quality:record_frame_measurements`) and `stack_comparison` (`quality:measure_stack`). The last two do not return their job's number, so the script waits until every job that is new for the target has ended.
5. For each window (stage or step) the CPU time, peak memory and disk bytes are cut out of the half-second samples, and the NVIDIA rows (every 2 seconds) inside the window are summarized. A window shorter than 2 seconds has no NVIDIA value.

**The stack really runs.** The throwaway settings file sets `skip_unchanged_stacks_enabled = "false"`. Without it the app keeps a stack whose frames, settings and code are unchanged, and a second run would skip the stacking stage and measure nothing. The setting is in the throwaway file only.

**Heavy-work slot.** A processing run takes one of the shared heavy-work slots first, with `heavy()` from `build/agent_tools/common.sh`, the same lock `check.sh` uses (`ASTROMETRICS_AGENT_SLOTS`, default 2, locks in `ASTROMETRICS_AGENT_LOCK_DIR`). It prints `[waiting for a heavy-work slot]` and starts when one is free. This holds for the made-up library too.

**Throwaway folder in the home folder.** Siril runs in a Flatpak sandbox with its own `/tmp` and `/var/tmp`, and it shares the home folder. So a processing run puts its throwaway folder in `~/.cache/astrometrics-tpm-runs` (unless `TMPDIR` is set) and is not sealed (the seal's `bwrap` forbids the user namespace Flatpak needs). The offline profile still applies: no telescope host, PHD2 or INDI.

**The made-up library.** `seed_library.py` normally draws a different random sky in every frame, which no stacker can register. With `TPM_STACKABLE_TARGET` set (the script sets it to the processing target) the six light frames of that target show the same 60 stars, moved by up to 4 pixels, with fresh noise. The calibration frames are the usual ones. The three program commands (Siril, GraXpert, Cosmic Clarity) are copied from your settings file, read only, so the made-up run uses the programs the real one uses. A made-up library is small (256 by 256 pixels, six frames), so its times say whether the whole pipeline works and give a regression signal for the fixed costs; the real data shows the scaling.

**Tool versions.** The setup fingerprint records the version of each external tool: Siril and GraXpert from their `--version`, Cosmic Clarity (which has none) by the size and date of its program. For a processing run these versions are part of the setup key, so an upgrade of a tool starts a new baseline instead of looking like a regression of the app. (A display-flow run does not use these tools, and its key does not change.) The key also holds the scenario, so a processing run is never compared with a flow run.

**Real data, safely.** A processing run on your real data works like `--real-snapshot` (copies of the three databases, the catalog caches, a copy-on-write overlay of the library) with these differences and checks:

- The `bwrap` lock of the plain real-snapshot mode cannot be used: Siril runs through Flatpak, Flatpak makes a user namespace, and the system refuses that inside the `bwrap` one (`apparmor_restrict_unprivileged_userns`). Landlock, the other lock that needs no root, was tried and also fails, because it forbids the mounts Flatpak makes.
- Instead, `prepare --mountpoint` rewrites every stored path that names the real library, in the database copies and in `calibration_frames.json`, to the overlay's mount point under `~/.cache/astrometrics-tpm-mounts`, and the settings name the mount point as the frames and stacks folders. The backend never sees a real path. All writes land in the overlay's upper folder (`overlay-writes.json` lists them) or in the throwaway folder.
- `real_snapshot.py prepare` first rewrites the paths under the real library to the overlay, then moves any other stored path (for example an old stacks folder that no longer exists) to `<throwaway folder>/outside/<original path>`, using `copy_path_guard.repoint(..., mirror_outside=True)`. It prints how many it moved.
- `real_snapshot.py guard` runs before the backend starts, with the check `build/agent_tools/realrun.sh` uses (`copy_path_guard.py`). It refuses the run if any absolute path stored in a database copy is outside the throwaway folder and the overlay, or the settings do not name the overlay.
- `real_snapshot.py fingerprint` records the size and change time of every file of the real library (and of the three databases) before the backend starts, and again after it stops. `verify` writes `real-library-check.json` and fails the run if a file of the real library was added, removed or changed. A database that changed is listed but does not fail the run, because your own app may be running.
- The result is a trend: every TPM has the status `trend`, and the run cannot update the baseline.

Siril's own Flatpak data folder (`~/.var/app/org.siril.Siril`) is written by Siril as always; it is not part of the library.

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

1. *Window.* `simulator/night_replay.py` picks the busiest window late in the night (the last 40% of the guide log). A window scores 3 points for each frame that arrives, 4 for each settle after a dither and 1 for each lost-star row (at most 4). The default window is 300 seconds. For 2026-10-03 it is 2026-10-04 08:48:36 to 08:53:36 UTC: three frames of NGC 7331 (115 to 117), a dither with its settle, 85 guide steps. Run `.venv/bin/python build/tpm/night_replay.py choose` to see the choice, or pass `--window-start` to `night_replay.py stage` to try another.
2. *Preload (setup).* Before the timed run, the frames that came before the window are loaded as if the app had already downloaded them. The run does not write database rows by hand. It puts a spread of 16 earlier frames (`--replay-preload-frames`) into the stand-in telescope computer's picture folder, adds the two targets (M 57 and NGC 7331) to the throwaway library with their coordinates, and asks the running throwaway backend to download each target with its `telescope:sync` method, the call behind the "Download Frames" button. The backend copies, sorts, indexes and measures the frames with its own code. The stand-in guide log and Ekos log are cut at the window's start, so the log pulls start from a log of the right length (6,880 and 8,814 lines for the default window). All of this is setup. It took about 2 minutes on the first runs (staging, building and serving the UI, and a 96 second ingest), almost all of it the quality measurement of the 16 frames. It is timed apart (`info.replay.setup_seconds`) and no TPM includes it. Only a subset is loaded: all 175 earlier frames would take about 15 to 30 minutes.
3. *Live window.* When setup ends, the run writes a start time 3 seconds ahead and the flow starts. The window then plays at the original spacing, divided by `--replay-speed` (default 1; at 2 the window covers 600 seconds of night in the same 300 seconds):
   - *Frames.* Each frame is copied into the stand-in picture folder at the moment the real camera saved it. A few seconds earlier the INDI simulator mount is pointed at the frame's coordinates and the simulated camera runs a short exposure. That only drives the camera status ("Exposing", then "Idle") that makes the app download. The simulated camera's own picture goes to a scratch folder and is not used. The frame is a real file, so the download, ingest and quality measurement work on real pixels.
   - *Guide events.* The stand-in PHD2 (`simulator/mock_phd2.py`) sends the window's guide steps, settle events and lost-star events (a guide row marked `DROP` becomes PHD2's `StarLost` event) at their real times, against the same start time as the frames.
   - *Logs.* The window's lines of the guide log and the Ekos log are appended to the stand-in files one at a time. The app's log pulls (after the first frame and then at most every 5 minutes) fetch the growing files. The pull interval is a constant in the app (`GUIDE_LOG_MINIMUM_INTERVAL_SECONDS`, not a setting). The first pull happens after the first downloaded frame, so every run has at least one. This harness does not change it.
4. *End.* The run waits until the driver has played the whole window and every window frame is in the library, then compiles the TPMs.

**Clocks.** The Ekos log counts seconds from a local start time and names the zone (MDT). The guide log has local times without a zone. The frame headers are UTC. The planner uses the log's zone name and reports `clock_check_seconds`: the median gap between the log's "frame saved" time and the header's end of exposure. A few seconds is normal; 3,600 would mean a wrong zone.

**What goes into `tpm.json`.** `info.replay` holds the night, the window (start, end, night seconds, real seconds), the speed, the frame counts (night total, before the window, preloaded, in the window, with the names), the guide events by type, the log line counts before and in the window, `clock_check_seconds`, `setup_seconds` (staging, building and serving the UI, and the ingest) and `ingest_seconds`.

**Approximations.** The app measures only the preloaded subset, not the whole night. The simulated camera runs a 6 second stand-in exposure per frame, not the real 120 seconds. The guide events are replayed from the log, so values the log does not hold (such as PHD2's `AvgDist`) are filled with zero. The replay's live guiding samples carry the time of the replay, while the pulled logs carry the date of the real night.

## The network seal

The simulation library, the simulated and replay profiles (and the offline profile, unless you pass `--no-seal`) run inside their own network. `run_tpm.sh` starts itself again under `bwrap --unshare-net --unshare-pid` (bubblewrap, the same tool the real-snapshot mode uses). The new network namespace has only a loopback interface. A network namespace has its own loopback, so everything that talks over 127.0.0.1 must be inside it, and it is: the script starts the backend, the INDI server and its simulators, the stand-in PHD2, the drivers, the UI server, the browser, Electron and `xvfb-run` as its own children. Nothing in the run can connect out, and nothing outside can connect in (your real PHD2, INDI server, StellarMate and backend are not reachable).

- The whole computer is bound read only (`bwrap --ro-bind / /`). Only the throwaway folder's parent (`TMPDIR`, which is `/var/tmp` for `--cold`), `build/tpm/results` and the display sockets (X11 and Wayland) are writable. `--unshare-pid` makes every process of the run end when the script ends.
- `python build/tpm/seal_guard.py sealed` checks the seal at the start: the only interface in `/proc/net/dev` is `lo`, and a connection to 1.1.1.1 and 8.8.8.8 fails.
- The older connection guard (the settings check and the `ss` check of the backend's connections) still runs as a second check.
- Because nothing has a route out, anything the app asks of the internet (for example an online lookup) fails at once. The numbers of a sealed offline run can differ a little from an older, unsealed baseline for that reason.
- A window on your display still works. The Wayland socket and the X11 socket are files, and they are reachable from the new namespace. `xrandr` ran from inside the seal during testing. A real-display Electron window was not opened as part of testing the seal.
- The simulation library is sealed like the others; its overlay is made before the seal (see "The simulation library").
- The real-snapshot mode is not sealed. It has its own mounts and its own bwrap lock for the backend, and bubblewrap inside bubblewrap is not worth the risk for a mode that already cannot write to your library.
- To look inside a running sealed run from another terminal, use `nsenter --preserve-credentials -U -n -t <pid of a process in the run> <command>`.

## The PHD2 contract test

The app reads PHD2 only through its JSON event stream and the guide log. The stand-in PHD2 (`simulator/mock_phd2.py`) is only useful while it sends what real PHD2 sends, so it is tested against a recording of the real one.

**The recording.** `record_phd2_contract.py` runs the user's own PHD2 build (`~/.local/bin/phd2`, under `xvfb-run`) against the INDI simulators (telescope and guide camera), all inside the same loopback-only seal as the TPM run (`bwrap --unshare-net`). PHD2 gets a throwaway `HOME` with its own settings file and an INDI camera and mount profile; the user's `~/.PHDGuidingV2` is never read or written. The script drives PHD2 through its JSON-RPC (connect, loop, find star, calibrate, guide, one dither, stop) and keeps `simulator/test/fixtures/phd2_contract_events.jsonl` (every event; the first line names the PHD2 version, now 2.6.14dev1 commit 264a64d, the date and the script) and `simulator/test/fixtures/phd2_contract_guide_log.txt` (the guide log PHD2 wrote). The INDI camera simulator draws stars by calling the `gsc` catalog program, which is not installed, so the script puts a small fake `gsc` (a fixed lattice of made-up stars) on indiserver's path only. A run takes about 3 minutes. After a PHD2 upgrade, run `.venv/bin/python build/tpm/record_phd2_contract.py` again, then `.venv/bin/pytest simulator/test/test_phd2_contract.py -q -s`.

**The test** checks that: (a) every event type the app consumes (`GuideStep`) is in the recording and in the stand-in's output; (b) the fields the stand-in sends exist in real PHD2 with the same JSON types, the fields the app reads are always sent, and duration and direction come together and only for a pulsed axis (extra real fields are printed as information); (c) the app's guide log parser reads the stand-in's log and the real log the same way; (d) the events around a dither come in the same order (`GuidingDithered`, `SettleBegin`, guide steps, `SettleDone`); and the app's `GuideStep` parser accepts every step of both.

**What is not covered.** `StarLost` is not in the recording (the run never lost its star), so its shape is still from PHD2's documentation. Real PHD2 also sends `HFD`, `AvgDist` and `Settling` events, and `TotalFrames` and `DroppedFrames` in `SettleDone`, which the stand-in leaves out because the app does not read them.

## The simulated observatory

**Shared code.** The stand-ins live in `simulator/` (see its README), not in this folder. The app uses the same code for its **Simulated sky** and **Replay a night** observatory sources (Settings, Observatory). `run_tpm.sh` starts every stand-in through one program, `python -m simulator.observatory start` (which uses `SimulatedObservatory` and the supervisor, as the app does), and stops them with `... observatory stop`. It uses the same settings text (`simulator/settings.py`), the same `standin_ssh.sh` and `.night_replay` for the replay staging. The sealed network namespace, the port choice in `run_tpm.sh` and the seeded library are the harness's own.

`--observatory simulated` runs the same flow with a telescope that is "on": live status, a guiding stream, automatic download of new frames, frame analysis and the guide log pulls all run through the app's real code. Every piece is a local stand-in inside the throwaway folder, on a random free port:

| Piece | What it is | Settings that point at it |
| --- | --- | --- |
| INDI server | `indiserver` with `indi_simulator_telescope`, `_ccd`, `_focus`, `_wheel` and `_guide`. | `[Observatory.Telescope] hostname = 127.0.0.1`, `indi_port` |
| Stand-in PHD2 | `simulator/mock_phd2.py`. It plays PHD2's event server and replays the guide steps and settle events of a real guide log at the pace of the log. | `[Observatory.Guiding] phd2_location = local`, `phd2_port` |
| Stand-in telescope computer | The folder `standin/home` in the throwaway folder. Frames are in `Pictures/M 13/Light`, guide logs in `.local/share/kstars/guidelogs`. | `[Observatory.Telescope] remote_pictures_path` |
| Stand-in ssh | `simulator/standin_ssh.sh`, put first on the backend's `PATH`. | none (the app still runs `ssh` and `rsync -e ssh`) |
| Exposure driver | `simulator/exposure_driver.py`. It connects the simulators, points the mount at M 13 (a seeded target), takes exposures one after another (`--sim-exposure-seconds`, default 60) and writes a growing guide log. | none |

**Transfer without ssh.** The app copies frames and logs with one rsync runner (`StellarMateInterface._run_rsync`) that starts `ssh` and `rsync -e ssh` against the telescope host. The simulation keeps that code and changes only what `ssh` is. The backend's `PATH` starts with a folder whose `ssh` is `standin_ssh.sh`. It drops the ssh options and the host, and runs the command on this computer with `HOME` set to the stand-in folder. It accepts only the host `127.0.0.1` or `localhost`, refuses port forwards, and does nothing without `ASTROMETRICS_STANDIN_HOME`. There is no second transfer code and no sshd.

**The offline profile has a refusing ssh.** The offline profile puts the same script first on the backend's `PATH` with `ASTROMETRICS_STANDIN_SSH_MODE=refuse`. It prints `ssh: connect to host localhost port 22: Connection refused` and exits 255 for any command, so the offline run never starts the real ssh (which fails inside the sealed network on the system's ssh configuration).

**Automatic download needs the real INDI path.** `ASTROMETRICS_TESTING` makes the backend use its built-in simulator and switches automatic downloads off. The simulated profile leaves it unset, so the backend uses the real INDI client (in its own process), the real PHD2 client and the automatic download service. The offline profile still sets it.

**The guide log.** `simulator/guide_log_sample.txt` is the first 620 lines of an Ekos internal guider log written on 2026-10-03 (about 20 minutes of guiding, with calibration and one dither). It was copied from the app's own `ekos_logs` folder; the original was only read. The replay is the same every run. Only the dates in the headers are set to the time of the replay, so the app files the samples under the current night. A gap between two guide sections is cut to 10 s so a short run is not silent.

**Safety.**

- Before anything starts, `observatory start` checks the settings file (`simulator.observatory_guard`) and refuses it unless the telescope host is `127.0.0.1` or `localhost` (and resolves only to this computer), the INDI and PHD2 ports are the ports the script started, PHD2 is `local`, the picture folder is inside the stand-in folder, every library folder (image library, frames, stacks, wayfinding) is inside the throwaway folder and none is one of your real folders, and no IPv4 address other than 127.x appears anywhere in the file (so the real StellarMate's address is refused).
- The run refuses the ports 5000, 4400 and 7624 for every stand-in.
- After the backend has started, and again after the flow, `python build/tpm/seal_guard.py connections` lists the TCP connections of the backend and its child processes (`ss -tnp`). Any connection to an address beyond this computer stops the run with a message.
- `indiserver` listens on all network interfaces; it has no option for loopback only. It runs for the length of the run and serves only simulators.
- The user's library folders, databases and the real StellarMate are never touched. The script ends all stand-ins when it ends.

**After the flow** the script waits (at most the exposure time plus 150 s) until the first simulated frame has reached the library, so even a short run shows the download working. It fails if none arrives. `observatory_metrics.py` then writes `observatory-metrics.json`. `frame_to_library_seconds` is the inode change time of the library copy minus the write time of the source file.

The unit tests for these pieces run with `.venv/bin/pytest build/tpm/test`.

For exact behavior, read the scripts in this folder.
