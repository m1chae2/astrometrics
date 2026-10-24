# Capture analysis

This folder judges one observing night of captured frames. The camera takes the frames, and the science library measures each one. The analysis asks whether the night's frames are all there and usable, what they show about saturation and star quality, and whether anything should change.

`pipeline.py` runs the three stages in order and builds the night's summary (`CaptureSessionAnalysis` in `wayfindinglib/models/session/capture_quality.py`). The summary lists which limits and definitions it used (`resolvedParameters`) so any result can be traced to the numbers behind it. A night is flagged when any recommendation is a warning.

## Stages

1. `pre_processing/` — is the data good? See its README.
2. `processing/` — what do the frames show? See its README.
3. `post_processing/` — what should change? See its README.

## What it reuses from the science library

The capture analysis does not measure pixels. It reads what `astrometricslib` already recorded.

- **Per-frame measurements** stored by the frame measurement job and read with `astrometrics.quality.query`: the saturated-pixel fraction, background level, Moffat star width, and star elongation (roundness is its inverse). Each metric is pinned to one definition version; a row of another version counts as missing. The frame record still gives sensor temperature and pointing.
- **No stacking verdict.** Whether a star clips is decided here, from each frame's stored saturated-pixel measurement. The stacking summary's saturation verdict and recommended exposure are not read (an earlier check found the two agreed on 67 of 69 exposure groups; the Moon frames differed). The exposure advice is a rule in the quality rule table.
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
