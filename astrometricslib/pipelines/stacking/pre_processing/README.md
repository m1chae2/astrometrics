# Stacking pre-processing

This stage checks the frames and calibration data before any pixels are combined. It does not stack anything. It decides which frames go in and whether the calibration data can be trusted.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

These terms appear throughout:

- A *light frame* is one camera exposure of the target. *Stacking* averages many aligned light frames into one image with less noise.
- *Calibration frames* are frames that measure the camera rather than the sky. A *bias* frame is a zero-length exposure (the camera's fixed electronic offset). A *dark* is taken with the shutter closed (the signal heat adds over the exposure). A *flat* is an exposure of an evenly lit surface (how sensitive each pixel is). A *master* is the average of many such frames.
- The *gain setting* is the camera's amplifier setting (a unitless number in the FITS `GAIN` card). *Offset* is a constant the camera adds to every pixel so that noise below zero is still recorded. *Binning* is the combining of neighboring pixels (for example 2 by 2) into one.
- *Siril* is the external program that builds the masters and stacks the frames.
- A *session* is the set of light frames of one camera, filter and exposure length taken less than 4 h apart. The frame-quality check judges each session on its own.

Before the numbered checks, the stage keeps the stack to one camera (`camera_selection.py`). Frames from two cameras differ in pixel size, gain, noise and calibration frames, so a stack that mixes them has no single meaning. `choose_camera_frames` picks a named camera's frames for `ProcessingPipelines.stack`. `ensure_single_camera` stops the stage with an error when the frames still come from more than one camera. Frames with no camera recorded do not count.

1. Gain check (`frame_homogeneity.py`). Frames taken at different camera gain settings have different read noise, so mixing them hurts the stack. The check keeps the most common gain and sets the others aside.
2. Frame-quality check (`frame_quarantine.py`). It measures each light frame's stars and moves a frame with clouds or trailed stars into an `_excluded` folder beside it. It skips spectral frames. [The frame-quality check](#the-frame-quality-check) describes it.
3. Background check (`background_homogeneity.py`). It compares each frame's sky background with the others of the same exposure length. It sets aside a frame taken through cloud or twilight (partly measured; see [STK-04](../../../../documentation/library_design/validation/stacking.md#stk-04)). `shared/quality/background_measurement.py` measures the sky.
4. Flat check (`flat_calibration.py`). It measures how many flats there are, how bright they are, and how noisy the master flat would be. It measures brightness against the camera profile's clip ceiling when the camera is known, and against a 16-bit guess otherwise. The clip ceiling is the highest raw count a saturated pixel reaches. A noisy master gets a blur width, which Siril applies as a Gaussian blur (partly measured; see [STK-05](../../../../documentation/library_design/validation/stacking.md#stk-05)). The check measures but does not blur the flat of a color sensor, which has a grid of red, green and blue filters over its pixels.
5. Unchanged-stack check (`stack_inputs.py`). It builds a record of these inputs:
    - the frames;
    - the calibration frames the library could pick for them (every temperature and binning of the matching camera, gain, offset and exposure, so a new dark never goes unnoticed);
    - the settings that change a stack's pixels;
    - the stacking-code version.

    It compares the record's hash (a short fingerprint of the record) with the one saved next to the stack on disk. When they match, the stage does not rebuild the stack. The [stacking README](../README.md#skipping-an-unchanged-stack-step-2) gives the rules and how to force a rebuild. A test scans the stacking code for every configuration setting it reads. A new setting has to be classified in `stack_inputs.py` (changes the stack, or does not) before the tests pass.
6. Frame-count check (`assess_input_quality.py`, `calibration_gates`). It reads the `calibration_blocking_flags` that the Siril driver recorded for each master bias, dark or flat built from fewer frames than `minimum_calibration_frames` (default 3; design estimate; see [stacking design estimates](../../../../documentation/library_design/validation/stacking.md#stk-35)). Each one becomes a flag reason, and the `calibration_frame_count` gate fails. The stack is still built.
7. Calibration-match check (`assess_input_quality.py`, `calibration_gates`). It reads two lists from the Siril driver. `calibration_mismatch_flags` records that a dark, bias or flat of another gain or offset, or a dark from another temperature, was applied. `calibration_match_blocking_flags` records that a kind of calibration frame was left out because its binning differs from the lights. `calibration_metadata_reasons` turns each list into one flag reason that quotes every sentence. The `calibration_metadata` gate fails when either list has an entry. A refused calibration fails the gate even when no frame of that kind was applied. The gate is `not_checked` only when nothing was applied and nothing was refused.
8. Input judgment (`assess_input_quality.py`). It collects the results into a `StackingInputQuality` (`models/quality/stacking_quality.py`).

### The frame-quality check

The check works on a session. It measures each frame with `shared/quality/raw_frame_check.py` and judges the frame against its session.

- **Few stars (cloud or heavy trailing).** A frame with fewer detected stars than half the session median moves (partly measured; see [STK-01](../../../../documentation/library_design/validation/stacking.md#stk-01)). The reason names the star count and the sky level. A sky level more than 1.15 times the median reads as cloud, and a normal sky level reads as trailing (partly measured; see [STK-01](../../../../documentation/library_design/validation/stacking.md#stk-01)). The 1.15 changes only the wording of the reason, not whether the frame moves.
- **Elongated stars (trailing).** A frame moves when its median star roundness is below 0.80 and at least 0.15 below the session median (0.20 for a color camera, whose small, coarsely sampled stars measure less steadily). Roundness is a star's narrow width divided by its wide width (1.0 for a circle). A session that is mildly elongated throughout keeps its mildly elongated frames. These limits are partly measured (see [STK-01](../../../../documentation/library_design/validation/stacking.md#stk-01), and [STK-02](../../../../documentation/library_design/validation/stacking.md#stk-02) for color cameras).
- **Left alone.** A satellite or aircraft streak leaves the median roundness and star count unchanged, so that frame stays. The stacker's pixel rejection removes the streak. Pixel rejection throws out a pixel's value in one frame when it disagrees strongly with the other frames.
- **Safeguards.** The check does not judge a session of fewer than 8 frames (partly measured; see [STK-01](../../../../documentation/library_design/validation/stacking.md#stk-01)). If more than a quarter of a session would move, none do, and the stage logs why (partly measured; see [STK-03](../../../../documentation/library_design/validation/stacking.md#stk-03)). Frames that cannot be read stay where they are.
- **Undoing it.** The check moves frames and never deletes them. `excluded_frames.json` in each `_excluded` folder records the original path, the kind (`clouded` or `trailed`), the reason, the measurements and the time. `ProcessingPipelines.restore_excluded_frames` moves them back, and `scripts/restore_excluded_frames.py` is its command-line form. `QualityRecords.measure_frames` lists them (`include=["excluded"]`) and shows what the check would move for a target without moving it (`kind="quarantine_preview"`, built by `quarantine_preview_report`). Frame scanning and incremental downloads treat `_excluded` as part of the target's folder, so a moved frame does not return to the target or download again.
- **Setting.** `quarantine_bad_frames_enabled` in `[Processing.Siril]`, on by default. Measuring takes about 1 s per frame.

The limits are in `frame_quarantine.py`.

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| `quarantine_bad_frames_enabled` in `[Processing.Siril]` | on | Turns the frame-quality check off |
| `minimum_calibration_frames` in `[Processing.Siril]` | 3 (design estimate; see [stacking design estimates](../../../../documentation/library_design/validation/stacking.md#design-estimates)) | Fewest frames per master before the frame-count check flags the stack |
| Frame-quality limits in `frame_quarantine.py` | 0.5 times the median star count; roundness 0.80 and 0.15 below the median; 8 frames; a quarter of a session (partly measured; see [STK-01](../../../../documentation/library_design/validation/stacking.md#stk-01) and [STK-03](../../../../documentation/library_design/validation/stacking.md#stk-03)) | Decide which frames move to `_excluded` |

## Quality metrics

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| `framesSubmitted`, `framesAccepted` | Frames | None | How many frames the stage got, and how many were left after the gain, frame-quality and background checks (checks 1 to 3) and so went to the stacking engine. A large gap means many frames were set aside | The UI; people reading the summary |
| `framesExcludedForGain`, `framesExcludedForBackground` | Frames | None | Counts of the frames set aside by each check | The UI; people reading the summary |
| `framesQuarantined` | Frames | None | How many frames with clouds or trailed stars the frame-quality check moved into `_excluded`. A non-zero count adds a flag reason to the stack, so a reader sees that the stack used fewer frames than the target holds | The UI; people reading the summary |
| `backgroundSplitDetected`, `backgroundSplitDetail` | Flag; text | None | Set when the frames fell into two sky-brightness groups. The detail gives each group's size and level | People reading the summary |
| `flatFrameCount`, `flatNoiseFraction`, `flatSmoothingSigmaPx` | Frames; fraction (0.005 is 0.5%); pixels | Noise 0.5% (partly measured; see [STK-05](../../../../documentation/library_design/validation/stacking.md#stk-05)) | The flat set's size, the master flat's relative noise, and the Gaussian width used to blur it, empty when none was applied. High noise means the blur is needed | The `flat_noise` gate; people reading the summary |
| `flatCalibrationIssues` | Sentences | None | One sentence per problem with the flats | Flag reasons |
| `calibrationMismatchFlags` and the blocking flags | Sentences | None | One sentence per mismatch between the lights and the calibration frames. The library writes each sentence, naming the settings it applied and the lights it applied them to (for a dark, the lights' temperature, the master's temperature and the tolerance). A soft flag means the nearest calibration frames were still applied despite a gain, offset or temperature mismatch, and the `calibrationMismatchFlags` field holds these. A blocking flag means a kind of calibration frame was not applied at all, because its binning differs. Blocking flags appear in the flag reasons and in the `calibration_metadata` gate. Both kinds fail that gate | The `calibration_metadata` gate; flag reasons |
| `calibration_blocking_flags` | Sentences | Minimum 3 frames (design estimate; see [stacking design estimates](../../../../documentation/library_design/validation/stacking.md#design-estimates)) | One sentence per master bias, dark or flat built from fewer frames than the minimum. They appear in the flag reasons and in the `calibration_frame_count` gate, and nothing else reads them | The `calibration_frame_count` gate; flag reasons |
| `flagReasons` | Sentences | None | One sentence per problem found in any check above. They feed the summary's flag reasons | The UI; people reading the summary |

## Files

- `camera_selection.py`: the one-camera rule.
- `frame_homogeneity.py`: the gain check.
- `frame_quarantine.py`: the frame-quality check and its limits.
- `background_homogeneity.py`: the background check.
- `flat_calibration.py`: the flat check.
- `sensor_noise.py`: measures a camera's gain and read noise from pairs of flat frames and pairs of bias frames. It is not a step of the stack. The library call `CalibrationCatalog.measure_sensor_noise` uses it. The difference of two frames taken one after the other holds only noise, and half its variance is the noise variance of one frame. Bias pairs give the read noise, and flat pairs give the gain (the bias-subtracted signal over the single-frame variance minus the read variance). It measures the central 1000 by 1000 pixels at one signal level, so it cannot show whether the sensor is linear. Its result matched a hand measurement (measured; see [STK-35](../../../../documentation/library_design/validation/stacking.md#stk-35)).
- `exposure_weighting.py`: the choice of per-frame weights for mixed exposure lengths.
- `stack_inputs.py`: the unchanged-stack record.
- `assess_input_quality.py`: the frame-count and calibration-match checks, and the `StackingInputQuality` judgment.

For exact behavior, read the code.
