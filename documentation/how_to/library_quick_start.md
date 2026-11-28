# Library quick start

*Version 1.2 · 2026-11-27 · Status: current*

## Abstract

This guide shows how to install the `astrometricslib` Python package, set up its configuration file, and make a first call to the public API. It is for programmers who want to use the library without the desktop application. Section 4 shows how to write a Command Console script so that a useful function can move into the library, and how to move it. The calls in Section 3 were checked against the library's signatures with `inspect.signature`. They were not executed for this guide, because they need a frame library on disk and Siril installed.

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

`get_configuration` loads the configuration once per process. The `Astrometrics` class takes it and exposes the sub-APIs `observatories`, `targets` (with `targets.frames`), `stars`, `calibration`, `processing` (one object per pipeline, such as `processing.stacking`), `quality`, `visualization` and `jobs`. The [public API design guide](../library_design/Public_API_Design_Guide.md) gives the rules they follow.

```python
from astrometricslib import Astrometrics, get_configuration

config = get_configuration()
astrometrics = Astrometrics(config)

# A target is one object in the sky, with its frames.
targets = astrometrics.targets.query(include_empty=True)
target = astrometrics.targets.get("M 13")

# Bring new frames into the library, stack the light frames, then make the picture.
astrometrics.targets.frames.add(target, ["/path/to/M_13_Light_001.fits"])
result = astrometrics.processing.stacking.run(target)
astrometrics.processing.enhancement.run(target)
print(result.stacked_path, result.frames_selected)

# Look at the stack with the detected stars and the named stars.
figure = astrometrics.visualization.plot_image(result.stacked_path, overlays=["detections", "stars"])
```

`targets.get` returns `None` when the target does not exist. Create it first with `astrometrics.targets.create("M 13")`. The `processing.stacking.run` and `processing.enhancement.run` calls run the two pipelines behind the Stack button in the app: the first returns a `StackResult`, the second makes the stack's picture. The `visualization.plot_image` call draws the stack as a Matplotlib figure with the app's stretch, with its axes counting pixels from 1. (`visualization.render` gives the same picture as PNG bytes.) Its `overlays` mark the stars the astrometry run detects and name the stars that have photometry, as Image Processing does. Without `overlays` it draws only the picture. A notebook shows the figure in the page. The app's Command Console already holds `astrometrics` and `wayfinder`, so the same lines run there without the first two.

## 4. Promote a console function into the library

The Command Console is the place to try an analysis idea, by hand or with a script the AI companion writes. A function that proves useful then moves into `astrometricslib`, so the app, scripts and the AI tools share one copy. Two rules make the move a lookup rather than a rewrite.

- *Every public method names its owner.* Each method of the public API is a thin wrapper of one internal function, its *owner*, and its docstring names it on an `Owner:` line. `help(astrometrics.targets.frames.query)` in the console shows it. Library code may not call the public objects, so it calls the owners.
- *A console script keeps its analysis in a function with explicit inputs.* The console-only helpers (`selection`, `show`, `doc`) and printing appear only in a few driver lines at the end. `selection.target` is the library's `Target` and `selection.star` gives `x_px` and `y_px` counted from 1, the same types and pixel count the library uses, so the function does not change when its inputs come from elsewhere.

A console script that follows the convention:

```python
def measure_night_sky(target, night):
    """Measure the sky level of each frame of one observing night."""
    frames = astrometrics.targets.frames.query(target, night=night)
    return [astrometrics.quality.blocks.measure_sky(frame.path) for frame in frames]


levels = measure_night_sky(selection.target, "2026-11-20")
print([round(sky.level, 1) for sky in levels if sky is not None])
```

The same function in the library. Each public call is replaced with the owner its docstring names. The owner of `targets.frames.query` also takes the configuration, which the public object held, so it becomes an input:

```python
from astrometricslib.pipelines.stacking.building_blocks import measure_image_sky
from astrometricslib.pipelines.stacking.frame_queries import query_frames


def measure_night_sky(target, night, config):
    """Measure the sky level of each frame of one observing night."""
    frames = query_frames(target, config, night=night)
    return [measure_image_sky(frame.path) for frame in frames]
```

The driver lines stay in the console. The steps:

1. Write the prototype with public calls only, following the convention above. Besides the sub-APIs, `astrometrics.quality.blocks` offers small building blocks that take a FITS path, a frame record or a pixel array and give 1-based pixel positions: `measure_sky`, `measure_stars`, `measure_sky_gradient`, `measure_aperture`, `measure_sky_corners`, `measure_robust_sigma` and `measure_clipped_values`. `astrometrics.quality.detections` measures single stars (`measure_profile`, `measure_widths`). Do not copy library code into the script.
2. Move the function into `astrometricslib/pipelines/shared/` when several pipelines need it, or else into the package of the stage it serves, such as `pipelines/stacking/post_processing/`. Replace each public call with the owner named on its `Owner:` line.
3. Make each number you chose a `TunedParameter` with a validation-register entry (see [Add a register entry](add_a_register_entry.md) and [Change a tuned value](change_a_tuned_value.md)). List a plain constant in the parameter inventory.
4. Write the tests and the docstrings: a description at the top of each file and a docstring on every function and test. Update the README of the package the function joins.
5. If a public method is wanted, add a thin wrapper on the sub-API whose work it matches, in `astrometricslib/api/`, whose docstring names the new function as its owner. It returns a typed result model with plain Python numbers. `astrometricslib/test/test_public_surface.py` checks its name, arguments and owner; describe it in `astrometricslib/api/README.md`.
6. Decide what the AI tools do with a new public method in `mcp_servers/inventory/tool_dispositions.py`. A public method with no decision fails a test. Then run `.venv/bin/python -m mcp_servers.inventory --write-runtime-manifests` and `.venv/bin/python build/mcp/generate_client_configs.py`.

## 5. Where to go next

- The [tutorial notebooks](../notebooks/index.md) walk through each pipeline with real frames.
- The [API reference](../api/astrometricslib.rst) lists every public name.
- [Tune for your setup](tune_for_your_setup.md) explains how to adapt the tuned values to your camera.
