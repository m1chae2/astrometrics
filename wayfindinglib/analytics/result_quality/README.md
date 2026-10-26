# Result-quality judgment

This folder judges the quality of the data a target has collected and says what to do next. It reads the measurements that `astrometricslib` stored, compares them with limits, and returns statements (`TargetStatement`, see `wayfindinglib/models/quality/README.md`). It stores nothing. Every result is worked out again each time someone asks, because a stored statement would go stale as soon as the equipment, a limit or a rule changed.

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

The theory is in Section 6.11 of the [Wayfinding library architecture](../../../documentation/library_design/Wayfinding_Library_Architecture.md). The code names and settings are in Section 6.10 of the [Wayfinding library implementation](../../../documentation/library_design/Wayfinding_Library_Implementation.md). The evidence behind each rule is in the [validation register](../../../documentation/library_design/validation/wayfinding.md).

## Layering rule

This folder is Foundation code. Both Planning (the per-target view) and Control (the per-night view) call it, so it imports neither of them, nor Execution, nor any hardware driver. It may import `wayfindinglib.models` and the public names of `astrometricslib`. Its functions are pure. The caller reads the measurements, frames and records and passes them in. The task modules that do the reading are `wayfindinglib/tasks/planning_tasks/target_report_tasks.py`, `tonight_ranking_tasks.py`, `result_judgment_tasks.py` and `wayfindinglib/tasks/control_tasks/night_quality_task.py`.

## What each module does

Rules and judging:

- `rules.py` holds `RESULT_QUALITY_RULES`, one row for each warning, recommendation or limit the library can state about a target's data. A row names the metrics it reads (each at one definition version), its action class, its limits, its wording and its register entry. `DEFERRED_RULES` lists the rules not built yet, with the reason.
- `star_result_rules.py` holds the rows for one star's science result (a spectrum class or a period).
- `session_rules.py` holds the rows for the per-night guiding, capture and sky analyses.
- Every row also has a plain wording (`plain_wording`, filled into the statement's `plain_message`) and says what its action becomes in the plan (`produces`: a package row, a checklist item, or nothing). The user interface shows only the plain message.
- `plain_text.py` holds the helpers that write plain messages: the hedged spectral-type phrase ("an orange K-type star"), the exposure phrases, and the plain note for a rule that could not be checked.
- `exposure_request.py` turns a "take more frames" statement into the exposure row of an observation package (`exposure_request_from_statement`).
- `all_rules.py` joins the three tables into `ALL_RULES`. A check that must hold for every rule walks this one list.
- `judge_results.py` applies the plain rules (one reading against one limit) and returns statements. `needs_attention` counts the warnings that need action, and every view uses it.
- `report_rules.py` builds the statements that need a join, a fit or a split first: frames lost, sky cost, altitude cost, the sharpness trade-off (for a stack of exposure groups, worked out on the whole stack), calibration gaps and "more frames will not help" (from the stop signal, or when a noise floor makes up too much of the noise for a count).
- `focus_drift.py` fits how star width grows with focuser temperature.
- `guiding_exposure.py` finds how long an exposure the guiding lets stay sharp.

Efficiency and noise:

- `efficiency.py` finds the efficiency of each frame against two references, the physical one and the equipment's own baseline, and splits the loss among causes.
- `science_goal.py` chooses the science goal for each capture setting.
- `aggregates.py` builds the loss account and the summaries of a capture setting, a night and a target.
- `noise_growth.py` forecasts how a stack's noise falls as frames are added, works out "take N more" and finds the noise floor that gives the stop signal. It follows the stacker's exposure groups: it keeps each group's measured frame noise, predicts a new group's, folds short sets of new frames where the stacker would, and says when new frames would change the heaviest group. A frame folded into a group of another length is forecast like any other member. The stacker scales each frame of a group to the group's reference frame, so the frame's noise is multiplied by the reference exposure over its own; a group that holds folded frames needs the reference frame's exposure and is not forecast without it. It never asks for fewer than 5 frames of a new exposure length. The floor comes from one stack's measured noise against the noise its frames predict, or from a fit over several stacks; when the floor is most of the noise, the answer says more frames will not help and states the floor. A forecast ratio is given as a fraction of the stars' flux, like the stack's normalized noise, so the combine's mean-exposure scale is left out.
- `forecast.py` reads a target's stack runs from the stored rows and checks an earlier forecast against the stack that followed it. For a stack of exposure groups it rebuilds each group's frame noise from the stored frame weights, frame count and exposure (the stored rows give no direct link from a frame to its group, so a frame goes to the group the stacker's fold rule gives its exposure length). A group that holds folded frames also needs its reference frame's exposure from the stored rows; without it the pair is not checked, and the reason says so. It applies the earlier stack's noise floor. It keeps a confounded pair out of the check, with a short reason: no aperture-set identity or a different one, a different noise definition, no calibration record or a changed camera, optic, flat, bias, Siril or stacking-algorithm version (a changed dark master counts only for an uncooled camera), a changed heaviest exposure group, a switch between one group and several, or added frames that were taken before the earlier stack. The sensor-temperature band the plan names for the uncooled Nikon is not stored, so it is not checked.
- `sky_model.py` fits the sky background to the user's own frames against Moon geometry and airmass.
- `capture_overheads.py` finds the time lost to downloads, dither settling and autofocus, and turns a frame count into clock time.
- `starting_point.py` gives a first exposure for a target with no frames.
- `observing_values.py` finds the conditions of a frame, such as airmass, Moon position and dew risk. These are context and drive no rule unless their metric is checked.

Inputs and plans:

- `library_inputs.py` reads what the analyses need from the library in one place, so the report, the ranking and the night review do not each read it their own way.
- `guiding_join.py` cuts the guide samples to each frame's exposure window.
- `allowed_capture_values.py` says which gain, offset, binning and filter a night plan may ask for.
- `reply_budget.py` measures a reply the way the MCP tool registry does, and shortens a statement's evidence when a reply is too long. Its last step empties the plain messages.

## Quality metrics that each module produces

- Efficiency is a number from 0 to 1. It is the time the frames are worth divided by the time a reference frame would need. It is stated for one goal, one noise regime and one named reference. A value near 1 means the frames are as good as the reference. Planning and Control read it through the loss account.
- A loss account has two parts: time of frames set aside, and the efficiency deficit of the frames kept, split by cause. A cause the library cannot measure is listed as "not included", and a remainder is "cause unknown" only where the efficiency is measured on its own.
- A statement's confidence is the status label of its register entry: Measured, Partly measured or Design estimate. A reader who needs to know how far to trust a statement reads that label.

## Settings

Each tuned value is a constant at the top of its module, with a comment that says where it came from. Its status and a way to measure it on your data are in the validation register. For example, the baseline needs at least three nights (design estimate; see [WAY-D22](../../../documentation/library_design/validation/wayfinding.md#design-estimates)). Changing a value, or the method behind it, means updating its register entry.

For exact behavior, read the code.
