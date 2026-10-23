# API

This folder holds the library's front door: the classes and functions that scripts, backend services and the MCP tools actually call. MCP (Model Context Protocol) tools are the adapters through which AI agents call the library. Each file wraps the pipelines and drivers underneath in a stateful, easy-to-use interface. Nothing outside `astrometricslib` should import from this folder directly. Code outside imports from the top-level `astrometricslib` package instead, which re-exports the public names.

Terms are defined in the [glossary](../../documentation/Glossary.md). This document also defines each term the first time it appears.

A target is one sky object the library tracks, with its raw frames (single camera exposure files) and results. A stack is the image made by aligning and averaging many frames.

## What each file is for

The `Astrometrics` class in the package's `__init__.py` (the "root") builds one object per file below and exposes each as an attribute, such as `astrometrics.stars` or `astrometrics.processing`. Each of these objects is a sub-API.

Each sub-API checks its arguments and hands the work to `pipelines/`. Three rules hold for every sub-API:

- A method that takes a target accepts its id or the `Target`. An id that names no target raises `NotFoundError`.
- A method that offers variants picks one with `kind=` and adds optional sections with `include=`. It refuses an argument that the chosen variant does not use.
- A method that builds a new answer returns a Pydantic model (a Python class with typed, validated fields), for example `TargetQueryResult` or `StackResult`. It does not return a dictionary.

### `targets.py`

`TargetCatalog` creates, reads, saves and deletes targets.

- `get` returns a `Target` for code to work with.
- `query` returns short records for people and AI clients. `detail="cameras"` adds the cameras used. `detail="camera_index"` adds each target's frames per configured camera, built by `pipelines/shared/target_camera_index.py`.
- `reindex_frames` brings frame records up to date with the files on disk:
  - Given `paths`, it adds only those files. A finished `.jpg`, `.png` or `.tiff` image becomes the target's processed image instead of a frame.
  - Given a target, it rescans that target's folder.
  - Given neither, it rescans every target folder and creates a target for each new folder.

### `stars.py`

`StellarCatalog` reads and changes the star records.

- `get` returns one star's full record.
- `query` answers every read about many stars. It selects by ids, name, target, sky region or position, with a cap on the size of the answer.
  - `detail` picks what comes back: ids, summaries, analysis records, full records, class counts, per-target counts, library statistics, or the stars placed in pixels on a target's stacked image (`detail="overlay"`, built by `pipelines/shared/star_overlay.py`).
  - Filters cover magnitude (the astronomer's brightness scale, where larger numbers are fainter), whether a star has a spectrum or photometry, spectral class, and a search text. The spectral class is the letter of the star's type in the O, B, A, F, G, K, M sequence, hottest to coolest. A full type such as `G2V` counts as `G`.
  - A filter on "real catalog magnitude" keeps stars whose magnitude came from a published catalog. A zero, a missing value, or an instrumental magnitude (one measured by this library's photometry, on its own uncalibrated scale) does not count.
  - `order` sorts by id, by usefulness (stars with spectra first, then named stars, then stars with photometry, then brightest first), or by spectrum match (stars whose own spectrum best matches a reference spectrum first).
- Single-frame detections are hidden unless `include_unresolved=True`. Photometry (brightness measurement) saves each point source it finds in one frame under an id ending in `:Star_<n>`. These are working records, not catalog stars, so a normal query leaves them out.
- `plate_solve` works out where a FITS frame (the standard astronomy image file) points, using the stars in it. It returns the pixel-to-sky mapping (a WCS, World Coordinate System), or `None`. The centering step in wayfindinglib uses it to point the telescope exactly at a target.
- `detect_point_sources` finds stars in a pixel array.
- Every other method on `StellarCatalog` changes star records.

### `processing.py`

`processing.py` holds `ProcessingPipelines` and its child `CalibrationCatalog`. `quality.py` holds `QualityRecords`, which is a sub-API of its own (see below), not a child of `ProcessingPipelines`.

`ProcessingPipelines` makes stacks and runs the analysis.

- `stack` chooses a target's frames and stacks them. It chooses by filter (the colored or clear glass in front of the camera that passes one band of light), file range, time window and camera, or takes an exact list. `plan_only=True` only reports the choice.
- `remake_preview` makes a stack's preview image again without restacking.
- `process_target` has two forms:
  - For one target, it runs the analysis stages (`astrometry`, `photometry`, `spectroscopy` and `asteroids`) on the frames and stack the target already has. It does not stack.
  - For a list of targets, or for every target, it runs the full pipeline: stacking first, then the analysis stages.
- A restack keeps the stack it replaces in a `_previous` folder. `discard_previous_stack` deletes it and `swap_with_previous_stack` puts it back.
- `record_frame_measurements(target)` measures a target's light frames and stores the numbers in the quality database (read them with `Astrometrics.quality.query`). It reads each frame once and measures the stars' width and stretch, the sky and its noise in electrons, the read noise and the temperatures. A value it cannot measure is stored as `not_measured` with the reason. A frame whose file has not changed since its last measurement is skipped, so a second call measures nothing again. A frame that fails, for any reason, is counted and tried again next time. It never moves or changes a frame file. Only one run measures a target at a time, and one run reads frames at a time; a second call for the same target returns at once with `already_running` set. A sync measures only the frames it added, at about 11 s per frame. It runs as a background job of type `quality` and returns a `FrameMeasurementSummary`. A sync that adds frames starts it by itself. See [frame measurement](../pipelines/stacking/README.md#frame-measurement) and. The star measurements are a design estimate (see [AST-17](../../documentation/library_design/validation/astrometry.md#ast-17)).
- `restore_excluded_frames(target, apply=True)` moves frames the stacker set aside back into the frames folder. The stacker sets a frame aside, into an `_excluded` folder, when clouds or trailed stars (stars smeared into short lines) make it unusable.

### `quality.py`

`QualityRecords` is reached as `astrometrics.quality`. It is the start of every quality read, whether the numbers were stored by an earlier run or are measured now. Nothing it does changes data.

Stored reads:

- `query` reads the quality measurements that earlier runs saved in `astrometrics.db`. It filters by `metric`, `level` (`frame`, `stack` or `run`), `target_id`, `observing_night_id`, `camera`, `telescope` and `since`. It gives the newest row for each subject and metric unless `latest_only=False`, and `row_ids` returns exactly the rows a report cited. A library with no quality table yet gives an empty list; the read opens the file read-only and creates no file and no table. A `metric` that is not registered raises `InvalidArgumentError`.
- `metrics` lists the declared metrics, or with `name=` just that one, or with `checkpoint=` only those measured at that checkpoint. An unknown `name`, `checkpoint` or `detail` raises `InvalidArgumentError`. About 170 metrics are registered, so the default `detail="summary"` gives a short `MetricSummary` for each (name, unit, checkpoint, levels, purpose). `detail="full"` gives the whole `MetricSpec`, which adds the meaning, which way is better and how the number is estimated; pair it with `checkpoint=` or `name=`.

Live checks:

- `measure_frames` measures raw frames:
  - `kind="input_quality"` measures them one by one.
  - `kind="raw_check"` checks a folder or a target's frames as a batch.
  - `kind="quarantine_preview"` previews which frames the stacker would set aside.
  - `include=["excluded"]` adds the frames already set aside.
- `measure_stack` measures a stack (star width, the share of rejected pixels, frame registration) and compares it with the previous stack or another stack file.
- `check_spectral_frames` measures a target's raw slitless-spectrum frames. These are frames taken through a grating with no slit, which spreads each star into a spectrum trail of colors. For each frame it reports:
  - where the zero order (the undispersed image of the star) sits;
  - the tilt and width of the spectrum trail, and the sky level;
  - every saturated patch, that is, pixels at the sensor's maximum value, where the true brightness is lost.

  Given `predict_exposure_seconds`, it scales the measured peaks to that exposure and says whether the spectrum would saturate ("clip") at that exposure.
- `flag_value_outliers` marks the entries of a list that lie more than a given number of standard deviations from the mean.

`CalibrationCatalog` tracks the dark, bias and flat frames (the calibration frames that measure the camera itself; see `drivers/README.md`).

- `query` reports counts, or how a target's light frames (frames of the sky) match the darks.
- `refresh("flat")` rescans one kind and reports what it found. For flats it also says whether each new flat set is good enough: enough frames, bright enough but not too bright, and a master flat (the combination of the set) that is not too noisy.
- `assess_flats` runs the same check on the flats already in the library.
- `measure_sensor_noise(camera_id, gain, offset, binning)` measures the camera's conversion gain (electrons per ADU, one step of the stored pixel value) and its read noise (the electronic noise added when a frame is read out) from the flat and bias frames in the library at exactly that setting. It pairs the frames in file-name order, so it needs at least 5 flat pairs and 3 bias pairs that share one setting, one flat exposure and one temperature. If it cannot measure, the result says why and holds no numbers. Reading never writes. With `save=True` a measured result goes into the camera profile in the configuration file, after a backup. The result's `provenance` sentence is stored as the value's source. Set `exposure_seconds`, `temperature`, `telescope` or `filter_type` to choose among flats of several kinds. It runs as a background job. Its result matched a hand measurement (measured; see [STK-35](../../documentation/library_design/validation/stacking.md#stk-35)).

### `visualization.py`

`Visualization` draws images and charts.

- `render_fits` draws a frame or stack as a PNG with a description (`kind="image"`) or as a data URL for the app's viewer (`kind="data_url"`).
- `plot(kind, ...)` draws one chart of a target or a star.

### `jobs.py`

`Jobs` is a read-only view of the job history (running and finished jobs, their log lines, stored results) and of the pipeline runs that produced a target's data. It opens the logs database read-only, so it cannot change it. The MCP tool `jobs_query` calls `Jobs.query`.

## Injecting drivers

A driver is the code that talks to one outside program or service. A caller can swap any of them, for example to run a test with no network. The library uses five:

- a plate solver, which works out which part of the sky an image shows (built-in: Astrometry.net);
- a stacking program (built-in: Siril);
- the SIMBAD database of named astronomical objects (built-in: through astroquery, a Python package for querying astronomy services);
- Gaia DR3's low-resolution XP spectra, which the European Space Agency's Gaia satellite measured for its third data release (built-in: through astroquery, cached on disk);
- a source of interstellar reddening values (built-in: Gaia DR3 through astroquery).

Reddening is the color excess E(B-V) that dust adds to a star's light. B and V are two standard brightness bands, blue (about 4400 Å) and visual (about 5500 Å). E(B-V) is how much bigger the star's B − V magnitude difference is than it would be without dust. The spectroscopy pipeline uses it to correct a spectrum before it classifies the star, that is, before it decides the star's spectral type.

The root's signature accepts a replacement for each driver:

```python
Astrometrics(config, catalog_access, *, plate_solve_driver=None, stacking_driver=None,
             simbad_driver=None, gaia_xp_driver=None, reddening_driver=None)
```

A driver left out stays the built-in one. The root puts the five into one `Drivers` object (`drivers/driver_set.py`) and gives it to `StellarCatalog` and `ProcessingPipelines`, which pass it to the pipelines as a `drivers=` keyword. The method signatures on the sub-APIs do not change.

A replacement subclasses `PlateSolveDriver`, `StackingDriver`, `SimbadDriver`, `GaiaXpDriver` or `ReddeningDriver`. The root exports these base classes, and also `StackSettings` and `StackRunResult`, which a stacking driver needs. A test can run a stage with a fake solver and no network:

```python
astrometrics = Astrometrics(config, catalog_access, plate_solve_driver=MySolver(), simbad_driver=MySimbad())
astrometrics.processing.process_target("M 13", stages=["astrometry"])
```

### Where the drivers reach

- `processing.stack` uses the stacking driver.
- `processing.process_target` for one target:
  - gives the plate solver and SIMBAD driver to the astrometry and spectroscopy stages, and the Gaia XP and reddening drivers to the spectroscopy stage;
  - does not give any driver to the photometry stage. Photometry identifies stars and plate-solves each session's reference frame with a `StarIdentifier` that it creates without drivers. That `StarIdentifier` falls back to an empty `Drivers()`, which builds the built-in plate solver and SIMBAD driver. A replacement plate solver or SIMBAD driver therefore never reaches photometry.
- `processing.run_spectroscopy_by_session` (spectroscopy frames grouped by observing session) and `stars.plate_solve` use the plate solver and SIMBAD driver.
- `processing.process_target` for a list of targets, or for every target, runs each target in its own worker process. A worker cannot receive a driver object, so the call raises `InvalidArgumentError` when any driver was given, instead of quietly using the built-in ones.

`test/test_driver_construction.py` fails when a pipeline module calls a built-in driver class directly, because such a pipeline would ignore the driver the caller chose. It does not catch photometry's case. Photometry never names a built-in driver class, because it gets its drivers through `Drivers` as the rule requires. The caller's `Drivers` simply never reaches it.

### Which frames a one-target run uses

`processing.process_target` for one target measures only the frames of one camera and one optic (one telescope or lens, identified by its focal length). It chooses them the way the batch run does:

- the camera: the first camera in the config's setups that has frames of the target, the primary camera first;
- the optic: the config's primary focal length (`[Observatory.Telescope] focal_length_mm`).

`camera_id` and `focal_length_mm` choose differently. Spectroscopy runs when that selection has spectral frames or the target has a spectral stack.

For exact behavior, read the code.
