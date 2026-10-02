# Pre-processing

This step checks the frames and calibration data before any pixels are combined. It does not stack anything. It decides which frames go in and whether the calibration data can be trusted.

## What it does

1. **Gain check** (`frame_homogeneity.py`). Frames taken at different camera gain settings have different read noise, so mixing them hurts the stack. The check keeps the most common gain and sets the others aside.
2. **Background check** (`background_homogeneity.py`). Each frame's sky background is compared with the others of the same exposure length. A frame taken through cloud or twilight is set aside. The sky is measured by `shared/quality/background_measurement.py`.
3. **Flat check** (`flat_calibration.py`). Measures how many flats there are, how bright, and how noisy the master flat would be. A noisy master gets a blur width, which Siril applies. A colour sensor's flat is measured but not blurred.
4. **Input judgement** (`assess_input_quality.py`). Collects the results into a `StackingInputQuality` (`models/stacking_quality.py`).

## The quality metrics

- **Frames submitted and accepted.** How many frames the stage got and how many passed the two checks.
- **Frames excluded for gain or background.** Counts of the frames set aside by each check.
- **Background split detected and detail.** Set when the frames fell into two sky-brightness groups. The detail gives each group's size and level.
- **Flat frame count, noise fraction and smoothing width.** The flat set's size, the master flat's relative noise (0.005 is 0.5%), and the Gaussian width in pixels used to blur it, empty when none was applied.
- **Flat calibration issues and calibration mismatch flags.** One sentence per problem.
- **Flag reasons.** One sentence per problem found in any check above. They feed the summary's flag reasons.

For exact behavior and thresholds, read the code.
