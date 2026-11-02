# Inventory for issue 97: photometry and asteroid detection

Date: 2026-11-01. Status: draft. Every "Proposed group" below is a proposal for you to approve or change. No code has changed. Read-only checks only; nothing was run on real data.

Sources: `VERDICT_REMOVAL_PLAN.md` (D3, D4, phase 2) and `QUALITY_METRICS_PLAN.md` section 7. Groups are the ones in section 7, trimmed to four for this inventory:

- **Self-check**: stays in astrometricslib as a measurement with a check outcome.
- **Judgment**: fitness for use. Moves to a wayfindinglib rule in `wayfindinglib/analytics/result_quality/`.
- **Detection label**: stays, reported with its statistic, its limit and its register entry (issue #97, decision of 2026-10-24).
- **Removed in phase 0**: already planned (below).

## 1. Already handled by phase 0 (plan item 4)

These four photometry fields have no reader outside tests and docs. I checked the whole repo (library, wayfindinglib, backend, ui, mcp_servers, scripts, notebooks): no other hit. They are still in the code on this branch, so "handled" means planned, not done.

| Field | Set in | Rule and limit | Readers found |
| --- | --- | --- | --- |
| `is_low_coverage` | `pre_processing/assess_input_quality.py` | `coverage_fraction < 0.5` (`LOW_COVERAGE_THRESHOLD`, PHO-D17) | model, README, 1 test file |
| `is_tracking_unstable` | same | drift > 20 px (`UNSTABLE_TRACKING_DRIFT_PX`, PHO-D16) | model, README, 1 test file |
| `is_low_confidence` | `post_processing/assess_output_quality.py` | `abs(margin) < 1` MAD (`LOW_CONFIDENCE_MARGIN`, PHO-D17) | model, README, 2 test files |
| `is_trustworthy` (photometry) | same | `not is_low_confidence` | model, 1 test file (`test_identify_variable_stars.py`) |

Cautions for phase 0, from the code:

- `UNSTABLE_TRACKING_DRIFT_PX` is also imported by `registration_drift` gate code in `record_quality.py`. Keep the constant.
- `LOW_COVERAGE_THRESHOLD` and `LOW_CONFIDENCE_MARGIN` have no other user. Removing them means updating PHO-D17 in `validation/photometry.md` and the two READMEs.
- Spectroscopy `is_trustworthy` is a different field and is not part of this inventory.
- The numbers stay (`coverage_fraction`, `max_registration_drift_px`, `margin_in_mad_units`). They sit on the star record as plain floats with no uncertainty. Whether they need one is not decided here (question 6).

## 2. Photometry gates (11 stored as `photometry.gate_*`)

Set in `post_processing/record_quality.py` (`photometry_run_gates`, helpers `_comparison_ensemble_gate`, `_registration_drift_gate`, `_flux_uncertainty_gate`). Each failure also sets `flagged` and a `flag_reasons` sentence through `record_gate` in `models/quality/quality_summary.py`.

Readers common to all 11, none outside astrometricslib except where listed: `metrics.py`, the photometry READMEs, `scripts/backfill_quality_measurements.py`, `test_gate_conformance.py`, the generated review guide, `library_design/pipelines/photometry.md`. No hit in backend, ui or mcp_servers for any gate name. No notebook names a gate. The `flagged` flag itself is read in many places (phase 3).

| Gate | Rule and limit (register) | Other readers (tests) | Proposed group | Matching wayfindinglib rule | Replacement |
| --- | --- | --- | --- | --- | --- |
| `ensemble_frame_rejection` | rejected >= 5 frames and >= 25% (PHO-D09) | none (2 test files) | Judgment. The rejected fraction is a measurement and stays. | none | New rule; limit moves with PHO-D09 |
| `capture_timestamps` | any frame without a time fails, limit 0 | `observation_times.py`, `frame_photometry.py`, `runner.py` (3) | Self-check | none | Stays |
| `session_content` | any empty session fails | none (2) | Self-check | none | Stays |
| `session_plate_solve` | any session without a plate solve fails (2+ sessions) | none (1) | Self-check | none | Stays |
| `photometry_work` | the run had nothing to measure | config name `photometry_work` is a different thing (ui `AdvancedForm.tsx`) (3) | Self-check | none | Stays |
| `comparison_ensemble` | >= 5 comparison stars (PHO-D06); comparison scatter <= 5 times predicted (PHO-02) | `variability_analyzer.py`, `runner.py` (10) | Self-check (questions 1) | none | Stays |
| `registration_drift` | drift <= 20 px, corner <= 1 px, centroid fallback <= 20% (PHO-D16, PHO-04) | `variability_analyzer.py`, `frame_photometry.py`, `runner.py`, `assess_input_quality.py`; stacking has its own gate of this name (9) | Self-check | none | Stays |
| `scatter_population` | >= 10 stars with 3+ points, >= 20 for the noise model (PHO-D13, PHO-D10) | none (2) | Self-check in my reading; the plan's section 7 lists it as a judgment (question 1) | none | Stays, or new rule |
| `variability_discrimination` | needs >= 10 catalogued variables and >= 30 unlisted; AUC above chance and >= 0.7 (PHO-D14) | `variability_skill.py` (1) | Judgment | none | New rule; limit moves |
| `detectable_amplitude` | <= 0.3 mag peak to peak (PHO-D15) | `wayfindinglib/tasks/planning_tasks/target_report_tasks.py` reads the stored metric `photometry.gate_detectable_amplitude` (2) | Judgment | partly: deferred rule `photometry_precision` in `rules.py` (not built) | New rule; the reader keeps the number and drops the gate name |
| `flux_uncertainty` | no limit ("no limit applies") | none (2) | Measurement only; no check outcome needed | none | Median error becomes a plain measurement |

## 3. Photometry detection labels and per-star judgments

| Field | Where set; rule and limit | Readers (tests) | Proposed group | wayfindinglib rule | Replacement |
| --- | --- | --- | --- | --- | --- |
| `periodogram.verdict` (detected, possible, not_detected, insufficient_data) | `processing/periodicity_search.py`; false alarm <= 0.01 detected, <= 0.05 possible, cycles needed (PHO-11, PHO-12) | ui `starDisplayFormat.ts`, `PhotometryViewer.tsx`; wayfindinglib `star_result_rules.py`; `scripts/validate_spectral_and_period_analysis.py`; generated `backendTypes.ts` | Detection label | `period_use_result`, `period_not_detected`, `period_not_testable` read it (all design estimate, WAY-D73) | Stays, with false-alarm probability, limit and register entry in the result |
| `transit_candidate.verdict` | same file; box search | same files | Detection label | same `period_*` rules | Same |
| `verdict_checks` (`holdout_nights`, `alias_ambiguity`), `uncorrected_verdict`, `unconfirmed` | `period_checks.py`, `family_wise_correction.py`, `batch.py`; a failed check lowers the verdict one level (PHO-D19) | `visualization/layers/track_overlay.py`; wayfindinglib `star_result_rules.py` (9 test files overall for verdict names) | Detection label (checks are self-checks of the detection) | `period_failed_check`, `period_unconfirmed` | Stays |
| Variable candidate (variability score above 1, FDR 0.01) | `variability_indices.py` (PHO-06 to PHO-10, PHO-D11, PHO-D12) | counted as `photometry.variable_candidate_count`; read by `target_report_tasks.py` | Detection label | none | Stays with score and q-value |
| Long-term variable candidate (significance > 3, amplitude >= 0.02 mag) | `variability_analyzer.py` (PHO-D18) | `scripts/backfill_quality_measurements.py` | Detection label | none | Stays |

## 4. Asteroid detection gates (5 stored as `asteroid_detection.gate_*`)

Set in `pipelines/asteroid_detection/record_quality.py` (`asteroid_run_gates`) and recorded by `runner.py`. No hit in backend, ui, mcp_servers, wayfindinglib or notebooks for any of the five names.

| Gate | Rule and limit (register) | Other readers (tests) | Proposed group | wayfindinglib rule | Replacement |
| --- | --- | --- | --- | --- | --- |
| `search_frames` | frames searched >= `min_frames_for_persistence` (3; MOV-D01) | none (4) | Self-check (a search with too few frames could not find anything) | none | Stays |
| `pointing_metadata` | any frame without RA, Dec or NAXIS fails, limit 0 | `pipeline.py` sets the count (7) | Self-check | none | Stays |
| `ephemeris_cross_match` | known-asteroid database (SkyBoT) unreachable fails | `ephemeris.py`, `moving_object_config.py` (4) | Self-check | none | Stays |
| `unmatched_movers` | any confirmed mover not matched to a known body fails: "worth a manual look" | none (2) | Judgment. The count of such movers stays as a measurement. | none; deferred rule `asteroid_linking` in `rules.py` needs arc length and rate first | New rule or leave deferred (question 3) |
| `track_residuals` | no chain with an assumed position error; limit = `residual_rms_max_multiple` (2.0) times the error (MOV-03) | none (3) | Self-check of the detection method | none | Stays with the ratio, limit and MOV-03 |

## 5. Asteroid detection labels

| Field | Where set; rule | Readers (tests) | Proposed group | wayfindinglib rule | Replacement |
| --- | --- | --- | --- | --- | --- |
| `cascade_stage` (persistence, reference frame, rate linearity, ephemeris matched, rejected stages) | `detection.py`, `ephemeris.py`, `pipeline.py` (MOV-01 to MOV-03, MOV-D01 to MOV-D07) | wayfindinglib `target_report_tasks.py` counts `ephemeris_matched`; `scripts/validate_asteroid_detection.py`, `run_empirical_validation.py`; generated `backendTypes.ts` (14 test files) | Detection label | none | Stays with its stage limits and register entries |
| `ephemeris_match` | `ephemeris.py`; radius 10 arcsec (MOV-D07) | same | Detection label | none | Stays |

## 6. Shared `flagged` and `flag_reasons` (phase 3, listed so nothing is lost)

Photometry and asteroid summaries inherit them from `PipelineQualitySummaryBase`. Their only outside reader I found is `target_report_tasks.py` (builds `flagged_pipelines` for all six pipelines). Others are in the plan's phase 3 list. The stacking timeout (`pipelines/tasks.py`) is not part of these two pipelines.

## 7. Open questions for you

1. **Mixed gates.** Is `comparison_ensemble` (ensemble size and 5 times predicted scatter) a self-check of the normalization, as I propose? Is `scatter_population` a self-check (it only says the run has enough stars to define normal scatter) or a judgment, as section 7 of the quality plan says?
2. **Rejected fraction.** `ensemble_frame_rejection` rejects frames as a processing decision. Do you want the 25% limit to move to wayfindinglib, or stay with the frame rejection that makes it?
3. **`unmatched_movers`.** Move it to a new rule, or leave the advice deferred under `asteroid_linking` and only keep the count?
4. **Missing rules.** `variability_discrimination`, `detectable_amplitude` and `ensemble_frame_rejection` need a rule and a register entry each. Is a recorded choice of no rule acceptable for any of them (D3)?
5. **`detectable_amplitude` reader.** `target_report_tasks.py` reads the stored name `photometry.gate_detectable_amplitude`. Renaming the metric is a stored-data change. Keep the name, or rename and backfill (the final restack rewrites it anyway)?
6. **Uncertainty on the kept numbers.** Phase 0 says to check each kept number is stored with its uncertainty. The three star-record numbers have none. Add one, or accept them as plain numbers?
7. **Period checks as self-checks.** `verdict_checks` hold `GateResult` objects inside a detection label. Keep that form, or turn them into measurements with check outcomes like the run gates?

## 8. What I could not check

- Number of stored rows holding these fields, and whether old rows load without them (real data was not touched).
- Readers through MCP tool results: searched by name only; none found under `mcp_servers/` except generic `verdict` mentions in READMEs and manifests.
- Test counts are counts of files that mention a name, not of test functions.
- Whether `unmatched_movers` or `ensemble_frame_rejection` is the better judgment-versus-self-check call: that needs your view of intent, not code.
- The full list of `flagged` readers (`target_overview.py`, `stack_request.py` and others): not re-verified here, left to phase 3.
- Gate-to-rule matching goes beyond names only for the period rules and `photometry_precision`; I read those. Other "none" entries mean no rule name or text in the three tables (`rules.py`, `star_result_rules.py`, `session_rules.py`) matched the gate's topic.
