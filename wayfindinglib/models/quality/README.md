# Quality models

This folder holds the shared shapes of quality judgment: what a statement is, what a rule is, and how efficiency and the loss account are written down. Planning (the per-target view) and Control (the per-night view) both use them, so they sit in Foundation. They are plain Pydantic models with field checks and no other behavior. Nothing here is ever stored. The code that fills them is in `wayfindinglib/analytics/result_quality/` (see its README).

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

The theory is in Section 6.11 of the [Wayfinding library architecture](../../../documentation/library_design/Wayfinding_Library_Architecture.md). The code names are in Section 6.10 of the [Wayfinding library implementation](../../../documentation/library_design/Wayfinding_Library_Implementation.md).

## Layering rule

These models import `astrometricslib` only from its package root. They import nothing from Control, Planning or Execution.

## What each file holds

- `statements.py` defines the words used to classify a statement and the statement itself.
  - `StatementKind` is `warning` (about frames already taken), `recommendation` (for the next session), `constraint` (a limit of the equipment, with no advice) `insufficient_data` (what is missing and how to get it) or `result` (what a star's science result is, with the number behind it, and no advice).
  - `ActionClass` is the kind of change a warning or recommendation asks for: capture settings, scheduling, calibration, data selection, session procedure, or adjustment of equipment the operator already owns. The library never advises new hardware.
  - `Severity` is `info`, `advice` or `warning`. A warning must have one.
  - `Confidence` is `measured`, `partly_measured` or `design_estimate`. It is the status label of the register entry behind the rule, and it is not a probability.
  - `TargetStatement` is one statement. It lists the exact measurement rows it used (`evidence`), the limits it was compared with (`limits`), any other numbers (`values`), a plain-language version of its message (`plain_message`, empty in older stored statements), a proposed action and its expected effect. `NeedsAttention` counts the warnings of a target or night.
- `rule.py` defines `QualityRule`, one row of a rule table, and `MetricRequirement`, which pins a metric name to one definition version. A row also holds a plain wording and a `produces` value (package row, checklist item or nothing). A rule that finds a row of another version asks for a remeasure and does not read the old number.
- `efficiency.py` defines the efficiency shapes: `ScienceGoal`, `NoiseRegime`, `EfficiencyReference`, `EfficiencyFactor`, `EfficiencyResult`, `LossShare` and `LossAccount`. A frame better than its reference is capped at a factor of 1.
- `capture_summary.py` defines `CaptureSettingSummary`: the counts, times and loss account for one capture setting (one camera and telescope with one filter, exposure length, gain setting, offset and binning).

## Rules the models enforce

- A warning has a severity.
- A warning or recommendation names exactly one action class.
- A constraint, an insufficient-data statement or a result names no action class and proposes no action.
- An insufficient-data statement gives a reason.

The tests in `test/` check these rules.

For exact behavior, read the code.
