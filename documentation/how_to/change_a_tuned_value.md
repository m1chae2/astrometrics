# Change a tuned value

*Version 1.0 · 2026-10-15 · Status: current*

## Abstract

A tuned value appears in the code, the implementation paper, the READMEs, the review guides and the validation register. This guide lists what to update so they agree. Changing a value or the method behind it means updating its register entry.

Terms are defined in the [glossary](../library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## 1. Steps

1. **Find the entry.** The implementation paper (`Astrometrics_Library_Implementation.md`) lists each tuned value with its status label and register ID. Read the entry first.
2. **Change the value.** A config key is changed in the configuration file or its default. A code constant is changed in the code.
3. **Re-run the procedure.** Run the entry's **Procedure** on your data, as described in [Validate a result on your data](validate_on_your_data.md). If you cannot, mark the old result as made with an earlier version.
4. **Update the entry.** Change **Settings it supports**, **Status**, **Our result** and **Verdict**. Record the code version, sample size, uncertainty and data split. A re-run adds a row to the **Runs** table.
5. **Update the documents that cite the value.** Search for the constant's name. Update the implementation paper's table and any README that states the value. The architecture paper states no tuned values, so it does not change.
6. **Regenerate the guides.** Run `.venv/bin/python build/documentation/generate_pipeline_review_docs.py` and, if gates changed, `.venv/bin/python build/documentation/audit_pipelines.py`.
7. **Run the checks.** The command is in [Contributing](https://github.com/m1chae2/astrometrics/blob/main/CONTRIBUTING.md#documentation).
