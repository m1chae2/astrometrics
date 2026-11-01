# Plan for the rest of issue 97: verdicts, flags and script-user cleanup

Date: 2026-11-01. Status: draft, waiting for the decisions in section 2. No code has changed.

The inventory behind this plan came from a code search of `134-integration`, not from running anything. Section 7 lists what it could not determine.

## 1. Scope

Issue #97 now covers what the public API restructure did not. Issues #95 and #96 are closed, so the wayfindinglib rules and the displays this work waits on exist.

| Work | Size | Risk |
| --- | --- | --- |
| A. Small removals: dead fields, observing advice, stacking timeout | Small | Low |
| B. Verdict fields no outside code reads | Small | Low |
| C. Verdict fields with readers, and `flagged` and `flag_reasons` | Large | Changes what the app shows |
| D. Night analysis flags in wayfindinglib | Medium | Low; no reader outside wayfindinglib was found |
| E. Duplicate stored values and `output_metrics` | Medium | Stored data |
| F. Notebook import check, simpler interactions, generated API pages | Medium | Low, except typed stage results |

Detection labels stay in astrometricslib (issue #97, user decision of 2026-10-24): the periodogram, transit, spectral feature and emission-line `verdict`, each reported with its statistic, its limit and its register entry.

### Requirement: no warning is lost (user, 2026-11-01)

Every warning a job detects today stays detected, and a user can ask what warnings a given job detected. This limits the rest of the plan.

- **Where a warning lives.** The measurement table already fits: each row has `check_outcome`, `reason`, the limit and its register entry, and `job_id`. A warning is a row with a failed check outcome.
- **Conditions with no row today.** Flag reasons that are not gates must become rows before `flag_reasons` goes: the quarantine note, the single-frame note, a group left out for a non-gate reason, the camera with no profile, and the stacking timeout. The inventory lists these. The baseline comparison found 60 flag reasons with no failed gate.
- **The read.** `jobs.query(job_id=..., detail="warnings")` lists them, with the same data available to the backend (RPC method `jobs:query`) and the MCP tool `jobs_query`. Built in phase 1b (2026-11-01). A warning is a stored row whose check outcome is `failed`; a row with no job id belongs to no job (backfilled rows, and calls outside any job).
- **Order.** The read and the new rows are built before any flag is removed. Phase 3 starts only when the comparison script shows that every warning on the copy's targets is still reported.

## 2. Decisions needed

### D1. Which pull request

**Decided by the user, 2026-11-01:** everything stays on one branch, on #135.

### D2. When the final restack runs

Stored summaries are rewritten by a restack, so a restack before this work stores fields that are then dropped. Old rows still load: the models do not forbid extra fields, so Pydantic ignores removed ones. This was read from the model configuration, not tested on stored rows.

**Decided by the user, 2026-11-01:** every update in this plan is made before the next restack. The one restack runs after phase 4, so the stored data has the final shape.

### D3. What happens to the gates

Stored gates (55 `*.gate_*` metric names; stacking 25, photometry 11, spectroscopy 9, astrometry 5, asteroid detection 5) mix two things: self-checks of the measurement, and judgments of fitness for use.

**Decided by the user, 2026-11-01:** all gates and measurements belong in astrometricslib. Each gate stays a measurement with a check outcome, and a job can always list its warnings. wayfindinglib rules only turn outcomes into suggestions of what to do next. `QUALITY_METRICS_PLAN.md` decision 1 needs updating to match, and the inventories' proposals to move gates to wayfindinglib no longer apply.

Whichever is chosen, some gates have no matching rule by name today: stack sharpness, rejected fraction, `scatter_population`, `variability_discrimination`, `catalog_agreement` and `gaia_xp_agreement`. Each needs a new rule or a recorded choice to have none.

### D4. Spectral `is_poor_match` and `is_ambiguous`

These are computed properties on `SpectroscopyResult`, with limits in astrometricslib (`NO_GOOD_MATCH_RMS`, `AMBIGUOUS_RMS_GAP`). wayfindinglib's star rules and the star display read them.

**Decided by the user, 2026-11-01:** these are verdicts, and astrometricslib makes no verdicts. They move to wayfindinglib. The rms and rms gap stay as measurements.

### D5. `FrameRecord.measurements`

Each frame's quality is stored in `FrameMeasurements` and in the measurement table.

**Decided by the user, 2026-11-01:** keep it. A field check found most of it is not in the measurement table: eight registration fields have no rows, and only background level and saturated fraction are true duplicates. Retiring it would add about nine metrics and a one-time copy on the real library, for little gain.

### D6. `processing_jobs.output_metrics`

The stack review screens read `outputMetrics.result` (`ui/common/services/imaging/stackReviewService.ts`).

**Decided by the user, 2026-11-01:** keep the column and drop only the repeated quality values, because preview remake and stack comparison results exist nowhere else.

### D7. Typed stage results

`ProcessTargetResult.results` is `dict[str, Any]`, which breaks the rule that a call returns a model. Typing it is the largest gain for script users, and it touches every stage's result.

**Decided by the user, 2026-11-01:** declined. It would change every stage's return value and every caller, and the user is driving complexity down. `process_target` stays a known exception to the rule that a call returns a model.

**Guiding principle (user, 2026-11-01):** drive complexity down, and keep every interpretation of good or bad in wayfindinglib.

### Answers to the inventory questions (user, 2026-11-01)

These replace the proposals in the three `inventory_97_*.md` files wherever the two differ.

- **Stack panel:** one list of job warnings (number, limit, outcome), plus suggestions from the existing wayfindinglib display. No count in the title.
- **Timeout:** recorded on the run that timed out, not the earlier stack.
- **New rules:** rejected pixels and stack sharpness. A rule for `is_ambiguous`, which covers the 83 stars the comparison found. A missing-darks rule only if `calibration_gap` does not already cover it.
- **Warning only, no rule:** saturated pixels (with a Design estimate register entry), temperature spread, `variability_discrimination`, `detectable_amplitude`, `ensemble_frame_rejection`, `unmatched_movers`.
- **Removed with no uncertainty required:** all yes/no quality fields, including photometry `is_low_coverage`, `is_tracking_unstable`, `is_low_confidence` and `is_trustworthy`, and spectroscopy `is_trustworthy` with its checkpoint metric.
- **Spectroscopy:** store the B-V difference and remove `colour_agrees`. Replace `differs_from_catalog` and `spectral_type_agrees` with the steps-apart number. Remove the classifier's `match_quality` text. Gates compare the rms with its limit as a measurement check.
- **Kept as is:** the name `photometry.gate_detectable_amplitude`, and `CatalogMatchQuality.is_ambiguous`.
- **Detection labels:** stay, with the limit and register entry stored once per spectrum. Issue #97 is amended to match. Period checks keep their form.
- **Cautions:** `possible_neighbor_contamination` and `neighbor_wing_status` stay as measurements and appear in job warnings.
- **Checkpoints:** the spectral checkpoints count as one self-check group.

### Spectrum star limit (user, 2026-11-01)

Spectroscopy analyzes the 10 brightest stars by default (`DEFAULT_SPECTRUM_STAR_LIMIT`), until a measured feature-detection limit decides which stars are worth analyzing. This reverses the all-stars default of 2026-10-13 for now. The measurement is injection-recovery into real faint spectra, reported as a curve per feature strength.

The user approved adopting the measured curves in place of this stopgap once they are ready. The full-pipeline profile runs after all updates are done, and the real hardware check runs when the user chooses.

Before that profile, review whether the asteroid search is efficient, for example whether it needs every frame when looking for slow-moving objects (user, 2026-11-01).

### Which stars get a spectrum (user, 2026-11-01)

Measured: below an SNR of about 7 per resolution element no realistic feature is identified. Zero-order brightness does not predict that SNR tightly enough to skip stars (scatter about 0.4 dex). So: skip stars whose trail is off the frame and stars with no catalog identity before extraction, keeping the latter as neighbors for wing subtraction; stop analysis below SNR 7; keep the top-10 limit, applied after the filters, until wing subtraction is fast in crowded fields.

### Other issues folded into this branch (user, 2026-11-01)

#83, #85, #92 and #133 are fixed here, so #135 closes them along with #93, #97 and #134. #85: wire safety limits and guiding, add session stop if the backend has a call, pass alignment exposure if accepted, remove custom stacking parameters and the timing point dropdown, and label dither settings as mosaic-only. #92: items 1 to 3 are fixed here, and items 4 and 5 go to a small follow-up issue. #133 runs last. The hardware-facing controls join the user's hardware check.

## 3. Phases

Each phase is one or more commits. Sonnet agents make the edits and scripts handle mechanical ones. CI runs the full suites. A pipeline's inventory goes in `documentation/plans/` and you approve it before that pipeline's code changes.

### Phase 0: small removals (A and B)

No app behavior changes.

1. Remove `registration_fwhm_y_px` and `registration_rmse` from `FrameMeasurements`, and their mentions in `scripts/backfill_quality_measurements.py`, `scripts/README.md`, the comment in `registration_records.py` and the tests. Issue #97 lists `stage.py` as a reader; it no longer is.
2. Remove `recommend_exposure_seconds`, `recommend_stack_exposure_seconds`, `recommended_exposure_for_groups`, `recommended_exposure_seconds` on the stacking metrics, and "take more flats" in `flat_calibration.py` and `stacking/metrics.py`. `recommended_exposure_for_groups` has four call sites in `stack_runner.py`. The guide exposure ladder in wayfindinglib stays.
3. Record the stacking timeout as a processing-decision measurement (`pipelines/tasks.py`). `timed_out` has no reader outside its setter.
4. Remove the verdict fields with no reader outside tests: photometry `is_low_coverage`, `is_tracking_unstable`, `is_low_confidence`, `is_trustworthy`; spectroscopy `is_trustworthy` and `is_subtype_finer_than_resolution` (always empty). Keep the numbers they rest on. Check first that each number is stored with its uncertainty.
5. Correct `QUALITY_METRICS_PLAN.md` sections 7 and 8: add the detection label row; remove `quality_advisory_tasks.py`, `TargetQualityAdvisory` and `science_recommended_exposure_seconds`, which no longer exist.

### Phase 1: the shadow script

A read-only script runs the wayfindinglib rules next to today's flags for every target, on the data copy, and lists each difference. You mark each difference as a bug fixed or an approved change. The first run is the baseline for phase 2.

### Phase 1b: the job warnings read

Built 2026-11-01: `jobs.query(detail="warnings")` from the measurement table. Record every non-gate flag reason as a row. Fill `job_id` wherever a job made the row. Extend the comparison script to check that every flag reason appears in its job's warnings.

### Phase 2: one pipeline at a time

In the order set by issue #97: photometry, spectroscopy, astrometry, stacking, asteroid detection. For each:

1. Write the inventory, sorted by D3 and D4, and get your approval.
2. Add any missing wayfindinglib rule, with its register entry.
3. Move readers to the judgments, then run the shadow script.
4. Turn remaining data-quality booleans into measurements with uncertainties: `saturation_flagged`, `rejected_fraction_flagged`, `zero_fraction_flagged`, `negative_pixels_flagged`, `fwhm_degraded`, `catalog_agrees`, `spectral_type_agrees`, `colour_agrees`.
5. Split `test_gate_conformance.py` between self-checks and rules.
6. Run the M 13 golden suite and the one-target check on the copy. Science numbers must not change.

Issue #97's note that `starDisplayFormat.ts` is the only desktop-app reader is out of date. `StackSummaryPanel.tsx` and `stackFlagMeaning.ts` read the flags (`FileBrowser.tsx` does not; it is not a stacking flag reader), and stacking is where the user will see the most change.

### Phase 3: drop `flagged` and `flag_reasons`

1. In astrometricslib: `PipelineQualitySummaryBase`, `StackingInputQuality`, `StackingOutputQuality`, `StackSummary` and `processing_results.py`. Readers to move first: `target_overview.py`, `stack_request.py`, `target_report_tasks.py`, `run_empirical_validation.py`, notebooks 06 and 09.
2. In wayfindinglib night analysis: `sky_quality`, `session_quality`, `capture_quality`, `night_quality`, and `night_analysis.py` and `night_quality_task.py`, moved to `TargetStatement`. `RequestCount.flagged` is a different field and stays.
3. Trim `output_metrics` by D6, and retire the repeated values in the JSON summaries.
4. Search everything, including notebooks, for each removed name. Regenerate `backendTypes.ts` with `build/codegen/generate-types.sh`.

### Phase 4: script users and API pages

1. Add the static notebook import check beside `check_notebook_outputs.py`.
2. Notebook edits: `targets.create`, drop the manual path filter, one filter spelling, the corrected text in notebooks 01, 03 and 09.
3. A linked read for a star's spectrum in place of the `::spectroscopy` id, and a short read for a target's stars.
4. Typed stage results: declined (D7).
5. Finish generated-only API pages. Issue #97's items need rewriting first: they name the dropped `astrometricslib.api` tier, and the pages now have a "Result fields" section and hand-written text this work should check.
6. Re-run every changed notebook with `build/linux/execute_notebooks.sh`, on a separate empty library.

### Phase 5: the final restack

Run only with the user's go-ahead (D2). Start with the frame-move preview and the folded group count that were skipped on 2026-11-01, and use code that matches the final branch.

## 4. Order and what can run at once

Phase 0, then 1, then 2 are in order. Within phase 2, pipelines run one after another, because each changes shared models and the generated types. Phase 3 needs all of phase 2. Phase 4 items 1 to 3 can run at any time, and items 4 to 6 come after phase 3.

## 5. Safeguards

- Nothing is removed before its replacement exists and its readers have moved (`QUALITY_METRICS_PLAN.md` section 8).
- Real-data checks run on the copy, one target by default.
- The real library changes only at the restack in phase 5, with your go-ahead.
- A change to a tuned value or a method updates its validation register entry.

## 6. Change control board

The plan changes what the app shows and moves limits between the libraries, so a board review of this document is worthwhile once D1 to D7 are settled.

## 7. Not determined by the inventory

- How many stored rows hold these fields, and whether old rows load without them on real data.
- Whether night analysis flags are stored anywhere; only request-time construction was found.
- Which MCP tools pass these models through in their results; the search was by name.
- The mapping of each gate to a rule, beyond matching names.
- How much work each pipeline is. Phase sizes in section 1 are estimates.
