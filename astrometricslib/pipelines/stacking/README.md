# Stacking pipeline

This pipeline combines many individual frames (light frames, also called sub-exposures) of the same target into one master image. Adding aligned frames makes real stars and galaxies brighter, while noise that occurs randomly in each frame (camera read noise, heat noise) tends to cancel out. Before combining, the pipeline also rejects temporary artifacts such as satellite streaks, cosmic rays and hot pixels (single pixels that read bright in every frame because of a sensor defect).

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## Terms used here

- *Calibration frames* are frames taken to measure the camera itself rather than the sky. A *bias* frame is a zero-length exposure that records the fixed electronic offset of every pixel. A *dark* frame is taken with the shutter closed and records the signal that heat adds over the exposure time. A *flat* frame is an exposure of an evenly lit surface that records how sensitive each pixel is. A *master* bias, dark or flat is the average of many such frames.
- *Calibrating* a light frame means subtracting the master bias and dark and dividing by the master flat.
- *Registration* means lining frames up pixel-for-pixel so the same star falls on the same pixel in every frame.
- *Pixel rejection* means throwing out a pixel's value in one frame when it disagrees strongly with the same pixel's values in the other frames, before averaging.
- *Siril* is the external image-processing program that this pipeline uses to calibrate, register and combine frames. *FITS* is the standard astronomy image file format. *CMOS* is the type of image sensor in the cameras used here.
- The *gain setting* is the camera's amplifier setting, a unitless number stored in the FITS `GAIN` card. (Elsewhere, "gain" in e-/ADU means the number of electrons that make one raw count; this README does not use that meaning.) *Offset* is a constant the camera adds to every pixel so that noise below zero is still recorded. *Binning* combines blocks of neighboring pixels (for example 2 by 2) into one larger pixel.
- A *session* is the set of frames of one camera, filter and exposure length taken less than 4 h apart. The quarantine step judges each session on its own.
- A *spectral frame* is a frame taken through a grating, which spreads each star into a spectrum trail of colors. The trail is the spectrum, so the pipeline treats these frames differently from imaging frames.
- The *zero-order star* is the plain, undispersed image of the star that a slitless spectrum frame records beside its spectrum.
- A pixel is *saturated* when it has reached the highest value the camera can record, so its true brightness is lost.
- *Flux* is the total light measured from a star.
- The *reference group* is the exposure group that carries the most weight when the groups are combined (step 5). The other groups are lined up with it and put on its brightness scale.

## What the pipeline does, in order

`stage.py` runs the steps below in this order for one target. Steps 3 to 5 run inside one call to `stack_runner.py`, which drives the stacking engine. Other READMEs refer to these step numbers.

1. Check that the frames agree. The gain, quarantine and background checks run first, and a frame that disagrees with the others is excluded rather than stacked. See [frame checks](#frame-checks-before-stacking-step-1).
2. Skip a stack that would come out the same. `stack_inputs.py` compares the stack's inputs with the record saved beside the last stack. See [skipping an unchanged stack](#skipping-an-unchanged-stack-step-2).
3. Group the frames by exposure length. `stack_runner.py` stacks each group on its own. See [exposure groups](#exposure-groups-step-3).
4. Run the stacking engine on each group: build the master calibration frames, calibrate, register and combine with pixel rejection. See [the stacking engine](#the-stacking-engine-step-4).
5. Line up and combine the exposure-group stacks, then trim the noisy edges. See [combining exposure groups](#combining-exposure-groups-step-5).
6. Judge the result. `stack_quality.py` decides whether the stacked image is good enough to use. See [judging the result](#judging-the-result-step-6).
7. Save a preview of the stack: a stretched JPEG and a stretched FITS file for people to look at. See [the preview image](#the-preview-image-step-7).

## Frame checks before stacking (step 1)

Three checks run before stacking:

- `frame_homogeneity.py` checks that the gain settings match across the frames.
- `frame_quarantine.py` measures each light frame's stars and moves a frame with clouds or trailed stars into an `_excluded` folder beside it. It skips spectral frames, because their spectrum trails are the spectra. [Frame quarantine](#frame-quarantine) describes the step.
- `background_homogeneity.py` compares each frame's background brightness with the other frames of the same exposure length to catch a frame taken through cloud or twilight. It finds one sudden jump in sky level, so a gradual build-up of cloud can pass it (partly measured; see [STK-04](../../../documentation/library_design/validation/stacking.md#stk-04)).

A frame that disagrees with the others is excluded rather than stacked. The pipeline records each check as a gate (see [quality outputs](#quality-outputs)).

### Frame quarantine

**What it does.** It compares each frame with the other frames of its own session. It moves a frame when:

- it has fewer stars than half the session's median star count (cloud, or heavy trailing); or
- its stars are much less round than the session's. Roundness is a star's narrow width divided by its wide width, 1.0 for a circle. The step moves the frame when roundness is below 0.80 and at least 0.15 below the session median (0.20 for a color camera).

These limits are partly measured (see [STK-01](../../../documentation/library_design/validation/stacking.md#stk-01) and, for color cameras, [STK-02](../../../documentation/library_design/validation/stacking.md#stk-02)). The session gap of 4 h is a design estimate (see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)).

A satellite or aircraft streak does not trigger it, because the stacker's pixel rejection removes those. The measurements come from `shared/quality/raw_frame_check.py`.

**Safeguards.** The step leaves a session of fewer than 8 frames alone, because its median cannot be trusted (partly measured; see [STK-01](../../../documentation/library_design/validation/stacking.md#stk-01)). If the step would move more than a quarter of a session, it moves none and logs why, because the session itself is then the problem (partly measured; see [STK-03](../../../documentation/library_design/validation/stacking.md#stk-03)).

**Undoing it.** The step moves frames and never deletes them. Each `_excluded` folder holds `excluded_frames.json`, which records every frame's original path, why it was moved and what was measured. The scanner and the downloader both leave `_excluded` alone, so moved frames neither return to the target nor download again. Two calls work with them:

- `QualityRecords.measure_frames(target, kind="quarantine_preview")` shows what the check would move without moving anything, and with `include=["excluded"]` lists what the step moved.
- `ProcessingPipelines.restore_excluded_frames(target, apply=True)` moves the frames back. `scripts/restore_excluded_frames.py` is its command-line form.

**Setting.** `quarantine_bad_frames_enabled` in `[Processing.Siril]` turns the step off.

**What it records.** The step is recorded in the stack's gate record as `frame_quarantine`:

- `passed` when at least one session was judged and none was over the cap;
- `failed` when a session had too many bad frames to move (the bad frames are still in the stack);
- `not_checked` when no session had enough readable frames, or when the step is off or the stack is spectral.

**What it stores.** The step measures every frame of a session it can read, not only the frames it moves. The stage stores two of those numbers for every such frame in the quality database, as `frame.quarantine_roundness` and `frame.quarantine_sky_median_adu`, so a frame that was kept can be compared with the frames that were moved. A session too small to judge is not measured. The stage also measures, in full, up to 40 kept frames that have never been measured (see [Frame measurement](#frame-measurement)). A failure to measure or store never stops a stack.

### Frame measurement

**What it does.** `frame_measurement_run.py` measures each light frame once and stores the numbers in the quality database. Each frame is read once and gets one row for each frame metric: the width, elongation and direction of its stars, the sky brightness and noise in electrons, the camera's read noise, the two temperatures and the older metrics. `ProcessingPipelines.record_frame_measurements(target)` runs it as a background job of type `quality`. It never moves or changes a frame file.

**When it runs.** There are three triggers:

- after a frame sync that added frames (wayfindinglib calls the public method with the frames it added, so older, never-measured frames are not swept in, and a failure there never fails the sync). A local ingestion goes through the same sync. The backend starts a whole-target run only after a library re-index that added frames (the backfill);
- when you or the app call `ProcessingPipelines.record_frame_measurements` (the RPC `processing:record_frame_measurements` starts it in the background). This is how older frames get measured;
- inside the stacking stage, for kept frames never measured, at most 40 per stack (the caller passes the time and a `FrameQualityRecording` with the configuration and the shared calibration library; pipeline code does not read the clock). This hook runs only for imaging stacks with the quarantine step turned on, because it sits inside that step. It skips the full measurement, and never waits, when a run for the target or another run has the turn.

**Worker processes.** Four frames are measured at once, each in its own worker process started with "spawn" (threads gave almost no speed-up, because the star fitting holds Python's interpreter lock). On real data this halved the time, to about 5.7 s a frame, and used about 2.7 GB of memory. A script that calls the job must keep its top-level code under `if __name__ == "__main__":`, because each worker imports the calling script again; without that guard the workers stop and every frame fails.

**One run at a time.** Every trigger goes through `record_frame_measurements_for_target`, which holds two guards: one run per target (a second caller for a target that is already being measured returns at once with a summary whose `already_running` is set), and one run reading frames at a time in the process (a run for another target waits its turn, and the stacking stage skips instead). The backend only starts a thread.

**How long it takes.** About 11 s per frame in a batch with four worker threads (61 frames of 3008 by 3008 pixels took about 650 s; partly measured, see [AST-17](../../../documentation/library_design/validation/astrometry.md#ast-17)). The job now measures frames in four worker processes, which took about 5.9 s per frame on the golden frames against 11.2 s for four threads (see [STK-D22](../../../documentation/library_design/validation/stacking.md)). A sync that adds many frames therefore returns that long after the copy. Stacking up to 40 unmeasured frames adds about seven minutes to a stack.

**Failures.** Any error on one frame, including an unexpected one from the image libraries, is logged and counted and does not stop the other frames. The same holds at the stacking stage: no error in this hook fails a stack. The frames' stamps are saved every 20 frames and at the end, and the save merges only the stamps into the stored target (`target_records.save_quality_stamps`), so it never overwrites other changes. A run inside another job (stacking, ingestion) gets its own run id and leaves that job's progress alone.

**Which frames it measures.** A frame is measured when its file differs from the size and change time stored on its record (`quality_measured_file_size` and `quality_measured_file_modified_ns`). The stamp is set only after the rows are stored, so a frame that failed is tried again, and a second run over unchanged files measures nothing. Frames are measured four at a time, in worker processes, newest first. Each frame has a budget of 60 s of its own CPU time for its star steps (waiting for the disk, a lock or other programs does not count). A frame whose budget ran out is not stamped: the rows that were measured are stored, the skipped metrics are left out, and the next run measures the frame again and adds a second row for each metric that was already stored (each run is a complete record of one measurement, and reads use the latest row). A frame that cannot be measured for a lasting reason (no gain entry, no bias frames, no pixel scale) is stamped, because a retry gives the same answer. The summary counts the cut-short frames in `frames_cut_short`.

**How the sky is measured.** The sky in electrons uses a sigma-clipped mean (3σ), not a median, because a 14-bit sensor stores steps of 4 ADU and a median of such data takes only a few values. The bias level uses the same estimator. The sky includes dark current and amp glow, and a frame with no sensor temperature (no `CCD-TEMP`, as from a DSLR) is stored as `assumed`. A raw color mosaic has its star shapes measured on 2 by 2 super-pixels, so the color filter pattern does not bend them. Each star row says how many detected sources each cut refused.

**The sky in electrons needs the bias level.** A raw frame does not state its bias level (the constant the camera adds to every pixel). The job reads it from up to five bias frames in the calibration library taken at exactly the frame's gain setting, offset and binning (`exact_bias_frames.py`, the one helper the sensor noise measurement uses too). A setting with no bias frames gets `not_measured` sky rows that say so. A bias frame of a nearby setting is never used.

**What it returns.** A `FrameMeasurementSummary`: frames considered, measured, skipped as unchanged, cut short and failed, rows written and seconds. When rows were stored, the backend sends the `quality:measured` event so open screens read the numbers again. The values are in the [Implementation paper](../../../documentation/library_design/Astrometrics_Library_Implementation.md). The star measurements are a design estimate (see [AST-17](../../../documentation/library_design/validation/astrometry.md#ast-17)).

## Skipping an unchanged stack (step 2)

**What it does.** Stacking a target rebuilds every stack it has: one per filter and one for spectra. Before it stacks, `stack_inputs.py` writes down what the stack is made from:

- the frames (path, size and modification time);
- the calibration frames the library picks for them (darks, biases and flats, with their modification times);
- the settings that change the pixels;
- a version number of the stacking code.

It saves that record, with a hash of it (a short fingerprint that changes whenever the record changes), next to the stack as `<stack name>_inputs.json`. On the next run it builds the record again. If the hash matches and the stack file is still there, the stage keeps the stack and returns its path. If not, it logs what changed (for example `3 frame(s) added`, `the flat frames changed`, `settings changed: get_stack_weight`) and rebuilds.

Adding luminance frames (imaging frames taken through a clear filter that passes all visible colors) to a target does not rebuild its spectral stack. Only the stacking step is skipped. Astrometry, photometry and spectroscopy still run on the stack that is there.

**When it never skips.** The skip never triggers when the stack has no record or when the inputs cannot be read. A stack with no record is rebuilt once. The record travels with the stack into the `_previous` folder.

**Settings.** To rebuild anyway, pass `force=True` to `ProcessingPipelines.stack`, pass `--force-restack` to `scripts/run_all_target_processing.py`, or turn off `skip_unchanged_stacks_enabled`.

**For developers.** The version number is `STACKING_ALGORITHM_VERSION` in `stack_inputs.py`. Raise it whenever a code change would give a different stack from the same inputs, or old stacks will be called up to date.

## Exposure groups (step 3)

Frames taken with different exposure times cannot be combined directly, since a longer exposure collects more light per pixel. `stack_runner.py` sorts the frames into exposure groups. It stacks each group on its own (step 4), then lines up and combines the group stacks (step 5), so no frame's light is wasted (measured; see [STK-11](../../../documentation/library_design/validation/stacking.md#stk-11)).

## The stacking engine (step 4)

**What it does.** `stack_runner.py` hands each batch of frames to a stacking engine, the program that calibrates, registers (lines up) and combines them with pixel rejection. Siril is the engine in use. The runner adds two safeguards. It stacks frames of different exposure lengths one length at a time (step 3). For spectral frames, Siril's normal star-based registration does not work on their dispersed images. The runner retries a registration that loses too many frames with a different star detection (partly measured; see [STK-12](../../../documentation/library_design/validation/stacking.md#stk-12)).

**The engine contract.** The contract is in `drivers/interfaces/stacking_driver.py`. A `StackSettings` goes in and a `StackRunResult` comes out, so nothing else in the pipeline reads Siril's commands or files. Two steps in the middle of an engine run are this pipeline's own science: the flat check (`flat_calibration.assess_flats`) and the phase-correlation alignment of spectral frames (`spectral_frame_alignment.align_calibrated_frames`). Both are described below. Phase correlation finds the shift between two images from their Fourier transforms. A driver may not import pipeline code, so the runner hands both to the engine on every run as a `PipelineSteps` (`stack_runner.PIPELINE_STEPS`). The quality summary records the engine's name and version (`stackingEngine`, `stackingEngineVersion`). To use another program, write a subclass of `StackingDriver` (see `drivers/siril_stacking_driver.py`). The runner and stage need no change.

**Weighting mixed exposure lengths.** When the frames of an imaging stack have different exposure lengths, `exposure_weighting.py` asks Siril to weight each frame by its noise (`-weight=noise`) unless a weight is configured. A short frame carries less information than a long one, so equal weights add noise. The exposure times alone predict how much. Equal weights leave the stack's noise higher than the best weighting by the factor √(mean(1/t) × mean(t)), for exposure times t (partly measured; see [STK-07](../../../documentation/library_design/validation/stacking.md#stk-07)).

**Keeping the previous stack.** A restack first moves the stack it replaces, with its images, into a `_previous` folder (see `previous_stack.py`), and puts it back if the restack fails.

### Checking the flat frames

**Why it matters.** A flat frame is an exposure of an evenly lit surface. Dividing each light frame by the master flat (the combined flats) removes vignetting (darker corners) and dust shadows. The division also copies the master flat's noise into every light frame. That noise is the same pattern in each frame, so stacking does not average it away unless the frames were dithered (moved a few pixels between exposures).

**What it measures.** `flat_calibration.py` measures the flat set before Siril builds the master:

- how many frames it has, how bright they are (as a share of full scale), and how noisy the master flat will be (the noise of one frame divided by the square root of the frame count);
- which pixel value counts as full scale. The brightness limits are 10% and 90% of it (design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)), and 98% of it counts as saturated (`_SATURATED_FRACTION_OF_FULL_SCALE` in `flat_calibration.py`; design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)). For integer counts, the camera profile gives the value. ADU (analog-to-digital units) are the raw numbers the camera records. The profile's `clip_ceiling_adu` is the highest value a clipped pixel reaches: 16,383 for the 14-bit Nikon D5300, which stores its counts unscaled, and 65,532 for the ASI533MM Pro. A flat that fills a D5300 sensor reads about 16,383, so judging it against 65,535 would call a good flat too faint. When the camera is unknown or has no profile of its own, the check guesses 65,535 (16-bit counts), or 1.0 for an image whose values all lie within 0 to 1. Siril runs pass the lights' camera name, and the library check (`assess_flat_group`) passes each set's camera;
- whether the noise is above the limit, 0.5% of the flat's level. The limit is set so that the fixed pattern adds under 3% to the variance of a typical stack's own noise (partly measured; see [STK-05](../../../documentation/library_design/validation/stacking.md#stk-05)).

**What it does about noise.** When the noise is above the limit, the pipeline adds Siril's `gauss` command (a Gaussian blur) to the script that builds the master flat. The Gaussian is just wide enough to reach the limit, at most 8 pixels (design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)). Siril mirrors the image at its border. Vignetting and dust shadows are many pixels wide and survive the blur. The pixel-to-pixel sensitivity differences, about 1% on a CMOS sensor, do not. Taking more flats is the better cure, because the blur is a fallback. The pipeline measures the flat of a color (Bayer) sensor but does not blur it, because the blur would mix the color channels. A Bayer sensor has a grid of red, green and blue filters over its pixels, so neighboring pixels see different colors.

**What it records.** The flat assessment records `full_scale` and `full_scale_source` (the camera profile and camera name, or the kind of guess) in the run's `flat_calibration` diagnostics, so a reader can see what the brightness was measured against. It records each finding in the stack's quality summary:

- `flatFrameCount`;
- `flatNoiseFraction` (relative noise of the master flat before smoothing, 0.005 is 0.5%);
- `flatSmoothingSigmaPx` (the blur width, empty when none was applied);
- `flatCalibrationIssues` (one sentence per problem). Each issue also appears in the summary's flag reasons, which flags the stack.

### Building the master calibration frames

Siril builds each master (bias, dark, flat) from the frames the library picked, and the same step applies them to the lights. The bias master comes off every flat, including a lone one, so the bias level does not stay in the master flat and under-correct the vignetting. A stack of one light is calibrated like any other light. Every Siril script sets `set32bits`, so the masters and the stack are saved as 32-bit floating point whatever bit depth the user's Siril preferences name. A 16-bit save would round the normalized stack to 65,536 levels. For the full rules, see the [drivers README](../../drivers/README.md).

### The match flags

**What it checks.** The calibration library files darks by gain, offset, binning and temperature, and bias and flat frames by gain, offset and binning (see the [drivers README](../../drivers/README.md)). When no frame fits the lights exactly, the Siril driver records one sentence per mismatch:

- A gain, offset or temperature mismatch still applies the nearest frames, and the sentence goes into the `calibration_mismatch_flags` diagnostic.
- A binning mismatch applies nothing of that kind, and the sentence goes into `calibration_match_blocking_flags`.

**What it records.** `assess_input_quality.py` reads both lists, adds the sentences to the flag reasons and fails the `calibration_metadata` gate. The stack is then flagged, and the reader sees which frames were applied to which lights, or why none were.

### The frame-count flag

**Why it matters.** Averaging and pixel rejection need several frames. One frame keeps its own noise and any cosmic ray hit, and two frames cannot tell a good value from a bad one.

**What it checks.** `calibration_count_flags` (in `drivers/siril_interface.py`) compares each master's frame count with `minimum_calibration_frames` in `[Processing.Siril]` (a whole number, default 3; design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)). Each master below the minimum gets one sentence, for example `flat master built from 1 frame(s), fewer than the minimum of 3: ...`.

**What it records.** The sentences go into the run's `calibration_blocking_flags` diagnostic. `assess_input_quality.py` reads them, adds them to the input quality's flag reasons and fails the `calibration_frame_count` gate, so the stack is flagged. The pipeline still builds the stack, because the library can legitimately hold one flat or dark. A person reading the quality summary, and the UI that shows it, sees which master was short. The setting does not change any pixel, so changing it does not trigger a rebuild.

### Aligning spectral frames

`spectral_frame_alignment.py` aligns a batch of already-calibrated spectral frames without star detection, since a dispersed spectrum has no normal point-source star pattern to align on. It uses phase correlation.

### Optional pre-fetch

**What it does.** Raw frames can live on a network drive (an rclone mount with a local disk cache). A frame that is not cached yet is downloaded when it is first read, and Siril reads frames one at a time, so each frame waits for its own download. When pre-fetch is on, the driver first reads every staged frame (lights plus the darks, biases and flats in use) with several parallel readers (`drivers/frame_prefetch.py`). The cache then holds them before Siril starts.

**Settings.** `prefetch_frames` in `[Processing]` turns it on (`true`). It is off by default because it downloads files. `prefetch_workers` sets the number of parallel readers. The default of 4 workers matches the mount's `--transfers 4` (design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)).

**What it records.** A failed read is logged and counted, not raised, and the job log gets one `Pre-fetch:` line with the file, byte, failure and time counts.

### Pixel rejection limits

**What it does.** While the engine combines the frames, it throws out any value of a pixel that sits too far from the middle of that pixel's values in the other frames. Two limits control this, in standard deviations (a measure of spread) below and above the middle: `rej <low> <high>` in the Siril script. Siril uses sigma clipping (dropping values more than a set number of standard deviations, "sigma", from the center) in a Winsorized form. Before it estimates the spread, it pulls extreme values in to a cut-off instead of dropping them, so one wild value cannot inflate the spread.

**How the limits are chosen.** In the default `adaptive` mode, `rejection_bounds` (in `utilities/rejection_thresholds.py`) sets them from the frame count. It starts from Chauvenet's criterion. This rule sets the limit so that, for normally distributed values, each good value has a 1 in 2N chance of being thrown out, where N is the number of frames. About half a good value is then thrown out per pixel (1.64σ for 5 frames, 1.86σ for 8, 2.13σ for 15, 2.50σ for 40). Two changes apply to small stacks:

- **Floor.** The high limit is never below `stack_rejection_sigma_floor` (default 2.5σ). Chauvenet's criterion treats the spread as known. With a handful of frames, the stacker estimates the spread from those few values, and the estimate is noisy.
- **Looser low limit.** The low limit is the high limit plus `stack_rejection_low_extra_sigma` (default 0.5σ). Satellite streaks, cosmic ray hits and hot pixels are brighter than the true value, so the high limit has to catch them. Values far below the middle are rare, so the looser low limit removes fewer good values at little cost.

A 5-frame stack therefore uses `rej 3.0000 2.5000`. A `rejection_sigma` passed by the caller, or `fixed` mode, bypasses both changes.

**What it records.** The run records the final limits, the mode, the floor, the extra and whether the floor applied (`rejection_sigma_low`, `rejection_sigma_high`, `rejection_sigma_mode`, `rejection_sigma_floor`, `rejection_sigma_low_extra`, `rejection_sigma_floor_applied`) in the quality summary's `resolvedParameters`. Both settings change the stack's pixels, so they are part of the skip record in step 2.

**Status.** The floor and the looser low limit are checked only in a simulation of Gaussian noise (partly measured; see [STK-08](../../../documentation/library_design/validation/stacking.md#stk-08)). On stacks of 40 or more frames the frame-count rule costs no sharpness (partly measured; see [STK-09](../../../documentation/library_design/validation/stacking.md#stk-09)).

## Combining exposure groups (step 5)

When a stack has more than one exposure group, `stack_runner.py` lines up the group stacks and combines them, using the code in `processing/`:

- `group_derotation.py` first turns spectral exposure-group stacks onto one common trail tilt (the angle at which the spectrum trail crosses the frame).
- `group_alignment.py` aligns each group's stack with the reference group. For images it matches stars, which also removes the small rotation and scale between nights.
- `exposure_groups.py` puts every group on the reference group's brightness scale by dividing it by a group scale factor. It leaves out a group whose brightness one scale factor cannot describe, and combines the rest. The [processing README](processing/README.md) describes how the group scale factor is measured.

### Trimming noisy edges

**Why it matters.** Frames shift between nights, so fewer frames cover the edges of a stack of several nights, and the edges are noisier.

**What it does.** After the stack is complete, `stack_crop.py` trims the stack and its rejection map back to where the pixel noise falls to within 15% of the interior's (partly measured; see [STK-23](../../../documentation/library_design/validation/stacking.md#stk-23)). The rejection map is an image of how many values the stacker threw out at each pixel. The trim measures the noise in 25-pixel strips and finds the noisy run on each side.

**Why these numbers.** The 15% limit sits between the noisy and the clean edges of the stacks measured. For frames of equal weight, 15% more noise means an edge covered by about three quarters of the frames, because noise goes as one over the square root of the number of frames. A 25-pixel strip holds thousands of sky pixels for a steady noise estimate and still places the edge within a few dozen pixels.

It then cuts out the largest box with the original proportions (a square stack stays square) that fits in what is left, as near the image center as the clean area allows. It changes nothing on a stack with clean edges. It refuses a trim of more than a quarter of a side. It moves the sky-coordinate reference with the crop.

**Setting.** `trim_noisy_stack_edges_enabled` (default `true`) turns it off.

## Judging the result (step 6)

`stack_quality.py` holds the rules for deciding whether a stacked image is good enough to use. `exposure_saturation.py` checks whether a given exposure length saturates the brightest star in the field. `exposure_group_report.py` records a description of the exposure groups a stack was built from.

## The preview image (step 7)

**What it makes.** `stack_preview.py` saves two images next to the finished stack: a JPEG named `<stack name>_preview.jpg` and a FITS file named `<stack name>_processed.fits`. Both hold the same stretched image, the same way up. Siril writes a JPEG upside down compared with its FITS source, so the JPEG script flips it first. The FITS file holds 32-bit numbers between 0 and 1, so the image viewer can zoom into it without the JPEG's 8-bit banding. It keeps the stack's observation keywords (exposure time, telescope, object) and a header note that it is a stretched image and not linear data.

**Why it stretches.** A stack holds linear data (pixel values proportional to the light collected). Linear data looks almost black on a screen, because most of the interesting detail sits just above the sky level. Stretching is a non-linear brightness curve that lifts that faint detail. The sub-steps below run on a scratch copy first. In short, they remove the sky gradient, balance the colors, remove excess green, stretch, remove noise, and dim the stars. Sub-step 6 (StarNet) replaces sub-steps 4 and 7 when it is chosen.

1. **Gradient removal.** GraXpert removes the sky's brightness gradient, if the `[Processing.GraXpert]` setting names a GraXpert command. Its AI model learns what the smooth background looks like and subtracts it.
2. **Color calibration.** This sub-step balances the colors of a color stack on the linear data after GraXpert.
    - **Default method.** By default (`preview_colour_calibration_method = "pcc"`) it runs Siril's photometric color calibration (PCC). Siril solves the field from the pointing, focal length and pixel size in the header, unless the stack already has a plate solution (the mapping from pixel position to sky coordinates). It matches the stars to the Gaia DR3 catalog (the star catalog from the European Space Agency's Gaia satellite, third data release) over the network, removes each channel's background and scales the channels. It takes about 10 s per stack (measured; see [STK-27](../../../documentation/library_design/validation/stacking.md#stk-27)).
    - **Fallback method.** If Siril, the solve or the network fails, the star-based method runs instead (`colour_calibration.py`). It needs no catalog or network, and `preview_colour_calibration_method = "stars"` uses it alone. The method works in these steps:
        1. It subtracts each channel's sky level (the median of its pixels).
        2. It finds the stars on the summed channels.
        3. It measures each star in each channel with aperture photometry: it sums the light in a circle around the star and subtracts the sky measured in a ring around it. Here the circle has a 3.5 pixel radius and the ring runs from 7 to 11 pixels (design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)). The circle is about 1.2 times the star width of the Nikon stacks, which catches most of a star's light and keeps the noise down. The ring lies well outside the star's faint outer light.
        4. It keeps the stars in the middle of the brightness range, from the 30th to the 85th percentile of total light (design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)). The brightest stars may be saturated and the faintest have noisy colors.
        5. It takes the median star to be neutral. The red and blue gains (the scale factors for the red and blue channels) are the factors that make the median red-to-green and blue-to-green flux ratios equal to 1. Green is the reference.
    - **Setting.** `preview_colour_calibration_enabled` in `[Processing.Siril]` (default `true`) turns the whole sub-step off.
    - **Effect on the stretch.** The pipeline stretches an image with calibrated colors with one shared stretch (`autostretch -linked`). Siril's default stretches each channel by itself, which sets every channel's sky and scale back to the same values and so undoes any color balance. When too few stars are found (fewer than 40), or the setting is off, the stack is stretched channel by channel. The [post-processing README](post_processing/README.md) shows how the gains compare with Siril's own calibration.
3. **Green removal.** Siril's Remove Green Noise (`rmgreen`) lowers excess green in a color stack, such as one from the Nikon DSLR.
    - **Why.** A color sensor has twice as many green pixels as red or blue and the sky's glow is often green, so these stacks look green.
    - **What it does.** It runs on the linear data in the same Siril script as the stretch, and only when the stack has three color planes, so it never touches the single-channel ZWO stacks. Siril's default method limits each pixel's green to the average of its red and blue and keeps the pixel's lightness. It does not remove color gradients across the frame, which are a separate problem.
    - **Setting.** `preview_remove_green_noise_enabled` in `[Processing.Siril]` (default `true`) turns it off.
    - **Status.** With the color calibration on, it changes little (partly measured; see [STK-28](../../../documentation/library_design/validation/stacking.md#stk-28)).
4. **Stretch.** Siril's Autostretch sets the black point (the pixel value shown as pure black) from the image's own background noise. It bends the brightness curve so the sky lands on a chosen gray level.
    - **Choosing the sky level.** `sky_level.py` chooses that level from the stack itself. It measures the brightest extended light (nebulae and galaxy halos, not stars) as a signal-to-noise ratio above the sky, then places that light at a fixed display brightness. A faint target needs a lot of lift, so its sky comes out lighter (up to 0.20) and its faint structure stays visible. A lighter sky also lifts the grain into view, which is why the limit is 0.20 and not Siril's own 0.25. A bright target needs little, so its sky comes out darker (down to 0.11) for more contrast. A stack whose sky cannot be measured gets 0.18 (`FALLBACK_SKY_LEVEL`; partly measured; see [STK-29](../../../documentation/library_design/validation/stacking.md#stk-29)).
    - **Tuning.** The rule has one tuned number, set by eye (partly measured; see [STK-29](../../../documentation/library_design/validation/stacking.md#stk-29)).
5. **Denoise.** Cosmic Clarity removes noise from the stretched image, if the `[Processing.CosmicClarity]` setting names its denoise program.
    - **Setting.** `denoise_strength` (0 to 1, default 0.9) controls how much it removes.
    - **Why after the stretch.** Autostretch measures the noise level of the image it stretches. After a denoise, it measures less noise and turns the contrast back up, so the remaining noise looks as strong as before and large blotchy patches appear.
    - **Status.** The order and the 0.9 default are partly measured (see [STK-30](../../../documentation/library_design/validation/stacking.md#stk-30)).
    - **Safety.** The program only reads and writes the `input` and `output` folders next to it. The sub-step takes an exclusive lock, refuses to run if `input` already holds files, and deletes only its own files.
6. **StarNet (optional).** StarNet (`starnet_stretch.py`) replaces the single stretch and the star toning.
    - **What it does.** Siril's `starnet` command splits the linear image into a starless image and a stars-only image. The pipeline stretches the starless image the usual way. It stretches the stars on their own with an arcsinh curve (`star_stretch`, default 1000: larger values lift fainter stars). It blends the two with a screen blend, which adds light without clipping, like projecting two slides onto one screen. The blend scales the stars by `star_strength` (default 0.8).
    - **How to choose it.** It is off by default and is chosen per run, with `starnet=True` on `ProcessingPipelines.remake_preview`. There is no setting for it. It needs StarNet set up in Siril.
    - **Status.** It adds about half a minute to a preview. The defaults 1000 and 0.8 were chosen by eye (partly measured; see [STK-31](../../../documentation/library_design/validation/stacking.md#stk-31)).
    - **Limits.** StarNet can fail or leave artifacts on a dense field. A failed run falls back to the normal stretch.
7. **Star toning.** Star toning (`star_tone.py`) dims the stars in proportion to their brightness and compresses the brightest values below pure white.
    - **Why.** The stretch makes nearly every star a small white dot of about the same brightness.
    - **What it does.** The toning separates the light above the local background and raises it to a power (1.8) so that fainter stars dim more than bright ones. It then applies a soft roll-off above 0.65 so a saturated core ends near 0.92. It dims only light that stands out by more than 0.05 to 0.12 above the background, so low-contrast structure (the knots along a galaxy's spiral arms) keeps its detail. It does not touch the background and nebulae. It tones a color image on its brightness (the mean of its channels) and scales every channel of a pixel by the same factor. Toning each channel alone gives a color stack a color cast, because the channels differ in noise and so are dimmed by different amounts.
    - **Noise-aware start.** The start of the dimming also follows the image's noise. Light counts as a star only if it stands at least 6 noise widths (standard deviations of the sky noise) above the background. The 0.05 and 0.12 limits above apply unchanged when the noise is small. The background under the stars is the local minimum, which lies below the sky, so sky noise alone reaches several noise widths above it. A start of 6 widths clears it and keeps the sky level where `sky_level.py` put it (partly measured; see [STK-32](../../../documentation/library_design/validation/stacking.md#stk-32)). Faint stars are then not dimmed, so a star field looks busier.
    - **Setting.** `preview_star_tone_enabled` in `[Processing.Siril]` (default `true`) turns it off.
    - **Tuning.** The power and the knee were chosen by eye (partly measured; see [STK-32](../../../documentation/library_design/validation/stacking.md#stk-32)).

### Bright objects such as the Moon

**Why it differs.** A stack of a bright extended object, such as the Moon, takes a different route. The Moon's sky is about 3,000 times fainter than its disc, so the normal stretch would turn the whole disc white and lose the craters.

**How it is detected.** `bright_object.py` recognizes this case from the data. It counts what share of the image the normal stretch would push to white. A nebula, galaxy or star field leaves only the cores of the brightest stars white. The Moon leaves far more. A share of 1% or more switches to the bright-object route (partly measured; see [STK-33](../../../documentation/library_design/validation/stacking.md#stk-33)).

**What it does.** It runs one Siril script with a different stretch. The black point is the sky level, the white point is near the top of the object's own brightness, and the curve places the object's median at a fixed gray (`BRIGHT_OBJECT_BRIGHTNESS`, 0.55; partly measured; see [STK-33](../../../documentation/library_design/validation/stacking.md#stk-33)). It skips GraXpert and the denoise, because GraXpert would treat the Moon's glow as sky background and the denoise could smooth the surface.

**Limits.** A planet is too small to reach the 1% share, so planet stacks use the normal route.

### Skipped sub-steps, failures and logging

Leaving a sub-step's setting blank skips it. Without Cosmic Clarity and the star toning, one Siril script stretches the image and saves the JPEG.

Both images are for people only. Nothing in the pipeline reads them, and the stack file does not change, so photometry and spectroscopy still read the linear data.

If GraXpert or Cosmic Clarity fails, the pipeline makes the image from the result of the sub-step before it. If Siril is missing or fails, the pipeline logs a warning and keeps the stack. A failed FITS copy does not fail the preview: the JPEG alone is still recorded.

Each sub-step that works logs a line saying so, and one `Preview steps:` line lists every sub-step and its result (for example `GraXpert done; Siril stretch done; Cosmic Clarity done; Star toning done`). A stack made from the viewer shows these lines in its job log, so you can see which sub-steps ran.

The step removes any older preview images first, so they always match the current stack. The Image Processing viewer's Stretch view shows the JPEG when it is at least as new as the stack.

### Recording the processed image

The step then records the FITS file as the target's processed image (`processed_image`), or the JPEG when no FITS was made. The Image Viewer shows it first, with the same zoom and pan as the FITS viewer, and does not stretch it again. It replaces a processed image that a person attached to the target (the attached file stays where it is). It skips a stack that the target does not show (a target with stacks from several telescope setups shows one). `scripts/backfill_stack_previews.py` makes previews for stacks that already existed and records them the same way.

### Remaking the preview

`post_processing/preview_remake.py` runs only this step again on the stack that is already there, without restacking (`ProcessingPipelines.remake_preview`). A run can override the denoise and star-toning settings, or choose StarNet, without writing them to the configuration. By default it copies the old preview images into a `_previous_preview` folder first, so you can compare the two, and a failed run puts them back. It never opens the stack file for writing.

## Where the output goes

**Output folder.** The pipeline writes its output (stacks, group stacks, rejection maps, registration files, previews and processed images) to `<stacks_path>/lights/<target>/`. The `stacks_path` entry in the `[Image Library]` section of the configuration sets that folder. Without it, the output goes beside the raw frames, in the frames path. The setting lets the raw frames stay on one disk while the output goes to another. For example, both can sit on a mounted cloud drive, with the databases kept on the local disk (see the storage layout section of the [installation guide](../../../documentation/Installation.md)). The backend's image server looks for a file in the frames folder first and then in the stacks folder, so the viewer asks for one URL either way. `scripts/move_stacks_to_stacks_path.py` moves the files that already exist and updates the paths in the database.

**Raw frames on a network drive.** The raw frames can sit on a network drive. The pipeline does not copy them first. Siril reads each frame once through a symlink and writes its working files to `~/.cache/astrometrics/siril` on the local disk. Set `frames_mount_point` in the same section to the folder where that drive mounts. While nothing is mounted there, downloads and file sorting stop with an error instead of writing frames to the local disk. The pipeline never rewrites a raw frame.

## Settings

Each setting is described in the section named in the last column. A setting with the status design estimate links to [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates).

| Setting | Default | Effect | Described in |
| --- | --- | --- | --- |
| `quarantine_bad_frames_enabled` in `[Processing.Siril]` | on | Turns the frame quarantine off | [Frame quarantine](#frame-quarantine) |
| `skip_unchanged_stacks_enabled` | on | Turns the skip of an unchanged stack off | [Skipping an unchanged stack](#skipping-an-unchanged-stack-step-2) |
| `minimum_calibration_frames` in `[Processing.Siril]` | 3 (design estimate) | Fewest frames per master before the stack is flagged | [The frame-count flag](#the-frame-count-flag) |
| `prefetch_frames` and `prefetch_workers` in `[Processing]` | off; 4 workers (design estimate) | Reads frames into the cache before Siril starts | [Optional pre-fetch](#optional-pre-fetch) |
| `stack_rejection_sigma_floor` and `stack_rejection_low_extra_sigma` | 2.5σ; 0.5σ (partly measured; see [STK-08](../../../documentation/library_design/validation/stacking.md#stk-08)) | Floor for the high rejection limit, and how much looser the low limit is | [Pixel rejection limits](#pixel-rejection-limits) |
| `trim_noisy_stack_edges_enabled` | on | Turns the edge trim off | [Trimming noisy edges](#trimming-noisy-edges) |
| `exposure_group_gain_tolerance` | 0.05 (partly measured; see [STK-20](../../../documentation/library_design/validation/stacking.md#stk-20)) | Largest flux-ratio difference before a group is left out | [Quality outputs](#quality-outputs) |
| `[Processing.GraXpert]`, `[Processing.CosmicClarity]` | blank | Names the program for gradient removal or denoising; blank skips the sub-step | [The preview image](#the-preview-image-step-7) |
| `preview_colour_calibration_method` and `preview_colour_calibration_enabled` | `pcc`; on | Chooses the color calibration method, or turns it off | [The preview image](#the-preview-image-step-7) |
| `preview_remove_green_noise_enabled`, `preview_star_tone_enabled` | on | Turn green removal or star toning off | [The preview image](#the-preview-image-step-7) |
| `denoise_strength` | 0.9 (partly measured; see [STK-30](../../../documentation/library_design/validation/stacking.md#stk-30)) | How much the denoise removes | [The preview image](#the-preview-image-step-7) |
| `star_stretch` and `star_strength` | 1000; 0.8 (partly measured; see [STK-31](../../../documentation/library_design/validation/stacking.md#stk-31)) | StarNet star stretch and blend strength | [The preview image](#the-preview-image-step-7) |
| `stacks_path` and `frames_mount_point` in `[Image Library]` | blank | Where the output goes, and where the network drive mounts | [Where the output goes](#where-the-output-goes) |

## Quality outputs

The pipeline records each gate in the stack's `gates` as `passed`, `failed` or `not_checked`. A gate is a run-level pass/fail entry in a quality record. A check that could not look is never recorded as passed. The red-team tests in `test/test_stack_gates.py` and `test/test_frame_quarantine_gate.py` (tests written to attack each check) feed each gate input that must fail it.

### Gates

| Gate | Fails when | Not checked when |
| --- | --- | --- |
| `frame_quarantine` | A session had too many bad frames to move, so they stay in the stack | No session had enough readable frames; the step is off; the stack is spectral |
| `gain_homogeneity` | Never. Setting aside a minority gain corrects the stack | No frame records its gain |
| `background_homogeneity` | An exposure length shows a sudden change of sky background | The check is off, or no exposure length has three frames with a measured background |
| `flat_level`, `flat_noise` | The flats are too faint, too bright or too noisy, or cannot be read | The stack used no flats; the noise could not be estimated |
| `calibration_metadata` | A calibration frame was applied although its gain, offset or temperature does not match the lights, or a kind of calibration frame was left out because its binning does not match | No calibration frame was applied and none was left out |
| `calibration_frame_count` | A master bias, dark or flat was built from fewer frames than `minimum_calibration_frames` (default 3) | No calibration frame was applied |
| `exposure_group_linearity` | A group's brightness is not one scale factor of the reference group's: measured on the same isolated, unclipped stars in both groups, its faint-star or bright-star flux ratio differs from the median ratio by more than `exposure_group_gain_tolerance` (default 0.05). Spectral stacks, and imaging stacks with too few clean stars, compare mid-range and bright-end pixels instead. The pipeline leaves that group out of the combined image | The stack has one exposure length, or no group could be compared with the reference |
| `exposure_groups_combined` | An exposure group that had frames is missing from the combined stack, because it failed to stack, could not be lined up or was nonlinear. The message names each group, why, and how many frames it holds. The stack's `frames_stacked`, session breakdown and sharpness sample then count only the groups that were combined | The stack has fewer than two exposure groups |
| `rejected_pixel_fraction` | The stacker threw out 15% or more of the pixels | The stacker did not report a count |
| `stack_sharpness` | The stack is more than 1.2 times wider than its input frames predict | The stack is spectral or a single frame, or a width could not be measured |
| `spectral_registration` | A frame's zero-order star moved or faded | The stack is not spectral, or the check did not run |
| `saturated_pixel_fraction` | 0.1% or more of the stack's pixels are saturated | The saturation could not be measured |
| `zero_pixel_fraction` | Half or more of an image stack's pixels are exactly zero, or 5% or more of a spectral stack's | The file could not be read |
| `raw_frame_clipping` | The raw frames of any exposure group have more than 20% of their pixels at exactly zero (a camera offset below the read noise cuts the faint sky off). A stack where only some groups are clipped fails too, and its message says the longer groups replace the faint pixels. A stack where every group is clipped cannot replace them. One-length stacks are measured as well | The raw frames could not be read |
| `negative_pixels` | Siril warns that 50% or more of a frame went negative | Never: no warning is the measurement |

**Where the limits come from.**

- `frame_quarantine`: partly measured; see [STK-03](../../../documentation/library_design/validation/stacking.md#stk-03).
- `background_homogeneity`: partly measured; see [STK-04](../../../documentation/library_design/validation/stacking.md#stk-04).
- `flat_noise`: partly measured; see [STK-05](../../../documentation/library_design/validation/stacking.md#stk-05). The `flat_level` limits are design estimates (see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates)).
- `exposure_group_linearity` (5%): partly measured; see [STK-20](../../../documentation/library_design/validation/stacking.md#stk-20).
- `rejected_pixel_fraction` (15%): partly measured; see [STK-24](../../../documentation/library_design/validation/stacking.md#stk-24).
- `stack_sharpness` (1.2 times): the predicted width is the root mean square of a sample of the input frames' star widths (see the [post-processing README](post_processing/README.md)). The limit is partly measured; see [STK-25](../../../documentation/library_design/validation/stacking.md#stk-25). The limit does not fire on normal stacks, and whether it catches a real failed alignment is untested.
- `saturated_pixel_fraction` (0.1%): design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates).
- `zero_pixel_fraction` and `negative_pixels`: measured; see [STK-26](../../../documentation/library_design/validation/stacking.md#stk-26).
- `raw_frame_clipping` (20%): partly measured; see [STK-22](../../../documentation/library_design/validation/stacking.md#stk-22).

**What raw-frame clipping means.** The camera's offset is meant to lift the readings so that noise below zero is still recorded. When the offset is smaller than the read noise, readings that would fall below zero are stored as 0. The faint sky then reads too bright on average and its noise reads too low. When only the shorter groups are clipped, the combination (step 5) leaves the clipped group out at those pixels and uses the longer groups, whose sky stays above zero.

**Known blind spots.** `gain_homogeneity` cannot see a missing gain, because a frame with no recorded gain is given the default `800`. `negative_pixels` trusts that Siril's log was read, so an unreadable log would pass.

### Metrics

The pipeline's per-stage quality metrics are tabled in the stage READMEs: [pre-processing](pre_processing/README.md), [processing](processing/README.md) and [post-processing](post_processing/README.md). `stage.py` stores the two judgments on the stack's quality summary as `inputQuality` (`StackingInputQuality`) and `outputQuality` (`StackingOutputQuality`). Each carries its own flag reasons, so a reader can tell a bad input from a bad result.

### Quality measurements

After the summary is built, the stage stores the same numbers and gates as quality measurements in the quality database (`post_processing/record_quality.py`). The summary and its JSON stay as they are. The caller passes the time (`measured_at`) and the job id. With no time, nothing is stored and a warning says so. A failure at any point is logged and never fails the stack. The rows are:

- **The summary's numbers**, under names that say how each was measured (for example `stacking.zero_pixel_fraction`). A number the summary does not have is stored as not measured, with a reason. They are about the stack, named by the target and the stack's file name. A value of one exposure group is about that group's own stack file in the `groups` folder.
- **The 16 gates**, one metric each (`stacking.gate_<gate name>`), stored with the gate's outcome and limit. They stay self-checks.
- **The stack's noise in empty sky** (`post_processing/stack_noise.py`). The stage places 100 small circles (radius 4 pixels, the photometry pipeline's star aperture) away from stars, in sky coordinates, the first time a target is stacked, and stores them in `quality_apertures.json` beside the target's stacks. Every later stack of the target measures the same sky patches. Each stack gives three numbers: the noise in the stack's own units, the brightness of 10 fixed reference stars, and their ratio. The ratio is comparable between stacks. The sky coordinates come from the stack's header, or, for a fresh stack (the astrometry step writes the header later), from the registration reference frame's header, moved by the edge trim. The stage checks that the stack's brightest stars sit where that frame shows stars, and measures nothing if they do not. The check keeps saturated stars in the frame's star list, because the frame's own brightness limit would cut the brightest stars that the averaged stack keeps. That header is only a first guess: the camera software writes the pointing the mount was told to use, not a plate solution (the mapping from pixel to sky found by matching stars), so it can be wrong by tens of pixels, and by a different amount on each night. The first stack of a target fixes its sky patches with its own guess. For a later stack with a guess like that, the stage searches for the stored reference stars within 500 pixels of where the guess puts them, finds the shift that at least 5 of them agree on (within 3 pixels), and moves the coordinates by that shift. With fewer agreeing stars the rows are not measured, with that reason. A stack whose header has a real solution is not shifted. With no coordinates the three rows are not measured, with the reason "no WCS for this stack". On a 3000 by 3000 pixel stack the step takes about 7 seconds the first time and 4 seconds after, when the coordinates come from the reference frame (under 1 second when the stack's header has them). A spectral stack has no noise rows.
- **The frames that went in** (`post_processing/stack_membership.py`). One `stacking.frame_weight` row for each frame that is a member of the stack, scaled so the weights average 1. Frames Siril dropped while registering (its sequence file marks them) and unreadable files are not members. With equal weights every frame is 1. With several exposure groups the weight is the group's weight (frames times exposure squared over frame noise squared) shared among its frames. With `-weight=wfwhm` (the `stack_weight` setting in `[Processing.Siril]`) the weights are rebuilt with Siril's own formula from each stacked frame's weighted star width, which Siril saves in its sequence file: the weight is (1/w² − 1/w_max²) / (1/w_min² − 1/w_max²), so the sharpest frame gets 1 and the blurriest gets 0, and the weights are then divided by their mean. When Siril weights by noise (`-weight=noise`) or by star count (`nbstars`) the weights are not rebuilt. The noise weight needs a scale (`pscale`) that Siril fits while it normalizes the frames and does not save, so each row is not measured, with that reason, and still records the membership. When fewer images were stacked than there are candidates, Siril's own filters dropped some without naming them, and the rows are marked assumed. `stacking.effective_frame_count` and `stacking.effective_time_s` say what the weighted stack is worth in equal frames and equal seconds.

### Subset stacks

Two jobs stack some of a target's frames apart from its library stack and measure the noise of each result. They exist to answer "how fast does the noise fall as frames are added" and "what did each night give". `ProcessingPipelines.stack_subset(target, frames, label=...)` stacks exactly the frames it is given. `measure_noise_growth(target)` and `measure_night_contributions(target)` are the two jobs; each runs as a background job of type `stacking`.

Where the files go. A subset stack is written to `<the target's stacks folder>/_subsets/<label>/<label>.fits`, beside the library stack and never over it. The library stack, its preview, its `_previous` copy and its `_inputs.json` are never touched, and nothing is assigned on the target or saved. The subset path (`subset_stack.py`) copies the frames, runs no frame quarantine step (the frames of a subset are frames the library stack already kept), makes no preview and writes no `_inputs.json`. It uses the target's normal settings: the configured rejection and weighting, exposure groups, and the edge trim. You can delete the `_subsets` folder at any time. Nothing reads it back.

Which frames. The library stack's `_inputs.json` lists the frames that went to the stacker, so that list is the stack's population. A stack with no such file raises `NotFoundError`; stack the target again to write one.

- **Noise against frame count** (`measure_noise_growth`). The frames are split by observing night (`utilities/observing_night.py`). The subsets are the first night, the first two nights and so on, up to one night fewer than the library stack. A target with one night uses the first quarter, half and three quarters of its frames. At most 4 subsets are made by default (`maximum_steps`), spaced evenly through the possible ones. A subset needs at least 4 frames (`MINIMUM_SUBSET_FRAMES`; a design choice, not validated). The library stack is the last point of the curve and is not stacked again. Each subset stores the stack noise (the three `stacking.empty_aperture_noise_*` and `stacking.noise_reference_flux_adu` rows) and the effective frame count and time at level `stack`. The subject id holds the subset's label, for example `M 99/_subsets/M99_L_Stacked.nights-2/M99_L_Stacked.nights-2.fits`. The noise is measured in the sky apertures stored beside the library stack (`quality_apertures.json`), so the numbers compare with the library stack's. If the target has no stored apertures, the noise rows are not measured with that reason; stack the target once so its first stack chooses them. Cost: one stack for each subset, each with fewer frames than the library stack, so expect the time of about one to three library stacks (a design estimate, not measured).
- **Each night's contribution** (`measure_night_contributions`, or `include_night_contributions=True` on the growth job). For each night, the library stack's frames without that night are stacked and measured, and two rows are stored on that leave-out stack, with the night in the subject's observing night: `stacking.night_weight_share` (the night's share of the library stack's total frame weight, read from the `stacking.frame_weight` rows that stack stored) and `stacking.noise_without_night` (the normalized noise of the stack without the night). The shares add up to 1. A share is stored as not measured, with the reason, when the stack's weights are unknown; equal weights are never assumed. A target with one night has nothing to leave out. Cost: one stack for every night, so it never runs by itself after a stack. The user asks for it.

## Where each piece lives

The folders follow the layout in the [pipelines README](../README.md). The [review guide for stacking](../../../documentation/library_design/pipelines/stacking.md) summarizes the pipeline's parameters and their status.

- `metrics.py` declares the quality metrics this pipeline measures for the metric registry: the summary's numbers, the 16 gates, the noise in empty sky, the effective frame count and time, the frame weights, and the quarantine step's star roundness and median pixel value of each frame. See [shared quality](../shared/quality/README.md).
- `stack_request.py` is the entry point behind `ProcessingPipelines.stack`. `choose_frames_to_stack` picks the light frames. It never mixes imaging and spectroscopy frames or two cameras. It narrows the choice by filter, file-name range, time window and camera, or takes an exact list. `run_stack` runs `stage.py` on the chosen frames as a tracked job, holds a stacking slot while Siril runs, and saves the target when a stack was made. A request that cannot be met raises an error.
- `stage.py` is the coordinator. It runs the steps above in order for one target, and the rest of the codebase calls it to stack a target's frames.
- `calibration_ingest.py` turns a rescan of the calibration library into a report. It compares the library's frame lists from before and after the rescan, lists the groups that gained or lost frames, and measures each new set of flats with the stacker's own flat check (`pre_processing/flat_calibration.py`). `CalibrationCatalog.refresh` and `CalibrationCatalog.assess_flats` call it.
- `frame_measurement_run.py` is the frame measurement job described above: it picks the frames that need measuring, finds each frame's camera profile, capture setting and bias level, measures four frames at a time with `shared/quality/frame_measurement.py`, stores each frame's rows in one transaction and stamps the frame. `record_frame_measurements_for_target` holds the guards, and `record_stage_frame_quality` is the stacking stage's entry. It sits here, not under `shared/quality/`, because it passes in the Gaussian star width from the astrometry pipeline.
- `subset_stack.py` stacks exactly the frames it is given into a scratch folder and changes nothing else (`stack_frame_subset`). `subset_noise_run.py` plans the growth series and the leave-one-night-out stacks, measures each subset's noise and members, and stores the rows (`run_noise_growth`, `run_night_contributions`, `stack_chosen_subset`). See [subset stacks](#subset-stacks).
- `exact_bias_frames.py` lists the bias frames taken at exactly one gain setting, offset and binning, or says why there are none. It is the one rule the frame measurement job and `sensor_noise_run.py` share.
- `sensor_noise_run.py` finds the flat and bias frames of one camera setting in the calibration library, loads them, pairs the flats by the time they were taken (at most 300 s apart, so a pair never spans two sessions) and the biases in file-name order, refuses flats above the camera profile's photometric linearity limit, and calls `pre_processing/sensor_noise.py`. With `save=True` it writes a measured result into the camera profile (`save_sensor_setting_noise`). `CalibrationCatalog.measure_sensor_noise` calls it. It sits here, not under `shared/quality/`, because it can write a profile and quality code only reads.
- `stack_runner.py` runs the stacking engine (steps 3 to 5): exposure groups, registration retries and the final read of the engine's files. The engine is the only code that stacks a batch's pixels. `processing/` combines the group stacks.
- `pre_processing/` checks and prepares the inputs. It holds the one-camera rule, the gain and background homogeneity checks, the flat-frame check, and the input-quality judgment (`StackingInputQuality`). The one-camera rule (`camera_selection.py`) picks a named camera's frames and refuses a set that still mixes cameras, because frames from two cameras differ in pixel size, gain, noise and calibration frames. See its [README](pre_processing/README.md).
- `processing/` lines up and combines frames: exposure groups, group alignment, spectral frame alignment and derotation. See its [README](processing/README.md).
- `post_processing/` judges and presents the finished stack. It holds the quality thresholds, the saturation check, the exposure-group report, the output-quality judgment (`StackingOutputQuality`), the bright-object rule, the sky level rule, the preview image and its remake, and the stack reports that the API returns (`stack_quality_report.py`). Those reports cover the stack's star width, share of rejected pixels and frame registration, an optional comparison with the previous or another stack, and the saved summary. See its [README](post_processing/README.md).

## Limits

- The pipeline calibrates and stacks with Siril only. Another engine needs a new `StackingDriver` subclass.
- The frame quarantine cannot judge a session of fewer than 8 frames, and it skips spectral stacks.
- The background check finds one sudden jump in sky level, so a gradual build-up of cloud can pass it.
- `gain_homogeneity` cannot see a missing gain, and `negative_pixels` trusts that Siril's log was read.
- StarNet can fail or leave artifacts on a dense field, and the bright-object route does not apply to planets.
- Many tuned values have only partial measurements. Each carries its status label and register link above.

## Scope

This pipeline produces one combined image (or one per exposure group and camera setup) from a target's raw frames. It does not detect stars, solve the sky position, or measure brightness. Those jobs belong to astrometry and photometry, which read this pipeline's output rather than duplicate its work.

For exact behavior, read the code.
