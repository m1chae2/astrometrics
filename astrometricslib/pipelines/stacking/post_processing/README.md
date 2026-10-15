# Post-processing

This step judges the finished stack and makes a picture of it for people. It never changes the stack file.

Terms are defined in the [glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary). The terms used most here:

- **Stack**: the single image made by aligning and averaging many frames (sub-exposures) of one target. It is saved as a FITS file, the standard astronomy image format.
- **Linear and stretched data**: a stack's pixel values are proportional to the light collected (linear), so it looks almost black on a screen. A **stretch** is a non-linear brightness curve that lifts the faint detail for display.
- **Siril** is the external image-processing program that stacks and stretches the images. **GraXpert**, **Cosmic Clarity** and **StarNet** are external tools for removing sky gradients, removing noise and separating stars from the background.
- **Rejection map**: an image, saved beside the stack, of how many values the stacker threw out at each pixel.

## What it does

1. **Output judgement** (`assess_output_quality.py`). Turns the measurements made on the stack into a `StackingOutputQuality` (`models/stacking_quality.py`).
2. **Thresholds** (`stack_quality.py`). The limits for the rejected-pixel share, the zero-pixel share and the negative-pixel warning.
3. **Saturation check** (`exposure_saturation.py`). Finds whether an exposure length saturates the brightest star and recommends one that does not.
4. **Exposure-group report** (`exposure_group_report.py`). Describes each exposure group in the summary.
5. **Preview** (`stack_preview.py`, `colour_calibration.py`, `sky_level.py`, `starnet_stretch.py`, `star_tone.py`, `bright_object.py`). Makes the stretched JPEG and FITS pictures: colour calibration of a colour stack, the stretch (or the optional StarNet star-separated stretch), star toning, and the separate route for a bright object such as the Moon. `preview_remake.py` runs this step again on an existing stack without restacking. See `../README.md`, step 7.
6. **Previous stack** (`previous_stack.py`). Before a restack overwrites a stack, moves the old stack and its companion files (rejection map, registration table, preview, stretched FITS) into a staging folder. If the restack works, those files go into `_previous` and replace that stack's older version; the other stacks of the target keep theirs. If it fails, everything moves back. Only one previous version is kept. `discard_previous_stack` deletes it and `swap_with_previous_stack` exchanges it with the current stack. The setting `keep_previous_stack_enabled` (default `true`) turns this off. `interrupted_jobs.py` puts back the old stack when the program running a restack was killed before it finished: the package root registers `restore_interrupted_stacks`, and `close_interrupted_jobs` calls it for each stacking job it closes.
7. **Stack comparison** (`stack_comparison.py`). Measures two stacks the same way (sky level, pixel noise, large-scale flatness, star width) and reports the change in each as a number and a sentence. It does not pick a winner: new flats should lower the flatness number and leave the noise alone, while more frames should lower the noise and leave the flatness alone.
8. **Stack reports** (`stack_quality_report.py`). Builds what `QualityDiagnostics.stack_quality` and `ProcessingPipelines.stack_summary` return: it finds a target's stack file, measures the stack's star width, share of rejected pixels (from the rejection map beside the stack) and frame registration (from the `_Registration.seq` file beside it), optionally compares it with the previous stack or another stack file, and reads back the summary the stage saved. It writes no file.

## The quality metrics

- **Rejected pixel fraction.** The share of pixel values the stacker threw out, 0 to 1. A high value means many artifacts or a poor alignment.
- **Saturated pixel fraction.** The share of pixels at the camera's saturation level (the highest value it can record, where the true brightness is lost).
- **Zero pixel fraction.** The share of exactly-zero pixels. For a colour stack it is the share in the worst channel. A mostly-zero stack is blank, because calibration removed more than the sky.
- **Negative pixel maximum percent.** The worst "many negative pixels" warning Siril printed after dark subtraction (removing the heat signal measured in dark frames, which are taken with the shutter closed).
- **Stacked, median input and expected FWHM.** Star width (full width at half maximum, in pixels, from a Gaussian fit to each star) in the stack and in a sample of the input frames spread over the whole set, images only. The expected width is the root mean square of the sampled frame widths, which is the width a stack of those frames should have: a stacked star is the average of the frames' stars, and for round Gaussian (bell-shaped) stars the average's variance is the mean of the individual variances, so its width is the root mean square of theirs. A stack more than 1.2 times wider than expected means alignment blurred it (partly measured; see [STK-25](../../../../documentation/library_design/validation/stacking.md#stk-25)).
  - *Combined stacks.* A stack combined from exposure groups is measured without the stars whose cores were replaced by a shorter group's data. The width measurement normally skips saturated stars by their flat, clipped tops (the saturated plateau). In a combined image the brightest stars' cores come from a shorter, unsaturated group, so they have no plateau for the measurement to find, and measured with the rest they read too wide (measured; see [STK-25](../../../../documentation/library_design/validation/stacking.md#stk-25)).
- **Spectral registration concern count.** Spectral frames the registration check questioned.
- **Flag reasons.** One sentence per failed threshold. They feed the summary's flag reasons.

For exact thresholds, read the code.

## Colour calibration compared with Siril's

The preview uses Siril's PCC (photometric colour calibration, which balances the colour channels by matching the image's stars to a star catalogue) by default and falls back to the star-based method of `colour_calibration.py` when PCC cannot run. Siril has a second calibration, SPCC (spectrophotometric colour calibration), which also uses the camera's measured sensitivity curves.

- **PCC** matches the stars to the Gaia DR3 catalogue (the star catalogue from the European Space Agency's Gaia satellite, third data release). It needs a plate solution (the mapping from pixel position to sky coordinates) and a network connection.
- **SPCC** is more accurate in principle, because it uses the camera's own sensitivity curves, but it takes longer per stack. The difference between PCC and SPCC pictures comes mostly from the white reference each assumes (the kind of light the calibration treats as white), and SPCC can be set to a warmer white reference.
- **The star-based method** works offline in a few seconds. It assumes the median star is neutral, which is the largest source of error: its printed uncertainty is the scatter between stars only.

PCC is the default because the user chose it after seeing the pictures, not because it is the most accurate method. Siril warns that PCC and SPCC give an imprecise solution when the image still holds a gradient, so both run after GraXpert. The three methods agree on the direction and rough size of the correction and differ from each other by 10 to 20 percent; the star-based method adds too much blue on Milky Way fields (measured; see [STK-27](../../../../documentation/library_design/validation/stacking.md#stk-27)).
