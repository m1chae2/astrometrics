# Stacking

This pipeline combines many individual pictures (sub-exposures, also called light frames) of the same target into one master image. Adding aligned pictures together makes real stars and galaxies brighter, while noise that occurs randomly in each picture — camera read noise, heat noise — tends to cancel out. Before combining, the pipeline also rejects temporary artifacts such as satellite trails, cosmic rays, and hot pixels (single pixels that read bright in every frame because of a sensor defect).

Terms are defined in the [glossary](../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary). This README also defines each term the first time it appears.

A few terms appear throughout:

- **Calibration frames** are pictures taken to measure the camera itself rather than the sky. A **bias** frame is a zero-length exposure that records the fixed electronic offset of every pixel. A **dark** frame is taken with the shutter closed and records the signal that heat adds over the exposure time. A **flat** frame is a picture of an evenly lit surface that records how sensitive each pixel is. A **master** bias, dark or flat is the average of many such frames.
- **Calibrating** a light frame means subtracting the master bias and dark and dividing by the master flat.
- **Registration** means lining frames up pixel-for-pixel so the same star falls on the same pixel in every frame.
- **Pixel rejection** means throwing out a pixel's value in one frame when it disagrees strongly with the same pixel's values in the other frames, before averaging.
- **Siril** is the external image-processing program that this pipeline uses to calibrate, register and combine frames. **FITS** is the standard astronomy image file format. **CMOS** is the type of image sensor in the cameras used here.
- The **gain setting** is the camera's amplifier setting, a unitless number stored in the FITS `GAIN` card. (Elsewhere, "gain" in e-/ADU means the number of electrons that make one raw count; this README does not use that meaning.) **Offset** is a constant the camera adds to every pixel so that noise below zero is still recorded. **Binning** combines blocks of neighbouring pixels (for example 2 by 2) into one larger pixel.

## What the pipeline does, in order

`stage.py` runs the steps below in this order for one target. Steps 3 to 5 run inside one call to `stack_runner.py`, which drives the stacking engine. Other READMEs refer to these step numbers.

### Step 1. Check that the frames agree

Three checks run before stacking:

- `frame_homogeneity.py` checks that the gain settings match across the frames.
- `frame_quarantine.py` measures each light frame's stars and moves a frame with clouds or trailed stars into an `_excluded` folder beside it. Spectral frames (frames taken through a grating, which spreads each star into a streak of colours) are skipped, because their streaks are the spectra. The step is described below.
- `background_homogeneity.py` compares each frame's background brightness with the other frames of the same exposure length to catch a frame taken through cloud or twilight. It finds one sudden jump in sky level, so a gradual build-up of cloud can pass it (partly measured; see [STK-04](../../../documentation/library_design/validation/stacking.md#stk-04)).

A frame that disagrees with the others is excluded rather than stacked. Each check is recorded as a gate (see "The gate record" below).

#### The quarantine step

**What it does.** It compares each frame with the other frames of its own session. A session here means frames of one camera, filter and exposure length taken less than four hours apart. The step moves a frame when:

- it has fewer stars than half the session's median star count (cloud, or heavy trailing); or
- its stars are much less round than the session's: roundness (a star's narrow width divided by its wide width, 1.0 for a circle) is below 0.80 and at least 0.15 below the session median (0.20 for a colour camera).

These limits are partly measured (see [STK-01](../../../documentation/library_design/validation/stacking.md#stk-01) and, for colour cameras, [STK-02](../../../documentation/library_design/validation/stacking.md#stk-02)).

A satellite or aircraft trail does not trigger it, because the stacker's pixel rejection removes those. The measurements come from `shared/quality/raw_frame_check.py`.

**Safeguards.** A session of fewer than eight frames is left alone, because its median cannot be trusted. If the step would move more than a quarter of a session, it moves none and logs why, because the session itself is then the problem (partly measured; see [STK-03](../../../documentation/library_design/validation/stacking.md#stk-03)).

**Undoing it.** The step moves frames and never deletes them. Each `_excluded` folder holds `excluded_frames.json`, which records every frame's original path, why it was moved and what was measured. The scanner and the downloader both leave `_excluded` alone, so moved frames neither return to the target nor download again. Two calls work with them:

- `QualityDiagnostics.frame_quality(target, kind="quarantine_preview")` shows what the check would move without moving anything, and with `include=["excluded"]` lists what was moved.
- `ProcessingPipelines.restore_excluded_frames(target, apply=True)` moves the frames back. `scripts/restore_excluded_frames.py` is its command-line form.

**Setting.** `quarantine_bad_frames_enabled` in `[Processing.Siril]` turns the step off.

**What it records.** The step is recorded in the stack's gate record as `frame_quarantine`:

- `passed` when at least one session was judged and none was over the cap;
- `failed` when a session had too many bad frames to move (the bad frames are still in the stack);
- `not_checked` when no session had enough readable frames, or when the step is off or the stack is spectral.

### Step 2. Skip a stack that would come out the same

**What it does.** Stacking a target rebuilds every stack it has: one per filter and one for spectra. Before it stacks, `stack_inputs.py` writes down what the stack is made from:

- the frames (path, size and modification time);
- the calibration frames the library picks for them (darks, biases and flats, with their modification times);
- the settings that change the pixels;
- a version number of the stacking code.

It saves that record, with a hash of it (a short fingerprint that changes whenever the record changes), next to the stack as `<stack name>_inputs.json`. On the next run it builds the record again. If the hash matches and the stack file is still there, the stage keeps the stack and returns its path. If not, it logs what changed (for example `3 frame(s) added`, `the flat frames changed`, `settings changed: get_stack_weight`) and rebuilds. Adding luminance frames (imaging frames taken through a clear filter that passes all visible colours) to a target does not rebuild its spectral stack. Only the stacking step is skipped: astrometry, photometry and spectroscopy still run on the stack that is there.

**When it never skips.** The skip never triggers when the stack has no record (a stack made before the records existed is rebuilt once) or when the inputs cannot be read. The record travels with the stack into the `_previous` folder.

**Settings.** To rebuild anyway, pass `force=True` to `ProcessingPipelines.stack`, `--force-restack` to `scripts/run_all_target_processing.py`, or turn off `skip_unchanged_stacks_enabled`.

**For developers.** The version number is `STACKING_ALGORITHM_VERSION` in `stack_inputs.py`; raise it whenever a code change would give a different stack from the same inputs, or old stacks will be called up to date.

### Step 3. Group frames by exposure length

Frames taken with different exposure times cannot be combined directly, since a longer exposure collects more light per pixel. `stack_runner.py` sorts the frames into exposure groups. It stacks each group on its own (step 4), then lines up and combines the group stacks (step 5), so no frame's light is wasted (measured; see [STK-11](../../../documentation/library_design/validation/stacking.md#stk-11)).

### Step 4. Run the stacking engine on each group

**What it does.** `stack_runner.py` hands each batch of frames to a stacking engine, the program that calibrates, registers (lines up) and combines them with pixel rejection. Siril is the engine in use. The runner adds two safeguards. It stacks frames of different exposure lengths one length at a time (step 3). For spectral frames, whose dispersed images cannot use Siril's normal star-based registration, it retries a registration that loses too many frames with a different star detection (partly measured; see [STK-12](../../../documentation/library_design/validation/stacking.md#stk-12)).

**The engine contract.** The engine's contract is in `drivers/interfaces/stacking_driver.py`: a `StackSettings` goes in and a `StackRunResult` comes out, so nothing else in the pipeline reads Siril's commands or files. Two steps in the middle of an engine run are this pipeline's own science: the flat check (`flat_calibration.assess_flats`) and the phase-correlation alignment of spectral frames (`spectral_frame_alignment.align_calibrated_frames`), both described below. Phase correlation finds the shift between two images from their Fourier transforms. A driver may not import pipeline code, so the runner hands both to the engine on every run as a `PipelineSteps` (`stack_runner.PIPELINE_STEPS`). The quality summary records the engine's name and version (`stackingEngine`, `stackingEngineVersion`). To use another program, write a subclass of `StackingDriver` (see `drivers/siril_stacking_driver.py`); the runner and stage need no change.

**Weighting mixed exposure lengths.** When the frames of an imaging stack have different exposure lengths, `exposure_weighting.py` asks Siril to weight each frame by its noise (`-weight=noise`) unless a weight is configured. A short frame carries less information than a long one, so equal weights add noise. The exposure times alone predict how much: equal weights leave the stack's noise higher than the best weighting by the factor √(mean(1/t) × mean(t)), for exposure times t (partly measured; see [STK-07](../../../documentation/library_design/validation/stacking.md#stk-07)).

**Keeping the previous stack.** A restack first moves the stack it replaces, with its pictures, into a `_previous` folder (see `previous_stack.py`), and puts it back if the restack fails.

#### Checking the flat frames

**Why it matters.** A flat frame is a picture of an evenly lit surface. Dividing each light frame by the master flat (the combined flats) removes vignetting (darker corners) and dust shadows. The division also copies the master flat's noise into every light frame. That noise is the same pattern in each frame, so stacking does not average it away unless the frames were dithered (moved a few pixels between exposures).

**What it measures.** `flat_calibration.py` measures the flat set before Siril builds the master:

- how many frames it has, how bright they are (as a share of full scale), and how noisy the master flat will be (the noise of one frame divided by the square root of the frame count);
- which pixel value counts as full scale. The brightness limits are 10% and 90% of it, and 98% of it counts as saturated. For integer counts (ADU, analog-to-digital units: the raw numbers the camera records), the camera profile gives the value: its `clip_ceiling_adu`, the highest value a clipped pixel reaches (16383 for the 14-bit Nikon D5300, which stores its counts unscaled, and 65532 for the ASI533MM Pro). A flat that fills a D5300 sensor reads about 16383, so judging it against 65535 would call a good flat too faint. When the camera is unknown or has no profile of its own, the check guesses 65535 (16-bit counts), or 1.0 for an image whose values all lie within 0 to 1. Siril runs pass the lights' camera name, and the library check (`assess_flat_group`) passes each set's camera.
- whether the noise is above the limit, 0.5% of the flat's level. The limit is set so that the fixed pattern adds under 3% to the variance of a typical stack's own noise (partly measured; see [STK-05](../../../documentation/library_design/validation/stacking.md#stk-05)).

**What it does about noise.** When the noise is above the limit, the pipeline adds Siril's `gauss` command (a Gaussian blur) to the script that builds the master flat. The Gaussian is just wide enough to reach the limit (at most 8 pixels). Siril mirrors the image at its border. Vignetting and dust shadows are many pixels wide and survive the blur. The pixel-to-pixel sensitivity differences, about 1% on a CMOS sensor, do not. Taking more flats is the better cure, because the blur is a fallback. A colour (Bayer) sensor's flat is measured but not blurred, because the blur would mix the colour channels. (A Bayer sensor has a grid of red, green and blue filters over its pixels, so neighbouring pixels see different colours.)

**What it records.** The flat assessment records `full_scale` and `full_scale_source` (the camera profile and camera name, or the kind of guess) in the run's `flat_calibration` diagnostics, so a reader can see what the brightness was measured against. Each finding is recorded in the stack's quality summary:

- `flatFrameCount`;
- `flatNoiseFraction` (relative noise of the master flat before smoothing, 0.005 is 0.5%);
- `flatSmoothingSigmaPx` (the blur width, empty when none was applied);
- `flatCalibrationIssues` (one sentence per problem). Each issue also appears in the summary's flag reasons, which flags the stack.

#### Build the master calibration frames

Siril builds each master (bias, dark, flat) from the frames the library picked, and the same step applies them to the lights. The bias master comes off every flat, including a lone one, so the bias level does not stay in the master flat and under-correct the vignetting. A stack of one light is calibrated like any other light. Every Siril script sets `set32bits`, so the masters and the stack are saved as 32-bit floating point whatever bit depth the user's Siril preferences name. A 16-bit save would round the normalised stack to 65536 levels. For the full rules, see `drivers/README.md`.

#### The match flags

**What it checks.** The calibration library files darks by gain, offset, binning and temperature, and bias and flat frames by gain, offset and binning (see `drivers/README.md`). When no frame fits the lights exactly, the Siril driver records one sentence per mismatch:

- A gain, offset or temperature mismatch still applies the nearest frames, and the sentence goes into the `calibration_mismatch_flags` diagnostic.
- A binning mismatch applies nothing of that kind, and the sentence goes into `calibration_match_blocking_flags`.

**What it records.** `assess_input_quality.py` reads both lists, adds the sentences to the flag reasons and fails the `calibration_metadata` gate, so the stack is flagged and the reader sees which frames were applied to which lights, or why none were.

#### The frame-count flag

**Why it matters.** Averaging and pixel rejection need several frames. One frame keeps its own noise and any cosmic ray hit, and two frames cannot tell a good value from a bad one.

**What it checks.** `calibration_count_flags` (in `drivers/siril_interface.py`) compares each master's frame count with `minimum_calibration_frames` in `[Processing.Siril]` (a whole number, default 3). Each master below the minimum gets one sentence, for example `flat master built from 1 frame(s), fewer than the minimum of 3: ...`.

**What it records.** The sentences go into the run's `calibration_blocking_flags` diagnostic. `assess_input_quality.py` reads them, adds them to the input quality's flag reasons and fails the `calibration_frame_count` gate, so the stack is flagged. The pipeline still builds the stack, because the library can legitimately hold one flat or dark. A person reading the quality summary, and the UI that shows it, sees which master was short. The setting does not change any pixel, so changing it does not trigger a rebuild.

#### Aligning spectral frames

`spectral_frame_alignment.py` aligns a batch of already-calibrated spectral frames without star detection, since a dispersed spectrum has no normal point-source star pattern to align on. It uses phase correlation.

#### Optional pre-fetch

**What it does.** Raw frames can live on a network drive (an rclone mount with a local disk cache). A frame that is not cached yet is downloaded when it is first read, and Siril reads frames one at a time, so each frame waits for its own download. When pre-fetch is on, the driver first reads every staged frame (lights plus the darks, biases and flats in use) with several parallel readers (`drivers/frame_prefetch.py`), so the cache holds them before Siril starts.

**Settings.** `prefetch_frames` in `[Processing]` turns it on (`true`). It is off by default because it downloads files. `prefetch_workers` sets the number of parallel readers; the default of 4 workers matches the mount's `--transfers 4` and is not tuned.

**What it records.** A failed read is logged and counted, not raised, and the job log gets one `Pre-fetch:` line with the file, byte, failure and time counts.

#### Pixel rejection limits

**What it does.** While the engine combines the frames, it throws out any value of a pixel that sits too far from the middle of that pixel's values in the other frames. Two limits control this, in standard deviations (a measure of spread) below and above the middle: `rej <low> <high>` in the Siril script. Siril uses sigma clipping (dropping values more than a set number of standard deviations, "sigma", from the centre) in a Winsorized form: before it estimates the spread, it pulls extreme values in to a cut-off instead of dropping them, so one wild value cannot inflate the spread.

**How the limits are chosen.** In the default `adaptive` mode, `rejection_bounds` (in `utilities/rejection_thresholds.py`) sets them from the frame count. It starts from Chauvenet's criterion, a rule that sets the limit so that, for normally distributed values, each good value has a 1 in 2N chance of being thrown out, where N is the number of frames, so about half a good value is thrown out per pixel (1.64 for 5 frames, 1.86 for 8, 2.13 for 15, 2.50 for 40). Two changes apply to small stacks:

- **Floor.** The high limit is never below `stack_rejection_sigma_floor` (default 2.5). Chauvenet's criterion treats the spread as known, but with a handful of frames the stacker estimates the spread from those few values, and the estimate is noisy.
- **Looser low limit.** The low limit is the high limit plus `stack_rejection_low_extra_sigma` (default 0.5). Satellite trails, cosmic ray hits and hot pixels are brighter than the true value, so the high limit has to catch them. Values far below the middle are rare, so the looser low limit removes fewer good values at little cost.

A 5-frame stack therefore uses `rej 3.0000 2.5000`. A `rejection_sigma` passed by the caller, or `fixed` mode, bypasses both changes.

**What it records.** The run records the final limits, the mode, the floor, the extra and whether the floor applied (`rejection_sigma_low`, `rejection_sigma_high`, `rejection_sigma_mode`, `rejection_sigma_floor`, `rejection_sigma_low_extra`, `rejection_sigma_floor_applied`) in the quality summary's `resolvedParameters`. Both settings change the stack's pixels, so they are part of the skip record in step 2.

**Status.** The floor and the looser low limit are design choices, checked only in a simulation of Gaussian noise (partly measured; see [STK-08](../../../documentation/library_design/validation/stacking.md#stk-08)). On stacks of 40 or more frames the frame-count rule costs no sharpness (partly measured; see [STK-09](../../../documentation/library_design/validation/stacking.md#stk-09)).

### Step 5. Line up and combine the exposure-group stacks

When a stack has more than one exposure group, `stack_runner.py` lines up the group stacks and combines them, using the code in `processing/`:

- `group_derotation.py` first turns spectral exposure-group stacks onto one common trail tilt (the angle at which the spectrum streak crosses the frame).
- `group_alignment.py` aligns each group's stack with the reference group, the group that carries the most weight in the combination (for images by matching stars, which also removes the small rotation and scale between nights).
- `exposure_groups.py` puts every group on the reference group's brightness scale by dividing it by a group scale factor, leaves out a group whose brightness one scale factor cannot describe, and combines the rest. `processing/README.md` describes how the group scale factor is measured.

#### Trimming noisy edges

**Why it matters.** Frames shift between nights, so the edges of a stack of several nights are covered by fewer frames and are noisier.

**What it does.** After the stack is complete, `stack_crop.py` trims the stack and its rejection map (an image of how many values the stacker threw out at each pixel) back to where the pixel noise falls to within 15% of the interior's. The trim measures the noise in 25-pixel strips and finds the noisy run on each side.

**Why these numbers.** The 15% limit sits between the noisy and the clean edges of the stacks measured (partly measured; see [STK-23](../../../documentation/library_design/validation/stacking.md#stk-23)). For frames of equal weight, 15% more noise means an edge covered by about three quarters of the frames, because noise goes as one over the square root of the number of frames. A 25-pixel strip holds thousands of sky pixels for a steady noise estimate and still places the edge within a few dozen pixels.

It then cuts out the largest box with the original proportions (a square stack stays square) that fits in what is left, as near the image centre as the clean area allows. It changes nothing on a stack with clean edges, it refuses a trim of more than a quarter of a side, and it moves the sky-coordinate reference with the crop.

**Setting.** `trim_noisy_stack_edges_enabled` (default `true`) turns it off.

### Step 6. Judge the result

`stack_quality.py` holds the rules for deciding whether a stacked image is good enough to use. `exposure_saturation.py` checks whether a given exposure length saturates the brightest star in the field, and `exposure_group_report.py` records a description of the exposure groups a stack was built from.

### Step 7. Save a picture of the stack

**What it makes.** `stack_preview.py` saves two pictures next to the finished stack: a JPEG named `<stack name>_preview.jpg` and a FITS file named `<stack name>_processed.fits`. Both hold the same stretched picture, the same way up (Siril writes a JPEG upside down compared with its FITS source, so the JPEG script flips it first). The FITS file holds 32-bit numbers between 0 and 1, so the image viewer can zoom into it without the JPEG's 8-bit banding. It keeps the stack's observation keywords (exposure time, telescope, object) and a header note that it is a stretched picture and not linear data.

**Why it stretches.** A stack holds linear data (pixel values proportional to the light collected), which looks almost black on a screen, because most of the interesting detail sits just above the sky level. Stretching is a non-linear brightness curve that lifts that faint detail. The steps below run on a scratch copy first. In short: remove the sky gradient, balance the colours, remove excess green, stretch, remove noise, and dim the stars. Sub-step 6 (StarNet) replaces sub-steps 4 and 7 when it is chosen.

1. **Gradient removal.** GraXpert removes the sky's brightness gradient, if the `[Processing.GraXpert]` setting names a GraXpert command. Its AI model learns what the smooth background looks like and subtracts it.
2. **Colour calibration.** This step balances the colours of a colour stack on the linear data after GraXpert.
    - *Default method.* By default (`preview_colour_calibration_method = "pcc"`) it runs Siril's photometric colour calibration (PCC). Siril solves the field from the pointing, focal length and pixel size in the header (unless the stack already has a plate solution, the mapping from pixel position to sky coordinates), matches the stars to the Gaia DR3 catalogue (the star catalogue from the European Space Agency's Gaia satellite, third data release) over the network, removes each channel's background and scales the channels. It takes about ten seconds per stack (measured; see [STK-27](../../../documentation/library_design/validation/stacking.md#stk-27)).
    - *Fallback method.* If Siril, the solve or the network fails, the star-based method runs instead (`colour_calibration.py`). That method subtracts each channel's sky level (the median of its pixels), finds the stars on the summed channels, measures each in each channel with aperture photometry (summing the light in a circle around the star and subtracting the sky measured in a ring around it: here a 3.5 pixel circle minus the sky of a 7 to 11 pixel ring), and keeps the stars in the middle of the brightness range (30th to 85th percentile of total light). The circle is about 1.2 times the star width of the Nikon stacks, which catches most of a star's light and keeps the noise down, and the ring lies well outside the star's faint outer light. The brightest stars may be saturated and the faintest have noisy colours, which is why the middle of the range is kept; the 30th and 85th percentiles are design estimates. The median star is taken to be neutral, so the red and blue gains (the scale factors for the red and blue channels) are the factors that make the median red-to-green and blue-to-green flux ratios equal to 1. Green is the reference. It needs no catalogue or network, and `preview_colour_calibration_method = "stars"` uses it alone.
    - *Settings.* `preview_colour_calibration_enabled` in `[Processing.Siril]` (default `true`) turns the whole step off.
    - *Effect on the stretch.* A picture whose colours were calibrated is stretched with one shared stretch (`autostretch -linked`). Siril's default stretches each channel by itself, which sets every channel's sky and scale back to the same values and so undoes any colour balance. When too few stars are found (fewer than 40), or the setting is off, the stack is stretched channel by channel as before. See `post_processing/README.md` for how the gains compare with Siril's own calibration.
3. **Green removal.** Siril's Remove Green Noise (`rmgreen`) lowers excess green in a colour stack, such as one from the Nikon DSLR.
    - *Why.* A colour sensor has twice as many green pixels as red or blue and the sky's glow is often green, so these stacks look green.
    - *What it does.* It runs on the linear data in the same Siril script as the stretch, and only when the stack has three colour planes, so the single-channel ZWO stacks are never touched. Siril's default method limits each pixel's green to the average of its red and blue and keeps the pixel's lightness. It does not remove colour gradients across the frame, which are a separate problem.
    - *Setting.* `preview_remove_green_noise_enabled` in `[Processing.Siril]` (default `true`) turns it off.
    - *Status.* With the colour calibration on, it changes little (partly measured; see [STK-28](../../../documentation/library_design/validation/stacking.md#stk-28)).
4. **Stretch.** Siril's Autostretch sets the black point (the pixel value shown as pure black) from the image's own background noise and bends the brightness curve so the sky lands on a chosen grey level.
    - *Choosing the sky level.* `sky_level.py` chooses that level from the stack itself. It measures the brightest extended light (nebulae and galaxy halos, not stars) as a signal-to-noise ratio above the sky, then places that light at a fixed display brightness. A faint target needs a lot of lift, so its sky comes out lighter (up to 0.20) and its faint structure stays visible. A lighter sky also lifts the grain into view, which is why the limit is 0.20 and not Siril's own 0.25. A bright target needs little, so its sky comes out darker (down to 0.11) for more contrast. A stack whose sky cannot be measured gets 0.18.
    - *Tuning.* The rule has one tuned number, set by eye (partly measured; see [STK-29](../../../documentation/library_design/validation/stacking.md#stk-29)).
5. **Denoise.** Cosmic Clarity removes noise from the stretched picture, if the `[Processing.CosmicClarity]` setting names its denoise program.
    - *Setting.* `denoise_strength` (0 to 1, default 0.9) controls how much it removes.
    - *Why after the stretch.* Autostretch measures the noise level of the image it stretches: after a denoise, it measures less noise and turns the contrast back up, so the remaining noise looks as strong as before and large blotchy patches appear.
    - *Status.* The order and the 0.9 default are partly measured; see [STK-30](../../../documentation/library_design/validation/stacking.md#stk-30).
    - *Safety.* The program only reads and writes the `input` and `output` folders next to it, so the step takes an exclusive lock, refuses to run if `input` already holds files, and deletes only its own files.
6. **StarNet (optional).** StarNet (`starnet_stretch.py`) replaces the single stretch and the star toning.
    - *What it does.* Siril's `starnet` command splits the linear image into a starless image and a stars-only image. The starless image is stretched the usual way, the stars are stretched on their own with an arcsinh curve (`star_stretch`, default 1000: larger values lift fainter stars), and the two are blended with a screen blend (a blend that adds light without clipping, like projecting two slides onto one screen), with the stars scaled by `star_strength` (default 0.8).
    - *How to choose it.* It is off by default and is chosen per run, with `starnet=True` on `ProcessingPipelines.remake_preview`; there is no setting for it. It needs StarNet set up in Siril.
    - *Status.* It adds about half a minute to a preview, and the defaults 1000 and 0.8 were chosen by eye (partly measured; see [STK-31](../../../documentation/library_design/validation/stacking.md#stk-31)).
    - *Limits.* StarNet can fail or leave artifacts on a dense field, and a failed run falls back to the normal stretch.
7. **Star toning.** Star toning (`star_tone.py`) dims the stars in proportion to their brightness and compresses the brightest values below pure white.
    - *Why.* The stretch makes nearly every star a small white dot of about the same brightness.
    - *What it does.* The toning separates the light above the local background, raises it to a power (1.8) so that fainter stars dim more than bright ones, and then applies a soft roll-off above 0.65 so a saturated core ends near 0.92. Only light that stands out by more than 0.05 to 0.12 above the background is dimmed, so low-contrast structure (the knots along a galaxy's spiral arms) keeps its detail. The background and nebulae are not touched. A colour picture is toned on its brightness (the mean of its channels) and every channel of a pixel is scaled by the same factor. Toning each channel alone gives a colour stack a colour cast, because the channels differ in noise and so are dimmed by different amounts.
    - *Noise-aware start.* The start of the dimming also follows the picture's noise: light counts as a star only if it stands at least 6 noise widths (standard deviations of the sky noise) above the background (the 0.05 and 0.12 limits above apply unchanged when the noise is small). The background under the stars is the local minimum, which lies below the sky, so sky noise alone reaches several noise widths above it; 6 widths clears it and keeps the sky level where `sky_level.py` put it (partly measured; see [STK-32](../../../documentation/library_design/validation/stacking.md#stk-32)). Faint stars are then not dimmed, so a star field looks busier.
    - *Setting.* `preview_star_tone_enabled` in `[Processing.Siril]` (default `true`) turns it off.
    - *Tuning.* The power and the knee were chosen by eye (see [STK-32](../../../documentation/library_design/validation/stacking.md#stk-32)).

#### Bright objects such as the Moon

**Why it differs.** A stack of a bright extended object, such as the Moon, takes a different route. The Moon's sky is about 3000 times fainter than its disc, so the normal stretch would turn the whole disc white and lose the craters.

**How it is detected.** `bright_object.py` recognises this case from the data: it counts what share of the picture the normal stretch would push to white. A nebula, galaxy or star field leaves only the cores of the brightest stars white; the Moon leaves far more. A share of 1% or more switches to the bright-object route (partly measured; see [STK-33](../../../documentation/library_design/validation/stacking.md#stk-33)).

**What it does.** It runs one Siril script with a different stretch. The black point is the sky level, the white point is near the top of the object's own brightness, and the curve places the object's median at a fixed grey (0.55). GraXpert and the denoise are skipped, because GraXpert would treat the Moon's glow as sky background and the denoise could smooth the surface.

**Limits.** A planet is too small to reach the 1% share, so planet stacks use the normal route.

#### Skipped steps, failures and logging

Leaving a step's setting blank skips that step. Without Cosmic Clarity and the star toning, one Siril script stretches the image and saves the JPEG.

Both pictures are for people only. Nothing in the pipeline reads them, and the stack file is not changed, so photometry and spectroscopy still read the linear data.

If GraXpert or Cosmic Clarity fails, the picture is made from the result of the step before it. If Siril is missing or fails, the pipeline logs a warning and keeps the stack. A failed FITS copy does not fail the preview: the JPEG alone is still recorded.

Each step that works logs a line saying so, and one `Preview steps:` line lists every step and its result (for example `GraXpert done; Siril stretch done; Cosmic Clarity done; Star toning done`). A stack made from the viewer shows these lines in its job log, so you can see which steps ran.

The step removes any older pictures first, so they always match the current stack. The Image Processing viewer's Stretch view shows the JPEG when it is at least as new as the stack.

#### Recording the processed image

The step then records the FITS picture as the target's processed image (`processed_image`), or the JPEG when no FITS was made. The Image Viewer shows it first, with the same zoom and pan as the FITS viewer, and does not stretch it again. It replaces a processed image that a person attached to the target (the attached file stays where it is), and it skips a stack that the target does not show (a target with stacks from several telescope setups shows one). `scripts/backfill_stack_previews.py` makes previews for stacks that already existed and records them the same way.

#### Remaking the preview

`post_processing/preview_remake.py` runs only this step again on the stack that is already there, without restacking (`ProcessingPipelines.remake_preview`). A run can override the denoise and star-toning settings, or choose StarNet, without writing them to the configuration. By default it copies the old pictures into a `_previous_preview` folder first, so the two can be compared, and a failed run puts them back. It never opens the stack file for writing.

## The gate record

A gate is a pass/fail check on the stack. Every check below is recorded in the stack's `gates` as `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. The red-team tests in `test/test_stack_gates.py` and `test/test_frame_quarantine_gate.py` (tests written to attack each check) feed each gate input that must fail it.

Terms in the table:

- The **zero-order star** is the plain, undispersed image of the star that a slitless spectrum frame records beside its spectrum.
- A pixel is **saturated** when it has reached the highest value the camera can record, so its true brightness is lost.
- **Flux** is the total light measured from a star.
- The **reference group** is the exposure group that carries the most weight when the groups are combined (step 5). The other groups are lined up with it and put on its brightness scale.

| Gate | Fails when | Not checked when |
|---|---|---|
| `frame_quarantine` | A session had too many bad frames to move, so they stay in the stack | No session had enough readable frames; the step is off; the stack is spectral |
| `gain_homogeneity` | Never. Setting aside a minority gain corrects the stack | No frame records its gain |
| `background_homogeneity` | An exposure length shows a sudden change of sky background | The check is off, or no exposure length has three frames with a measured background |
| `flat_level`, `flat_noise` | The flats are too faint, too bright or too noisy, or cannot be read | The stack used no flats; the noise could not be estimated |
| `calibration_metadata` | A calibration frame was applied although its gain, offset or temperature does not match the lights, or a kind of calibration frame was left out because its binning does not match | No calibration frame was applied and none was left out |
| `calibration_frame_count` | A master bias, dark or flat was built from fewer frames than `minimum_calibration_frames` (default 3) | No calibration frame was applied |
| `exposure_group_linearity` | A group's brightness is not one scale factor of the reference group's: measured on the same isolated, unclipped stars in both groups, its faint-star or bright-star flux ratio differs from the median ratio by more than `exposure_group_gain_tolerance` (default 0.05). Spectral stacks, and imaging stacks with too few clean stars, compare mid-range and bright-end pixels instead. The pipeline leaves that group out of the combined image | The stack has one exposure length, or no group could be compared with the reference |
| `exposure_groups_combined` | An exposure group that had frames is missing from the combined stack, because it failed to stack, could not be lined up or was nonlinear. The message names each group, why, and how many frames it holds; the stack's `frames_stacked`, session breakdown and sharpness sample then count only the groups that were combined | The stack has fewer than two exposure groups |
| `rejected_pixel_fraction` | The stacker threw out 15% or more of the pixels | The stacker did not report a count |
| `stack_sharpness` | The stack is more than 1.2 times wider than its input frames predict | The stack is spectral or a single frame, or a width could not be measured |
| `spectral_registration` | A frame's zero-order star moved or faded | The stack is not spectral, or the check did not run |
| `saturated_pixel_fraction` | 0.1% or more of the stack's pixels are saturated | The saturation could not be measured |
| `zero_pixel_fraction` | Half or more of an image stack's pixels are exactly zero, or 5% or more of a spectral stack's (measured; see [STK-26](../../../documentation/library_design/validation/stacking.md#stk-26)) | The file could not be read |
| `raw_frame_clipping` | The raw frames of any exposure group have more than 20% of their pixels at exactly zero (a camera offset below the read noise cuts the faint sky off). A stack where only some groups are clipped fails too; its message says the longer groups replace the faint pixels. A stack where every group is clipped cannot replace them. One-length stacks are measured as well | The raw frames could not be read |
| `negative_pixels` | Siril warns that 50% or more of a frame went negative | Never: no warning is the measurement |

**Where the limits come from.**

- `frame_quarantine`: partly measured; see [STK-03](../../../documentation/library_design/validation/stacking.md#stk-03).
- `background_homogeneity`: partly measured; see [STK-04](../../../documentation/library_design/validation/stacking.md#stk-04).
- `flat_noise`: partly measured; see [STK-05](../../../documentation/library_design/validation/stacking.md#stk-05). The `flat_level` limits are design estimates.
- `exposure_group_linearity` (5%): partly measured; see [STK-20](../../../documentation/library_design/validation/stacking.md#stk-20).
- `rejected_pixel_fraction` (15%): partly measured; see [STK-24](../../../documentation/library_design/validation/stacking.md#stk-24).
- `stack_sharpness` (1.2 times): the predicted width is the root mean square of a sample of the input frames' star widths (see `post_processing/README.md`). Partly measured: the limit does not fire on normal stacks, and whether it catches a real failed alignment is untested; see [STK-25](../../../documentation/library_design/validation/stacking.md#stk-25).
- `saturated_pixel_fraction` (0.1%): a design estimate.
- `zero_pixel_fraction` and `negative_pixels`: measured; see [STK-26](../../../documentation/library_design/validation/stacking.md#stk-26).
- `raw_frame_clipping` (20%): partly measured; see [STK-22](../../../documentation/library_design/validation/stacking.md#stk-22).

**What raw-frame clipping means.** The camera's offset is meant to lift the readings so that noise below zero is still recorded. When the offset is smaller than the read noise, readings that would fall below zero are stored as 0. The faint sky then reads too bright on average and its noise reads too low. When only the shorter groups are clipped, the combination (step 5) leaves the clipped group out at those pixels and uses the longer groups, whose sky stays above zero.

**Known blind spots.** `gain_homogeneity` cannot see a missing gain, because a frame with no recorded gain is given the default `800`. `negative_pixels` trusts that Siril's log was read, so an unreadable log would pass.

## Where each piece lives

The folders follow the layout in `pipelines/README.md`.

- `stack_request.py` is the entry point behind `ProcessingPipelines.stack`. `choose_frames_to_stack` picks the light frames: it never mixes imaging and spectroscopy frames or two cameras, and it narrows the choice by filter, file-name range, time window and camera, or takes an exact list. `run_stack` runs `stage.py` on the chosen frames as a tracked job, holds a stacking slot while Siril runs, and saves the target when a stack was made. A request that cannot be met raises an error.
- `stage.py` is the coordinator: it runs the steps above in order for one target, and is what the rest of the codebase calls to stack a target's frames.
- `calibration_ingest.py` turns a rescan of the calibration library into a report. It compares the library's frame lists from before and after the rescan, lists the groups that gained or lost frames, and measures each new set of flats with the stacker's own flat check (`pre_processing/flat_calibration.py`). `CalibrationCatalog.refresh` and `CalibrationCatalog.assess_flats` call it.
- `stack_runner.py` runs the stacking engine (steps 3 to 5): exposure groups, registration retries and the final read of the engine's files. The engine is the only code that stacks a batch's pixels. `processing/` combines the group stacks.
- `pre_processing/` checks and prepares the inputs: the one-camera rule (`camera_selection.py`, which picks a named camera's frames and refuses a set that still mixes cameras, because frames from two cameras differ in pixel size, gain, noise and calibration frames), the gain and background homogeneity checks, the flat-frame check, and the input-quality judgement (`StackingInputQuality`). See its README.
- `processing/` lines up and combines frames: exposure groups, group alignment, spectral frame alignment and derotation. See its README.
- `post_processing/` judges and presents the finished stack: the quality thresholds, the saturation check, the exposure-group report, the output-quality judgement (`StackingOutputQuality`), the bright-object rule, the sky level rule, the preview picture and its remake, and the stack reports that the API returns (`stack_quality_report.py`: the stack's star width, share of rejected pixels and frame registration, an optional comparison with the previous or another stack, and the saved summary). See its README.

`stage.py` stores the two judgements on the stack's quality summary as `inputQuality` and `outputQuality`. Each carries its own flag reasons, so a reader can tell a bad input from a bad result.

## Where the output goes

**Output folder.** The pipeline writes its output (stacks, group stacks, rejection maps, registration files, previews and processed pictures) to `<stacks_path>/lights/<target>/`. The `stacks_path` entry in the `[Image Library]` section of the configuration sets that folder. Without it, the output goes beside the raw frames, in the frames path. The setting lets the raw frames stay on one disk while the output goes to another (for example, both on a mounted cloud drive, with the databases kept on the local disk; see `documentation/Installation.md`, "Storage layout"). The backend's image server looks for a file in the frames folder first and then in the stacks folder, so the viewer asks for one URL either way. `scripts/move_stacks_to_stacks_path.py` moves the files that already exist and updates the paths in the database.

**Raw frames on a network drive.** The raw frames can sit on a network drive. The pipeline does not copy them first. Siril reads each frame once through a symlink and writes its working files to `~/.cache/astrometrics/siril` on the local disk. Set `frames_mount_point` in the same section to the folder where that drive mounts. While nothing is mounted there, downloads and file sorting stop with an error instead of writing frames to the local disk. The pipeline never rewrites a raw frame.

## A note on scope

This pipeline produces one combined image (or one per exposure group/camera setup) from a target's raw frames. It does not detect stars, solve the sky position, or measure brightness — those are astrometry and photometry, which read this pipeline's output rather than duplicate its work.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
