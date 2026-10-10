# Pre-processing

This step checks the frames and calibration data before any pixels are combined. It does not stack anything. It decides which frames go in and whether the calibration data can be trusted.

## What it does

1. **Gain check** (`frame_homogeneity.py`). Frames taken at different camera gain settings have different read noise, so mixing them hurts the stack. The check keeps the most common gain and sets the others aside.
2. **Frame-quality check** (`frame_quarantine.py`). Measures each light frame's stars and moves a frame with clouds or trailed stars into an `_excluded` folder beside it. Spectral frames are skipped. See below.
3. **Background check** (`background_homogeneity.py`). Each frame's sky background is compared with the others of the same exposure length. A frame taken through cloud or twilight is set aside. The sky is measured by `shared/quality/background_measurement.py`.
4. **Flat check** (`flat_calibration.py`). Measures how many flats there are, how bright (against the camera profile's clip ceiling when the camera is known, and a 16-bit guess otherwise), and how noisy the master flat would be. A noisy master gets a blur width, which Siril applies. A colour sensor's flat is measured but not blurred.
5. **Unchanged-stack check** (`stack_inputs.py`). Builds a record of the frames, the calibration frames the library could pick for them (every temperature and binning of the matching camera, gain, offset and exposure, so a new dark never goes unnoticed), the settings that change a stack's pixels and the stacking-code version, and compares its hash with the one saved next to the stack on disk. When they match, the stack is not rebuilt. See the stacking README for the rules and for how to force a rebuild. A test scans the stacking code for every configuration setting it reads, so a new setting has to be classified in `stack_inputs.py` (changes the stack, or does not) before the tests pass.
6. **Frame-count check** (`assess_input_quality.py`, `calibration_gates`). Reads the `calibration_blocking_flags` that the Siril driver recorded for each master bias, dark or flat built from fewer frames than `minimum_calibration_frames` (default 3). Each one becomes a flag reason, and the `calibration_frame_count` gate fails. The stack is still built.
7. **Calibration-match check** (`assess_input_quality.py`, `calibration_gates`). Reads two lists from the Siril driver: `calibration_mismatch_flags` (a dark, bias or flat of another gain or offset, or a dark from another temperature, was applied) and `calibration_match_blocking_flags` (a kind of calibration frame was left out because its binning differs from the lights). `calibration_metadata_reasons` turns each list into one flag reason that quotes every sentence. The `calibration_metadata` gate fails when either list has an entry. A refused calibration fails the gate even when no frame of that kind was applied. The gate is `not_checked` only when nothing was applied and nothing was refused.
8. **Input judgement** (`assess_input_quality.py`). Collects the results into a `StackingInputQuality` (`models/stacking_quality.py`).

## The frame-quality check

The check works on a batch: the light frames of one camera, filter and exposure length taken less than four hours apart. It measures each frame with `shared/quality/raw_frame_check.py` and judges the frame against its batch.

- **Few stars (cloud or heavy trailing).** A frame with fewer detected stars than half the batch median moves. The reason names the star count and the sky level. A sky level more than 1.15 times the median reads as cloud; a normal sky level reads as trailing.
- **Elongated stars (trailing).** A frame moves when its median star roundness is below 0.80 and at least 0.15 below the batch median. Roundness is a star's narrow width divided by its wide width. A session that is mildly elongated throughout keeps its mildly elongated frames.
- **Left alone.** A satellite or aircraft trail leaves the median roundness and star count unchanged, so that frame stays; the stacker's pixel rejection removes the trail.
- **Safeguards.** A batch of fewer than eight frames is not judged. If more than a quarter of a batch would move, none do, and the stage logs why. Frames that cannot be read stay where they are.
- **Undoing it.** Frames are moved, never deleted. `excluded_frames.json` in each `_excluded` folder records the original path, the kind (`clouded` or `trailed`), the reason, the measurements and the time. `ProcessingPipelines.restore_excluded_frames` moves them back (`scripts/restore_excluded_frames.py` is its command-line form), `QualityDiagnostics.frame_quality` lists them (`include=["excluded"]`) and shows what the check would move for a target without moving it (`kind="quarantine_preview"`). Frame scanning and incremental downloads treat `_excluded` as part of the target's folder, so a moved frame does not return to the target or download again.
- **Setting.** `quarantine_bad_frames_enabled` in `[Processing.Siril]`, on by default. Measuring takes about one second per frame.

The limits are in `frame_quarantine.py`, each with the measurements it was checked against.

## The quality metrics

- **Frames submitted and accepted.** How many frames the stage got and how many passed the two checks.
- **Frames excluded for gain or background.** Counts of the frames set aside by each check.
- **Frames quarantined.** How many frames with clouds or trailed stars the frame-quality check moved into `_excluded`. A non-zero count adds a flag reason to the stack, so a reader sees that the stack used fewer frames than the target holds.
- **Background split detected and detail.** Set when the frames fell into two sky-brightness groups. The detail gives each group's size and level.
- **Flat frame count, noise fraction and smoothing width.** The flat set's size, the master flat's relative noise (0.005 is 0.5%), and the Gaussian width in pixels used to blur it, empty when none was applied.
- **Flat calibration issues and calibration mismatch flags.** One sentence per problem.
- **Calibration match flags.** One sentence per mismatch between the lights and the calibration frames. The library writes each sentence, naming the settings it applied and the lights it applied them to (for a dark, the lights' temperature, the master's temperature and the tolerance). The `calibrationMismatchFlags` field of the quality summary holds the soft ones. The blocking ones (binning) appear in the flag reasons and in the `calibration_metadata` gate.
- **Calibration frame-count flags.** One sentence per master bias, dark or flat built from fewer frames than the minimum. They appear in the flag reasons and in the `calibration_frame_count` gate, and nothing else reads them.
- **Flag reasons.** One sentence per problem found in any check above. They feed the summary's flag reasons.

For exact behavior and thresholds, read the code.
