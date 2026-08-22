# Astrometrics Installation

*Version 1.0 · 2026-08-07 · Status: current*

## Abstract

This document covers obtaining a working Astrometrics installation on Linux and Windows, including the system libraries the observatory-control and image-processing paths depend on. It is written for an observer setting up the suite for the first time. Once installation completes, `Getting_Started.md` walks through a first session against the libraries.

## 1. Introduction

**Statement of need.** Astrometrics spans a Python science library, an observatory-control library, and a desktop application, and it delegates two of its heaviest jobs to external programs: frame calibration and stacking to Siril [1], and mount and device control to INDI [2]. Those delegations mean a working installation is not a single `pip install` — the external programs and their development headers must be present before the Python environment is built. This document states what is required, why each piece is required, and how to verify the result.

## 2. Requirements

### 2.1 Python

Astrometrics requires **Python 3.14 or newer**. This is a hard floor rather than a preference: the codebase uses parenthesis-less multi-exception handling (`except A, B:`), which earlier interpreters reject at parse time. An installation attempted on Python 3.13 or earlier fails when the package is first imported, not when it is installed.

> [!WARNING]
> A virtual environment created from an older interpreter appears to install successfully and only fails later, at import. The provided setup script refuses interpreters below 3.14 for this reason; when installing by hand, verify the version first.

### 2.2 System libraries

The scientific and control stacks link against libraries that are installed through the operating system rather than through Python. Table 1 lists them and the capability each one enables.

**Table 1.** External system dependencies.

| Package | Enables | Required for |
| :--- | :--- | :--- |
| `build-essential`, `swig` | Compilation of the INDI Python bindings | Observatory control |
| `libcfitsio-dev` | FITS input and output | Image processing |
| `libnova-dev` | Celestial mechanics routines used by INDI | Observatory control |
| `libdbus-1-dev`, `libglib2.0-dev` | Desktop message bus used by the INDI stack | Observatory control |
| `indi-full`, `libindi-dev` | INDI server, device drivers, and headers | Observatory control |
| Siril | Frame calibration, registration, and stacking | Image processing |

Only the image-processing packages are needed to use `astrometricslib` on existing data. The INDI packages are required to drive real hardware through `wayfindinglib`; without them the library still imports and plans observations, because the bindings are substituted by a stub when absent.

## 3. Installing on Linux

Install the system dependencies, adding the INDI project's package archive for the observatory-control stack:

```bash
sudo apt-get update
sudo apt-get install -y build-essential swig libcfitsio-dev libnova-dev libdbus-1-dev libglib2.0-dev
sudo add-apt-repository -y ppa:mutlaqja/ppa
sudo apt-get install -y indi-full libindi-dev
```

Create the virtual environment and install the project:

```bash
./build/linux/setup_venv.sh
```

The script locates a Python 3.14 interpreter, creates `.venv`, installs the project and its dependencies, and seeds a local configuration file from the tracked template if one does not already exist.

Install the INDI Python bindings, which are distributed separately from the project's own dependencies:

```bash
.venv/bin/pip install pyindi-client
```

## 4. Installing on Windows

Windows installation runs the suite under the Windows Subsystem for Linux, because the INDI server is a Linux program:

```powershell
.\scripts\windows\setup_windows.ps1
```

Observers who only intend to process existing images, and never to control hardware, may instead create a native virtual environment and install the project with `pip install .`, omitting the INDI packages entirely.

## 5. Configuration

Installation produces `astrometricslib/astrometrics.config` from the tracked template. Two settings must be reviewed before first use:

- `frames_path` under `[Image Library]` — the absolute path to the directory holding captured frames, which is expected to contain `lights`, `darks`, `flats`, and `biases` subdirectories.
- `api_key` under `[Processing.Astrometry.Online Solver]` — an API key for the online plate-solving service, required only when solving online rather than with a locally installed solver.

The configuration file holds machine-specific paths and a credential, and is therefore excluded from version control. The template beside it, `astrometrics.config.example`, is the tracked copy.

## 6. Verifying the installation

Confirm that both libraries import and report a version:

```bash
.venv/bin/python -c "import astrometricslib, wayfindinglib; print(astrometricslib.__version__, wayfindinglib.__version__)"
```

Run the test suites:

```bash
.venv/bin/python -m pytest
```

Tests that require hardware or observational data absent from the machine report as skipped rather than failed, so a run with skips is a normal result.

Launch the desktop application:

```bash
./build/linux/run_astrometrics.sh start
```

## Acknowledgments

Astrometrics builds on Siril [1] for frame calibration and stacking, the INDI framework [2] for device control, and Astropy [3] for coordinate handling, FITS access, and time systems.

## References

[1] C. Richard et al., "Siril: an astronomical image processing software," Siril Project, 2024.

[2] E. C. Downey and J.-L. Gach, "INDI: Instrument-Neutral Distributed Interface," INDI Library Project, 2023.

[3] Astropy Collaboration, "The Astropy Project: Sustaining and Growing a Community-oriented Open-source Project," *Astrophys. J.*, vol. 935, no. 2, p. 167, 2022.
