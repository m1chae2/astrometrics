# Wayfinding Validation and Tuning

*Version 1.0 · 2026-10-24 · Status: current*

This file lists the evidence behind the quality statements that `wayfindinglib` makes: the rules that turn the measurements of `astrometricslib` into warnings and recommendations, the efficiency references, and the forecast of "take N more". Each entry names the setting, the data you need, the library calls that produce the number, what we measured, and how to tune the setting on your own equipment. It is written for two readers: a learner who wants to see how a number was found, and a reviewer who wants to repeat the check. Terms are defined in the [glossary](../../Glossary.md), and the status labels (Measured, Partly measured, Design estimate) are explained in [index.md](index.md).

Every Python example assumes the library is installed and configured with your own frame library. Entries are added as the rules and models they back are built, so a rule that has no entry here yet is not yet in the library.

## Measured and partly measured values

### WAY-01

**The statement and confidence vocabulary**

- **Claim:** Every statement the library makes about a target's data has one kind (warning, recommendation, constraint or insufficient data), at most one action class, and a confidence taken from the status label of the register entry behind its rule. A rule whose entry is a design estimate is shown as tentative.
- **Settings it supports:** None. The entry backs the vocabulary, not a tuned value.
- **Status:** Partly measured. The checks that the models enforce are tested on made-up statements only. No real night has been judged with them yet.
- **Data you need:** None for the vocabulary itself. To check a real rule, you need the rule table and the status of each rule's register entry.
- **Procedure:**
  1. Build a statement of each kind with `TargetStatement`.
  2. Check that a warning without a severity, a warning or recommendation without an action class, and a constraint or missing-data statement with an action class are all refused.
  3. Check that a rule's confidence equals the status label of its register entry.

  ```python
  from wayfindinglib.models.quality.statements import ActionClass, Confidence, StatementKind, TargetStatement

  statement = TargetStatement(
      rule="example",
      kind=StatementKind.RECOMMENDATION,
      action_class=ActionClass.CAPTURE_SETTINGS,
      message="Take 20 more frames.",
      confidence=Confidence.DESIGN_ESTIMATE,
  )
  print(statement.model_dump(by_alias=True, mode="json"))
  ```

  `TargetStatement` <!-- impl: wayfindinglib.models.quality.statements.TargetStatement --> is the shape of a statement, and `QualityRule` <!-- impl: wayfindinglib.models.quality.rule.QualityRule --> is the shape of one row of the rule table. The confidence values are those of `Confidence` <!-- impl: wayfindinglib.models.quality.statements.Confidence -->.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether each invalid combination is refused with a message, and whether every rule row carries a register entry and a confidence that match.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The models check the shape of a statement, not whether the statement is true. The match between a rule's confidence and its entry's status is checked by the rule conformance test once the rule table exists.
- **To tune for your setup:** Nothing to tune. To add a rule, give it a register entry (or a design-estimate row) first and copy that status into the rule's confidence.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/models/quality/test/test_quality_statements.py` checks each combination.
- **Raw record:** None kept.

## Design estimates

These values have not been measured. Each is a reasoned choice; the last column says how you could measure it on your data.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| WAY-D01 | Seeing used by the physical efficiency reference when the site's typical seeing is not set | None: the reference uses the telescope's diffraction limit alone | `SiteProfile.typical_seeing_fwhm_arcsec` <!-- impl: wayfindinglib.models.equipment_and_site.site_profile.SiteProfile --> | Every frame looks worse than the reference, so the efficiency reads low and losses are overstated. | Enter your typical good-night star width in arcseconds, or take the lower decile of your own star widths over many nights, and compare the efficiency with and without it. |
