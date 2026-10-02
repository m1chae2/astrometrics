# Post-processing

This step judges the finished stack and makes a picture of it for people. It never changes the stack file.

## What it does

1. **Output judgement** (`assess_output_quality.py`). Turns the measurements made on the stack into a `StackingOutputQuality` (`models/stacking_quality.py`).
2. **Thresholds** (`stack_quality.py`). The limits for the rejected-pixel share, the zero-pixel share and the negative-pixel warning.
3. **Saturation check** (`exposure_saturation.py`). Finds whether an exposure length saturates the brightest star and recommends one that does not.
4. **Exposure-group report** (`exposure_group_report.py`). Describes each exposure group in the summary.
5. **Preview** (`stack_preview.py`, `sky_level.py`, `bright_object.py`). Makes the stretched JPEG. See `../README.md`, step 7.

## The quality metrics

- **Rejected pixel fraction.** The share of pixel values the stacker threw out, 0 to 1. A high value means many artifacts or a poor alignment.
- **Saturated pixel fraction.** The share of pixels at the camera's saturation level.
- **Zero pixel fraction.** The share of exactly-zero pixels. A mostly-zero stack is blank, because calibration removed more than the sky.
- **Negative pixel maximum percent.** The worst "many negative pixels" warning Siril printed after dark subtraction.
- **Stacked and median input FWHM.** Star width in the stack and in the input frames, images only. A wider stack means alignment blurred it.
- **Spectral registration concern count.** Spectral frames the registration check questioned.
- **Flag reasons.** One sentence per failed threshold. They feed the summary's flag reasons.

For exact thresholds, read the code.
