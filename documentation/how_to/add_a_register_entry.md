# Add a register entry

*Version 1.0 · 2026-10-15 · Status: current*

## Abstract

A register entry records the evidence for one tuned value. This guide lists the steps to add one. The entry template and the status labels are in the [validation register](../library_design/validation/index.md), and `astrometricslib/test/test_validation_register.py` enforces the format.

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

## 1. Steps

1. **Pick the file and the next ID.** Each pipeline has its own file under `documentation/library_design/validation/`. The ID is the file's prefix (`STK`, `AST`, `PHO`, `SPE` or `MOV`) and the next number, as a `### XXX-nn` heading followed by a bold title.
2. **Fill the fields in template order.** The test requires these fields: Claim, Settings it supports, Status, Data you need, Procedure, Acceptance criterion, What to look at, Our result, Verdict, Limits, To tune for your setup, Regenerate with, Guarded by tests and Raw record. A re-run entry adds an optional Runs table after Verdict.
3. **Write the status in the exact form.** `**Status:** Measured. <one sentence saying why>.` The label is Measured, Partly measured or Design estimate.
4. **Link each setting to code.** In **Settings it supports**, give each setting a `<!-- impl: dotted.path -->` comment and a `Relation:` of derived from, confirmed by, sanity check only or challenged by. The test checks that each path resolves.
5. **Give the Verdict in its form.** It starts with Pass, Fail, Inconclusive or Not judged.
6. **Record the facts a reader needs.** Give the code version, sample size, uncertainty and data split in **Our result**. Write "not recorded" when unknown.
7. **Cite the entry.** Every entry must be cited by at least one method document: a README, a review guide or the implementation paper. The review guides cite entries through the generator, so a new entry for a tuned value usually means editing `build/documentation/generate_pipeline_review_docs.py` and regenerating (see [Add a quality gate](add_a_quality_gate.md)).
8. **Run the checks.** The command is in [Contributing](https://github.com/m1chae2/astrometrics/blob/main/CONTRIBUTING.md#documentation).

## 2. Design estimates

A value with no measurement has no entry. It gets a row in its register file's `## Design estimates` table, with an ID such as `STK-D01` and a note on how it could be measured.
