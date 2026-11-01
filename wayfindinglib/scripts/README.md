# Scripts

This folder holds standalone command-line scripts for `wayfindinglib`. Each one is run directly (`python <script>.py [options]`), and the library never imports it.

Terms are defined in the [glossary](../../documentation/Glossary.md). This document also defines each term the first time it appears.

## Checks on the quality judgments

- `shadow_compare_verdicts.py` compares the result-quality rules with the flags that `astrometricslib` stores today. It is the shadow run of issue #97 (`documentation/plans/VERDICT_REMOVAL_PLAN.md`, phase 1, and `QUALITY_METRICS_PLAN.md`, safeguard 4).
  - For every target it reads the stored `flagged`, `flag_reasons` and gate outcomes of each pipeline (stacking, spectral stacking, astrometry, photometry, spectroscopy, asteroid detection). It also reads each star's match distance and gaps, and works out the old poor-match and ambiguity flags from them. It then asks `review.get_advisory` for the target's quality statements and for each star's result statements.
  - A rule is tied to a gate when the rule reads the gate's metric (`<pipeline>.gate_<name>`). A difference is a failed gate whose rule stays quiet, a rule that fires on a gate that did not fail, a flagged pipeline where no rule of that pipeline fires, a flag reason that no failed gate gives, or a rule with no flag to compare with.
  - The report lists the differences by pipeline and kind, the largest kinds by gate or rule, the gates that no rule reads (with how often each passed, failed and was not checked), and every difference by target. A person marks each difference as a bug fixed or an approved change.
  - Detection labels (period, transit, spectral feature and emission-line verdicts) stay in `astrometricslib` and are not compared.
  - It also checks that no warning is lost: each stored flag reason must be the reason of a failed row in the measurement table, the rows that `Jobs.query(detail="warnings")` lists. A reason with no row is counted as old data when the newest row of its pipeline on that target is older than `--new-rows-since` (default 2026-11-02, the day after the notice rows were written), and as a gap otherwise. The report states the two counts apart.
  - It is read-only. It writes only the file given with `--report` and nothing to any database. Run it on the data copy with `build/agent_tools/realrun.sh`, not on the real library. `--target ID` (repeatable) limits the run.
  - Its tests, on made-up summaries and statements, are in `test/test_shadow_compare_verdicts.py`.

## Catalog and reference data

- `build_deep_star_catalog.py` downloads the Gaia DR3 stars the Planetarium draws, once, to this computer.
- `download_all_target_frames.py` downloads the frames of every remote folder on the telescope.
- `generate_projection_reference.py` rewrites the planetarium reference file that the UI tests compare against.
