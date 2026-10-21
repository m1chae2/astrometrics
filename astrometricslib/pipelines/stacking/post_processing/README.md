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
3. Saturation check (`exposure_saturation.py`). It finds whether an exposure length saturates the brightest star and recommends one that does not.
4. Exposure-group report (`exposure_group_report.py`). It describes each exposure group in the summary.
5. Preview (`stack_preview.py`, `colour_calibration.py`, `sky_level.py`, `starnet_stretch.py`, `star_tone.py`, `bright_object.py`). It makes the stretched JPEG and FITS images. The steps are color calibration of a color stack, the stretch (or the optional StarNet star-separated stretch), star toning, and the separate route for a bright object such as the Moon. `preview_remake.py` runs this step again on an existing stack without restacking. See [step 7 of the stacking README](../README.md#the-preview-image-step-7).
6. Previous stack (`previous_stack.py`). Before a restack overwrites a stack, it moves the old stack and its companion files (rejection map, registration table, preview, stretched FITS) into a staging folder. If the restack works, those files go into `_previous` and replace that stack's older version, and the other stacks of the target keep theirs. If it fails, everything moves back. The stage keeps only one previous version. `discard_previous_stack` deletes it and `swap_with_previous_stack` exchanges it with the current stack. `interrupted_jobs.py` puts back the old stack when the program running a restack was killed before it finished. The package root registers `restore_interrupted_stacks`, and `close_interrupted_jobs` calls it for each stacking job it closes.
7. Stack comparison (`stack_comparison.py`). It measures two stacks the same way (sky level, pixel noise, large-scale flatness, star width) and reports the change in each as a number and a sentence. It does not pick a winner. New flats should lower the flatness number and leave the noise alone, while more frames should lower the noise and leave the flatness alone.
8. Stack reports (`stack_quality_report.py`). It builds what `QualityDiagnostics.stack_quality` and `ProcessingPipelines.stack_summary` return. It finds a target's stack file and measures the stack's star width, share of rejected pixels (from the rejection map beside the stack) and frame registration (from the `_Registration.seq` file beside it). It optionally compares the stack with the previous stack or another stack file, and reads back the summary the stage saved. It writes no file.

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

For exact behavior, read the code.
