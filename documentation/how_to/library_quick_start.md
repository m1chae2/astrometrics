# Library quick start

*Version 1.0 · 2026-10-15 · Status: current*

## Abstract

This guide shows how to install the `astrometricslib` Python package, set up its configuration file, and make a first call to the public API. It is for programmers who want to use the library without the desktop application. The calls in Section 3 were checked against the library's signatures with `inspect.signature`. They were not executed for this guide, because they need a frame library on disk and Siril installed.

Terms are defined in the [glossary](../library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## 1. Install

The library needs Python 3.14 or newer. [Installation](../Installation.md) lists the system packages (Siril and the plate solvers). Then create the environment from the repository root:

```bash
./build/linux/setup_venv.sh
```

The script installs the package into `.venv` and copies the configuration template to `~/.config/astrometrics/astrometrics.config.toml` when no configuration exists yet.

## 2. Configure

The template is `astrometricslib/astrometrics.config.example.toml`. Edit the copy in `~/.config/astrometrics`, not the template. Set `frames_path` under `["Image Library"]` to the folder that holds your raw frames, and set the plate-solver and Siril entries described in [Installation](../Installation.md#4-configuration). The environment variable `ASTROMETRICS_CONFIG_PATH` points the library at a different file.

## 3. Make a first call

`get_configuration` loads the configuration once per process. The `Astrometrics` class takes it and exposes the sub-APIs `targets`, `stars`, `processing`, `visualization` and `jobs`.

```python
from astrometricslib import Astrometrics, get_configuration

config = get_configuration()
astrometrics = Astrometrics(config)

# A target is one object in the sky, with its frames.
targets = astrometrics.targets.list()
target = astrometrics.targets.get("M 13")

# Register frames that are already on disk, then stack the light frames.
astrometrics.targets.reindex_frames(target, paths=["/path/to/M_13_Light_001.fits"], role="LIGHT")
result = astrometrics.processing.stack(target)
print(result.stacked_path, result.frames_selected)
```

`targets.get` returns `None` when the target does not exist. Create it first with `astrometrics.targets.create("M 13")`. The `processing.stack` call runs the same stage as the Stack button in the app and returns a `StackResult`.

## 4. Where to go next

- The [tutorial notebooks](../notebooks/index.md) walk through each pipeline with real frames.
- The [API reference](../api/astrometricslib.rst) lists every public name.
- [Tune for your setup](tune_for_your_setup.md) explains how to adapt the tuned values to your camera.
