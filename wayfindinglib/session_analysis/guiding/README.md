# Guiding analysis

This folder judges one observing night of guiding. The autoguider measures how far the guide star has drifted from its target position and commands small mount corrections to put it back. The analysis asks whether that record is trustworthy, how well the mount tracked, and whether anything should change.

`pipeline.py` runs the three stages in order and builds the night's summary (`GuidingSessionAnalysis` in `wayfindinglib/models/session/session_quality.py`). The summary lists which limits it used (`resolvedParameters`) so any result can be traced to the numbers behind it. A night is flagged when any recommendation is a warning.

## Stages

1. `pre_processing/` — is the data good? See its README.
2. `processing/` — what does the data show? See its README.
3. `post_processing/` — what should change? See its README.

## How far the limits apply to a night

The limits are worked out for the equipment in use now. A past night may have used other equipment. Each night gets a match level, recorded as `limitsEquipmentMatch`:

- `exact` — a session that night used the current setup. Every limit applies.
- `guide_optics_only` — the guide scope and guide camera match, but the imaging equipment is unknown or different. The limits apply with lower confidence.
- `none` — the guide optics differ. No limit applies. The night still gets its measurements, but no verdicts.

## What this analysis does not do

It does not estimate polar alignment. Ekos does not keep the result of its polar alignment routine. The net declination corrections were tested as a stand-in on this observatory's real nights, and they did not follow the pattern a polar misalignment must produce (the per-run values change sign between runs at nearly the same hour angle), so no polar alignment claim is made.

For exact behavior, read the code. The code is the source of truth.
