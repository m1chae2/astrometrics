# API

This folder holds the library's front door: the classes and functions scripts, backend services, and the MCP tools actually call. Each file wraps the pipelines and drivers underneath in a stateful, easy-to-use interface. Nothing outside `astrometricslib` should import from this folder directly — import from the top-level `astrometricslib` package instead, which re-exports the public names.

## What each file is for

Each sub-API checks its arguments and hands the work to `pipelines/`. A method that takes a target accepts its id or the `Target`; an id that names no target raises `NotFoundError`. A method that offers variants picks one with `kind=`, adds optional sections with `include=`, and refuses an argument that the chosen variant does not use. Methods that build a new answer return a Pydantic model (for example `TargetQueryResult` or `StackResult`) rather than a dictionary.

- `targets.py` — `TargetCatalog`, which creates, reads, saves and deletes targets. `get` returns a `Target` for code to work with; `query` returns short records for people and AI clients, including the cameras used (`detail="cameras"`) and each target's frames per configured camera (`detail="camera_index"`, built by `pipelines/shared/target_camera_index.py`). `reindex_frames` brings frame records up to date with the files on disk: given `paths` it adds only those files (a finished `.jpg`, `.png` or `.tiff` picture becomes the target's processed image instead of a frame), given a target it rescans that target's folder, and given neither it rescans every target folder and creates a target for each new folder.
- `stars.py` — `StellarCatalog`. `get` returns one star's full record. `query` answers every read about many stars (by ids, name, target, sky region or position, with a cap on the answer); `detail` picks ids, summaries, analysis records, full records, class counts, per-target counts, the stars placed in pixels on a target's stacked image (`detail="overlay"`, built by `pipelines/shared/star_overlay.py`) or library statistics. Filters cover magnitude, spectra, photometry, real catalog magnitude, spectral class and a search text, and `order` sorts by id, by usefulness, or by spectrum match. Single-frame photometry detections (ids ending in `:Star_<n>`) are hidden unless `include_unresolved=True`. `plate_solve` works out where a FITS frame points from its stars and returns the pixel-to-sky mapping (a WCS), or `None`; wayfindinglib's centering uses it. `detect_point_sources` finds stars in a pixel array. The other methods change stars.
- `processing.py` — `ProcessingPipelines` and its two children.
  - `ProcessingPipelines.stack` chooses a target's frames (by filter, file range, time window and camera, or an exact list) and stacks them; `plan_only=True` only reports the choice. `remake_preview` makes a stack's preview picture again without restacking. `process_target` runs the analysis stages (`astrometry`, `photometry`, `spectroscopy` and `asteroids`) for one target, or the full pipeline for a list of targets or for every target. A restack keeps the stack it replaces in a `_previous` folder; `discard_previous_stack` deletes it and `swap_with_previous_stack` puts it back. `restore_excluded_frames(target, apply=True)` moves frames the stacker set aside back into the frames folder.
  - `QualityDiagnostics` measures without changing anything. `frame_quality` measures raw frames (`kind="input_quality"`), checks a folder or a target's frames as a batch (`kind="raw_check"`), or previews which frames the stacker would set aside (`kind="quarantine_preview"`); `include=["excluded"]` adds the frames already set aside. `stack_quality` measures a stack (star width, the share of rejected pixels, frame registration) and compares it with the previous stack or another stack file. `spectral_frame_check` measures a target's raw slitless-spectrum frames: where the zero order (the undispersed image of the star) sits, the tilt and width of the spectrum streak, the sky level, and every saturated patch. Given `predict_exposure_seconds`, it scales the peaks to that exposure and says whether the spectrum would clip. `flag_value_outliers` marks the entries of a list that lie more than a given number of standard deviations from the mean.
  - `CalibrationCatalog` tracks the dark, bias and flat frames. `query` reports counts or how a target's light frames match the darks; `refresh("flat")` rescans one kind and reports what it found and whether each new flat set is good enough; `assess_flats` runs the same check on the flats already in the library.
- `visualization.py` — `Visualization`. `render_fits` draws a frame or stack as a PNG with a description (`kind="image"`) or as a data URL for the app's viewer (`kind="data_url"`). `plot(kind, ...)` draws one chart of a target or a star.
- `jobs.py` — `Jobs`, a read-only view of the job history (running and finished jobs, their log lines, stored results) and of the pipeline runs that produced a target's data. It opens the logs database read-only, so it cannot change it. The MCP tool `jobs_query` calls `Jobs.query`.

## Injecting drivers

A driver is the code that talks to one outside program or service. The library uses five: a plate solver (Astrometry.net), a stacking program (Siril), the SIMBAD database (through astroquery), Gaia DR3's low-resolution XP spectra (through astroquery, cached on disk) and a source of interstellar reddening values (Gaia DR3 through astroquery). Reddening is the colour excess E(B-V) that dust adds to a star's light; the spectroscopy pipeline uses it to correct a spectrum before it classifies the star. A plate solver works out which part of the sky an image shows.

`Astrometrics(config, catalog_access, *, plate_solve_driver=None, stacking_driver=None, simbad_driver=None, gaia_xp_driver=None, reddening_driver=None)` accepts a replacement for each one. A driver left out stays the built-in one. The root puts the five into one `Drivers` object (`drivers/driver_set.py`) and gives it to `StellarCatalog` and `ProcessingPipelines`. They pass it to the pipelines as a `drivers=` keyword. The method signatures on the sub-APIs do not change.

A replacement subclasses `PlateSolveDriver`, `StackingDriver`, `SimbadDriver`, `GaiaXpDriver` or `ReddeningDriver`. The root exports these base classes, and `StackSettings` and `StackRunResult`, which a stacking driver needs. A test can use this to run a stage with a fake solver and no network:

```python
astrometrics = Astrometrics(config, storage, plate_solve_driver=MySolver(), simbad_driver=MySimbad())
astrometrics.processing.process_target("M 13", stages=["astrometry"])
```

Where the drivers reach:

- `processing.stack` uses the stacking driver.
- `processing.process_target` for one target gives the plate solver and SIMBAD driver to the astrometry and spectroscopy stages, and the Gaia XP and reddening drivers to the spectroscopy stage. The photometry stage still builds the built-in ones.
- `processing.process_target` for one target measures only the frames of one camera and one optic, chosen the way the batch run chooses them (the camera the batch would give the target to, and the config's primary focal length). `camera_id` and `focal_length_mm` choose differently. Spectroscopy runs when that selection has spectral frames or the target has a spectral stack.
- `processing.run_spectroscopy_by_session` and `stars.plate_solve` use the plate solver and SIMBAD driver.
- `processing.process_target` for a list of targets, or for every target, runs each target in its own worker process. A worker cannot receive a driver object. The call raises `InvalidArgumentError` when any driver was given, instead of quietly using the built-in ones.

`test/test_driver_construction.py` fails when a pipeline module builds a built-in driver itself, because that pipeline would ignore the driver the caller chose.

For exact behavior, read the code — the code is always the source of truth.
