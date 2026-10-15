# Add a quality gate

*Version 1.0 · 2026-10-15 · Status: current*

## Abstract

A gate is a run-level pass or fail entry in a pipeline's quality record. The review guides list every gate, and they are generated from the code. This guide lists the steps to add a gate so the guides and tests stay in step. The generator is `build/documentation/generate_pipeline_review_docs.py`.

Terms are defined in the [glossary](../library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## 1. Steps

1. **Define the name in the pipeline code.** Add a module-level constant named `GATE_NAME`, or ending in `_GATE_NAME`, with a string value, for example `GATE_NAME = "frame_quarantine"` in `astrometricslib/pipelines/stacking/pre_processing/frame_quarantine.py`. The generator and the parity test find gates by reading the source for this pattern, in every non-test file of the pipeline package.
2. **Record the gate in the run.** Return it from the module's gate function with `passed_gate`, `failed_gate` or `unchecked_gate` (in `astrometricslib/models/gate_result.py`), using the constant as the name.
3. **Describe it for the guide.** Add one entry to `GATE_DESCRIPTIONS` in `generate_pipeline_review_docs.py`, keyed by the gate name. Say when it fails and when it is not checked. Name the limit's constant in backticks.
4. **Give a tuned limit a status.** If the gate has a tuned limit, add its row to the generator's parameter table with a status label and the register entry ID, using `status_cell`. Add or update the entry as described in [Add a register entry](add_a_register_entry.md).
5. **Regenerate.** Run `.venv/bin/python build/documentation/generate_pipeline_review_docs.py`. The guides under `documentation/library_design/pipelines/` and their gate counts update. Never edit them by hand.
6. **Run the checks.** `astrometricslib/pipelines/test/test_pipeline_doc_parity.py` fails if a gate has no description, if a description names a gate the code lacks, or if a committed guide is out of date.

If a pipeline README has a gate table, add the gate there too. The columns are Gate, Fails when and Not checked when.
