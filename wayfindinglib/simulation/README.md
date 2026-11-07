# Simulated observatory

This folder holds a telescope that is "on" without a telescope. The app uses it for its **Simulated** observatory source (Settings, Observatory). The technical performance measure (TPM) harness in `build/tpm/` uses the same pieces, and also plays back a past night (`--observatory replay`). Every piece runs on this computer.

| Module | What it does |
| --- | --- |
| `observatory.py` | `SimulatedObservatory` makes the folders and settings, checks them, and starts and stops the stand-ins. It is also a program: `python -m wayfindinglib.simulation.observatory start` or `stop`. `prepare_offline_world` builds a world with no telescope at all. |
| `library_copy.py` | A temporary, copy-on-write view of your real library for the app: database copies (SQLite online backup) and a `fuse-overlayfs` overlay of the frames and stacks. |
| `supervisor.py` | Runs one stand-in and ends it, with its children, when the process that started it ends. |
| `mock_phd2.py` | The stand-in PHD2. It plays PHD2's event stream from a guide log. |
| `exposure_driver.py` | Connects the INDI simulators and takes exposures into the stand-in telescope computer's picture folder. |
| `replay_driver.py`, `night_replay.py` | Harness only: play back a past night from the real library and logs (read only). Copied frames carry `ORIGIN = 'ASTROMETRICS-SIMULATOR'`. |
| `guide_log_replay.py`, `guide_log_sample.txt` | Turn a guide log into PHD2 events and paced log lines. |
| `observatory_guard.py` | Refuses settings that point beyond this computer or at a real library folder. |
| `simulated_calibration.py` | `record_simulated_calibrations` records a guider calibration and focus model for the simulator, from your real data, so the library's rules let capture, focus and guiding through. |
| `settings.py` | The settings text that points the app at the stand-ins, or at nothing (offline). |
| `standin_ssh.sh` | An `ssh` stand-in that runs the command in the stand-in home folder. With `ASTROMETRICS_STANDIN_SSH_MODE=refuse` it refuses every connection. |

## The library copy

- The databases are copied at switch time and at every launch of the app in a simulator source, one thread per database, so the copy takes about as long as the biggest database. Each table is scanned once to fix its paths (the copy time is in `observatory:get_source`, `library_copy`). The report's `bytes` are the final sizes of the copies, measured after the paths are rewritten and the write-ahead log is merged into the file. Paths inside the copies that name your real frames or stacks folder point at the overlay.
- The overlay's lower layer is your real folder (read only). Every write lands in `<root>/overlay/upper`. A stacks folder inside the library shares the library's overlay; a separate one gets its own.
- Leaving the mode, or stopping the app, unmounts the overlay and deletes the upper layer and the copies, only inside the simulator folder. A start in Real mode removes what a crash left.

## Safety

- Commands are allowed for the simulated telescope (`allow_commands = "true"` in `SIMULATED_TELESCOPE_SETTINGS`), because the devices are stand-ins on this computer. When the simulator is up, the backend's `ObservatorySourceService` runs a ready hook that puts the app in controller mode (`control.safety.enter_controller_mode(shadow_first=True)`: the correction capabilities are first moved to SHADOWED, the step the rules need). The library's rules still apply. Before the hook takes control it calls `record_simulated_calibrations`, which saves a guider calibration and a focus model into the simulator's own wayfinding library (a copy of yours, made at each start), so every capability can become AUTHORITATIVE. Each value comes from the first source that has it: (1) your newest real record in the library copy, copied under the active equipment names; (2) for the guider, your real guide-log runs (the guide camera's plate scale, and the median calibrated RA and Dec speeds of the last 20 runs that look sensible, 1.5 to 15 arcsec/s), or else the active equipment's plate scale; (3) the INDI simulators' behavior (guide rate 7.52 arcsec/s, camera angle 0, a focuser with no backlash or thermal model). The records are redone at every start, so they follow new real data. Each record's `source_note` starts with `Simulator:` and names its source. The function refuses to run unless the wayfinding library is inside the simulator folder and is not one of your real folders, and it only reads the library it is given. `observatory:get_source` reports the result as `commands` (`allowed`, and `off` with a reason for each), the log lists what stays off once, and the status header shows it in the SIMULATOR chip tooltip. The hook never runs for the real telescope or the offline world, which keep monitor mode. The offline settings keep `allow_commands = "false"`.

- The INDI server and the stand-in PHD2 use free ports on this computer, never 5000, 4400 or 7624.
- `SimulatedObservatory.prepare` refuses a settings file with a host other than `127.0.0.1` or `localhost`, another port, a picture folder outside the stand-in home, any other IP address in the file, or an image library, frames, stacks or wayfinding folder that is outside the simulator folder or is one of your real folders.
- The app checks every settings save against the same rules while a simulator runs (`check_settings_update`).
- If the simulator cannot start, the app runs offline: no telescope, no PHD2, no downloads. It never falls back to the real telescope.
- Every process starts through `supervisor.py`, so none outlives the app, even after a crash. The harness starts them with the same program.

For how the app switches source, read `backend/observatory_source.py`. For the harness, read `build/tpm/README.md`.
