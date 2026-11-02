# Inventory for issue 97: spectroscopy and astrometry

Date: 2026-11-01. Status: draft. Every "Proposed group" below is a proposal for you to approve or change. No code has changed. This was read from the code of branch `97-verdict-removal`; nothing was run on real data, and no tests were run.

Sources: issue #97, `VERDICT_REMOVAL_PLAN.md` (D3, D4, phase 2) and `QUALITY_METRICS_PLAN.md` section 7. The groups are:

- **Self-check**: stays in astrometricslib as a measurement with a check outcome.
- **Judgment**: fitness for use. Moves to a wayfindinglib rule in `wayfindinglib/analytics/result_quality/`.
- **Detection label**: a science product. Stays, with its statistic, its limit and its register entry.
- **Measurement**: a yes/no verdict field becomes a number with an uncertainty (phase 2, step 4). Or it is removed.

Reader columns count source files outside tests, then test files ("tests"). A grep by name found them across the library, wayfindinglib, backend, ui, mcp_servers, scripts, notebooks and `build/`. Generated files (`ui/common/types/backendTypes.ts`, the review guides) follow the models and are not counted.

## 1. Findings that change the plan

- Wayfindinglib has **no rule for any of the 14 gates** below. Its only spectroscopy rules are per star, in `star_result_rules.py`: `spectral_type_poor_match` (SPE-16), `spectral_class_ambiguous` (SPE-18), `spectral_type_not_found`, `spectrum_use_result` and `spectral_reachability`. It has none for astrometry. I checked `rules.py`, `report_rules.py`, `session_rules.py`, `judge_results.py` and `star_result_rules.py`.
- A poor match is stored **four times**: the computed `SpectroscopyResult.is_poor_match`, the stored `OutputQualityAssessment.is_poor_match`, the checkpoint metric `poor_match`, and the run count `poor_match_classification_count`. Ambiguity is stored the same way.
- The poor-match and ambiguity limits are also used **inside the pipeline**, not only for verdicts: checkpoint 2 (`assess_processing_quality.py`, lines 331 and 348), the `spectral_classification` and `processing_quality` gates, the classifier's `match_quality` text (`spectral_classifier.py:849`) and `class_gap_calibration.py` (8 uses). This matters for D4 outcome B.
- No spectroscopy or astrometry verdict field has a reader in `backend/` or `mcp_servers/` by name. The reader of the per-star fields in ui and mcp is `starDisplayFormat.ts` and, indirectly, `star_query` (it returns `summarize_star`, `pipelines/shared/star_analysis.py`, from `star_catalog_queries.py:797`). I did not trace the MCP server itself.
- The "feature" and "emission line" detection labels store the p-value, depth and significance, but **not the limit or the register entry** (the entry dicts in `spectral_feature_detector.py` ~1024 and `emission_line_detector.py` `_entry_for`). Issue #97 requires both.
- `CatalogComparison.colour_agrees` stores only a yes/no and a note. No B-V difference is stored, so it cannot become a measurement without a new field.
- `catalog_agrees` and `gaia_xp_agreement` are listed as fitness-for-use gates in `QUALITY_METRICS_PLAN.md` section 7. My reading of the code is that they are self-checks (see questions 2).

## 2. Spectroscopy: per-star fields

All set in `pipelines/spectroscopy/` unless noted. Stored on `SpectroscopyResult` (`models/stellar_source.py`).

| Field | Where set | Rule and limit (register) | Readers (src; tests) | Proposed group | Wayfinding rule | Replacement |
| --- | --- | --- | --- | --- | --- | --- |
| `is_poor_match` | computed property `stellar_source.py:1014`; copies in `post_processing/assess_output_quality.py` | best RMS > `NO_GOOD_MATCH_RMS` 0.15 (SPE-16, partly measured) | lib 4; wayfinding 1 (`star_result_rules.py`); ui 1 (`starDisplayFormat.ts`); mcp via `star_analysis.py` (`no_good_match`); 7 tests | **D4 decides** (section 5) | `spectral_type_poor_match` exists (SPE-16) | A: stays. B: wayfinding compares RMS with its own copy of 0.15 |
| `is_ambiguous` (subtype) | computed `stellar_source.py:1075`; copy in `assess_output_quality.py` | gap to second best < `AMBIGUOUS_RMS_GAP` 0.02 (SPE-18, measured) | lib 6; ui 1; wayfinding 0 (the star rule judges only the class level); mcp via `star_analysis.py`; about 8 tests | **D4 decides** | none | A: stays. B: new subtype rule, or UI shows the gap only |
| `is_class_ambiguous` | computed `stellar_source.py:1111`; copy in `assess_output_quality.py` | gap to next class < 0.02 (SPE-18) | lib 4; wayfinding 1; ui 1; mcp via `star_analysis.py`; 6 tests | **D4 decides** | `spectral_class_ambiguous` exists (SPE-18) | as `is_poor_match` |
| `OutputQualityAssessment.is_trustworthy` | `assess_output_quality.py:203` | `not (poor or subtype-ambiguous or catalog_agrees is False)` | no reader outside its setter; checkpoint metric `trustworthy` and flag `not_trustworthy`; 6 tests (shared with photometry) | Measurement: remove (already phase 0, item 4) | none needed | Remove; the inputs stay |
| `is_subtype_finer_than_resolution` | `assess_output_quality.py:201` | always `None` (verified: the line sets `None`) | none outside setter; 2 tests | Remove (phase 0, item 4) | none | Remove |
| `OutputQualityAssessment.catalog_agrees` | copy of `CatalogComparison.spectral_type_agrees` | measured type within `DIFFERS_FROM_CATALOG_SUBTYPES` 20 steps (SPE-19) | no reader outside the checkpoint metric, the `trustworthy` input and the catalog gate counts; 3 tests | Measurement: drop the copy | none | `catalog_type_steps_apart` already exists (SPE-19) and stays |
| `CatalogComparison.spectral_type_agrees`, `colour_agrees` | `post_processing/compare_to_catalog.py` | 20 steps (SPE-19); B-V differs by > `COLOUR_DISAGREEMENT_MAGNITUDES` 0.4 (SPE-27) | lib 5 (`record_quality.py` counts for the catalog gate); no ui; 3 tests | Self-check. Keep the boolean as a check outcome | none | Add the B-V difference as a stored number (question 5) |
| `StellarObject.differs_from_catalog` | computed `stellar_source.py:1306` | same 20 steps (SPE-19) | lib 2; ui 1 (`starDisplayFormat.ts`, 8 hits); mcp via `star_analysis.py`; 2 tests | Measurement plus Judgment (question 6) | none | steps apart as the number; the UI wording reads the advisory or the number |
| `is_luminosity_class_uncertain` | `compare_to_catalog.py` | catalog says giant or supergiant; references are dwarfs only | lib only (`spectroscopy_quality.py`) | Detection label (context note), stays | none | Stays |
| "no type named" | `spectral_classifier.py` | best RMS > `UNRELIABLE_MATCH_RMS` 0.45 (SPE-17) | classifier output | Processing decision, stays | `spectral_type_not_found` | Stays |

## 3. Spectroscopy: detection labels

| Label | Where set | Rule and limit (register) | Readers (src; tests) | Proposed group | Replacement |
| --- | --- | --- | --- | --- | --- |
| Absorption feature `verdict` (detected, possible, inconclusive, not_detected, not_covered), list `probable_spectral_features` | `processing/spectral_feature_detector.py` `_verdict_for`, then noise cap and blend demotion | p <= 0.01 detected, <= 0.05 possible, depth >= 0.01; inconclusive: depth >= 0.065 and p <= 0.25; noise cap 0.11 on depth uncertainty (SPE-24) | lib 4 (`star_analysis.py`, `assess_processing_quality.py`, `pipeline.py`); ui 4 (`SpectrumViewer.tsx`, `StellarAnalysisDetails.tsx`, `starDisplayFormat.ts`, `spectralFeatureTypes.ts`); scripts 2; mcp via `star_analysis.py`; 5 tests | **Detection label, stays** | Add the limit used and the register entry (SPE-24) to each entry |
| Emission line `verdict` (detected, unclear, not_seen, not_covered), list `emission_lines` | `processing/emission_line_detector.py:430-436` | significance >= 5.0 detected, >= 3.0 unclear (SPE-25) | lib 5; ui 3 (`StellarAnalysisDetails.tsx`, `starDisplayFormat.ts`, types); scripts 2; mcp via `star_analysis.py`; 5 tests | **Detection label, stays** | Add the limit and SPE-25 to each entry |
| `is_emission_line_source` | `spectrum_analysis.py` | at least 2 detected lines (`MINIMUM_DETECTED_FOR_EMISSION_SOURCE`, SPE-25) | lib 4; ui 1 (`StellarAnalysisDetails.tsx`); scripts 2 | **Detection label, stays** | Report with the count and the limit |

## 4. Spectroscopy: run level

Gates are built in `post_processing/record_quality.py` (`spectroscopy_run_gates`), `wavelength_scale.py` and `compare_to_gaia_xp.py`. A failed gate sets `flagged` and appends to `flag_reasons` through `PipelineQualitySummaryBase.record_gate` (`models/quality/quality_summary.py:214`). Readers common to all nine: `pipelines/spectroscopy/metrics.py`, `scripts/backfill_quality_measurements.py`, `test_gate_conformance.py`, the generated review guide. Nothing in wayfindinglib, backend, ui or mcp_servers reads a `spectroscopy.gate_*` name. Tests are files that name the gate's constant.

| Gate | Rule and limit (register) | Tests | Proposed group | Wayfinding rule | Replacement |
| --- | --- | --- | --- | --- | --- |
| `spectra_extracted` | 0 spectra fails; limit 1 (geometry) | 2 | Self-check | none | Stays |
| `zero_order_saturation` | worst saturated share > 0.001 (SPE-D11, design estimate; guess, not validated) | 2 | Self-check (question 4) | none | Stays; the fraction `zero_order_saturated_pixel_fraction` stays |
| `spectral_classification` | any star poor match or class-ambiguous; limit 0 (uses SPE-16, SPE-18) | 2 | **Follows D4** | per-star rules only | A: self-check. B: run-level judgment built from the per-star rules |
| `catalog_agreement` | any star more than 20 steps from catalog (SPE-19) | 2 | Self-check (plan says Judgment; question 2) | none | Stays; steps apart is the number |
| `feature_significance` | any star's p-values not calibrated (< 20 control windows); limit 0 | 2 | Self-check | none | Stays |
| `resolution_measured` | never fails: passes if at least one spectrum had a measured resolution, else not_checked (verified) | 2 | Self-check | none | Stays |
| `processing_quality` | more than 50% of classified spectra fail a checkpoint-2 metric (SPE-D13, design estimate) | 3 | Self-check, or Judgment (question 3) | none | Stays, or new rule |
| `wavelength_scale` | RMS after correction > 11 A, one dispersion pixel (SPE-D12, design estimate) | 1 | Self-check | none | Stays |
| `gaia_xp_agreement` | median absolute tilt > 3 percent per 1000 A, needs 3 stars (SPE-28, design estimate) | 3 | Self-check (plan says Judgment; question 2) | none | Stays |

Other run-level fields (`SpectroscopyPipelineQualityMetrics` and the summary):

| Field | Where set; rule | Readers (src; tests) | Proposed group | Replacement |
| --- | --- | --- | --- | --- |
| `zero_order_saturation_flagged` | `batch.py:567`, `runner.py:479`; worst share > 0.001 (SPE-D11) | no reader outside setters, metrics and generated files; 2 tests | Measurement: remove the boolean | The fraction stays; the gate carries the outcome |
| `poor_match_classification_count`, `ambiguous_classification_count` | `runner.py`, `batch.py`; counts of the verdicts above (SPE-16, SPE-18) | script 1 (`backfill_quality_measurements.py`); lib 5; 3 tests | Follows D4 | A: stay as context counts. B: recomputed from wayfinding or dropped |
| `flagged_spectral_classifications` (list of `SpectralClassificationConcern`) | `runner.py:444-533`, `batch.py:594` | notebooks 2 (`06_...` and `09_...`); lib 3; 5 tests | Measurement: remove the list | Notebooks read each star's `self_determined_spectral_type_rms` and `rms_gap_to_next_class` |
| `flagged`, `flag_reasons` | `record_gate`; phase 3 drops them | notebooks 2 (summary status line); `scripts/run_empirical_validation.py:64`; wayfinding 1 (`target_report_tasks.py:327-329`, fills `flagged_pipelines`; I found no reader of that field outside its model and one test); 19 tests across pipelines | Removed in phase 3 | `GateResult` list on the summary and stored measurements |
| `stage_quality` checkpoints (4 stages, about 38 `metric(...)` calls with limits, `flags`, `has_failed_metric`) | `pre_processing/assess_*`, `processing/assess_processing_quality.py`, `post_processing/assess_output_quality.py` | lib 13; no reader outside astrometricslib; 12 tests | Self-check, not sorted metric by metric (question 7) | Stays |

## 5. Decision D4: `is_poor_match` and `is_ambiguous` (and `is_class_ambiguous`)

Proposal, not decision. The user has not chosen.

| | **A: detection labels (plan's recommendation)** | **B: verdicts that move to wayfindinglib** |
| --- | --- | --- |
| What stays in astrometricslib | The three computed properties, the RMS, the gaps, the limits `poor_match_rms_limit` and `ambiguous_rms_gap_limit` | The RMS and the two gaps only |
| What changes | Add the register entry (SPE-16, SPE-18) next to the limit, since the limit properties do not carry it today. Drop the stored copies (`OutputQualityAssessment` trio, `trustworthy`) and reword the docstrings that say "should not be claimed" | Remove the three properties and the two limit properties (`stellar_source.py:1014-1130`). Remove the `no_good_match`, `is_ambiguous`, `is_class_ambiguous` keys in `star_analysis.py` |
| Limits | `NO_GOOD_MATCH_RMS` and `AMBIGUOUS_RMS_GAP` stay in `stellar_source.py` | Wayfinding already keeps copies (`POOR_MATCH_RMS_LIMIT`, `AMBIGUOUS_RMS_GAP_LIMIT`) with a test that pins them to the library. That test goes. The pipeline still needs the numbers for checkpoint 2, two gates, the classifier text and `class_gap_calibration.py`. Either astrometricslib keeps the constants (the limit then lives in two places) or those checks change |
| Readers to change | None. `star_result_rules.py` and `starDisplayFormat.ts` read the same fields | `star_result_rules.py` compares RMS with its own limit. `starDisplayFormat.ts` (3 and 2 hits) reads the `advisory:result` call instead (`ui/common/queries/qualityAdvisoryQueries.ts` exists) |
| Register | SPE-16 and SPE-18 `impl` links stay | Their `impl` links move to wayfindinglib; `test_validation_register.py` must follow |
| MCP | `star_query` output unchanged | `star_query` loses three keys; agents use `review_get_advisory` |
| Tests touched | About 7 test files in astrometricslib, 1 ui, 1 wayfinding, plus `test_science_limit_values.py` | The same plus the pin test and the register test |
| Risk | Low. astrometricslib still holds a limit that reads like a judgment | Medium. Changes what the star panel shows, and the limit lives in more than one place |
| Matches the rule that astrometricslib measures | By the science-products boundary: a poor match describes the classification | By section 7 of the quality plan as first written |

## 6. Astrometry

Gates are built in `pipelines/astrometry/post_processing/record_quality.py` (`astrometry_run_gates`) and `runner.py:419-429` (plate solve). Failure sets `flagged` the same way. Readers common to all five: `pipelines/astrometry/metrics.py`, `scripts/backfill_quality_measurements.py`, `test_gate_conformance.py`, the generated review guide. Nothing in wayfindinglib, backend, ui or mcp_servers reads an `astrometry.gate_*` name. The only outside reader of the astrometry summary's `flagged` is `target_report_tasks.py:327`.

| Gate | Rule and limit (register) | Tests | Proposed group | Wayfinding rule | Replacement |
| --- | --- | --- | --- | --- | --- |
| `plate_solve` | no solution fails; no limit | 3 | Self-check | none | Stays |
| `source_detection` | fewer than 1 detected star fails (geometry) | 1 | Self-check | none | Stays |
| `catalog_matches` | fewer than 20 matched stars (`MINIMUM_MATCHED_STARS`, AST-05, design estimate) | 1 | Self-check (its code comment says the residual needs this many to mean anything; question 8) | none | Stays; matched count is already a measurement |
| `astrometric_residual` | residual over star width > 0.5 (`MAXIMUM_RESIDUAL_FRACTION_OF_FWHM`, AST-04, partly measured; it catches a failed solve) | 1 | Self-check | none | Stays; the fraction is the number |
| `catalog_lookup` | breaker tripped, or at least 50% of lookups failed (`MAXIMUM_FAILED_LOOKUP_FRACTION`, AST-D09, design estimate) | 1 | Self-check (service health) | none | Stays |

| Field | Where set; rule | Readers (src; tests) | Proposed group | Replacement |
| --- | --- | --- | --- | --- |
| `CatalogMatchQuality.is_ambiguous` (per star) | `processing/star_identifier.py:2148-2161`; another catalog entry within `UNRESOLVED_COMPANION_RADIUS_ARCSEC` 3.0; always False for Gaia (line 2278) | lib: `photometry/processing/catalog_zero_point.py:383` drops such stars from the zero point; `assess_match_quality.py`; no wayfinding, ui, backend, mcp; tests in `test_provenance_recording.py` | Measurement (a fact about the field, not a verdict). Not a spectral label despite the shared name | Stays. Optional rename to avoid the clash (question 9) |
| `plate_solve_succeeded`, `solve_attempted` | `runner.py:~402` | lib 5; script 1; 9 tests | Measurement, stays | Stays |
| `astrometry_flags` (list of keys such as `gaia_row_limit_reached`) | `star_identifier.py`; documented as "do not flag the run" | lib 6; no outside reader; 6 tests | Self-check context, stays | Stays |
| `remote_catalog_circuit_breaker_tripped` | `runner.py` from the Gaia statistics | lib; feeds `catalog_lookup` | Measurement, stays | Stays |
| `flagged`, `flag_reasons` | `record_gate` | see section 4 | Removed in phase 3 | Gate list and stored measurements |

No data-quality boolean such as `saturation_flagged` exists in astrometry. I found no astrometry field that needs to move to wayfindinglib.

## 7. Questions for you

1. **D4.** Outcome A or B for `is_poor_match`, `is_ambiguous` and `is_class_ambiguous` (section 5)? It also decides `spectral_classification` and the two run counts.
2. **`catalog_agreement` and `gaia_xp_agreement`.** Plan section 7 calls them fitness-for-use gates. I read both as self-checks: one catches a spectrum that is probably not this star's, the other points at the instrument response. Do you agree, or should they move and get rules?
3. **`processing_quality`.** Is "more than half the classified spectra fail a checkpoint" a self-check of the pipeline or a fitness call?
4. **`zero_order_saturation`.** Keep as a self-check? No wayfinding rule covers spectrum exposure, and I found none to match.
5. **Colour agreement.** Add the B-V difference (mag) as a stored number so the boolean can become a check outcome?
6. **`differs_from_catalog`.** The star panel reads it (8 hits). Replace it with the steps-apart number, or keep a computed property?
7. **Checkpoints.** Should I list the about 38 checkpoint metrics one by one, or treat the four stages as one self-check group?
8. **`catalog_matches`.** Self-check (my reading) or a rule about when a solve is good enough?
9. **Name clash.** Rename `CatalogMatchQuality.is_ambiguous` (for example `has_unresolved_companion`)? It is a stored field, so old records must still load.
10. **Detection label fields.** Adding the limit and register entry to every feature and line entry changes stored JSON and the TypeScript types. Fields to add: limit used and register id. Acceptable?
11. **Not sorted.** `possible_neighbor_contamination` and `neighbor_wing_status` (spectroscopy) look like cautions about a result. They are not in the plan's list. Include them here?

## 8. What I could not check

- Real stored data: how many records hold each field, and whether old records load without them. The model note says records before 2026-10-10 hold `is_poor_match` as `None`.
- The MCP server code. I followed `star_query` to `summarize_star` only.
- Whether the star panel already calls `advisory:result` for the spectrum badge. The query hook exists; I did not trace its use.
- Test counts are test files that name the term, not test functions. Test counts for `is_ambiguous` also include astrometry's field of the same name.
- Exact list of the checkpoint metrics with limits (about 38 call lines counted, not distinct metrics).
- The generated review guides and `Pipeline_Audit_Report.md`, which I did not read.
- No tests, lint or documentation checks were run. The `astrometricslib-core` and other MCP servers failed to connect in this session; I used the repo files only.
