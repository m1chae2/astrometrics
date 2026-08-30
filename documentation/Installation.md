# Astrometrics Installation

*Version 1.4 · 2026-08-30 · Status: current*

## 1. Introduction

This guide outlines the installation process for Astrometrics and its dependencies on a Linux system. Once complete, the Desktop App can be launched to capture images.

:::{note}
Windows is not yet supported.
:::

## 2. Requirements

Astrometrics requires **Python 3.14 or newer**. Please ensure this version is installed before proceeding.

## 3. Installing on Linux

A script is provided to install all required system packages, including tools for image stacking and telescope control.

Run the following command in the terminal:

```bash
sudo ./build/linux/install_ubuntu_deps.sh
```

The script takes two optional settings. Run it with `--help` to see them in the terminal.

The first chooses where Siril comes from. Siril is the external program that does the stacking. The default is Ubuntu's own package:

```bash
sudo ./build/linux/install_ubuntu_deps.sh --siril-source=apt
```

Ubuntu's package stays at whatever version Ubuntu ships. To get a newer Siril, install it from Flathub instead. This is also the only option that supports weighted stacking, described in Section 4:

```bash
sudo ./build/linux/install_ubuntu_deps.sh --siril-source=flatpak
```

The second chooses where plate solving runs. Plate solving is the step that works out which part of the sky an image shows. It is off by default, because the alternative is the online [astrometry.net](https://nova.astrometry.net/api_help) service, which needs no local install — only an API key. To solve on your own machine instead, turn it on:

```bash
sudo ./build/linux/install_ubuntu_deps.sh --with-local-solver
```

Plate solving needs one of the two. Neither is set up by default, so pick one and configure it in Section 4.

The two settings are independent, so they can be combined:

```bash
sudo ./build/linux/install_ubuntu_deps.sh --siril-source=flatpak --with-local-solver
```

Next, run the setup script to create the Python environment and install the application:

```bash
./build/linux/setup_venv.sh
```

## 4. Configuration

After installation, the setup script creates a configuration file at `astrometricslib/astrometrics.config`. Its values suit the default install, but four of them depend on choices only you can make. Check each one before proceeding:

- `frames_path` (under `[Image Library]`): The path to the folder where images are saved. Set this to your own folder.
- `api_key` (under `[Processing.Astrometry.Online Solver]`): An astrometry.net API key. Set this if you did not install the local solver.
- `siril_executable` (under `[Processing.Siril]`): The command used to run Siril. It ships as `siril-cli`, which is correct for an apt install. If you installed Siril from Flathub, change it to `flatpak run --command=siril-cli org.siril.Siril`.
- `stack_weight` (under `[Processing.Siril]`): Ships blank, which is correct for an apt install. If you installed Siril from Flathub, you can set it to `wfwhm` to turn on weighted stacking.

The installer prints the exact values to use when it finishes. It does not edit the configuration file itself.

:::{warning}
Stacking must use Siril's `-cli` command. The plain `siril` and `flatpak run org.siril.Siril` commands start the graphical build, which will not run without a display connection, even when stacking headlessly.
:::

:::{warning}
Weighted stacking (`stack_weight = wfwhm`) needs a Siril new enough to accept the `stack` command's `-weight=` argument. Ubuntu's apt package is not, and stacking fails with `Unexpected argument to stacking`. This is why `stack_weight` ships blank. Install Siril with `--siril-source=flatpak` to get a version that supports it.
:::

:::{note}
`--with-local-solver` installs index files sized for the camera and telescope in the configuration template. A different camera or telescope sees a different amount of sky and needs index files sized to match. These are listed by field-of-view size at [data.astrometry.net](https://data.astrometry.net), or run `apt-cache search astrometry-data`.
:::

## 5. Launching the application

To start the Astrometrics Desktop App, run:

```bash
./run_astrometrics.sh
```
