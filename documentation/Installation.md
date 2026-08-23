# Astrometrics Installation

*Version 1.1 · 2026-08-22 · Status: current*

## Abstract

This document covers installing Astrometrics on Linux, including the prerequisite system libraries and build tools. Once installation completes, `Getting_Started.md` walks through using the application.

> [!NOTE]
> Windows is not yet a supported installation target

## 1. Introduction

Astrometrics spans a Python science library, an observatory-control library, and a desktop application, and it delegates two of its heaviest jobs to external programs: frame calibration and stacking to Siril [1], and mount and device control to INDI [2]. Those delegations mean a working installation is not a single `pip install` — the external programs and their development headers must be present before the Python environment is built. This document states what is required, why each piece is required, and how to verify the result.

## 2. Requirements

### 2.1 Python

Astrometrics requires **Python 3.14 or newer**.

> [!WARNING]
> A virtual environment created from an older interpreter appears to install successfully and only fails later, at import. The provided setup script refuses interpreters below 3.14 for this reason; when installing by hand, verify the version first.

### 2.2 System libraries

The scientific and control stacks link against libraries that are installed through the operating system rather than through Python. Table 1 lists them and the capability each one enables.

**Table 1.** External system dependencies.

| Package | Enables | Required for Library |
| :--- | :--- | :--- |
| `build-essential`, `swig` | Compilation of the INDI Python bindings | `wayfindinglib` (Observatory control)* |
| `libcfitsio-dev` | FITS input and output | `astrometricslib` (Image processing) |
| `libnova-dev` | Celestial mechanics routines used by INDI | `wayfindinglib` (Observatory control)* |
| `libdbus-1-dev`, `libglib2.0-dev` | Desktop message bus used by the INDI stack | `wayfindinglib` (Observatory control)* |
| `indi-full`, `libindi-dev` | INDI server, device drivers, and headers | `wayfindinglib` (Observatory control)* |
| Siril | Frame calibration, registration, and stacking | `astrometricslib` (Image processing) |

*\* Without these packages, `wayfindinglib` still imports and plans observations, but hardware control is substituted by a silent no-op stub.*

## 3. Installing on Linux

Ubuntu requires several PPAs for Python 3.14 and the INDI framework, along with several system-level dependencies. A convenience script is provided to add the necessary repositories and install all required packages:

```bash
sudo ./build/linux/install_ubuntu_deps.sh
```

Create the virtual environment and install the project:

```bash
./build/linux/setup_venv.sh
```

The script creates a Python 3.14 `.venv`, installs the project, seeds a local configuration file, and automatically verifies that the libraries import cleanly. It also attempts to install the INDI Python bindings (`pyindi-client`). If this fails, setup logs a warning instead of aborting, since `wayfindinglib` falls back to a no-op stub. If observatory control is needed and setup logged this warning, install the bindings directly and verify:

```bash
.venv/bin/pip install pyindi-client
./build/linux/verify_indi_client.sh
```

## 4. Configuration

Installation produces `astrometricslib/astrometrics.config` from the tracked template. Two settings must be reviewed before first use:

- `frames_path` under `[Image Library]` — the absolute path to the directory holding captured frames, which is expected to contain `lights`, `darks`, `flats`, and `biases` subdirectories.
- `api_key` under `[Processing.Astrometry.Online Solver]` — an Astrometry.net API key (from https://nova.astrometry.net/api_help), required only when solving online rather than with a locally installed solver.

## 5. Launching the application

Launch the desktop application:

```bash
./astrometrics.sh start
```

## Acknowledgments

Astrometrics builds on Siril [1] for frame calibration and stacking, the INDI framework [2] for device control, and Astropy [3] for coordinate handling, FITS access, and time systems.

## References

[1] C. Richard et al., "Siril: an astronomical image processing software," Siril Project, 2024.

[2] E. C. Downey and J.-L. Gach, "INDI: Instrument-Neutral Distributed Interface," INDI Library Project, 2023.

[3] Astropy Collaboration, "The Astropy Project: Sustaining and Growing a Community-oriented Open-source Project," *Astrophys. J.*, vol. 935, no. 2, p. 167, 2022.
