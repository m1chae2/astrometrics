# Astrometrics Installation

*Version 1.1 · 2026-08-22 · Status: current*

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

Next, run the setup script to create the Python environment and install the application:

```bash
./build/linux/setup_venv.sh
```

## 4. Configuration

After installation, the setup script creates a configuration file at `astrometricslib/astrometrics.config`. Two settings should be verified before proceeding:

- `frames_path` (under `[Image Library]`): The path to the folder where images are saved.
- `api_key` (under `[Processing.Astrometry.Online Solver]`): An Astrometry.net API key, if the online solver is to be used.

## 5. Launching the application

To start the Astrometrics Desktop App, run:

```bash
./run_astrometrics.sh
```
