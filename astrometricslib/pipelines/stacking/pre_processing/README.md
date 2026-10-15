# Pre-processing

This step checks the frames and calibration data before any pixels are combined. It does not stack anything. It decides which frames go in and whether the calibration data can be trusted.

Terms are defined in the [glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary). The terms used most here:

- **Light frame**: one picture of the target. **Stacking** averages many aligned light frames into one image with less noise.
- **Calibration frames**: pictures that measure the camera rather than the sky. A **bias** frame is a zero-length exposure (the camera's fixed electronic offset), a **dark** is taken with the shutter closed (the signal heat adds over the exposure), and a **flat** is a picture of an evenly lit surface (how sensitive each pixel is). A **master** is the average of many such frames.
- The **gain setting** is the camera's amplifier setting (a unitless number in the FITS `GAIN` card), **offset** a constant the camera adds to every pixel so that noise below zero is still recorded, and **binning** the combining of neighbouring pixels (for example 2 by 2) into one.
- **Siril** is the external program that builds the masters and stacks the frames.

## What it does

Before the numbered checks, the stage keeps the stack to one camera (`camera_selection.py`). Frames from two cameras differ in pixel size, gain, noise and calibration frames, so a stack that mixes them has no single meaning. `choose_camera_frames` picks a named camera's frames for `ProcessingPipelines.stack`, and `ensure_single_camera` stops the stage with an error when the frames still come from more than one camera. Frames with no camera recorded do not count.

1. **Gain check** (`frame_homogeneity.py`). Frames taken at different camera gain settings have different read noise, so mixing them hurts the stack. The check keeps the most common gain and sets the others aside.
2. **Frame-quality check** (`frame_quarantine.py`). Measures each light frame's stars and moves a frame with clouds or trailed stars into an `_excluded` folder beside it. Spectral frames are skipped. See below.
3. **Background check** (`background_homogeneity.py`). Each frame's sky background is compared with the others of the same exposure length. A frame taken through cloud or twilight is set aside (partly measured; see [STK-04](../../../../documentation/library_design/validation/stacking.md#stk-04)). The sky is measured by `shared/quality/background_measurement.py`.
4. **Flat check** (`flat_calibration.py`). Measures how many flats there are, how bright (against the camera profile's clip ceiling, the highest raw count a saturated pixel reaches, when the camera is known, and a 16-bit guess otherwise), and how noisy the master flat would be. A noisy master gets a blur width, which Siril applies as a Gaussian blur (partly measured; see [STK-05](../../../../documentation/library_design/validation/stacking.md#stk-05)). A colour sensor's flat (one with a grid of red, green and blue filters over its pixels) is measured but not blurred.
5. **Unchanged-stack check** (`stack_inputs.py`). Builds a record of the frames, the calibration frames the library could pick for them (every temperature and binning of the matching camera, gain, offset and exposure, so a new dark never goes unnoticed), the settings that change a stack's pixels and the stacking-code version, and compares its hash (a short fingerprint of the record) with the one saved next to the stack on disk. When they match, the stack is not rebuilt. See the stacking README for the rules and for how to force a rebuild. A test scans the stacking code for every configuration setting it reads, so a new setting has to be classified in `stack_inputs.py` (changes the stack, or does not) before the tests pass.
6. **Frame-count check** (`assess_input_quality.py`, `calibration_gates`). Reads the `calibration_blocking_flags` that the Siril driver recorded for each master bias, dark or flat built from fewer frames than `minimum_calibration_frames` (default 3). Each one becomes a flag reason, and the `calibration_frame_count` gate fails. The stack is still built.
7. **Calibration-match check** (`assess_input_quality.py`, `calibration_gates`). Reads two lists from the Siril driver: `calibration_mismatch_flags` (a dark, bias or flat of another gain or offset, or a dark from another temperature, was applied) and `calibration_match_blocking_flags` (a kind of calibration frame was left out because its binning differs from the lights). `calibration_metadata_reasons` turns each list into one flag reason that quotes every sentence. The `calibration_metadata` gate fails when either list has an entry. A refused calibration fails the gate even when no frame of that kind was applied. The gate is `not_checked` only when nothing was applied and nothing was refused.
8. **Input judgement** (`assess_input_quality.py`). Collects the results into a `StackingInputQuality` (`models/stacking_quality.py`).

## The frame-quality check

The check works on a batch: the light frames of one camera, filter and exposure length taken less than four hours apart. It measures each frame with `shared/quality/raw_frame_check.py` and judges the frame against its batch.

- **Few stars (cloud or heavy trailing).** A frame with fewer detected stars than half the batch median moves. The reason names the star count and the sky level. A sky level more than 1.15 times the median reads as cloud; a normal sky level reads as trailing. The 1.15 changes only the wording of the reason, not whether the frame moves.
- **Elongated stars (trailing).** A frame moves when its median star roundness is below 0.80 and at least 0.15 below the batch median (0.20 for a colour camera, whose small, coarsely sampled stars measure less steadily). Roundness is a star's narrow width divided by its wide width (1.0 for a circle). A session that is mildly elongated throughout keeps its mildly elongated frames. These limits are partly measured (see [STK-01](../../../../documentation/library_design/validation/stacking.md#stk-01), and [STK-02](../../../../documentation/library_design/validation/stacking.md#stk-02) for colour cameras).
- **Left alone.** A satellite or aircraft trail leaves the median roundness and star count unchanged, so that frame stays; the stacker's pixel rejection (throwing out a pixel's value in one frame when it disagrees strongly with the other frames) removes the trail.
- **Safeguards.** A batch of fewer than eight frames is not judged. If more than a quarter of a batch would move, none do, and the stage logs why (partly measured; see [STK-03](../../../../documentation/library_design/validation/stacking.md#stk-03)). Frames that cannot be read stay where they are.
- **Undoing it.** Frames are moved, never deleted. `excluded_frames.json` in each `_excluded` folder records the original path, the kind (`clouded` or `trailed`), the reason, the measurements and the time. `ProcessingPipelines.restore_excluded_frames` moves them back (`scripts/restore_excluded_frames.py` is its command-line form), `QualityDiagnostics.frame_quality` lists them (`include=["excluded"]`) and shows what the check would move for a target without moving it (`kind="quarantine_preview"`, built by `quarantine_preview_report`). Frame scanning and incremental downloads treat `_excluded` as part of the target's folder, so a moved frame does not return to the target or download again.
- **Setting.** `quarantine_bad_frames_enabled` in `[Processing.Siril]`, on by default. Measuring takes about one second per frame.

The limits are in `frame_quarantine.py`.

## The quality metrics

- **Frames submitted and accepted.** How many frames the stage got, and how many were left after the gain, frame-quality and background checks (checks 1 to 3) and so went to the stacking engine.
- **Frames excluded for gain or background.** Counts of the frames set aside by each check.
- **Frames quarantined.** How many frames with clouds or trailed stars the frame-quality check moved into `_excluded`. A non-zero count adds a flag reason to the stack, so a reader sees that the stack used fewer frames than the target holds.
- **Background split detected and detail.** Set when the frames fell into two sky-brightness groups. The detail gives each group's size and level.
- **Flat frame count, noise fraction and smoothing width.** The flat set's size, the master flat's relative noise (0.005 is 0.5%), and the Gaussian width in pixels used to blur it, empty when none was applied.
- **Flat calibration issues.** One sentence per problem with the flats.
- **Calibration match flags.** One sentence per mismatch between the lights and the calibration frames. The library writes each sentence, naming the settings it applied and the lights it applied them to (for a dark, the lights' temperature, the master's temperature and the tolerance). There are two kinds. A soft flag means the nearest calibration frames were still applied despite a gain, offset or temperature mismatch; the `calibrationMismatchFlags` field of the quality summary holds these. A blocking flag means a kind of calibration frame was not applied at all, because its binning differs; these appear in the flag reasons and in the `calibration_metadata` gate. Both kinds fail that gate.
- **Calibration frame-count flags.** One sentence per master bias, dark or flat built from fewer frames than the minimum. They appear in the flag reasons and in the `calibration_frame_count` gate, and nothing else reads them.
- **Flag reasons.** One sentence per problem found in any check above. They feed the summary's flag reasons.

For exact behavior and thresholds, read the code.
