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
| `settings.py` | The settings text that points the app at the stand-ins, or at nothing (offline). |
| `standin_ssh.sh` | An `ssh` stand-in that runs the command in the stand-in home folder. With `ASTROMETRICS_STANDIN_SSH_MODE=refuse` it refuses every connection. |

## The library copy

- The databases are copied at switch time (the copy time is in `observatory:get_source`, `library_copy`). Paths inside the copies that name your real frames or stacks folder point at the overlay.
- The overlay's lower layer is your real folder (read only). Every write lands in `<root>/overlay/upper`. A stacks folder inside the library shares the library's overlay; a separate one gets its own.
- Leaving the mode, or stopping the app, unmounts the overlay and deletes the upper layer and the copies, only inside the simulator folder. A start in Real mode removes what a crash left.

## Safety

- The INDI server and the stand-in PHD2 use free ports on this computer, never 5000, 4400 or 7624.
- `SimulatedObservatory.prepare` refuses a settings file with a host other than `127.0.0.1` or `localhost`, another port, a picture folder outside the stand-in home, any other IP address in the file, or an image library, frames, stacks or wayfinding folder that is outside the simulator folder or is one of your real folders.
- The app checks every settings save against the same rules while a simulator runs (`check_settings_update`).
- If the simulator cannot start, the app runs offline: no telescope, no PHD2, no downloads. It never falls back to the real telescope.
- Every process starts through `supervisor.py`, so none outlives the app, even after a crash. The harness starts them with the same program.

For how the app switches source, read `backend/observatory_source.py`. For the harness, read `build/tpm/README.md`.
