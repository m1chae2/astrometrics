# Simulator From Real Data Plan

This plan gets a simulated observatory ready for two jobs. First, it must be a faithful
stand-in for the user's hardware, so that the app's telescope control and every processing
workflow can be proven against it. Second, it must behave like the user's real telescope, with
settings fitted from the recorded nights and refitted as more data arrives.

The simulator is a layer around the existing application. The core (`astrometricslib`,
`wayfindinglib`, the backend, the UI and Electron) knows as little about simulation as possible.
The layer provides stand-in hardware, prepares the app's inputs (settings and a throwaway
library) and reaches the app only through its public interfaces. The core gets small,
general-purpose additions where the layer needs them; changes to the app's features are later
tickets (section 9).

The findings come from a read-only survey of the repository and two review-board rounds on
2026-11-07. The code is the source of truth. Line numbers drift, so this plan names files and
functions.

## Status

Status on 2026-11-08: decisions D1 to D24 are made, and the two plan reviews and the review of
pull request 151 are folded in (appendices A to C). Phases L0 to L3 and S0 to S4 are built; S1
and S5 are in issue 160. The "verify" items of section 5 were checked against the INDI 2.2.5
driver source, and the table gives what was found. One change from the plan: S3 converts each
measurement by inverting the driver's own formulas instead of a closure run at every start. A
live closure check on 2026-11-07 agreed within 4 % for sky glow and 9 % for star width.

## 1. Goal

- The simulated telescope, camera, focuser, filter wheel and guider stand in for the user's
  hardware, and the sky stands in for the user's sky. The app reaches them over the same
  protocols as on a real night: INDI through `indiserver`, PHD2's JSON-RPC interface, and ssh
  and rsync to a stand-in telescope computer.
- The simulator is set up exactly like the real telescope. The same settings sections and code
  paths are used, and only the endpoints point at local programs.
- Its numbers come from the user's recorded nights, and new nights refit them without anyone
  editing them.
- It is the app's standard integration test bed, and the match with the real telescope is
  measured.

## 2. Decisions (user, 2026-11-07)

| ID | Decision |
| :--- | :--- |
| D1 | Use existing tools, not our own. Run real PHD2 (`~/.local/bin/phd2`) and the INDI simulators (telescope, CCD, focuser, filter wheel, guide). Our code only fits and writes those tools' settings. Stand-ins stay only where repeatability is the point: the TPM baselines replay a recorded guide log through `mock_phd2`. The PHD2 contract test checks that stand-in against real PHD2. |
| D2 | Every command in simulation mode goes the same path as a real night: app, then `wayfindinglib` control, then INDI over `indiserver`. Guiding goes through the PHD2 JSON-RPC interface. The same safety and delegation rules apply. The simulator is given recorded calibrations instead of relaxed rules. |
| D3 | Treat the system as feedback loops. Guiding and the cooler are loops: a real controller plus a fitted plant and disturbance. Focus, slews, downloads and sky are fitted disturbance or latency models. Fitting is system identification from the user's data. Each value carries a source note, chosen in this order: newest real record, history fit, performance envelope, simulator default. The values are refitted at each simulator start. |
| D4 | It must be possible to show that the simulator and the real telescope behaved alike (phase S5). |
| D5 | Autofocus becomes an app command (later ticket, section 9). |
| D6 | The TPM simulation runs keep `mock_phd2`, so baselines stay repeatable. Real PHD2 runs in the interactive simulation mode and the contract test. |
| D7 | The cooler setpoint becomes an app command (later ticket, section 9). |
| D8 | The CI image may install the INDI packages for the sealed `indiserver` job (phase S1). The `setup-python-env` action already installs `indi-bin`. |
| D9 | Work that changes what the user sees or does lands on `150-ux-clean-up`. Other work gets its own issue (section 8). |
| D10 | In the end the INDI simulators stand in for the hardware only, and this app has full control: it does what Ekos does today (capture sequences, autofocus, cooler, guiding through PHD2, slews, plate solving). PHD2 stays as the guiding tool that the app commands (D1). INDI drivers stay as the device layer, for the real hardware and the simulators alike; the app replaces Ekos and KStars on top of them, not INDI. |
| D11 | This plan's focus is getting the simulator ready. Changes to the app's features come in later tickets (section 9). Until the app can command PHD2 and run captures, an operator step inside the layer's stand-in processes plays the operator's part for those two jobs only (bringing PHD2 to guiding, looping exposures), and says so in its log. |
| D12 | The simulator's stars come from the real GSC catalog, installed for the INDI CCD and guide simulators, so the field matches where the telescope points and plate solving can work. The invented star lattice (`FAKE_GSC`) stays only in the PHD2 contract recorder and tests. |
| D13 | "Behaved alike" (D4) is judged with margins taken from the user's own night-to-night spread, not fixed percentages. Night conditions (seeing, sky, temperature, targets, Moon) are copied from the night being compared. Only the instrument's settings are fitted, with that night left out. The check rotates over at least five nights of one equipment setup. |
| D14 | When the simulator images a target the user already has real frames of (same camera, filter and capture setting), it delivers one of those real frames as if it had just taken it, taking it out of the simulator's library first so the library never holds two copies. The frame reaches the simulator's library through the app's normal download, so every processing workflow runs on real data. For other targets the INDI simulator's own frame is used. |
| D15 | Delivered real frames carry the simulated time ("as if taken now"), with no flag in the frame records. Time-dependent results computed from them land only in the simulator's throwaway library, which is deleted when the user leaves the mode. |
| D17 | In simulation mode the app sends nothing off the computer: no submissions (the asteroid report to the Minor Planet Center), no online catalog or name lookups (SIMBAD, online Gaia), no online plate solving. It talks only to the stand-ins on this computer, and lookups use the local catalogs copied into the simulator. |
| D16 | The simulator is a layer around the backend and the libraries (top-level `simulator/` package). The core never imports it, and it uses only the libraries' public names and the backend's public interface. Core changes are allowed where the layer needs them, and they must be general-purpose (useful for a second real observatory or a real night too). Measurements that also describe a real night are made in the core and reached through its public API; the layer only turns them into simulator settings. |
| D18 | Review of pull request 151: the RPC endpoint gets a host check (local addresses, plus the LAN addresses in LAN mode) and loses the hand-set "any origin" reply header in this pull request. Requiring the session token on every RPC is its own issue (#158), because it touches the whole UI. |
| D19 | The launcher contract (the source choice, its file and the restart exit code) lives in `backend/public_interface.py`. The simulator may import that one backend module and nothing else from the backend. |
| D20 | The connection guard is fixed now: it fails closed, checks UDP as well as TCP, and is documented as a detector. Running the simulated world with no network at all (a network namespace) comes with S1. |
| D21 | Frames drawn by a camera simulator carry a general "synthetic frame" header keyword, and the quality measurements of those frames carry a warning that their noise, sky and depth values do not describe a real sky. Real frames delivered "as if taken now" (D14, D15) do not carry it. |
| D22 | The observing night follows the computer's time zone today. Working it out from the site's longitude, or a configured zone, is its own issue (#159). |
| D23 | The TPM keeps its baselines per machine setup. Frame-end detection moves from the backend into `wayfindinglib` control in S2, when the capture runner needs it. |
| D24 | All other review findings on pull request 151 are fixed in that pull request, the UX findings included. |

## 3. What exists today

- Simulation code lives inside the core:
  - `wayfindinglib/simulation` is a public module of the library (`SimulatedObservatory`,
    `library_copy`, `exposure_driver`, `mock_phd2`, `supervisor`, `observatory_guard`,
    `settings`, `night_replay`, `simulated_calibration`), pinned in
    `wayfindinglib/test/test_public_surface.py` and allowed into the backend by an
    import-linter exception in `pyproject.toml`.
  - `backend/observatory_source.py` owns the world switch: it sets the `XDG_*`,
    `ASTROMETRICS_CONFIG` and `ASTROMETRICS_REAL_*` variables, builds the world and restarts
    the backend with `execve`. `backend/main_backend.py` runs it when the module is loaded, and
    a real-mode start deletes leftover simulator folders without a lock.
  - `ObservatorySourceService` starts and stops the stand-ins, seeds targets and removes the
    library copy; `/api/ready` waits for it; `backend/container.py`'s ready hook
    (`_take_command_of_simulator`) records the simulator's calibrations through private names
    (`control._context.butler`, `_save_calibration`, `_save_focus_model`) and enters controller
    mode with `shadow_first=True`, which the public RPC does not offer.
  - `astrometricslib/foundation/paths.py` knows the simulator folder's name
    (`SIMULATOR_FOLDER_NAME`, `is_in_simulator_world`), the public `astrometricslib.drivers`
    exports it, and the backup script skips backups by that name.
  - The UI allows commands when `source === 'simulated'`
    (`ui/common/hooks/useCommandAvailability.ts`), and the Electron tray matches the word
    "simulator" in the source label.
  - The settings-save guard comes from `wayfindinglib.simulation`.
- `_copy_settings_text` keeps the real `Observatory.Telescope`, `Observatory.RemoteTransfer` and
  `Observatory.Guiding` sections and changes only their endpoint keys (phase S0 built).
- `exposure_driver` sets the sensor, focal length and gain and offset of the INDI simulators from
  the world's settings (phase S0 built), and connect, site, unpark, track, upload and exposure, on
  one fixed target. No `gsc` program is installed here, so the simulators draw no stars; the
  launcher reports it as a note.
- The INDI server runs with its own socket and its own `HOME` inside the world, so the simulators
  no longer read or write the user's `~/.indi` (phase S0 built).
- The TPM harness builds the simulator world itself (`build/tpm/simulation_library.py`,
  `observatory start --existing-world`) and runs the backend unaware of it, so the backend
  reports the real telescope and the SIMULATOR chip check is switched off for now.
- `build/tpm/record_phd2_contract.py` starts real PHD2 against the simulators with its own
  JSON-RPC client (`Phd2Session`) and an invented star lattice. The contract test checks only
  `GuideStep`. The app's `phd2_client.py` only reads events.
- `reindex_frames(prune_missing=True)` removes a missing frame's record but not its quality
  measurements; nothing in the quality store deletes measurements. `images:delete` removes a
  frame and its record through the backend.
- The frame marker `ORIGIN = 'ASTROMETRICS-SIMULATOR'` written by `night_replay.copy_frame` is
  read by nothing.
- `test_indi_interface_live.py` (hard-coded port 7624) and `test_centering_live.py` are skipped
  in CI (`ASTROMETRICS_FAST_TEST=1`).

## 4. Target structure

```text
simulator/                 the layer; the core never imports it
  launch.py                profile, lock, world, stand-ins, then the backend as a child; teardown
  world/                   library_copy, settings, loopback guard
  standins/                supervisor, observatory, standin_ssh.sh, mock_phd2, PHD2 launch
  operator/                exposure_driver: the D11 operator and the S4 frame source
  fit/                     S3: core measurements -> INDI properties, clamping, results file
  replay/                  night_replay, replay_driver, guide_log_replay
  validate/                S5
  test/
```

`build/tpm` becomes a client of the layer. Two import-linter contracts: the core
(`astrometricslib`, `wayfindinglib`, `backend`, `mcp_servers`) never imports `simulator`; and
`simulator` uses only the libraries' public names and never imports `backend` (it reaches the
backend over `/api/rpc`).

Core additions the layer needs, all general-purpose:

| Addition | Where | Also serves |
| :--- | :--- | :--- |
| Launch identity: a read-only `[Launch]` section in the config the layer writes (`label`, `live_hardware`, `disposable_library`, locked endpoint keys); `observatory:get_source` reports it, and the backend never builds or tears down anything from it | Backend | A second real observatory |
| Restart contract: `observatory:set_source` writes the choice and exits with an agreed code; the launcher relaunches | Backend, `run_backend.sh` | Any launcher |
| Locked endpoint keys: settings saves that change them are refused | `settings_service` | Shared or managed installs |
| Backup switch in the config instead of the folder-name check; `library_folders(config_file)` instead of `real_folders`; the `ASTROMETRICS_REAL_*` variables move to the layer | `astrometricslib` | Any throwaway or test library |
| No outbound network: a general switch in the launch identity that every outgoing client in the core honors (D17), plus the layer's connection guard (`build/tpm/seal_guard.py connections`, moved into the layer) that stops the simulator if the backend opens any connection off the computer | Core outgoing clients (SIMBAD, online catalogs, online solver, asteroid report) | Observing without internet |
| Public `record_calibration` and `record_focus_model` on Control, with RPCs | `wayfindinglib`, backend | The PHD2 command ticket on real nights |
| `shadow_first` on `observatory:enter_controller_mode` | Backend | Real nights |
| Command rights from the delegation policy and `allow_commands`, not from the source | UI, Electron tray | Real nights |
| `quality.forget(subject_ids)`: remove a frame's measurements | `astrometricslib` | Deleting frames on real nights |
| Measured unguided mount error per run (period, amplitude, phase, uncertainty, residual random and drift terms), from signed pulses and guide rates | `wayfindinglib` review | Periodic-error correction decisions |
| PHD2 controller settings (aggressiveness, minimum move, hysteresis, exposure) parsed into the guiding run | `wayfindinglib` | Guiding review |
| Public sky-rate prediction (`predict_sky_rate_at`) and the focus temperature fit in steps (`fit_thermal_coefficient_steps_per_c`) | `wayfindinglib` | Exposure planning, focus refits (D5) |
| Unstored measurement of a folder of frames with the `frame.*` metric set | `astrometricslib` quality | Checking tonight's staging folder |
| `exclude_nights` on the envelope, the guiding spectrum and the measurements above | `wayfindinglib` | Leave-one-out reviews |
| A narrow public `wayfindinglib.drivers` module exporting the PHD2 client (or the PHD2 command ticket brought forward) | `wayfindinglib` | The PHD2 command ticket |

## 5. Simulator settings

Properties checked in the INDI 2.2.5 programs (`indi_simulator_ccd`, `_guide`, `_telescope`,
`_focus`). The measurements come from the core's public API (section 4); the layer converts
them to these properties and clamps them to valid ranges.

| What | INDI property | Source | Fallback |
| :--- | :--- | :--- | :--- |
| Focal length, aperture | CCD and guide `SCOPE_INFO` (the telescope simulator has no `SCOPE_INFO` in INDI 2.2.5) | Settings (`Observatory.Optic`, `Observatory.Setup`), not fitted | None needed |
| Sensor size and pixel size | CCD and guide `SIM_XRES`, `SIM_YRES`, `SIM_XSIZE`, `SIM_YSIZE` | Camera profile and settings, not fitted | None needed |
| Camera identity | Frame headers `INSTRUME`, `GAIN`, `OFFSET` | Set to match the real camera profile, so simulator frames file under the right camera and bias | None needed |
| Guide rate | Telescope `GUIDE_RATE_WE`, `GUIDE_RATE_NS` (fraction of sidereal: the driver's unit is 15.0 arcsec per second, with the cos(Dec) convention of the calibration) | Recorded guider calibration, else the real mount's set rate | Mount default |
| Periodic error | CCD `SIM_PEMAX`, `SIM_PEPERIOD`; guide `SIM_PE_MAX`, `SIM_PE_PERIOD` (zero to peak, divided by cos(Dec); the two cameras run their own phase, which cannot be set; the main camera's period is limited to 60 s, so a longer period is clamped and the amplitude scaled to keep the peak speed) | The core's measured unguided mount error (section 4), not the guiding spectrum's peak to peak, which measures what guiding left | Zero |
| Random and slow guiding disturbance | Guide `SIM_RA_RAND`, `SIM_DEC_RAND`, `SIM_RA_DRIFT`, `SIM_DEC_DRIFT` | The same measurement's residual terms: the median signed drift, and the frame-to-frame jitter (median step divided by the square root of 2) as a uniform term of half-width jitter times the square root of 3 | Simulator default |
| Polar error | CCD and guide `SIM_POLAR`, `SIM_POLARDRIFT` | The newest polar alignment record (one today: 34'25", 2026-10-03), labeled as a single measurement. Not the pointing model's ME and MA | Zero |
| Dec backlash | Telescope `DEC_BACKLASH` (ms) | `dec_backlash_estimate_ms` | Zero |
| Guide controller | PHD2 profile | The parsed PHD2 controller settings of the newest run (section 4) | PHD2 defaults |
| Seeing, main camera | CCD `SIM_SEEING` | The envelope median star width, converted with the driver's own profile formula (checked by closure, see the status). Applied only after the focuser reports a position, because the driver ignores seeing until then | Simulator default |
| Seeing, guide camera | Guide `SIM_SEEING` | Its own closure on guide frames (the guide star is undersampled at 6.39 arcsec per pixel, so its width is not taken from the main camera). Until that closure is built, the width is the main camera's sky width but never under a star 3.5 pixels wide, because real PHD2 rejects a narrower star as a hot pixel (live run 2026-11-07: 2.4 pixels failed, 3.5 pixels gave a half-flux diameter of 2.2 pixels and a signal-to-noise ratio of 62) | Star 3.5 pixels wide |
| Sky glow | CCD and guide `SIM_SKYGLOW` (magnitudes) | The core's sky-rate prediction in ADU per second, converted with the driver's flux formula. When the target is below the horizon at the start, the prediction is made on the meridian at the target's declination. The guide camera keeps its default: no guide-camera sky is measured, and the main camera's rate does not transfer | Simulator default |
| Noise, bias, maximum value | CCD `SIM_NOISE`, `SIM_MAXVAL`; guide `SIM_BIAS` | Camera profile (read noise 4.82 ADU, clip ceiling). The driver adds uniform noise from 0 to N-1, so N is the nearest whole number to the square root of (12 times the read noise squared plus 1), and the pedestal is the bias less (N-1)/2 | Simulator default |
| Focus temperature behavior | CCD `SIM_FOCUS_POSITION`, `SIM_FOCUS_MAX` (verify) | The core's focus temperature fit in steps per °C, recorded only: the driver's best focus is a fixed number and the focuser's temperature is only a reading, so nothing can be set | No drift |
| Download time | Stand-in transfer delay | `capture_overheads` download time, recorded only: the stand-in transfer has no delay option yet | Not delayed |
| Dither settle | PHD2 setting | Guide controller row | PHD2 default |
| Slew time, cooler response | No INDI property | Later tickets (section 9) | Not settable |

The simulator renders stars as the driver draws them (verify: Gaussian profile, uniform noise
without shot noise, no 4 ADU steps). Until shot noise is confirmed, noise, signal-to-noise and
stacking-depth checks are not valid on simulator frames. Frames delivered by S4 are real and
have none of these limits.

## 6. Phases

Each phase can merge on its own and keeps the app and the TPM harness working. L0 to L3 build
the layer; S0 to S5 build the simulator inside it.

### Phase L0: move the package

Move `wayfindinglib/simulation` to the top-level `simulator/` package by script, updating the
backend's importers and `build/tpm` in the same commit, with one temporary, named
`backend -> simulator` import-linter exception. Update `.claude/CLAUDE.md` section 2, the public
surface pin, the import-linter contracts, the ruff per-file paths and `ARCHITECTURE_PLAN.md`.

Acceptance: every test passes; nothing but the move changed.

### Phase L1: core additions

Add the general-purpose additions in section 4 that the launcher needs first: the launch
identity, the restart contract, locked endpoint keys, the backup switch and `library_folders`,
the no-outbound-network switch, public calibration and focus-model recording with RPCs,
`shadow_first` on the RPC, command rights from the delegation policy, and `quality.forget`. Each
is tested with a non-simulator profile. The TPM harness writes the launch identity, so its
SIMULATOR chip check is switched back on.

Acceptance: the existing backend path still works; a TPM simulation run shows the chip; with
the no-outbound-network switch on, no outgoing client opens a connection off the computer.

### Phase L2: the launcher

`simulator/launch.py` reads the choice, holds an exclusive lock on the simulator folder for the
world's whole lifetime, builds the world (with the S0 items below), starts the stand-ins, runs
the backend as a child with the world's config and folders, and tears down after it exits.
`build/linux/run_backend.sh` starts the launcher, behind a flag at first. After `/api/ready`,
the launcher records the calibrations and enters controller mode through RPC (the logged D11
operator step), instead of the backend's ready hook.

Acceptance: switching Real and Simulated in the app works through the restart contract; a second
process never changes a running world; the backend runs unaware of simulation; the connection
guard sees no connection off the computer during a simulated session.

### Phase L3: remove simulation from the core

Delete the backend's world code (`observatory_source.py` except the choice file, which moves to
the layer), the simulator parts of `ObservatorySourceService`, `_take_command_of_simulator`, the
`/api/ready` wait, the shutdown teardown, the module-load call in `main_backend.py`, the
simulator names in `astrometricslib/foundation/paths.py`, the simulation-specific settings guard
and the temporary import-linter exception. Add the two contracts in section 4.

Acceptance: the core imports nothing from `simulator`; every test passes.

### Phase S0: set-up fixes in the world the layer builds

- Run `indiserver` with its own home folder inside the simulator folder, so the simulators never
  read or write the user's `~/.indi`.
- Keep the real `Observatory.Telescope` and `Observatory.Guiding` sections in the copy and change
  only the endpoint keys, listing them as locked in the launch identity.
- Install the GSC catalog for the simulators (D12).
- Make simulator frames carry the real camera's identity (section 5).

These live in code that moves in L0, so they can be done before or after it.

Acceptance: `~/.indi` is untouched by a simulator run; the copy's telescope settings equal the
real ones except the endpoints; a simulated field shows catalog stars and files under the real
camera.

### Phase S1: the simulator as the integration test bed

A test fixture starts `indiserver` with the simulators on a free port and its own socket
(`-u <tmp>/sock`), refuses port 7624, and checks the device names. In one sealed CI job
(`bwrap --unshare-net`, checked first on the runner):

- the live tests (`test_indi_interface_live.py` with the port from the fixture,
  `test_centering_live.py`),
- new command tests for connect, unpark, track, slew, expose, filter change and focuser move,
  through the app's control path, each reading its effect back from the INDI property,
- launcher tests against real stand-ins, replacing the backend tests that monkeypatch the
  source's processes,
- the contract tests (section 7).

The interactive simulation mode also runs its world with no network (D20): the launcher starts
the backend and the stand-ins in their own network namespace, so the connection guard becomes a
second check rather than the only one.

Acceptance: the job runs and passes in CI and never reaches port 7624 or any network outside
the sandbox.

### Phase S2: real PHD2 in simulation mode

- Frame-end detection (`FrameEndDetector` and its tuned values) moves from the backend's
  auto-download service into `wayfindinglib` control, so the capture runner and the backend use
  one detector (D23). The backend keeps only the scheduling.
- One PHD2 client: the command methods the operator needs (connect, loop, guide with
  calibration, stop) are added to the core's PHD2 client and exported through the narrow public
  module (section 4). `record_phd2_contract.py` moves to it.
- The launcher chooses the guider: `phd2` for interactive simulation mode, `mock` for the TPM and
  the replay (D6). If PHD2 is missing, it falls back to `mock_phd2` and reports it as a status.
- PHD2 runs with `HOME` set to the stand-in telescope computer's home, an instance number whose
  two ports (4399 + i and 4299 + i) are free, `WAYLAND_DISPLAY` unset and `GDK_BACKEND=x11`, and
  its profile takes the guide controller settings (section 5).
- With real PHD2, no guide log is replayed, so guiding runs are not counted twice.
- The operator step (D11), in the layer's `operator/`, checks it talks to the PHD2 it started,
  brings it to guiding after the settings are written, and does not guide again after an app
  slew. A failure is a status, not an error.

Acceptance: in simulation mode the app's guiding status and guide graph come from real PHD2,
and PHD2's guide log is a genuine one that the app ingests.

### Phase S3: write fitted settings into the INDI simulators

The layer's `fit/` reads the core's public measurements on the simulator's library copy (made
from the real databases at this start; the real folders are never opened), converts each row of
section 5, clamps it, and writes the properties with the operator's `indi_setprop` helper after
each device's `CONNECTION` is On, before the backend starts. It writes one results file: for
each value, the public call and its parameters, the night IDs used, the source note and date, a
fingerprint of the library copy, and any clamping. Replies marked `truncated` are not used: a
run list too large to send is asked for again with fewer runs.

Acceptance: after start, each property equals the value in the results file; a value without
data falls back one step; a new night changes the values at the next start.

Tests: each conversion with synthetic measurements; the fallback order; the closure calibration
of seeing and sky glow; a live test that reads the properties back.

### Phase S4: real frames for targets the user has imaged (D14, D15)

- The frame source in the layer's `operator/` watches the frames the CCD simulator saves. When
  the mount points at a target that has real light frames with the same camera, filter and full
  capture setting (gain, offset, binning, preferring the requested exposure), it takes the next
  of them, in the order they were taken, starting again at the first when all were used.
  Otherwise it keeps the simulator's frame. SPEC frames are delivered like any other. The camera
  is compared by the library's own camera name, and a filter by the library filter it stands for
  (`L` and `Luminance` are one filter). Frames a camera simulator drew are never treated as real.
- The simulated sky always points at M 13. The user's real M 13 frames were taken at offset 0,
  while the camera profile now sets offset 10, so a capture there keeps the simulator's frame,
  as the full capture-setting rule requires. The live delivery check of 2026-11-08 used an
  offset 0 capture.
- No second copy: before delivering a frame it calls the backend's `images:delete` for it (which
  writes a whiteout in the overlay, leaving the real file untouched, and removes the frame's
  record) and `quality.forget` for its measurements. It never opens the library while the
  backend runs.
- It copies the frame with `night_replay.copy_frame` (never a link, refusing the real folders),
  gives it a new file name in the capture program's naming, and sets `DATE-OBS`, `DATE-END` and
  `MJD-OBS` to the simulated time. The original values go to short keywords (`ODATEOBS`,
  `OMJDOBS`); `AIRMASS`, `OBJCTALT`, `OBJCTAZ` and `OBJCTHA` move to original-value keywords so
  the app recomputes them; `FOCPOS`, `FOCTEMP`, `CCD-TEMP` and `PIERSIDE` stay as they were.
  `ORIGIN` is left alone, and a separate keyword marks the frame as simulated.
- The app downloads, ingests, measures and stacks it as a new frame. Time-dependent results land
  only in the throwaway library (D15).

Acceptance: a capture of a known target yields one real frame in the simulator's library,
through the normal download; the library never holds two copies or two sets of measurements; a
target without real frames yields the simulator's frame; the real library and files are never
written.

Tests: frame matching; header changes; the hide step through the backend; frame and measurement
counts before and after a delivery; a live delivery test.

### Phase S5: validate against held-out nights

- Night conditions come from the night being compared: seeing, sky rate over time, temperature
  curve, targets and times (driving the run from `night_replay.build_plan`), and the Moon. For
  validation runs only, the layer runs the sandbox (stand-ins and backend) on that night's clock
  (verify feasibility, for example with `libfaketime`), at time factor 1.
- The instrument settings are fitted with that night excluded from every source
  (`exclude_nights`, section 4).
- Comparisons on the instrument outputs: guiding RMS, total and per axis, after the same event
  removal the envelope uses, with guide-star signal matched to the night; capture overheads (the
  download delay is a fitted input); periodic-error period stability from night to night, once
  S2 data exist.
- Star width and sky background are checked by a separate closure test, because they are copied
  from the night.
- Margins are the user's night-to-night spread (two standard deviations, as WAY-D70 does), each
  real metric's uncertainty comes from a block bootstrap, and each result reports the
  uncertainty of the margin itself (about 35 % from five nights). The held-out night rotates
  over at least five nights of one equipment setup. A script writes the results to the
  validation register; the tuned margins go in `Astrometrics_Library_Implementation.md`.

Acceptance: each comparison is recorded with its margin and pass or fail. A miss names the
setting to inspect.

## 7. Testing

The simulator is the standard integration test bed (phase S1). Three layers of testing:

1. Unit tests, as today.
2. Integration tests on the simulator, in CI: the app's INDI and PHD2 protocols, the safety and
   delegation rules, the download, ingestion, quality and stacking workflows on real frames
   (S4), the launcher and the TPM flows.
3. A hardware-in-the-loop checklist at the telescope, for what the simulator cannot cover, with
   one validation-register entry per item, re-run when drivers, firmware or equipment change:
   real INDI drivers and firmware quirks; the USB and Wi-Fi link to the StellarMate (including
   the RTL8821CU drop); real ssh and rsync timing and failures; the mount's RA and Dec stalls;
   the cooler; focus with temperature; long overnight runs. The commissioning criteria in
   `wayfindinglib/models/policy/commissioning.py` that need only protocol behavior run on the
   simulator; those about measured hardware performance stay on the checklist.

Contract tests keep the stand-ins honest:

- PHD2: every event and reply the app or the operator reads, recorded from real PHD2
  (`test_phd2_contract.py`).
- INDI simulators: device names, the section 5 properties, valid ranges and clamping, recorded
  from INDI 2.2.5.
- Stand-in telescope computer: folder layout, file naming, `INSTRUME` and header keys, checked
  against a recorded listing of a real StellarMate picture folder (read once, stored as a
  fixture), and `standin_ssh.sh` against the ssh and rsync options the app sends.

## 8. Branch and issue

By decision D9:

| Phase | Changes what the user sees or does? | Lands on |
| :--- | :--- | :--- |
| L0 to L3. The layer | Yes: the Real and Simulated switch, the chip and command rights change how they work | `150-ux-clean-up` |
| S0. Set-up fixes | Yes: real stars and real settings in simulation | `150-ux-clean-up` |
| S1. Integration test bed in CI | No (tests only) | Its own issue (#160) |
| S2. Real PHD2 | Yes: guiding in simulation comes from real PHD2 | `150-ux-clean-up` |
| S3. Fitted settings | Yes: the simulator behaves like the user's telescope | `150-ux-clean-up` |
| S4. Real frames | Yes: processing workflows run on real data in simulation | `150-ux-clean-up` |
| S5. Validation | No (a script and a register entry) | Its own issue (#160) |

## 9. Later tickets: changes to the app's features

| Ticket | What it adds | Simulator part it replaces |
| :--- | :--- | :--- |
| PHD2 command client | The app connects, loops, calibrates, guides, dithers and stops PHD2 under the safety rules, by wiring the S2 client into `Phd2GuidingDriver`; PHD2's calibration becomes the `GuiderCalibration` record on real and simulated nights | The operator's guiding step and the derived guider calibration |
| The app runs captures | The app's capture sequence produces the frames in simulation mode, after the start-of-night commands a real night needs | The operator's exposure loop (the TPM may keep it) |
| Autofocus command (D5) | The app starts autofocus and stores the focus run | None |
| Cooler setpoint command (D7) | The app sets and watches the camera cooler, with a fitted cooler model | None |
| Slew-time model | A slew-time model fitted from alignment attempts and the Ekos log, used by planning | None |
| Plate solving in simulation | The app's solve and sync against GSC-drawn frames | None |
| Simulator settings screen | Shows each fitted value with its source and date (S3 results file) | None |
| Simulator comparison screen | Shows the S5 comparison | None |
| MCP servers in simulation mode | The library MCP servers say which world they read (real library or simulator world), or follow the active world, so an agent never mixes the two | None |

## 10. Risks

- Moving the package (L0) touches many imports. Mitigation: a scripted move in one commit, with
  every test passing before and after.
- The restart contract changes how the app switches sources. Mitigation: the launcher sits
  behind a flag until it is proven, and the backend's old path stays until L3.
- Real PHD2 needs a display (`xvfb`) and must not open on the user's Wayland desktop.
  Mitigation: the S2 environment and a static launch test.
- Fitted values may drive the simulators outside their valid ranges. Mitigation: clamping, said
  in the results file.
- Sparse data for a parameter. Mitigation: the fallback order and the visible source note.
- The simulators do not model everything (shot noise, wind, flexure, the mount's stalls).
  Mitigation: the validity note in section 5 and the hardware checklist in section 7.
- A delivered real frame moves from the night it was taken to the simulated night. Mitigation:
  the original time stays in its header; the throwaway library is deleted when the user leaves
  the mode, and the real library never changes.
- Running the sandbox on another night's clock (S5) may confuse a process that reads the real
  clock. Mitigation: prove it first; validation runs only.
- Driver behavior marked "verify" may differ from what this plan assumes. Mitigation: confirm it
  against the INDI 2.2.5 source before the phase that uses it.
- The interim operator steps (D11) could outlive their purpose. Mitigation: each later ticket in
  section 9 names the step it removes.

## 11. Out of scope

- Writing our own telescope, camera, guider or sky simulator.
- Relaxing safety or delegation rules in simulation mode.
- Changing the TPM baselines or their replay of recorded guide logs.
- Changes to the app's features (section 9).
- Simulating color frames: the CCD simulator is monochrome, so the Nikon D5300 (Bayer, uncooled)
  is simulated only through delivered real frames (S4).
- Local language models and uploads to Drive.
- The in-process test doubles (`wayfindinglib/drivers/simulators/`, the `simulator` guiding
  protocol): they stay as they are and do not grow.

## Appendix A. Review board changes

Review board on 2026-11-07: observational astronomer, software engineer and technical architect.
All three approved with changes. Claims were checked against the code and the INDI 2.2.5
programs before folding in.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| The backend module rebuilds the simulator folder under a running app; no lock | Software engineer | Launcher lock (L2), start-up move (L3) |
| The simulators use the user's `~/.indi` | Software engineer | S0 own home folder |
| Real telescope settings dropped, focal length hard-coded | Architect | S0 keep real sections; section 4 settings rows |
| Equipment values listed as fitted (plate scale) | Architect, astronomer | Section 5 settings rows |
| No `gsc` installed, so no stars | Software engineer, astronomer | D12, S0 |
| Simulated frames may file under the generic camera | Architect | S0 check |
| A second PHD2 client would be built and later thrown away | Architect, software engineer | S2 one client in `drivers/phd2` |
| PHD2's calibration cannot replace the derived record yet | Architect, software engineer | S2 and L2; section 9 |
| PHD2 sealing, port mapping, home folder and Wayland | Software engineer | S2 environment |
| Real PHD2 would also change the TPM | Software engineer | S2 guider choice, `mock` by default |
| Guide logs counted twice | Software engineer | S2 no replayed log with real PHD2 |
| Fitting runs too late, after exposures and calibration | Architect | S3 settings written before the backend starts |
| Operator step must live in the simulator's processes | Architect, software engineer | D11, S2 |
| The fit reads the copy, not the real databases | Architect, software engineer | S3 wording |
| The telescope simulator has no periodic error; properties per device | Software engineer, astronomer | Section 5 per property |
| The guiding spectrum measures guided residual, not periodic error | Astronomer | Section 5, core measurement (section 4) |
| ME and MA are not polar terms | Astronomer | Section 5 polar alignment record |
| Seeing and sky units do not map directly | Astronomer | Section 5 closure calibration |
| Noise missing; no shot noise | Astronomer | Section 5 noise row and validity note |
| Focus drift in the wrong units | Astronomer | Section 5 steps per °C |
| S5 leaks the held-out night and mixes conditions with instrument | Astronomer | D13, S5 |
| Fixed tolerances do not fit the data | Astronomer | D13, S5 margins |
| S5 needs targets and timings, and is slow | Architect, software engineer | S5 replay plan, time factor |
| The first draft's slew and cooler phase builds models nothing reads | Architect, software engineer | Removed; section 9 |
| S1 tests could reach real hardware and would skip in CI | Software engineer | S1 fixture and own job |
| Color and spectra cannot be simulated | Astronomer | Section 11 |

No finding was rejected. The astronomer's points about driver behavior that could not be read
from the installed programs (star profile, noise model, focus properties, periodic-error phase,
time factor) are kept as "verify" items.

## Appendix B. Second review: the simulator as a layer

Review board on 2026-11-07, asked to judge the plan as a layer around the backend and the
libraries (decision D16). All three approved with changes. Claims were checked against the code
before folding in.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| No phase builds the layer; S0, S2 and S3 added simulation to the core | Architect, software engineer | Phases L0 to L3; S0 to S3 rewritten as layer work |
| The backend owns the world switch, start-up and teardown | Architect, software engineer | Launch identity and restart contract (section 4), L2, L3 |
| `astrometricslib` knows the simulator folder's name; backups skip by name | Architect, software engineer | Backup switch, `library_folders` (section 4), L3 |
| Calibration records written through private names; `shadow_first` missing on the RPC | Architect, software engineer | Public recording and RPC (section 4), L1, L2 |
| The one PHD2 client would sit in a private module | Architect | Narrow public module (section 4), S2 |
| The UI decides command rights from the source | Architect | Command rights from the delegation policy (section 4), L1 |
| The settings guard is simulation-specific | Architect, software engineer | Locked endpoint keys (section 4), L1 |
| Setting the source variable from the TPM would rebuild the world; the TPM shows no chip | Software engineer | Chip check switched off until L1; launch identity |
| Fits that describe real nights belong in the core | Architect, astronomer | D16; core measurements (section 4) |
| S4 left the old quality measurements, and a layer-side write could be undone | Software engineer | `quality.forget`, `images:delete` through the backend (S4) |
| Re-timed frames reach time-dependent science; nothing reads the marker | Astronomer | D15: throwaway library; D17: nothing sent off the computer |
| Unguided error and disturbance need data the public API does not give | Astronomer | Core measurement of the unguided mount error (section 4) |
| Seeing and sky closure need the right estimators and the right bias | Astronomer | Unstored folder measurement, camera identity, simulator pedestal (sections 4, 5) |
| Sky prediction and the focus fit are private or unwired | Astronomer | Public in the core (section 4) |
| Leave-one-out is incomplete | Astronomer | `exclude_nights` (section 4), S5 |
| Time factor and the wall clock break the comparison | Astronomer | S5 at time factor 1 on the night's clock |
| PHD2 controller settings not parsed; guide-star seeing differs | Astronomer | Section 4 parsing; section 5 guide controller and guide seeing rows |
| Overheads comparison had no fitted input | Astronomer | Section 5 download delay row; S5 |
| Header handling for delivered frames; full capture-setting match | Astronomer | S4 |
| Margin uncertainty from five nights | Astronomer | S5 |
| Can the simulator replace integration testing? (user's question) | Software engineer | Section 7: test bed, hardware checklist, contract tests |
| The "settings applied" file and the starting step are no longer needed | Architect | Removed: the layer writes settings before the backend starts |

No finding was rejected. The astronomer's proposal to flag simulated frames in the database was
not taken: the user chose to keep "as if taken now" without a flag, because the simulator's
library is thrown away (D15).

## Appendix C. Review of pull request 151

Review board on 2026-11-07 over the whole pull request: the astronomer, the software engineer and
the architect, with a user-experience and a security reviewer. All five approved with changes.
Every major claim was checked against the code before it was accepted.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| The connection guard fails open when its check errors, and sees only TCP | Software engineer, security | D20; fixed in the layer |
| A missing or mistyped `offline` value means online | Security | Fails closed; the launcher also sets an environment flag |
| Stored paths in the world's databases and `calibration_frames.json` can still name real files | Software engineer | The TPM path guard moved into the layer and runs on every world |
| A backend restart is allowed when the busy check fails, and misses imaging, log sync and download timers | Software engineer | Fails closed; all four counted |
| Section headers with a trailing comment are not recognized when sections are dropped | Security | Fixed |
| The TPM real-snapshot run has no offline flag; the seal exposes the session's runtime folder | Security | Fixed |
| Remote shell strings are not quoted; record RPCs overwrite by id; the RPC client follows proxies | Security | Fixed |
| RPC calls need no token; replies allow any origin; no host check | Security | D18 |
| Simulated frames sit about 385 ADU below the bias the app subtracts | Astronomer | The simulator gets the real bias as its pedestal; the header keeps the camera offset |
| The camera model is described as realistic, but only its geometry and labels match | Astronomer | Wording fixed; D21 |
| The copied guider calibration does not match the guide simulator | Astronomer | The simulator's own angle and rate are recorded |
| A failed target lookup counts as a complete read and stays cached | Software engineer | Counted as incomplete |
| Frame rows with no night count as finished | Astronomer | Left out |
| The text says a restack does not refit the camera part; it does | Astronomer | Text fixed; blind spots recorded in WAY-D107 |
| The night boundary depends on the computer's time zone | Astronomer | D22 |
| The SIMULATOR chip is hidden in a narrow window | UX | Never hidden; a short label at narrow widths |
| One command flag, taken from the mount, turns on every device command | UX | Each panel checks its own capability |
| Failed-simulator detection matches the text "fail" | UX | Uses the backend's failed flag |
| Error toasts write browser console errors; the header strip uses a hidden button; smaller UI items | UX | Fixed |
| Simulator-only names and errors live in the core's public surface; `real_folders` is defined twice; a second path parser | Architect | Moved to the layer; D19 |
| Two loopback checks; the wheel ships `simulator`; the import contract does not forbid `backend` | Architect | One check; removed from the wheel; contract extended |
| Unused public methods `invalidate_night` and `query_runs` | Architect | Removed |
| Warm-ups run outside the heavy lane and log failures as errors | Architect, software engineer | Through the lane, as warnings, cancelled on shutdown |
| The MCP servers read the real library while the backend is in the simulated world | Architect | Section 9 |
| Frame-end detection is control logic in the backend | Architect | D23, S2 |
| The TPM harness is large; postpone per-setup baselines | Architect | D23: kept |

Rejected: going back to absolute paths in `.mcp.json`. The user chose project-relative paths
earlier. Corrected: the architect reported that `query_runs` had no caller, but the simulator
called it; it is removed anyway because the review query already returns guiding runs.
