# Installation

*Version 2.0 · 2026-10-15 · Status: current*

This page shows how to install the Astrometrics desktop app from source on Linux, check that it works, update it, and remove it. It is for people who want to run the app. To use only the Python library, see the [library quick start](how_to/library_quick_start.md).

:::{note}
Windows is not supported. The install scripts are written for Ubuntu and use the `apt` package manager.
:::

## What you need

**System.** A Linux computer. The system-package script (`install_ubuntu_deps.sh`) is written for Ubuntu and uses `apt`. It needs `sudo` rights and an internet connection.

**Hardware.** A computer is enough to open and process frames that are already on disk. To capture new frames or move a telescope, you also need a motorized mount and a camera that work with INDI. INDI is the open standard that telescope software uses to talk to mounts, cameras and focusers. The installer adds the INDI packages.

**Software.** The steps below install or set up each item.

| Software | Why the app needs it | How you get it |
| --- | --- | --- |
| Git | Downloads the code | Your package manager |
| Python 3.14 or newer | Runs the backend, the Python program that does the real work | `install_ubuntu_deps.sh` adds it from the deadsnakes package archive |
| Node.js 24 and npm | Runs the app window (built with Electron, a toolkit for desktop apps) | You install it yourself. `install_ubuntu_deps.sh` does not add it |
| Siril | Stacks frames. Stacking combines many frames into one image | `install_ubuntu_deps.sh` |
| INDI and `swig`, `build-essential` and other build tools | Controls the telescope hardware | `install_ubuntu_deps.sh` |
| A plate solver | Works out which part of the sky an image shows | The online astrometry.net service (needs an API key) or a local solver from `install_ubuntu_deps.sh --with-local-solver` |

Node.js 24 is the version the project's own tests use. On Ubuntu under WSL (Windows Subsystem for Linux), `sudo ./build/linux/setup_wsl.sh` installs it from NodeSource.

## Install the app

1. Download the code and move into its folder.

   ```bash
   git clone https://github.com/m1chae2/astrometrics.git
   cd astrometrics
   ```

2. Install the system packages. This takes several minutes.

   ```bash
   sudo ./build/linux/install_ubuntu_deps.sh
   ```

   The script has two optional settings. Run it with `--help` to list them.

   **Siril source.** `--siril-source=apt` is the default. It uses Ubuntu's own package, which stays at the version Ubuntu ships. `--siril-source=flatpak` installs Siril from Flathub, which tracks new releases more closely. It is also the only choice that supports weighted stacking.

   **Local plate solver.** `--with-local-solver` also installs astrometry.net and its index files, so plate solving runs on your computer. Leave it off to use the online service instead.

   The two settings work together:

   ```bash
   sudo ./build/linux/install_ubuntu_deps.sh --siril-source=flatpak --with-local-solver
   ```

3. Install Node.js 24 and npm if `node -v` does not show version 24 or newer. See the table above.

4. Create the Python environment and install the app into it.

   ```bash
   ./build/linux/setup_venv.sh
   ```

   The script makes a `.venv` folder in the repository, installs the app and its Python packages, and copies the configuration template to `~/.config/astrometrics/astrometrics.config.toml` if you have no configuration yet. Running it again is safe. It skips steps that are already done.

5. Edit the configuration file. See the next section.

6. Start the app.

   ```bash
   ./astrometrics.sh start
   ```

   The first start downloads the frontend packages with `npm install` if they are missing. A splash screen shows while the backend loads its star catalog.

## Configure the app

The configuration file is `~/.config/astrometrics/astrometrics.config.toml`. Its default values suit most installs. Four of them depend on choices only you can make. Check each one before you use the app.

| Setting | Section | What to do |
| --- | --- | --- |
| `frames_path` | `[Image Library]` | Set the folder where raw and calibration frames are saved. Use a folder on your local disk. |
| `api_key` | `[Processing.Astrometry.Online Solver]` | Enter your astrometry.net API key. Do this if you did not install the local solver. |
| `siril_executable` | `[Processing.Siril]` | The default, `siril-cli`, is correct for an `apt` install. For a Flathub install, set `flatpak run --command=siril-cli org.siril.Siril`. |
| `stack_weight` | `[Processing.Siril]` | Leave it blank for an `apt` install. For a Flathub install, you can set `wfwhm` to turn on weighted stacking. |

The installer prints the exact values to use when it finishes. It does not edit the file itself.

:::{warning}
Stacking must use Siril's `-cli` command. The plain `siril` and `flatpak run org.siril.Siril` commands start the graphical build, which needs a display even when stacking runs without a window.
:::

:::{warning}
Weighted stacking (`stack_weight = wfwhm`) needs a Siril that accepts the `-weight=` argument of the `stack` command. Ubuntu's `apt` package does not. Stacking then fails with `Unexpected argument to stacking`. This is why `stack_weight` ships blank.
:::

:::{note}
`--with-local-solver` installs index files sized for the camera and telescope in the configuration template. A different camera or telescope sees a different amount of sky and needs index files to match. They are listed by field-of-view size at [data.astrometry.net](https://data.astrometry.net). You can also run `apt-cache search astrometry-data`.
:::

### Storage layout

Astrometrics keeps each kind of file in its own place. The library folders follow your settings. The other folders follow the Linux standard for program files (the XDG Base Directory layout), so a backup tool can skip the disposable ones and a cache can be wiped in one step.

| Tier | What it holds | Example location | Setting |
| --- | --- | --- | --- |
| Working library | Raw and calibration frames, stacks, previews, processed images | `~/Astrometrics/Library` (local disk) | `frames_path`, `stacks_path` |
| Backup copy | The same library, kept in step automatically | `drive:Library` on Google Drive | None |
| Settings | `astrometrics.config.toml` (the Electron app keeps its own browser profile in the same folder) | `~/.config/astrometrics` | None |
| Local data | The databases (`astrometrics.db`, `astrometrics_log.db`), `calibration_frames.json`, the star catalog, the observatory database and Ekos logs (`wayfinding/`), the capability-gap database, your saved scripts (`scripts/`) | `~/.local/share/astrometrics` | `path` (leave empty) |
| Logs | App and Siril logs (`logs/`), backend and Electron console output (`logs/run/`, earlier runs in `logs/run_archive/`), the stop-time backup log | `~/.local/state/astrometrics` | None |
| Disposable files | Scratch files, Siril work folders, Gaia caches, locks, backup staging, integrity manifests | `~/.cache/astrometrics` | None |

The working library is on the local disk, so frames and stacks open at disk speed and work offline. Set `frames_mount_point` only when the library is on a drive that can be missing, such as a USB disk. Keep the databases on the local disk too. SQLite needs real file locking, and a network mount does not provide it reliably.

**Backups.** Optional scripts copy the library and the databases to Google Drive with rclone, a command-line program that syncs files with cloud storage. The `build/linux/install_backup_timer.sh` script sets up the systemd user timers that run them. Each script, its limits and its options are described in `astrometricslib/scripts/README.md`.

**Cleanup.** `./build/linux/clean_caches.sh` deletes disposable files from `~/.cache/astrometrics`. Add `--dry-run` to list what it would remove first.

## Check that it works

1. The last lines of `setup_venv.sh` should show a check mark next to `astrometricslib` and next to `wayfindinglib`. The script stops with an error if either import fails.
2. Start the app and look at its status.

   ```bash
   ./astrometrics.sh start
   ./astrometrics.sh status
   ```

   The status command should print `Electron is running`, `Vite is running` and `Backend is running`. Vite is the development server that feeds the app window.
3. The app window opens after the splash screen. The status dot at the right of the top bar stands for the telescope mount. It is red until a mount is connected, so a red dot on a computer with no hardware is normal.
4. To check telescope control, run `./build/linux/verify_indi_client.sh`. It tests that the real INDI client is active and not the no-op stand-in that `setup_venv.sh` falls back to.

Stop the app with `./astrometrics.sh stop`.

## Update the app

1. Stop the app with `./astrometrics.sh stop`.
2. Get the new code with `git pull`.
3. Run `./build/linux/setup_venv.sh` again, then `npm install`.
4. Start the app with `./astrometrics.sh start`.

The setup script never overwrites an existing configuration file. If a new version adds settings, compare your file with `astrometricslib/astrometrics.config.example.toml`.

## Uninstall

No uninstall script exists. To remove the app by hand:

1. Stop it with `./astrometrics.sh stop`.
2. Delete the repository folder. This also removes `.venv`, `node_modules` and `.run_pids`.
3. Optional: delete the user folders in the storage table. `~/.local/share/astrometrics` holds your databases and saved scripts. Back them up first if you may want them again.
4. Optional: if you ran `build/linux/install_backup_timer.sh`, disable its systemd user units and delete them from `~/.config/systemd/user`. If you ran `build/linux/install_desktop_icon.sh`, delete the launcher it wrote to `~/.local/share/applications`.

The system packages stay installed. Remove them with `apt` only if no other program needs them. Your frames are in the `frames_path` folder, which the steps above leave alone.

## Troubleshooting

**`No Python 3.14+ interpreter found`.** `setup_venv.sh` accepts only Python 3.14 or newer, because the code uses syntax that older versions reject. Install `python3.14`, or set `PYTHON_BIN` to the path of a 3.14 interpreter, and run the script again.

**`Virtual environment ... appears broken`.** Run `./build/linux/setup_venv.sh --force`. It deletes `.venv` and builds it again.

**`pyindi-client install failed`.** The telescope-control package needs system build packages. Without it, the app uses a stand-in and the mount always shows as disconnected. Run `install_ubuntu_deps.sh`, then `setup_venv.sh` again.

**`C extension compilation failed`.** The app falls back to pure-Python code for that step. Install `build-essential` (the installer adds it) and run `setup_venv.sh` again.

**`Backend process died during startup`, or the backend times out.** The script prints the last lines of the backend log. The full log is `~/.local/state/astrometrics/logs/run/backend.log`. The splash screen waits for the backend, so check this log first when it does not go away.

**The app does not start, or a port is in use.** An earlier run may still hold a port. Run `./astrometrics.sh stop`. It stops the app and frees the Vite port (5173 by default), Electron's debug port (9222) and the backend port (5000). It force-ends whatever listens on them.

**`Unexpected argument to stacking`.** Siril is too old for weighted stacking. Leave `stack_weight` blank, or reinstall Siril with `--siril-source=flatpak`.

**Plate solving fails.** Plate solving needs one solver. Install the local one with `--with-local-solver`, or set `api_key` for the online one.

## Next steps

- Take your first session in [Getting started](Getting_Started.md).
- Use the Python library without the app in the [library quick start](how_to/library_quick_start.md).
