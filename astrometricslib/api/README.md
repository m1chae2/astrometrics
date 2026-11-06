# API

This folder holds the library's front door: the classes that scripts, backend services and the MCP tools actually call. MCP (Model Context Protocol) tools are the adapters through which AI agents call the library. Each file wraps the pipelines and drivers underneath in a stateful, easy-to-use interface.

## Public layout

The library has a small public surface. Everything else is private.

| Import from | What it holds |
| :--- | :--- |
| `astrometricslib` (the root, 16 names) | `Astrometrics`, `AppConfiguration`, `get_configuration`, `configure_logging`, `configure_offline_iers` and `warm_earth_orientation_data`; the sub-API classes `TargetCatalog`, `StellarCatalog`, `ProcessingPipelines`, `CalibrationCatalog`, `QualityRecords` and `Visualization`; and the core records `Target`, `FrameRecord`, `StellarObject` and `FilterType`. |
| `astrometricslib.exceptions` | The errors a script catches, `ErrorInfo` with `to_error_info` and `error_from_info`, and `FITS_READ_ERRORS`. |
| `astrometricslib.reports` | The quality reports and quality row shapes that checks return, `CaptureSetting` and `observing_night_id`. |
| `astrometricslib.jobs` | `Jobs` (`astrometrics.jobs` is an instance of it), the job store and records, and the tools that run a function as a tracked background job. |
| `astrometricslib.drivers` | The data store and file tools that other apps share (`Butler`, `connect_db`, `file_lock`, `is_path_inside`, `resolve_mounted_path` and the like). |
| `astrometricslib._internal` | Everything else, in logical groups. |

The rules:

- **Only `astrometricslib` itself may import `_internal`.** `wayfindinglib`, the backend and the MCP servers use the root and the four public modules only. Tests of the libraries are the one exception.
- **A result model is private, and its fields are not.** A call returns a Pydantic model (a Python class with typed, validated fields) such as `TargetQueryResult` or `StackResult`. A caller reads the fields by name and does not import the class. The API reference page lists the fields of each result without an import path.
- **A caller reaches a sub-API through `Astrometrics`.** Build one `Astrometrics` and use `astrometrics.targets`, `astrometrics.processing` and the rest, so all of them read and write the same storage.
- **Old names have no alias.** Every rename of the cleanup is listed in [API renames](../../documentation/plans/API_RENAMES_134.md).

Terms are defined in the [glossary](../../documentation/Glossary.md). This document also defines each term the first time it appears.

A target is one sky object the library tracks, with its raw frames (single camera exposure files) and results. A stack is the image made by aligning and averaging many frames.

## What each file is for

The `Astrometrics` class in the package's `__init__.py` (the "root") builds one object per file below and exposes each as an attribute, such as `astrometrics.stars` or `astrometrics.processing`. Each of these objects is a sub-API.

Each sub-API checks its arguments and hands the work to `pipelines/`. Three rules hold for every sub-API:

- A method that takes a target accepts its id or the `Target`. An id that names no target raises `NotFoundError`.
- A method that offers variants picks one with `kind=` and adds optional sections with `include=`. It refuses an argument that the chosen variant does not use.
- A method that builds a new answer returns a Pydantic model (a Python class with typed, validated fields), for example `TargetQueryResult` or `StackResult`. It does not return a dictionary.

- A row carries the values a caller would otherwise import a helper for. These are worked out when read and are not saved, so the stored form does not change: `FrameRecord.is_spectral`, `Target.object_category`, `Target.ra_deg` and `Target.dec_deg`, and `TargetStackingResult.preview_path`. A limit a row reports is the limit in force now, not the one in force when the row was stored. Two limits sit on returned answers instead: `CalibrationQueryResult.dark_temperature_tolerance_c`, and `limits` on the `MetricSpec` of `frame.saturated_pixel_fraction` (the saturated-star blob size in pixels and the share of frames that makes an exposure length clipped).

### `targets.py`

`TargetCatalog` creates, reads, saves and deletes targets.

- `get` returns a `Target` for code to work with. With `refresh=True` it first brings that one target up to date with storage (see `pipelines/shared/target_records.py`): nothing is read when the library write counter has not moved, one row is read when it has, and every target is read only when this catalog does not hold the target yet. `list` skips its read the same way. Unsaved edits to a held target are kept. A lock keeps threads from refreshing at the same time.
- `query` returns short records for people and AI clients. `detail="cameras"` adds the cameras used. `detail="camera_index"` adds each target's frames per configured camera, built by `pipelines/shared/target_camera_index.py`.
- `get_stars(target)` returns every star the library holds for a target, as full `StellarObject` records in id order. It is the short form of `stars.query(target_id=..., detail="objects", limit=None)`; use `stars.query` for a filter, an order or a smaller answer.
- `get_header` returns a target's file header as text cards. `read_frame_header` reads the first header of any FITS file by path and returns it with typed values. It reads no pixels. The backend and wayfindinglib use it instead of opening FITS files themselves.
- `select_frames` picks a target's timed light frames by filter, file name range, start time (ISO 8601 text) and count, oldest first. With a range or time bound `limit` keeps the first frames in it; otherwise it keeps the newest. `resolve` turns an id into its `Target` and raises `NotFoundError` for an unknown id.
- `add_frames(paths, target, telescope=...)` moves new FITS files (folders to search, or single files) into the library and returns the new path of each file that was not there before. Light frames go under the target; dark, bias and flat frames go to the calibration folders. It does not update the frame records: call `reindex_frames` with the returned paths. `processing.calibration.add_frames(paths, telescope=...)` does the same for calibration files only and leaves light frames where they are.
- `sessions(target, night_date=None)` groups a target's frames into `TargetSession` records: one for each night (noon to noon, local time) and gain and offset setting. `night_date` keeps one night.
- `reindex_frames` brings frame records up to date with the files on disk:
  - Given `paths`, it adds only those files. A finished `.jpg`, `.png` or `.tiff` image becomes the target's processed image instead of a frame.
  - Given a target, it rescans that target's folder.
  - Given neither, it rescans every target folder and creates a target for each new folder.

### `stars.py`

`StellarCatalog` reads and changes the star records.

- `get` returns one star's full record.
- `get_spectrum(star_id)` returns a star's `SpectroscopyResult` from the star itself, or `None`. Older libraries also hold a copy of the star under an id ending in `::spectroscopy`. `get_spectrum` reads that copy when the star has no spectrum of its own, and accepts that id, so scripts never need to build or test for it.
- `query` answers every read about many stars. It selects by ids, name, target, sky region or position, with a cap on the size of the answer.
  - `detail` picks what comes back: ids, summaries, analysis records, full records, class counts, per-target counts, library statistics, or the stars placed in pixels on a target's stacked image, or on any image named by `image_path` (`detail="overlay"`, built by `pipelines/shared/star_overlay.py`).
  - Filters cover magnitude (the astronomer's brightness scale, where larger numbers are fainter), whether a star has a spectrum or photometry, spectral class, and a search text. The spectral class is the letter of the star's type in the O, B, A, F, G, K, M sequence, hottest to coolest. A full type such as `G2V` counts as `G`.
  - A filter on "real catalog magnitude" keeps stars whose magnitude came from a published catalog. A zero, a missing value, or an instrumental magnitude (one measured by this library's photometry, on its own uncalibrated scale) does not count.
  - `order` sorts by id, by usefulness (stars with spectra first, then named stars, then stars with photometry, then brightest first), or by spectrum match (stars whose own spectrum best matches a reference spectrum first).
- Single-frame detections are hidden unless `include_unresolved=True`. Photometry (brightness measurement) saves each point source it finds in one frame under an id ending in `:Star_<n>`. These are working records, not catalog stars, so a normal query leaves them out.
- `dataset_version` counts the star catalog writes made by any program. The count is kept in the library database, so it also sees a write by a maintenance script and survives a restart. A cache of a whole-catalog answer compares it to the number it saw last, to know whether to rebuild. `astrometricslib.drivers.get_library_data_version` gives the counters of every kind of data (targets, quality, stars) for a cache that depends on more than the stars.
- Every other method on `StellarCatalog` changes star records.

### `processing.py`

`processing.py` holds `ProcessingPipelines` and its child `CalibrationCatalog`. `quality.py` holds `QualityRecords`, which is a sub-API of its own (see below), not a child of `ProcessingPipelines`.

`ProcessingPipelines` makes stacks and runs the analysis.

- `stack` chooses a target's frames and stacks them. It chooses by filter (the colored or clear glass in front of the camera that passes one band of light), file range, time window and camera, or takes an exact list. `plan_only=True` only reports the choice.
- `remake_preview` makes a stack's preview image again without restacking. The result carries `preview_settings`, read from the record saved beside the stack (`<stack name>_preview_settings.json`), or `None` when the picture has no record.
- `process_target` has two forms:
  - For one target, it runs the analysis stages (`astrometry`, `photometry`, `spectroscopy` and `asteroids`) on the frames and stack the target already has. It does not stack.
    - Each stage takes its options as a plain dictionary. The asteroid search takes `asteroids={"moving_object_config": {"detection_threshold_sigma": 4.0}}`: the keys are the `[Processing.MovingObject]` setting names, a key left out keeps its default, and a key that names no setting raises `InvalidArgumentError`. The model behind the dictionary is private.
  - For a list of targets, or for every target, it runs the full pipeline: stacking first, then the analysis stages.
- A restack keeps the stack it replaces in a `_previous` folder. No public call deletes it or puts it back.
- `open_in_siril(file_path)` opens a FITS file in the Siril program's window, through the stacking driver's `open_in_viewer`. The app's "open in Siril" button uses it, so the backend never builds a Siril driver. A stacking driver with no window raises `ProcessingError`. It is not offered as an AI tool.
- `stop_stacking_programs()` stops every Siril process the library started and still runs, through the stacking driver's `stop_running_programs`. The backend's `Container.shutdown_resources()` calls it, because the backend's exit runs no `atexit` handlers and Siril runs in a session of its own. A Siril window a person opened by hand is not touched. It is not offered as an AI tool.
- `predict_stack_weights` forecasts a stack before it exists. Given the frames' exposure lengths (and, when known, their star widths, variances and group noises), it runs the stacker's own rules and returns a `StackWeightPrediction`: the weighting mode, the exposure group each frame lands in, and each frame's weight, or the reasons the weights are unknown. It reads and saves nothing.
- `stack_subset(pipelines, target, frames, label=...)` is not a method. It is an internal function (in `astrometricslib._internal.processing_scripts`, code in `script_calls.py`) that only the library's own scripts and tests call. It stacks exactly the given frames into the target's `_subsets` folder with the normal settings, and stores the result's noise, effective frame count and effective time. With `record_forecast_rows=True` it also stores the rows a library stack stores for the forecast check (one `stacking.frame_weight` for each frame on the frame's subject id plus `#<label>`, so the weights never hide the library stack's newest rows; the exposure-group rows on the subset's own group subjects; and a calibration record), so a subset such as the stack of all nights but the last (`label=EARLIER_NIGHTS_VARIANT`, a constant in `astrometricslib.reports`) can be the earlier member of the forecast check's pair. It never writes to the library stack's path, assigns nothing on the target and saves nothing. `measure_noise(target, kind="growth")`, the default kind, stacks the first night, the first two nights and so on (at most 4 subsets by default) and measures each result's noise in the library stack's sky apertures. It also runs the odd–even difference noise below, in the same lock, unless `include=[]` is given (`include=["night_contributions"]` adds the night contributions). `measure_noise(target, kind="night_contributions")` stacks the library stack's frames without each night in turn and stores the night's share of the weight and the noise without it. `measure_noise(target, kind="odd_even_difference")` stacks two disjoint halves of the library stack's frames on one registration reference, subtracts them and stores the noise of the difference beside the library stack's empty-aperture noise (the halves are deleted afterwards; the job refuses with a reason rather than guess). None of the three kinds starts by itself after a stack; a caller starts them (the app has the one RPC `processing:measure_noise`, which takes the same `kind`). They cost one stack per subset (the odd–even difference job costs about one library stack's time in all, two stacks of half the frames, plus the temporary disk space Siril needs for each: its converted and registered copies of the frames, deleted after each stack; the M 106 run in the comment in `siril_interface/imaging_stacker.py` measured 65 GB for 238 frames, about 0.27 GB a frame, so a half of 30 frames needs about 8 GB), run as background jobs of type `stacking`, and raise `NotFoundError` for a target with no stack. `measure_noise` returns the result of its kind: `NoiseGrowthResult`, `NightContributionResult` or `OddEvenDifferenceNoiseResult`; `stack_subset` returns `SubsetStackResult`. `maximum_steps` applies to `growth` only, `include` to `growth` only and `keep_halves` to `odd_even_difference` only; giving one with another kind raises `InvalidArgumentError`. See [subset stacks](../pipelines/stacking/README.md#subset-stacks).
- `restore_excluded_frames(pipelines, target, apply=True)` is not a method either. It is an internal function in the same module, used by `scripts/restore_excluded_frames.py`. It moves frames the stacker set aside back into the frames folder. The stacker sets a frame aside, into an `_excluded` folder, when clouds or trailed stars (stars smeared into short lines) make it unusable.
- `plate_solve` works out where a FITS frame (the standard astronomy image file) points, using the stars in it. It returns the pixel-to-sky mapping (a WCS, World Coordinate System), or `None`. The centering step in wayfindinglib uses it to point the telescope exactly at a target.
- `detect_point_sources(pipelines, data)` is an internal function in the same module, used by `scripts/spectral_registration_quality_analysis.py`. It finds stars in a pixel array and returns a `PointSourceDetection`, whose `sources` are `PointSource` records (center, flux, peak, shape numbers and radius), brightest first.

`CalibrationCatalog` tracks the dark, bias and flat frames (the calibration frames that measure the camera itself; see `astrometricslib/_internal/drivers/README.md`).

- `query` reports counts, or how a target's light frames (frames of the sky) match the darks.
- `camera_profile(camera_name)` returns the stored `CameraProfile` for a camera, matched in any spelling the profile lists (case, spaces and punctuation are ignored). It carries the camera's saturation limits with their sources and its gain and read noise for each sensor setting. A missing or unlisted name gives the generic fallback profile (`is_generic_fallback` is `True`). The profile describes the camera model, not calibration frames, but the call sits here because it reads the same configuration and no other sub-API fits better.
- `reindex_frames("flat")` rescans one kind and reports what it found. `report.assessments` holds one `CalibrationSetHealth` (the record `assess` returns) for each set that gained frames. A new flat set is measured: enough frames, bright enough but not too bright, and a master flat (the combination of the set) that is not too noisy. A dark or bias set is not measured by the rescan; it is judged from the numbers `measure_frames` stored, so a new frame with no numbers yet shows checks that are `not_checked`, with the reason. The app starts `measure_frames` by itself after it ingests or downloads dark or bias frames; a script must call it.
- `assess(kind, ...)` judges the sets of one kind (`"dark"`, `"bias"` or `"flat"`) already in the library. It gives one record for each set, with one check per condition, each passed, failed or `not_checked` with the reason. A dark or bias set is judged from the numbers stored for each of its frames, so it needs `measure_frames` to have run. A flat set is measured when asked. A check with no reference (for example a bias level the camera profile does not hold) is `not_checked`, never judged against a default. Nothing is written.
- `measure_frames(kind=None, camera_id=None, force=False)` measures the dark and bias frames that are new or changed and stores their numbers. It runs as a background job, because a first pass over a large library takes a long time (about half a second per frame). It never changes a frame file.
- `measure_sensor_noise(camera_id, gain, offset, binning)` measures the camera's conversion gain (electrons per ADU, one step of the stored pixel value) and its read noise (the electronic noise added when a frame is read out) from the flat and bias frames in the library at exactly that setting. It pairs the frames in file-name order, so it needs at least 5 flat pairs and 3 bias pairs that share one setting, one flat exposure and one temperature. If it cannot measure, the result says why and holds no numbers. Reading never writes. With `save=True` a measured result goes into the camera profile in the configuration file, after a backup. The result's `provenance` sentence is stored as the value's source. Set `exposure_seconds`, `temperature_c`, `telescope` or `filter_name` to choose among flats of several kinds. It runs as a background job. Its result matched a hand measurement (measured; see [STK-35](../../documentation/library_design/validation/stacking.md#stk-35)).

### `quality.py`

`QualityRecords` is reached as `astrometrics.quality`. It is the start of every quality read, whether the numbers were stored by an earlier run or are measured now. Nothing it does changes data.

Stored reads:

- `query` reads the quality measurements that earlier runs saved in `astrometrics.db` and returns a `QualityQueryResult`: the `rows`, `total_matching`, `offset` and `truncated`. There is no limit unless `limit` is given; `limit` and `offset` page the answer. It filters by `metric`, `level` (`frame`, `stack` or `run`), `target_id`, `observing_night_id`, `camera`, `telescope` and `since`. It gives the newest row for each subject and metric unless `latest_only=False`, and `row_ids` returns exactly the rows a report cited. `variant` keeps only the rows about one subset stack, and `library_only=True` leaves subset-stack rows out (giving both raises `InvalidArgumentError`). A library with no quality table yet gives an empty list; the read opens the file read-only and creates no file and no table. A `metric` that is not registered raises `InvalidArgumentError`.
- `calibration_identities(run_ids, target_id)` reads which calibration each library stacking run used: the flat, bias, flat-bias (the bias removed from the flats, at the flats' own gain and offset) and dark masters (path, size, modification time), optic, camera, Siril version and stacking algorithm version. Two runs of a target whose records differ are confounded by a calibration change. A record made before schema version 6 has no flat-bias record (`None`), and the forecast check does not count that as a difference. An unmigrated or missing database gives an empty list.
- To tell whether two stacks' noise was measured in the same apertures, read the stack's `stacking.aperture_set_number` row with `query(metric="stacking.aperture_set_number", target_id=..., library_only=True)` and turn each row's `value` into text with `aperture_identity_from_number` (a private helper; `wayfindinglib` keeps a copy pinned by a test). Equal text means the same set; a not-measured row or a missing row means unknown. An odd–even difference run stores the same number as `stacking.odd_even_difference_aperture_set_number`.
- To test where the fixed-pattern noise sits, read the `stacking.odd_even_difference_aperture_record_count` row of the library stack: its reason holds the path of a JSON record (one entry per empty aperture with its position, its distance from the image center and from the target, and its values on the sum A + B and the difference A - B of the two half stacks), which `quality.get_aperture_record(path)` reads. It returns `None` for a missing, unreadable or other-version file. The row's four neighbors, `stacking.odd_even_difference_row_average_scatter_stack_unit`, `..._column_...`, `stacking.odd_even_sum_row_average_scatter_stack_unit` and `..._column_...`, give the line pattern.
- `metrics` lists the declared metrics, or with `name=` just that one, or with `checkpoint=` only those measured at that checkpoint. An unknown `name`, `checkpoint` or `detail` raises `InvalidArgumentError`. About 170 metrics are registered, so the default `detail="summary"` gives a short `MetricSummary` for each (name, unit, checkpoint, levels, purpose). `detail="full"` gives the whole `MetricSpec`, which adds the meaning, which way is better and how the number is estimated; pair it with `checkpoint=` or `name=`.

Recorded reads:

- `record_frame_measurements(target)` measures a target's light frames and stores the numbers in the quality database (read them with `query`). It reads each frame once and measures the stars' width and stretch, the sky and its noise in electrons, the read noise and the temperatures. A value it cannot measure is stored as `not_measured` with the reason. A frame whose file has not changed since its last measurement is skipped, so a second call measures nothing again. A frame that fails, for any reason, is counted and tried again next time. It never moves or changes a frame file. Only one run measures a target at a time, and one run reads frames at a time; a second call for the same target returns at once with `already_running` set. A sync measures only the frames it added, at about 11 s per frame. It runs as a background job of type `quality` and returns a `FrameMeasurementSummary`. A sync that adds frames starts it by itself. See [frame measurement](../pipelines/stacking/README.md#frame-measurement). The star measurements are a design estimate (see [AST-17](../../documentation/library_design/validation/astrometry.md#ast-17)).

Live checks:

- `measure_frames` measures raw frames:
  - `kind="input_quality"` measures them one by one.
  - `kind="raw_check"` checks a folder or a target's frames as a batch.
  - `kind="quarantine_preview"` previews which frames the stacker would set aside.
  - `kind="spectral"` measures a target's raw slitless-spectrum frames. These are frames taken through a grating with no slit, which spreads each star into a spectrum trail of colors. For each frame it reports:
    - where the zero order (the undispersed image of the star) sits;
    - the tilt and width of the spectrum trail, and the sky level;
    - every saturated patch, that is, pixels at the sensor's maximum value, where the true brightness is lost.

    Given `predict_exposure_seconds`, it scales the measured peaks to that exposure and says whether the spectrum would saturate ("clip") at that exposure. `exposure_seconds` and `predict_exposure_seconds` apply to this kind only; giving them with another kind raises `InvalidArgumentError`.
- `measure_stack` measures a stack (star width, the share of rejected pixels, frame registration) and compares it with the previous stack or another stack file. The comparison reports the sky level, the noise in empty sky (the target report's `stacking.empty_aperture_noise_normalized`), the flatness and the star width, with one sentence for each and a reason for any number it could not measure.

### `visualization.py`

`Visualization` draws images and charts.

- `render_fits` draws a frame or stack as a PNG with a description (`kind="image"`) or as a data URL for the app's viewer (`kind="data_url"`).
- `plot(kind, ...)` draws one chart of a target or a star.

### `jobs.py`

`Jobs` is a read-only view of the job history (running and finished jobs, their log lines, stored results) and of the pipeline runs that produced a target's data. It opens the logs database read-only, so it cannot change it. `Jobs.query` returns a `JobQueryResult`, in which only the fields of the chosen `detail` are filled. The MCP tool `jobs_query` calls `Jobs.query`.

`detail="warnings"` answers the question "what warnings did this job detect?" and needs a `job_id`. A warning is a stored quality row of the job whose check failed (`check_outcome` is `failed`); it is listed with its pipeline, metric, subject, reason sentence, value, limit and validation register entry. A check that passed is not a warning. A check that could not run (`not_checked`) detected nothing, so it is only counted in `checks_not_run_count`. The answer also gives `measurement_count`, the number of rows the job stored, so three cases differ: an empty list with a nonzero count (the job was checked and nothing failed), a zero count (the job stored no rows, so there was nothing to check) and an unknown id (`NotFoundError`). Warnings that are not gates, such as frames moved to the excluded folder or a camera with no profile, are stored as notice rows (`<pipeline>.notice_*`) so that they appear here too. A row with no job id, such as one from a call with `register_job=False` outside any job or one backfilled from an older summary, belongs to no job and is listed for none. The backend serves the same read as the RPC method `jobs:query`.

## Injecting drivers

`Astrometrics(config, catalog_access)` takes only those two arguments. It has no driver arguments, so a program always runs the built-in drivers. Only a test swaps a driver, and it does so through private code (`astrometricslib._internal.testing.astrometrics_with_drivers`). Nothing outside the library's tests and scripts may import that module.

A driver is the code that talks to one outside program or service. A test can swap any of them, for example to run with no network. The library uses five:

- a plate solver, which works out which part of the sky an image shows (built-in: Astrometry.net);
- a stacking program (built-in: Siril);
- the SIMBAD database of named astronomical objects (built-in: through astroquery, a Python package for querying astronomy services);
- Gaia DR3's low-resolution XP spectra, which the European Space Agency's Gaia satellite measured for its third data release (built-in: through astroquery, cached on disk);
- a source of interstellar reddening values (built-in: Gaia DR3 through astroquery).

Reddening is the color excess E(B-V) that dust adds to a star's light. B and V are two standard brightness bands, blue (about 4400 Å) and visual (about 5500 Å). E(B-V) is how much bigger the star's B − V magnitude difference is than it would be without dust. The spectroscopy pipeline uses it to correct a spectrum before it classifies the star, that is, before it decides the star's spectral type.

A test builds the root with the helper:

```python
from astrometricslib._internal.testing import astrometrics_with_drivers

astrometrics_with_drivers(config, catalog_access, *, plate_solve_driver=None, stacking_driver=None,
                          simbad_driver=None, gaia_xp_driver=None, reddening_driver=None)
```

A driver left out stays the built-in one. The helper puts the five into one `Drivers` object (`drivers/driver_set.py`) and gives it to `StellarCatalog` and `ProcessingPipelines`, which pass it to the pipelines as a `drivers=` keyword. The method signatures on the sub-APIs do not change.

A replacement subclasses `PlateSolveDriver`, `StackingDriver`, `SimbadDriver`, `GaiaXpDriver` or `ReddeningDriver` (from `astrometricslib._internal.drivers`; `StackSettings` and `StackRunResult`, which a stacking driver needs, are there too). A test can run a stage with a fake solver and no network:

```python
astrometrics = astrometrics_with_drivers(
    config, catalog_access, plate_solve_driver=MySolver(), simbad_driver=MySimbad()
)
astrometrics.processing.process_target("M 13", stages=["astrometry"])
```

### Where the drivers reach

- `processing.stack` uses the stacking driver.
- `processing.process_target` for one target:
  - gives the plate solver and SIMBAD driver to the astrometry, photometry and spectroscopy stages, and the Gaia XP and reddening drivers to the spectroscopy stage;
  - photometry gives them to every `StarIdentifier` it builds: the one for each session's reference frame and the one that solves a session's frame to match stars across sessions. The photometry workers (the frame-measuring pool) never build one, so they need no driver.
- `processing.run_spectroscopy_by_session` (spectroscopy frames grouped by observing session) and `processing.plate_solve` use the plate solver and SIMBAD driver.
- `processing.process_target` for a list of targets, or for every target, runs each target in its own worker process. A worker cannot receive a driver object, so the call raises `InvalidArgumentError` when any driver was given, instead of quietly using the built-in ones.

`test/test_driver_construction.py` fails when a pipeline module calls a built-in driver class directly, because such a pipeline would ignore the driver the caller chose. It also fails when a pipeline builds a class that falls back to an empty `Drivers()` (such as `StarIdentifier`) without a `drivers` argument, because that fallback builds the built-in drivers. `api/test/test_driver_injection.py` shows that photometry uses the fake plate solver and SIMBAD driver and touches no built-in one.

### Which frames a one-target run uses

`processing.process_target` for one target measures only the frames of one camera and one optic (one telescope or lens, identified by its focal length). It chooses them the way the batch run does:

- the camera: the first camera in the config's setups that has frames of the target, the primary camera first;
- the optic: the config's primary focal length (`[Observatory.Telescope] focal_length_mm`).

`camera_id` and `focal_length_mm` choose differently. Spectroscopy runs when that selection has spectral frames or the target has a spectral stack.

For exact behavior, read the code.
