# Installation

*Version 2.2 · 2026-11-27 · Status: current*

This page shows how to install the Astrometrics desktop app from source on Linux, check that it works, update it, and remove it. Windows 11 has its own setup, described in the note below. It is for people who want to run the app. To use only the Python library, see the [library quick start](how_to/library_quick_start.md).

:::{note}
Ubuntu is the main supported system, and the rest of this page is written for it. Windows 11 runs the app from source for work on frames that are already on disk: viewing, stacking with Siril and the quality measurements. Run `build\windows\setup_windows.ps1` once in PowerShell. It needs Python 3.14 and Node.js, sets up a Python environment and the Node packages, and downloads Siril (stacking) and GraXpert (removes the sky's gradient from the preview picture) from their own projects, each checked against a pinned checksum, with no administrator rights needed. Then start the app with `build\windows\run_astrometrics.ps1`, or from the shortcut that `build\windows\install_desktop_icon.ps1` makes. That script also adds Astrometrics to the **Open with** menu of `.fits` and `.fit` files. Opening a file that way starts the app, but the app does not yet show the file it is given.

These parts do not work on Windows yet:

- Telescope control and the simulated observatory. Both need INDI, which is not available on Windows.
- Downloading frames from the telescope computer, which uses `rsync`.
- The local plate solver. Plate solving uses the online astrometry.net service instead, which needs an API key.
- A packaged installer. Windows runs from a copy of the source code.

Windows limits a path to 260 characters unless long paths are turned on, and a deep library folder, for example on Google Drive, can pass that. The setup script warns when they are off and prints the one-line command that turns them on. It needs administrator rights and a restart.

Keep the frames on the same drive as `%LOCALAPPDATA%`, usually `C:`. Siril reads them through hard links, which cost no space or time but work only within one NTFS drive. A stack of frames on another drive, or in a Google Drive or network folder, stops with an error that says so.
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

### Graphics card on a laptop with two cards

On Linux, a laptop with an Intel card and an NVIDIA card can draw the app window on either one. The `gpu` setting in the `[Frontend]` section picks the card. You can also set the environment variable `ASTROMETRICS_GPU` for one start, and it wins over the file.

| Value | Meaning |
| --- | --- |
| `auto` (default) | NVIDIA when the charger is plugged in and the NVIDIA driver for render offload is installed. Intel on battery or without that driver. |
| `intel` | Always the Intel card. |
| `nvidia` | Always the NVIDIA card. Falls back to Intel when the driver is missing. |

The app chooses at start. It cannot switch cards while it runs, so plugging in or unplugging the charger changes nothing until you restart the app. The start-up log (`electron.log`) says which card was asked for and which one the window got. If NVIDIA was chosen but the window did not get it, the app restarts once on Intel.

On Windows the `gpu` setting does nothing. The app asks Windows for the high-performance card (the NVIDIA or AMD card next to an Intel one) every time it starts, on battery too. On a computer with one card this changes nothing. The start-up log, `main.log` in `%LOCALAPPDATA%\astrometrics\logs`, names the card the window got, on the line that starts with `GPU:`.

GraXpert, which removes the sky's gradient from the preview picture, chooses its card on its own. On Windows it uses the graphics card through DirectML, Microsoft's interface for running AI models on any graphics card.

### Siril's tools on an NVIDIA graphics card

Siril's AI tools can run on an NVIDIA graphics card, which is usually faster than the processor. They are GraXpert, StarNet2 (which removes stars from an image) and the Python scripts Siril runs, such as GraXpert-AI and Cosmic Clarity. With the Flathub Siril, each tool needs extra pieces: permissions for the Siril sandbox, NVIDIA's CUDA libraries at fixed versions, a GPU build of ONNX Runtime (the library that runs the AI models) and two small wrapper scripts. `build/linux/setup_siril_gpu.sh` puts each piece in place. It is optional. Without it, the tools run on the processor.

1. Install Siril from Flathub (`--siril-source=flatpak`), open it once and run any script from its **Scripts** menu. Siril then makes its Python environment and settings file.
2. Install GraXpert and StarNet2 in `~/Applications`, or note where they are.
3. See what the script would do. It changes nothing in this step.

   ```bash
   ./build/linux/setup_siril_gpu.sh --dry-run
   ```

4. Close Siril, then run the script.

   ```bash
   ./build/linux/setup_siril_gpu.sh
   ```

   Run it as your own user, not with `sudo`. It downloads 2 to 3 GB of libraries from PyPI. It checks each file against a pinned checksum and refuses any file that does not match. If GraXpert or StarNet2 is somewhere else, add `--graxpert=<path>` or `--starnet=<path>`.

5. Check the result. Every part should show `ready`.

   ```bash
   ./build/linux/setup_siril_gpu.sh --check
   ```

The script adds only settings that are missing and never removes one. It fills the GraXpert setting of the app (`graxpert_executable`) and the StarNet and GraXpert settings of Siril only when they are empty. On a computer without an NVIDIA card, or without the Flathub Siril, it says so and stops without an error. GraXpert 4 also needs NVIDIA's CUDA 13 libraries in `/usr/local/cuda`. The script reports whether they are there but does not install them. The details are in `build/linux/README.md`.

Windows needs none of this. Siril's normal Windows installer has no sandbox to open, and GraXpert uses the graphics card through DirectML.

### Storage layout

Astrometrics keeps each kind of file in its own place. The library folders follow your settings. The other folders follow the Linux standard for program files (the XDG Base Directory layout), so a backup tool can skip the disposable ones and a cache can be wiped in one step. On Windows they are in `%LOCALAPPDATA%\astrometrics`, the folder Windows keeps for each user's program files on this computer. The databases and the settings file are in that folder itself, the disposable files are in `Cache` inside it, and the logs, including the launcher's `logs\run`, are in `logs`. The Electron app's browser profile goes to `Electron` in the same folder, not to the roaming profile (`%APPDATA%`), which Windows copies between computers.

| Tier | What it holds | Example location | Setting |
| --- | --- | --- | --- |
| Working library | Raw and calibration frames, stacks, previews, processed images | `~/Astrometrics/Library` (local disk) | `frames_path`, `stacks_path` |
| Backup copy | The same library, kept in step automatically | `drive:Library` on Google Drive | None |
| Settings | `astrometrics.config.toml` (the Electron app keeps its own browser profile in the same folder) | `~/.config/astrometrics` | None |
| Local data | The databases (`astrometrics.db`, `astrometrics_log.db`), `calibration_frames.json`, the star catalog, the observatory database and Ekos logs (`wayfinding/`), the capability-gap database, your saved scripts (`scripts/`) | `~/.local/share/astrometrics` | `path` (leave empty) |
| Logs | App and Siril logs (`logs/`), backend and Electron console output (`logs/run/`, earlier runs in `logs/run_archive/`), the stop-time backup log | `~/.local/state/astrometrics` | None |
| Disposable files | Scratch files, Siril work folders, Gaia caches, locks, local database snapshots (`backup/`), integrity manifests | `~/.cache/astrometrics` | None |

The working library is on the local disk, so frames and stacks open at disk speed and work offline. Set `frames_mount_point` only when the library is on a drive that can be missing, such as a USB disk. Keep the databases on the local disk too. SQLite needs real file locking, and a network mount does not provide it reliably.

**Backups.** Optional scripts keep two kinds of copy. One copies the library's frames to Google Drive with rclone, a command-line program that syncs files with cloud storage. The other saves snapshots of the databases to the local `backup/` folder, to undo a change to the app that goes wrong. The `build/linux/install_backup_timer.sh` script sets up the systemd user timers that run them. Each script, its limits and its options are described in `astrometricslib/scripts/README.md`.

**Database copies on a synced folder.** Set `backup_path` in `[Image Library]` to keep the database snapshots in a folder that a sync program copies off the computer, such as a Google Drive folder. The databases stay on the local disk. Each snapshot is made there and moved to `backup_path` only when it is complete, so the sync program never uploads a half-written copy. With `backup_path` set, the backend runs the backup every 4 hours while the app is open and once when it stops. This is the way to back up the databases on Windows, which has no systemd timer.

**Cleanup.** `./build/linux/clean_caches.sh` deletes disposable files from `~/.cache/astrometrics`. Add `--dry-run` to list what it would remove first.

### Work without a telescope

To work on the app with your telescope off, open **Settings**, then **Observatory**, and set **Observatory source** to **Simulated**. The app then shows a temporary copy of your library and a fake telescope on this computer. New simulated frames download, analyze and review with the normal code. Your real library is only read: every change goes to a temporary folder in `~/.local/share/astrometrics-simulator`, which is deleted when you switch back to **Real**.

Switching restarts the backend and waits until the fake telescope is up. A **SIMULATOR** chip stays in the top bar; **SIMULATOR FAILED** means it could not start, and the app then has no telescope at all (never your real one). It needs `fuse-overlayfs`, `indiserver`, the `indi_simulator_*` drivers and `rsync`, and does not run in the packaged app. It does not run on Windows either, where INDI is not available: the **Simulated** button is turned off, and its tooltip says why.

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

On Windows, in PowerShell:

1. Close the app, or stop its services with `build\windows\run_astrometrics.ps1 -Stop`.
2. Get the new code with `git pull`.
3. Run `build\windows\setup_windows.ps1` again. It updates the Python and Node packages and rebuilds the UI MCP server. When the new code pins a newer Siril or GraXpert, it sets that version up. It never replaces a Siril or GraXpert you installed yourself.
4. Start the app from its shortcut.

The setup script never overwrites an existing configuration file. If a new version adds settings, compare your file with `astrometricslib/astrometrics.config.example.toml`.

## Uninstall

No uninstall script exists. To remove the app by hand:

1. Stop it with `./astrometrics.sh stop`.
2. Delete the repository folder. This also removes `.venv`, `node_modules` and `.run_pids`.
3. Optional: delete the user folders in the storage table. `~/.local/share/astrometrics` holds your databases and saved scripts. Back them up first if you may want them again.
4. Optional: if you ran `build/linux/install_backup_timer.sh`, disable its systemd user units and delete them from `~/.config/systemd/user`. If you ran `build/linux/install_desktop_icon.sh`, delete the launcher it wrote to `~/.local/share/applications`.

The system packages stay installed. Remove them with `apt` only if no other program needs them. Your frames are in the `frames_path` folder, which the steps above leave alone.

On Windows:

1. Close the app, then run `build\windows\run_astrometrics.ps1 -Stop`.
2. Delete the `Astrometrics` shortcuts from the Desktop and the Start menu, and unpin it from the taskbar. Also delete the hidden `Electron.lnk` in `%APPDATA%\Microsoft\Windows\Start Menu\Programs`, which the app needs to show notifications.
3. Delete the repository folder. This also removes `.venv-win` and `node_modules`.
4. Optional: delete `%LOCALAPPDATA%\astrometrics`. It holds your databases and settings, the Siril and GraXpert that setup installed, the logs (`logs`), the scratch files (`Cache`) and the app's browser profile (`Electron`). Back up the database files first if you may want them again.
5. Optional: delete `~\.config\astrometrics`, which holds the settings, and the link handler the app registered, with `reg delete HKCU\Software\Classes\astrometrics /f`.

Your frames, and any database copies in `backup_path`, are in folders that the steps above leave alone.

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
