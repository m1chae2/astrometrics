# Inventory for issue 97: stacking

Date: 2026-11-01. Status: draft. Every "Proposed group" below is a proposal for you to approve or change. No code has changed. Read-only checks; nothing was run on real data. Spectral stacking uses the same models and gates, so this covers both.

Sources: `VERDICT_REMOVAL_PLAN.md` (D3, phase 2), `QUALITY_METRICS_PLAN.md` section 7, issue #97.

Groups (the same four as the photometry inventory):

- **Self-check**: stays in astrometricslib as a measurement with a check outcome.
- **Processing decision**: the stacker acts on it (moves or drops frames, smooths a flat). Stays, recorded with what was done.
- **Judgment**: fitness for use. Moves to a wayfindinglib rule in `wayfindinglib/analytics/result_quality/`.
- **Measurement**: a yes/no verdict field becomes a number with its limit and uncertainty.

## 1. What phase 0 already handles

The working tree is dirty: another session is editing. I read the files as they are now.

| Item | State on this branch |
| --- | --- |
| Observing advice (`recommended_exposure_seconds`, `recommend_*exposure*`, "take more flats") | In progress, uncommitted. The model field is gone, a test shows an old row still loads, and tests assert the flat text is gone. Not mine to change. |
| `timed_out` | Not started. `pipelines/tasks.py:285-287` still sets `timed_out`, `flagged` and a `flag_reasons` line. No reader of `timed_out` outside its setter, the generated docs and one backfill test. |
| Two dead `FrameMeasurements` fields | Done (commit 8fbeb70d). |

One thing to know about the timeout: it writes to the **existing** stack summary (`target.stacking.quality_summary`). That is the previous good stack when the new one timed out. So today a timeout flags the old stack. It also touches only imaging (`target.stacking`), not the spectral summary. The measurement should be recorded on the run that timed out. Question 6.

## 2. Verdict fields

"Set" is where the value is written. Test counts are test files that name the field, by search.

| Field | Set | Rule and limit | Readers | Proposed group | wayfindinglib rule | Replacement |
| --- | --- | --- | --- | --- | --- | --- |
| `rejected_fraction_flagged` (metrics) | `stage.py:1269` | rejected >= 15% (`stack_quality.py`, STK-24) | `assess_output_quality.py`, `stack_quality_report.py:194`, UI panel, 5 test files | Measurement | none | Number stays; judgment is gate `rejected_pixel_fraction` (section 3) |
| `saturation_flagged` | `stage.py:1276` | saturated >= 0.1% (`saturation.py:89`, "a guess so far") | `assess_output_quality.py`, 5 tests | Measurement | `overexposed_setting` (WAY-D05) reads the star peak, not this | Number stays |
| `zero_fraction_flagged` | `calibration_health.py:68` | zero >= 50% image, 5% spectrum (STK-26) | `assess_output_quality.py`, 5 tests | Measurement | none | Number stays; gate is a self-check |
| `negative_pixels_flagged` | `calibration_health.py:74` | Siril negative warning >= 50% (STK-26) | `assess_output_quality.py`, 5 tests | Measurement | none | Number stays |
| `fwhm_degraded` | `fwhm_sampling.py:169` | stack FWHM / expected >= 1.2 (STK-25, "sensitivity unvalidated") | `assess_output_quality.py`, `stack_quality_report.py:199` (`star_width_px.degraded`), UI panel, 5 tests | Measurement | none | Ratio stays |
| `spectral_registration_flags` | `registration_records.py:165` | zero-order star check; any frame fails | `assess_output_quality.py`, 4 tests | Self-check (list of frames) | none | Count stays |
| `calibration_mismatch_flags`, `flat_calibration_issues` | `stage.py:1238-1241` | sentences from the stacker | `assess_input_quality.py`, `StackSummary.calibration_mismatches`, UI panel (count), 7 and 2 tests | Self-check (sentences) | `calibration_gap` (WAY-D63) | Sentences stay as detail of the gate |
| `background_split_detected`, `_detail` | `stage.py` from `frame_screening.py` | gap ratio >= 4 (STK-04) | `assess_input_quality.py`, 3 tests | Processing decision | none | Stays, with frames dropped |
| `exposure_groups[].saturated`, `.clipped_at_zero`, `.gain_nonlinear`, `.left_out_reason` | `exposure_group_report.py` | 20% zero; gain tolerance 5% (STK-20, STK-22) | `summarize_stack`, UI group table | Processing decision | `offset_too_low` (STK-22) | Stays; UI table unchanged |
| `StackingInputQuality.is_flagged`, `.flag_reasons` | `assess_input_quality.py` | any reason | `_finalize_stack_quality_flags`; no other reader found | Remove (phase 3) | n/a | Gates already hold each reason |
| `StackingOutputQuality.is_flagged`, `.flag_reasons` | `assess_output_quality.py` | any failed output gate | same | Remove (phase 3) | n/a | Gates |
| `flagged`, `flag_reasons` (every summary) | `stage.py:_finalize_stack_quality_flags`, `record_gate`, `tasks.py:286`, `applied_camera_profile.py:123` | any failed gate, any extra reason | see section 5 | Remove (phase 3), after readers move | `needs_attention` count | Statements |
| `gates` (list on the summary) | `record_gate` | all 25 | `record_quality.py:930` stores one row each | Stays (the self-check record) | n/a | Stays |

Extra flag reasons that are **not** gates (so they vanish with `flag_reasons` unless given a home): "N frame(s) with clouds or trailed stars moved to the _excluded folder"; "single-frame stack: no rejection/registration quality processing applied"; "the X s exposure group ..." for a group left out for a reason other than nonlinearity; "camera 'X' has no camera profile" (any pipeline); "stacking timed out". Question 4.

## 3. The 25 gates

All are set in `stage.py` (`record_gate`, lines 1310-1411) from the builders named in `metrics.py` (`_GATE_METRICS`). All 25 are stored as `stacking.gate_*` rows by `record_quality.py`. Readers common to all 25: `metrics.py`, `record_quality.py` (limit sources), `scripts/backfill_quality_measurements.py`, `test_gate_conformance.py`, `scripts/report_gate_limits.py`, `wayfindinglib/scripts/shadow_compare_verdicts.py`, the generated review guide and `library_design/pipelines/stacking.md`. No hit in backend, mcp_servers or notebooks for any gate name. The UI has no reader of a stack gate either (Section 5, row UI3).

"Tests" counts test files that name the gate (gate name, metric or constant), as a search, including `test_gate_conformance.py`.

| Gate | Rule and limit | Tests | Proposed group | wayfindinglib rule | Replacement |
| --- | --- | --- | --- | --- | --- |
| `gain_homogeneity` | Fails never; not checked if no frame records gain. Minority gain set aside | 1 | Processing decision | none | Stays |
| `frame_quarantine` | Over cap (25% of a session, 8 frames to judge, STK-03) fails, none moved | 2 | Processing decision | `frames_lost` (WAY-D60) reads frame metrics, not this gate | Stays |
| `background_homogeneity` | Gap ratio 4, 3 frames (STK-04). Fail: split found, frames dropped | 1 | Processing decision | none | Stays |
| `flat_level` | Level outside 10% to 90% of full scale (design estimate) | 3 | Self-check | none | Stays. Shared with the Calibration Library check (`calibration_assessment.py:1103`) |
| `flat_noise` | Master flat noise > 0.5% (STK-05) | 6 | Processing decision (the stacker smooths on the same limit) | `master_flat_noisy` (STK-05) reads `stacking.flat_noise_fraction` | Rule exists; gate stays |
| `calibration_metadata` | Any mismatch in gain, offset, temperature, binning; limit 0 | 4 | Self-check | `calibration_gap` (WAY-D63), partly | Stays |
| `calibration_frame_count` | A master built from fewer than 3 frames, or thin (<10 different) or copies (STK-53) | 7 | Self-check | `retake_bad_calibration_set` (WAY-D95) reads it | Stays; rule exists |
| `dark_level` | Dark vs bias level > 2 steps (STK-51) | 4 | Self-check | `retake_bad_calibration_set` reads it | Stays |
| `dark_noise` | Dark noise / read noise < 0.9 (STK-49) | 4 | Self-check | same | Stays |
| `dark_zero_pixel_fraction` | > 0.1% zero (STK-47) | 5 | Self-check | same | Stays |
| `dark_light_leak` | Block range excess > 2 steps (STK-50) | 5 | Self-check | same | Stays |
| `bias_level` | Bias vs profile > 2 steps (STK-48) | 4 | Self-check | same | Stays |
| `bias_noise` | Noise / read noise outside 0.9 to 1.1 (STK-49) | 5 | Self-check | same | Stays |
| `bias_zero_pixel_fraction` | > 0.1% zero (STK-47) | 3 | Self-check | same | Stays |
| `dark_missing` | Any light with no dark within 0.1 s; limit 0 | 3 | Self-check | none (`calibration_gap` is about the library, not this stack) | Stays; question 3 |
| `light_temperature_spread` | Lights of one exposure span > 3 C (STK-55) | 1 | Self-check | none | Stays; question 3 |
| `exposure_group_linearity` | Group ratio off by > 5% (STK-20, "design choice, not validated") | 3 | Processing decision (group left out) | none | Stays |
| `exposure_groups_combined` | Any group with frames left out; limit 0 | 3 | Self-check (completeness) | none | Stays |
| `raw_frame_clipping` | > 20% raw pixels at zero (STK-22) | 3 | Self-check | `offset_too_low` (STK-22) reads it | Stays; rule exists |
| `rejected_pixel_fraction` | >= 15% (STK-24) | 3 | **Judgment** | none (D3 lists it) | New rule `stack_rejected_fraction_high`; limit moves with STK-24 |
| `stack_sharpness` | Stack FWHM >= 1.2 x expected (STK-25) | 3 | **Judgment** | none (`sharpness_tradeoff` is a different thing) | New rule; limit moves with STK-25 |
| `spectral_registration` | Any frame with a registration concern; limit 0 | 2 | Self-check | none | Stays; question 5 |
| `saturated_pixel_fraction` | >= 0.1% (no register entry; unvalidated) | 6 | **Judgment** | `overexposed_setting` (WAY-D05) covers the cause, not this reading | Question 2: new rule or none |
| `zero_pixel_fraction` | >= 50% image, 5% spectrum (STK-26) | 3 | Self-check (a blank stack is a broken run) | none | Stays |
| `negative_pixels` | Siril warning >= 50% (STK-26) | 5 | Self-check | none | Stays |

Count: 3 judgments, 4 processing decisions (with `flat_noise`), 18 self-checks. Only two rules read a stack gate today (`offset_too_low`, `retake_bad_calibration_set`), so for 17 of the 25 the shadow script will print "gate failed, no rule reads it". A self-check needs no rule to stay.

## 4. What the gates change when `flagged` goes

Today a failed gate flags the stack and its sentence goes into `flag_reasons` (`record_gate`). After the change a gate keeps its outcome row and nothing else. The self-check rows are already stored. What disappears is the roll-up: "this stack is flagged" and the list of sentences.

## 5. Readers of the stack verdicts

| Id | Reader | Reads | Today | After (proposal) |
| --- | --- | --- | --- | --- |
| L1 | `stack_request.py:255` (`StackResult`) | `flagged`, `flag_reasons` | `processing.stack` returns them; MCP `processing_stack` passes the result on | Drop both fields; the result keeps `quality_summary` |
| L2 | `stack_quality_report.py:173-221` (`StackSummary`, via `processing.stack_summary`, RPC `processing:stack_summary`, MCP `processing_stack_summary`) | `flagged`, `flag_reasons`, `rejected_fraction_flagged`, `star_width_px.degraded`, group fields | the short answer lists flags | Fields replaced by numbers plus limits; see UI1 |
| L3 | `target_overview.py:68` | `flagged` | `targets.query` target detail shows `stack_flagged` | Drop key. No reader of the key found. MCP pass-through not checked |
| L4 | `wayfindinglib/tasks/planning_tasks/target_report_tasks.py:332` | `flagged` (all 6 pipelines) | `flagged_pipelines` in the target report | Remove (phase 3). The type `flaggedPipelines` exists in `backendTypes.ts`; no UI reader found |
| L5 | `wayfindinglib/scripts/shadow_compare_verdicts.py` | `flagged`, `flag_reasons`, gates | the comparison | Stays as the baseline until phase 3 |
| L6 | `scripts/run_empirical_validation.py:258` | `flagged`, `flag_reasons` of the stack | prints them | Print gate outcomes instead |
| L7 | `scripts/backfill_quality_measurements.py` | gates; `timed_out` listed as "a verdict" (test) | backfills rows | Update lists |
| L8 | `build/documentation/generate_pipeline_review_docs.py` | field descriptions (6 for the *_flagged group, `timed_out`, `flagged`) | generated guides | Edit the generator, regenerate |
| UI1 | `ui/imageProcessingDisplay/components/StackSummaryPanel.tsx` | `flagged`, `flag_reasons`, `rejected_fraction_flagged`, `star_width_px.degraded`, `calibration_mismatches`, group `saturated`, `clipped_at_zero`, `left_out_reason` | see below | see below |
| UI2 | `ui/imageProcessingDisplay/utils/stackFlagMeaning.ts` | the sentence text, by 15 regexes | titles and plain explanations | Delete with the flag list |
| UI3 | `ui/imageProcessingDisplay/fileBrowser/FileBrowser.tsx` | **not a stacking reader** | "Rejected" comes from `analysisResults.rejectedFiles` (the photometry 3-sigma frame clip, `variability_analyzer.py:1434`); "Outlier" is a local right-click toggle | No change. Correct the plan |
| UI4 | `ui/observatoryManager/calibration/calibrationHealth.tsx` | Same check names (`dark_level`, `dark_noise`, ...) | Reads `calibration:assess` (per set), not a stack | No change |
| M1 | `mcp_servers` | `processing_stack`, `processing_stack_summary` return L1 and L2 as-is (manifest and README only; no code reads the fields) | AI sees the flags | AI sees numbers and the rule statements |
| N1 | Notebooks | none for stacking. 06 and 09 read **spectroscopy** `flagged`; 03 and 09 read only `stacked_path` | | No stacking edit |
| B1 | `backend/` | none found for `flagged` or a gate | | None |

### What the desktop user sees in `StackSummaryPanel` today

Image Processing view, under each stacked image (imaging and spectral). A closed "details" block, title: `Imaging summary: 40 of 42 frames, 2 flags, made <date>` (`describeFlagCount`). Inside:

- "Pixels rejected: 16.1% (flagged: more than expected)" when the 15% limit is passed.
- "Star width (FWHM): 3.4 px in the stack, against 2.7 expected from the input frames ... (flagged: wider than expected)" when the ratio is 1.2 or more.
- "Calibration mismatches: N" (count only).
- A "Flags:" line. With flags: "The stacking step raised N flags. A flag is a warning ...", then one item per sentence, with a plain title, an explanation and "Show the raw flag". Without: "None raised." A flag with no reason: "Flagged (no reason recorded)".
- A table of exposure groups with "Clipped" and "Left out" columns.

I ran the 15 patterns of `stackFlagMeaning.ts` against sentences the gates write. Four are wrong or raw today:

- The raw-clipping sentence ("raw frames clipped at zero in the 1 s exposure group(s) ...") is shown as **"Exposure group left out"**, which is wrong: the group is not left out.
- The camera-profile sentence, the timeout sentence and the quarantine over-cap sentence show as raw text.
- The dark and bias gate sentences are probably raw too (one sample tested).

### What the user would see after (proposal)

- Title: `Imaging summary: 40 of 42 frames, made <date>`, with no flag count. A count of rule statements could replace it.
- "Pixels rejected: 16.1% (limit 15%, register STK-24)" as a plain number with its limit. Same for star width (ratio 1.26, limit 1.2). No "flagged" word.
- The "Flags" block becomes a list of statements from the wayfindinglib rules (the common `StatementList` in `ui/common/components/quality/`), plus a short "Checks" list of self-check outcomes (passed, failed, not checked). The group table stays.
- Loss until the new rules exist: no warning for high rejection, wide stars or saturation. Today those warn. A rule for rejection and sharpness is part of this proposal; saturation is question 2.
- Work needed: the Image Processing view has no statement list yet (it is used only in the Calibration Library, Camera Profiles and the star detail panel). The panel needs a read of the stack's statements; `advisory:target_report` exists but I did not check that it carries the stack rules' output per stack.

## 6. Phase 2 step 4 fields

Of the nine named in phase 2 step 4, stacking owns five: `saturation_flagged`, `rejected_fraction_flagged`, `zero_fraction_flagged`, `negative_pixels_flagged`, `fwhm_degraded`. Each already has its number beside it (`saturated_pixel_fraction`, `rejected_pixel_fraction`, `zero_pixel_fraction`, `negative_pixel_max_percent`, `stacked_fwhm_px` with `expected_stack_fwhm_px`). None of these numbers carries an uncertainty. The fraction numbers are counts over millions of pixels, so their sampling error is tiny; the FWHM ratio does have a spread (the sample of frames). Question 7.

## 7. Open questions for you

1. **Judgments.** Do you agree only `rejected_pixel_fraction`, `stack_sharpness` and `saturated_pixel_fraction` move? Every other gate stays as a self-check or a processing decision.
2. **Saturated pixels.** Its 0.1% limit has no register entry and the code calls it a guess. Write a new rule with a design-estimate entry, or record "no rule" and show only the number?
3. **`dark_missing` and `light_temperature_spread`.** No rule reads them. Add a rule (for example "take darks at these exposures"), or leave them as self-check rows only?
4. **Reasons that are not gates** (quarantine note, single-frame, group left out, camera without a profile, timeout). Each needs a home, or you accept that the line is gone. My proposal: quarantine and single-frame become numbers already stored; the camera-profile line stays visible as an applied-profile field (it exists on the summary); timeout becomes the measurement.
5. **`spectral_registration`.** Self-check (my proposal) or judgment? It decides whether spectra can be used, which looks like fitness for use, but it measures zero-order tracking.
6. **Timeout.** Record it on the run that timed out (a new row), not on the previous summary. Agree? The new row needs a place when the run made no summary.
7. **Uncertainties** (section 6). Required for these five before the booleans go, or the number alone is enough?
8. **UI.** Replace the flag list with a statement list and a checks list (section 5)? Or keep a flag-style summary line fed by the rules' warning count?
9. **Order.** Add the two new rules and the UI read first; remove `flagged` only at phase 3, as the plan says?

## 8. What I could not check

- Stored rows: how many hold these fields, and whether old rows load without them (the plan's own open point).
- That `advisory:target_report` or `advisory:result` returns the stack rules' statements in a form `StackSummaryPanel` could show.
- Whether MCP tools pass `stack_flagged` (L3) or `flagged_pipelines` through; the search was by name.
- The sentence text of every dark and bias gate against the UI patterns (one sample per kind only).
- Test counts are by search for names; a test that reaches a field through a fixture is not counted.
- Whether `saturated_pixel_fraction` and `zero_pixel_fraction` gate names collide with spectroscopy or frame tests (the 6 tests found include unrelated frame-statistics files).
- Concurrent edits: the working tree changed during my reading (phase 0 advice removal). Line numbers may move.
