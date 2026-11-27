# Library quick start

*Version 1.1 · 2026-11-26 · Status: current*

## Abstract

This guide shows how to install the `astrometricslib` Python package, set up its configuration file, and make a first call to the public API. It is for programmers who want to use the library without the desktop application. Section 4 shows how to move a function you tried in the app's Command Console into the library. The calls in Section 3 were checked against the library's signatures with `inspect.signature`. They were not executed for this guide, because they need a frame library on disk and Siril installed.

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

## 1. Install

The library needs Python 3.14 or newer. [Installation](../Installation.md) lists the system packages (Siril and the plate solvers). Then create the environment from the repository root:

```bash
./build/linux/setup_venv.sh
```

The script installs the package into `.venv` and copies the configuration template to `~/.config/astrometrics/astrometrics.config.toml` when no configuration exists yet.

## 2. Configure

The template is `astrometricslib/astrometrics.config.example.toml`. Edit the copy in `~/.config/astrometrics`, not the template. Set `frames_path` under `["Image Library"]` to the folder that holds your raw frames, and set the plate-solver and Siril entries described in [Installation](../Installation.md#configure-the-app). The environment variable `ASTROMETRICS_CONFIG_PATH` points the library at a different file.

## 3. Make a first call

`get_configuration` loads the configuration once per process. The `Astrometrics` class takes it and exposes the sub-APIs `targets`, `stars`, `processing`, `visualization` and `jobs`.

```python
from astrometricslib import Astrometrics, get_configuration

config = get_configuration()
astrometrics = Astrometrics(config)

# A target is one object in the sky, with its frames.
targets = astrometrics.targets.query(include_empty=True)
target = astrometrics.targets.get("M 13")

# Bring new frames into the library, then stack the light frames.
astrometrics.targets.frames.add(target, ["/path/to/M_13_Light_001.fits"])
result = astrometrics.processing.stack(target)
print(result.stacked_path, result.frames_selected)

# Look at the stack with the detected stars and the named stars.
figure = astrometrics.visualization.render_fits(
    result.stacked_path, target=target, kind="figure", overlays=["detections", "stars"]
)
```

`targets.get` returns `None` when the target does not exist. Create it first with `astrometrics.targets.create("M 13")`. The `processing.stack` call runs the same stage as the Stack button in the app and returns a `StackResult`. The `visualization.render_fits` call with `kind="figure"` draws the stack as a Matplotlib figure with the app's stretch. Its `overlays` mark the stars the astrometry run detects and name the stars that have photometry, as Image Processing does. Without `overlays` it draws only the picture. A notebook shows the figure in the page. The app's Command Console already holds `astrometrics` and `wayfinder`, so the same lines run there without the first two.

## 4. Promote a console function into the library

The Command Console is the place to try an analysis idea. A function that proves useful then moves into `astrometricslib`, so the app, scripts and the AI tools share one copy.

1. Write the prototype with public calls only. Besides the sub-APIs, `astrometrics.quality.blocks` offers small building blocks that take a FITS path or a pixel array and give 1-based pixel positions: `measure_sky`, `find_stars`, `star_widths`, `measure_sky_gradient`, `measure_aperture`, `sky_corners`, `solved_stars`, `robust_sigma` and `sigma_clip`. For example, `astrometrics.quality.blocks.find_stars(path).stars` lists the stars the astrometry run finds. Do not copy library code into the script.
2. Put the algorithm in `astrometricslib/pipelines/shared/` when several pipelines need it, or else in the package of the stage it serves, such as `pipelines/stacking/post_processing/`. Call the existing owners (the sky measurement, the robust spread) instead of writing new ones.
3. Add a method on the sub-API whose work it matches, in `astrometricslib/api/`. It returns a Pydantic result model with plain Python numbers. Pin it in `astrometricslib/test/test_public_surface.py` and describe it in `astrometricslib/api/README.md`.
4. Make each tuned number a `TunedParameter` with a validation-register entry (see [Add a register entry](add_a_register_entry.md) and [Change a tuned value](change_a_tuned_value.md)). List a plain constant in the parameter inventory.
5. Write the tests and the docstrings: a description at the top of each file and a docstring on every function and test.
6. Decide what the AI tools do with the method in `mcp_servers/inventory/tool_dispositions.py`. A public method with no decision fails a test. Then run `.venv/bin/python -m mcp_servers.inventory --write-runtime-manifests` and `.venv/bin/python build/mcp/generate_client_configs.py`.

## 5. Where to go next

- The [tutorial notebooks](../notebooks/index.md) walk through each pipeline with real frames.
- The [API reference](../api/astrometricslib.rst) lists every public name.
- [Tune for your setup](tune_for_your_setup.md) explains how to adapt the tuned values to your camera.
