# Post-processing

This step judges the finished stack and makes a picture of it for people. It never changes the stack file.

## What it does

1. **Output judgement** (`assess_output_quality.py`). Turns the measurements made on the stack into a `StackingOutputQuality` (`models/stacking_quality.py`).
2. **Thresholds** (`stack_quality.py`). The limits for the rejected-pixel share, the zero-pixel share and the negative-pixel warning.
3. **Saturation check** (`exposure_saturation.py`). Finds whether an exposure length saturates the brightest star and recommends one that does not.
4. **Exposure-group report** (`exposure_group_report.py`). Describes each exposure group in the summary.
5. **Preview** (`stack_preview.py`, `sky_level.py`, `star_tone.py`, `bright_object.py`). Makes the stretched JPEG. See `../README.md`, step 7.
6. **Previous stack** (`previous_stack.py`). Before a restack overwrites a stack, moves the old stack and its companion files (rejection map, registration table, preview, stretched FITS) into a staging folder. If the restack works, those files go into `_previous` and replace that stack's older version; the other stacks of the target keep theirs. If it fails, everything moves back. Only one previous version is kept. `discard_previous_stack` deletes it and `swap_with_previous_stack` exchanges it with the current stack. The setting `keep_previous_stack_enabled` (default `true`) turns this off.
7. **Stack comparison** (`stack_comparison.py`). Measures two stacks the same way (sky level, pixel noise, large-scale flatness, star width) and reports the change in each as a number and a sentence. It does not pick a winner: new flats should lower the flatness number and leave the noise alone, while more frames should lower the noise and leave the flatness alone.

## The quality metrics

- **Rejected pixel fraction.** The share of pixel values the stacker threw out, 0 to 1. A high value means many artifacts or a poor alignment.
- **Saturated pixel fraction.** The share of pixels at the camera's saturation level.
- **Zero pixel fraction.** The share of exactly-zero pixels. For a colour stack it is the share in the worst channel. A mostly-zero stack is blank, because calibration removed more than the sky.
- **Negative pixel maximum percent.** The worst "many negative pixels" warning Siril printed after dark subtraction.
- **Stacked, median input and expected FWHM.** Star width (full width at half maximum, in pixels, from a Gaussian fit to each star) in the stack and in a sample of the input frames spread over the whole set, images only. The expected width is the root mean square of the sampled frame widths, which is the width a stack of those frames should have. A stack more than 1.2 times wider than expected means alignment blurred it. A stack combined from exposure groups is measured without the stars whose cores were replaced by a shorter group's data. Those patched cores are the brightest stars in the combined image, because it has no saturated plateau left for the check to skip, and measured alone they read too wide. On the M 57 stack of 2026-10-03 they read 3.40 px against 2.38 px for the other stars.
- **Spectral registration concern count.** Spectral frames the registration check questioned.
- **Flag reasons.** One sentence per failed threshold. They feed the summary's flag reasons.

For exact thresholds, read the code.
