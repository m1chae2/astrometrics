# Stacking post-processing

This stage judges the finished stack and makes a preview image of it for people. It never changes the stack file.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

These terms appear throughout:

- A *stack* is the single image made by aligning and averaging many frames of one target. It is saved as a FITS file, the standard astronomy image format.
- *Linear* and *stretched* data differ in how they show brightness. A stack's pixel values are proportional to the light collected (linear), so it looks almost black on a screen. A *stretch* is a non-linear brightness curve that lifts the faint detail for display.
- *Siril* is the external image-processing program that stacks and stretches the images. *GraXpert*, *Cosmic Clarity* and *StarNet* are external tools for removing sky gradients, removing noise and separating stars from the background.
- The *rejection map* is an image, saved beside the stack, of how many values the stacker threw out at each pixel.

The steps are:

1. Output judgment (`assess_output_quality.py`). It turns the measurements made on the stack into a `StackingOutputQuality` (`models/quality/stacking_quality.py`).
2. Thresholds (`stack_quality.py`). They hold the limits for the rejected-pixel share, the zero-pixel share and the negative-pixel warning.
3. Saturation check (`exposure_saturation.py`). It finds whether an exposure length saturates the brightest star and recommends one that does not. It also holds the largest estimated peak, in multiples of the room, that was checked against two exposure lengths for each camera (`checked_estimate_range`; see STK-41 in the validation register).
4. Exposure-group report (`exposure_group_report.py`). It describes each exposure group in the summary.
5. Preview (`stack_preview.py`, `colour_calibration.py`, `sky_level.py`, `starnet_stretch.py`, `star_tone.py`, `bright_object.py`). It makes the stretched JPEG and FITS images. The steps are color calibration of a color stack, the stretch (or the optional StarNet star-separated stretch), star toning, and the separate route for a bright object such as the Moon. `preview_remake.py` runs this step again on an existing stack without restacking. See [step 7 of the stacking README](../README.md#the-preview-image-step-7).
6. Previous stack (`previous_stack.py`). Before a restack overwrites a stack, it moves the old stack and its companion files (rejection map, registration table, preview, stretched FITS) into a staging folder. If the restack works, those files go into `_previous` and replace that stack's older version, and the other stacks of the target keep theirs. If it fails, everything moves back. The stage keeps only one previous version. `discard_previous_stack` deletes it and `swap_with_previous_stack` exchanges it with the current stack. `interrupted_jobs.py` puts back the old stack when the program running a restack was killed before it finished. The package root registers `restore_interrupted_stacks`, and `close_interrupted_jobs` calls it for each stacking job it closes.
7. Stack comparison (`stack_comparison.py`). It measures two stacks the same way (sky level, pixel noise, large-scale flatness, star width) and reports the change in each as a number and a sentence. It does not pick a winner. New flats should lower the flatness number and leave the noise alone, while more frames should lower the noise and leave the flatness alone.
8. Stack reports (`stack_quality_report.py`). It builds what `QualityRecords.measure_stack` and `ProcessingPipelines.stack_summary` return. It finds a target's stack file and measures the stack's star width, share of rejected pixels (from the rejection map beside the stack) and frame registration (from the `_Registration.seq` file beside it). It optionally compares the stack with the previous stack or another stack file, and reads back the summary the stage saved. It writes no file.
9. Quality measurements (`record_quality.py`, `record_science.py`, `stack_noise.py`, `empty_apertures.py`, `difference_noise.py`, `stack_image.py`, `stack_membership.py`, `expected_noise.py`, `flat_noise_contribution.py`). They store the summary's numbers and gates as measurements, measure the stack's noise in empty patches of sky on a flux scale fixed by reference stars, work out the noise the frames predict and the noise the master flat adds, and give each frame that went into the stack its weight. See [quality measurements in the stacking README](../README.md#quality-measurements).

### Color calibration compared with Siril's

The preview uses Siril's PCC (photometric color calibration, which balances the color channels by matching the image's stars to a star catalog) by default. It falls back to the star-based method of `colour_calibration.py` when PCC cannot run. Siril has a second calibration, SPCC (spectrophotometric color calibration), which also uses the camera's measured sensitivity curves.

- **PCC.** It matches the stars to the Gaia DR3 catalog (the star catalog from the European Space Agency's Gaia satellite, third data release). It needs a plate solution (the mapping from pixel position to sky coordinates) and a network connection.
- **SPCC.** It uses the camera's own sensitivity curves, and it takes longer per stack. The difference between PCC and SPCC images comes mostly from the white reference each assumes (the kind of light the calibration treats as white). SPCC can be set to a warmer white reference.
- **The star-based method.** It works offline in a few seconds. It assumes the median star is neutral, which is the largest source of error. Its printed uncertainty is the scatter between stars only.

Siril warns that PCC and SPCC give an imprecise solution when the image still holds a gradient, so both run after GraXpert. The three methods agree on the direction and rough size of the correction and differ from each other by 10 to 20%. The star-based method adds too much blue on Milky Way fields (measured; see [STK-27](../../../../documentation/library_design/validation/stacking.md#stk-27)).

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| `keep_previous_stack_enabled` | on | Turns the previous-stack backup off |
| Limits in `stack_quality.py` | Listed in the [stacking README](../README.md#quality-outputs) | Set the thresholds for the gates |
| Preview settings in `[Processing.Siril]`, `[Processing.GraXpert]` and `[Processing.CosmicClarity]` | See the [stacking README](../README.md#settings) | Turn each preview sub-step on or off and tune it |

## Quality metrics

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `rejectedPixelFraction` | Fraction, 0 to 1 | 15% (partly measured; see [STK-24](../../../../documentation/library_design/validation/stacking.md#stk-24)) | The share of pixel values the stacker threw out. High means many artifacts or a poor alignment | The `rejected_pixel_fraction` gate |
| `saturatedPixelFraction` | Fraction | 0.1% (design estimate; see [stacking design estimates](../../../../documentation/library_design/validation/stacking.md#design-estimates)) | The share of pixels at the camera's saturation level, the highest value it can record, where the true brightness is lost. High means lost star cores | The `saturated_pixel_fraction` gate |
| `zeroPixelFraction` | Fraction | Half of an image stack, 5% of a spectral stack (measured; see [STK-26](../../../../documentation/library_design/validation/stacking.md#stk-26)) | The share of exactly-zero pixels. For a color stack it is the share in the worst channel. High means a blank stack, because calibration removed more than the sky | The `zero_pixel_fraction` gate |
| `negativePixelMaxPercent` | % | 50% (measured; see [STK-26](../../../../documentation/library_design/validation/stacking.md#stk-26)) | The worst "many negative pixels" warning Siril printed after dark subtraction, which removes the heat signal measured in dark frames. High means the dark subtraction over-corrected | The `negative_pixels` gate |
| `stackedFwhmPx`, `medianInputFwhmPx`, `expectedStackFwhmPx` | px | Stacked width over expected width above 1.2 (partly measured; see [STK-25](../../../../documentation/library_design/validation/stacking.md#stk-25)) | Star width (FWHM, full width at half maximum, from a Gaussian fit to each star) in the stack and in a sample of the input frames spread over the whole set. Images only. The expected width is the root mean square of the sampled frame widths. A stacked star is the average of the frames' stars, and for round Gaussian (bell-shaped) stars the average's variance is the mean of the individual variances, so its width is the root mean square of theirs. A stack more than 1.2 times wider than expected means alignment blurred it | The `stack_sharpness` gate |
| `spectralRegistrationConcernCount` | Frames | None | Spectral frames the registration check questioned. High means the zero-order star moved or faded | The `spectral_registration` gate |
| `flagReasons` | Sentences | None | One sentence per failed threshold | The summary's flag reasons |

**Combined stacks.** The width measurement skips the stars whose cores a shorter group's data replaced in a stack combined from exposure groups. The measurement normally skips saturated stars by their flat, clipped tops (the saturated plateau). In a combined image the brightest stars' cores come from a shorter, unsaturated group, so they have no plateau for the measurement to find. Measured with the rest, they read too wide (measured; see [STK-25](../../../../documentation/library_design/validation/stacking.md#stk-25)).

## Files

- `assess_output_quality.py`: the `StackingOutputQuality` judgment.
- `stack_quality.py`: the gate thresholds.
- `exposure_saturation.py`, `exposure_group_report.py`: the saturation check and the exposure-group report.
- `colour_calibration.py`, `sky_level.py`, `starnet_stretch.py`, `star_tone.py`, `bright_object.py`, `stack_preview.py`, `preview_remake.py`: the preview.
- `previous_stack.py`, `interrupted_jobs.py`: the previous-stack backup and its recovery.
- `stack_comparison.py`, `stack_quality_report.py`: the comparison and the stack reports.
- `stack_crop.py`: the noisy-edge trim.
- `record_quality.py`: builds the stack's quality measurements from the finished summary and stores them. It judges nothing.
- `stack_noise.py`: the noise in empty sky, measured in the stored sky apertures and reference stars. With fewer than 20 clean apertures the noise is not measured, and the row says so.
- `empty_apertures.py`: choosing, storing and reloading the apertures and reference stars. The apertures stay clear of bright stars and of faint sources, which a significance mask of the stack flags (3.5 robust sigma, at three smoothings; partly measured, see [STK-36](../../../../documentation/library_design/validation/stacking.md#stk-36)). Aperture files are version 3, and older files are replaced.
- `difference_noise.py`: the noise of the difference of two stacks, and the per-aperture sums that let two stacks be compared aperture by aperture.
- `stack_image.py`: reading a stack's pixels and sky level, and the sum of the light in one aperture that all of the above share.
- `stack_membership.py`: the frames a stack holds, their weights, and the effective frame count and time.
- `expected_noise.py`: the noise the stack's frames predict in an empty aperture, from each frame's sky, read noise, gain and weight, as a sky part and a read part; and the stack's sky level.
- `flat_noise_contribution.py`: the noise the master flat adds to the same apertures, from the flat's noise, its blur width and the stack's sky level.
- `flat_match.py`: `flat_match_rows` asks whether the flats' dust shadows still match the lights (dust moved, added or cleaned), from up to 16 raw flats and 24 raw lights rebuilt as bias-subtracted maps (at least 8 lights must have a sky of 10 times the read noise). It stores 10 rows: the shadow count (a diagnostic), the correlation at zero offset and at the best placement, a control outside the shadows, the chance spread of the correlation, the search's excess over a null of 8 displaced placements, the split-half correlation of the lights, and the shadow pattern's shift and rotation (with uncertainties) when that excess clears the null's limit of 3.1 null spreads. The shadows sit on the camera window, which turns with the sensor, so the rotation is the pattern's, not the camera's. A time limit (`flat_match_time_limit_seconds`, default 300 s) is checked before each frame is read; when it passes, all 10 rows are not measured and the reason says how many frames were read. The bias level comes from the run's bias frames (`bias_frame_paths`), because the camera profile holds no bias level. The profile holds read noise only for gain 0 and offset 10, so most production groups are not measured today (a missing sky requirement or missing bias frames gives 10 not-measured rows with the reason). Partly measured; see [STK-46](../../../../documentation/library_design/validation/stacking.md#stk-46). For exact behavior, read the code.
- `field_rotation.py`: `field_rotation_measurements` stores `stacking.frame_registration_rotation_deg` for each frame, the rotation Siril fitted when it lined the frame up with the reference. A positive angle turns the frame's x axis toward +y, which is counterclockwise when the array is drawn with row 0 at the top. The row has no uncertainty. A spectral stack, a sequence already registered and a frame Siril dropped give not-measured rows. It takes the angle from the polar decomposition of the transform's 2 by 2 part and names the reference frame and the image parity in each row's reason. It does not convert the angle to a polar-alignment error (partly measured; see [STK-43](../../../../documentation/library_design/validation/stacking.md#stk-43)).
- `registration_shifts.py`: `pointing_measurements` stores the registration step and the pointing drift of the run (eight `stacking.registration_step_*` and `stacking.registration_drift_*` rows, with a jump count that shows a dither every N frames) from Siril's transforms; `record_quality.frame_geometry_measurements` calls it with the rotation. The pointing is the middle of the image. Steps across two nights and steps over 50 pixels are left out and counted; the step rows are not measured when over 10% of a night's pairs exceed 50 pixels or over half of the frames are identity. The drift rate is in pixels per hour (with its error); its direction and extent are named only for a significant drift on which the nights agree. `drift_of_runs` finds the axis for the half-stack pattern. A sequence already registered, or a stack of several exposure groups, gives not-measured rows (partly measured; see [STK-44](../../../../documentation/library_design/validation/stacking.md#stk-44)).
- `half_pattern.py`: `measure_half_pattern` finds the variance two half stacks share at a few pixels, the part of any fixed pattern that stays coherent in both halves (faint sky is shared too), and the correlation along the drift minus across it, which separates a smeared sensor pattern from sky. `half_pattern_measurements` builds the eight rows, which include the raw covariance, the high-pass difference variance and the reference-frame correction. `subset_noise_run._half_pattern_safely` calls it inside the half-difference job (partly measured; see [STK-45](../../../../documentation/library_design/validation/stacking.md#stk-45)).
- Target clipping moved to the astrometry pipeline (`../../astrometry/post_processing/target_clipping.py`), because it needs a plate solution of the stack. This stage only writes the registration record it reads (`../../shared/registration_record.py`, see the [stacking README](../README.md)).
- `record_science.py`: turns the expected noise, the flat's noise, the stack's sky level and the brightest star's peak of each exposure group (the group's entry in the summary, built by `exposure_group_report.summarize_group_peak`) into measurements. It stores parts, never the ratio of one to another. A failure here never stops a stack or drops the other rows.
- `record_quality.py` also serves the subset jobs: `noise_measurements` and `membership_count_measurements` build the noise and the effective count and time for any stack, and `measure_noise_safely` takes the apertures file to use. `stack_membership.registration_reference_from_diagnostics` names the frame Siril lined the others up with.

For exact behavior, read the code.
