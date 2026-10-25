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
- `report_rules.py` builds the statements that need a join, a fit or a split first: frames lost, sky cost, altitude cost, the sharpness trade-off, calibration gaps and "more frames will not help".
- `focus_drift.py` fits how star width grows with focuser temperature.
- `guiding_exposure.py` finds how long an exposure the guiding lets stay sharp.

Efficiency and noise:

- `efficiency.py` finds the efficiency of each frame against two references, the physical one and the equipment's own baseline, and splits the loss among causes.
- `science_goal.py` chooses the science goal for each capture setting.
- `aggregates.py` builds the loss account and the summaries of a capture setting, a night and a target.
- `noise_growth.py` forecasts how a stack's noise falls as frames are added, works out "take N more" and fits the noise floor that gives the stop signal.
- `forecast.py` checks an earlier forecast against the stack that followed it.
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
