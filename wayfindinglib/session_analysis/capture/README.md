# Capture analysis

This folder judges one observing night of captured frames. The camera takes the frames, and the science library measures each one. The analysis asks whether the night's frames are all there and usable, what they show about saturation and star quality, and whether anything should change.

`pipeline.py` runs the three stages in order and builds the night's summary (`CaptureSessionAnalysis` in `wayfindinglib/models/session/capture_quality.py`). The summary lists which limits and definitions it used (`resolvedParameters`) so any result can be traced to the numbers behind it. A night is flagged when any recommendation is a warning.

## Stages

1. `pre_processing/` — is the data good? See its README.
2. `processing/` — what do the frames show? See its README.
3. `post_processing/` — what should change? See its README.

## What it reuses from the science library

The capture analysis does not measure pixels. It reads what `astrometricslib` already recorded.

- **Per-frame measurements** on each frame record: the saturated-pixel fraction, background level, star width and roundness from registration, sensor temperature, and pointing.
- **Saturation verdicts** from each target's stacking summary. The science library checks on the pixels whether a star clips at each exposure length. The capture analysis uses that verdict whenever the target has been stacked, and quotes the library's recommended exposure. Frame counts decide only when no verdict exists. On this observatory's 69 exposure groups with both, the two agreed on 67. The two that differed are Moon frames, where an extended bright disc, not a star, reaches the ceiling.
- **Definitions** exported from the science library: the smallest saturated blob that counts as a star (4 pixels), the share of frames that makes an exposure length count as clipped (half), and how far a dark frame's temperature may differ from a light's (3 degrees Celsius).
- **Spectral or imaging** from `frame_is_spectral`, and the camera profiles, to match frames to the equipment.

## What limits it uses

Every limit comes from the equipment's performance envelope (`wayfindinglib/analytics/performance_envelope.py`), so it follows the equipment. The capture analysis adds four:

- the shortest exposure whose star measurements count: three guide cycles of the equipment's own guider;
- the star width above which a night is unusual, from the equipment's earlier nights;
- the star roundness below which a night is unusual, from the equipment's earlier nights;
- the share of cancelled exposures above which a night is unusual, from the equipment's earlier nights.

A night is judged only against the equipment's **earlier** nights. Until the equipment has five earlier nights with enough measured frames, the limit reports no value and the night gets measurements but no verdict.

## What this analysis does not do

- It gives no saturation advice for imaging frames. In a deep imaging field the brightest stars clip at any useful exposure, and avoiding that would make the exposure too short for the faint target. The frame records do not say which star is the target, so clipping is reported for imaging frames but never recommended against.
- It does not say how many more frames to take. That needs the noise each frame adds, and the camera profile does not hold a measured gain or read noise yet.
- It does not use the stack-level summaries per night. They are per target, across every night stacked, so they join to frames by target and exposure length only.

For exact behavior, read the code. The code is the source of truth.
