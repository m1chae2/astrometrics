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

### WAY-05

**The rule table and its conformance checks**

- **Claim:** Every warning, recommendation and constraint the library makes about a target's data has one row in the rule table. The row names the metrics it reads, each at one definition version, one action class, its limits, and the register entry behind it. A rule's confidence equals the status label of that entry, and a rule never reads an exploratory metric.
- **Settings it supports:** None. The entry backs the structure of the table, not a tuned value. The tuned values are the design estimates WAY-D05 to WAY-D09 and WAY-D60 to WAY-D68. The conformance test walks `ALL_RULES` <!-- impl: wayfindinglib.analytics.result_quality.all_rules.ALL_RULES -->, which joins the data-quality table and the per-star result table (WAY-73, WAY-D73), and also scans the modules named in `computed_by` for exploratory metric names.
- **Status:** Partly measured. The structural checks run on every test run. No real night has been judged with the table yet.
- **Data you need:** None for the structure. To judge a real target you need its stored quality measurements and its capture settings.
- **Procedure:**
  1. Read the rule table and the rules that are deferred.
  2. Run the conformance test, which compares every rule with the metric registry and this register.
  3. Judge a target's stored measurements.

  ```python
  from astrometricslib import Astrometrics
  from wayfindinglib.analytics.result_quality.judge_results import judge_results, needs_attention

  astro = Astrometrics()
  rows = astro.quality.query(target_id="M 57")
  settings = astro.quality.capture_settings()
  statements = judge_results(rows, capture_settings=settings)
  for statement in statements:
      print(statement.kind.value, statement.rule, statement.message)
  print(needs_attention(statements))
  ```

  `RESULT_QUALITY_RULES` <!-- impl: wayfindinglib.analytics.result_quality.rules.RESULT_QUALITY_RULES --> is the table, `DEFERRED_RULES` <!-- impl: wayfindinglib.analytics.result_quality.rules.DEFERRED_RULES --> lists the catalog items that wait for a metric, and `judge_results` <!-- impl: wayfindinglib.analytics.result_quality.judge_results.judge_results --> judges the plain rules.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether each statement cites the rows it used, whether a metric that is missing or measured under another definition version gives a statement that says so, and whether a rule's confidence matches the status of its entry.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The checks prove the table is consistent with the registry and this register, not that a rule is right. Stack rows carry no capture setting today, so the caller must say which stack belongs to which setting. Rules made by other modules (noise growth, efficiency, the guide exposure ladder, the target report) have their rows here but are checked by the entries of those modules.
- **To tune for your setup:** Nothing to tune in the table itself. To change a limit, change its design-estimate row first, then the constant it names.
- **Regenerate with:** The example above.
- **Guarded by tests:** `wayfindinglib/test/test_quality_rule_conformance.py` checks the table against the registry and this register, and `wayfindinglib/analytics/result_quality/test/test_judge_results.py` checks each plain rule at its limit.
- **Raw record:** None kept.

### WAY-20

**Efficiency: the factors, the goal exponents and the loss split**

- **Claim:** The efficiency of a frame is the product of a seeing factor, a sky-brightness factor and a read-noise factor, each capped at 1. The seeing factor is the reference star width over the frame's effective star width, raised to 2 (faint point source) or 4 (astrometric precision, sky-limited; 2 otherwise). The sky and read-noise factors come from the exact form B0 / (B + RN^2 / t). Transparency is not included. The lost time is split among the causes by the logarithms of the factors, so the shares add up to the kept-frame deficit exactly.
- **Settings it supports:**
  - code constant `SEEING_EXPONENT_POINT_SOURCE` = 2 in `analytics/result_quality/efficiency.py`. Relation: derived from this entry. <!-- impl: wayfindinglib.analytics.result_quality.efficiency.SEEING_EXPONENT_POINT_SOURCE -->
  - code constant `SEEING_EXPONENT_ASTROMETRY_SKY_LIMITED` = 4 in the same file. Relation: derived from this entry. <!-- impl: wayfindinglib.analytics.result_quality.efficiency.SEEING_EXPONENT_ASTROMETRY_SKY_LIMITED -->
  - code constant `DEPTH_MAGNITUDES_PER_LOG_TIME` = 1.25 in the same file. Relation: derived from this entry. <!-- impl: wayfindinglib.analytics.result_quality.efficiency.DEPTH_MAGNITUDES_PER_LOG_TIME -->
- **Status:** Partly measured. The formulas and the adding-up of the split are tested on made-up frames. No real night has been judged with them yet. The effective star width they read is model-derived and reads 3 to 6 percent low for stars with wings (AST-18).
- **Data you need:** Frames of one capture setting with the registered frame measurements (`frame.star_width_effective_arcsec`, `frame.sky_rate_e_per_s_per_px`, `frame.sky_noise_e`, `frame.read_noise_e`), from at least three nights for the baseline reference.
- **Procedure:**
  1. Read the frame measurements and turn them into observations.
  2. Build the baseline reference of the equipment and assess the efficiency of the frames.
  3. Check that the shares of the loss account add up to captured minus effective time.

  ```python
  from astrometricslib import Astrometrics
  from wayfindinglib.analytics.result_quality.efficiency import (
      assess_efficiency,
      baseline_key,
      baseline_reference,
      build_baseline_nights,
      frame_observations_from_measurements,
  )
  from wayfindinglib.models.quality.efficiency import ScienceGoal

  astro = Astrometrics()
  rows = astro.quality.query(target_id="M 27", level="frame")  # frame measurement rows
  settings = astro.quality.capture_settings()
  observations = frame_observations_from_measurements(rows, settings)
  key = baseline_key(observations[0].capture_setting)
  reference = baseline_reference(build_baseline_nights(observations), key).reference
  assessment = assess_efficiency(observations, ScienceGoal.FAINT_POINT_SOURCE, reference)
  print(assessment.result.eta, assessment.result.efficiency_effective_seconds)
  ```

  `assess_efficiency` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.assess_efficiency --> finds eta, and `split_kept_frame_deficit` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.split_kept_frame_deficit --> splits the loss.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The efficiency of a night whose cause of loss you know (a moonlit night, a night with a known focus fault), and whether the largest share of the loss is that cause.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Transparency is not included. The physical reference has no sky value. The baseline hides a lasting fault. The depth view holds only for a sky-limited point source.
- **To tune for your setup:** Set `SiteProfile.typical_seeing_fwhm_arcsec` to your own good-night star width; collect at least three nights per equipment key for the baseline.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/result_quality/test/test_efficiency.py` and `test_aggregates.py`.
- **Raw record:** None kept.

### WAY-21

**Noise growth: the forward model of a stack and the noise floor fit**

- **Claim:** A stack's variance in rate units is the sum of w^2 v over the square of the sum of w, with v = (B t + RN^2) / t^2 for each frame and the weights of the stacker's recorded mode (equal, noise, or Siril star width). The error of an empty-aperture noise is 1.17 / sqrt(n) of the noise for n apertures. A noise floor is reported only when the fitted constant of measured variance = alpha x expected variance + b is above 0 by two standard errors.
- **Settings it supports:**
  - code constant `APERTURE_NOISE_ERROR_FACTOR` = 1.17 in `analytics/result_quality/noise_growth.py`. Relation: derived from this entry. <!-- impl: wayfindinglib.analytics.result_quality.noise_growth.APERTURE_NOISE_ERROR_FACTOR -->
  - code constant `TARGET_NOISE_RATIO` (1 over the square root of 2, about 0.707) in the same file. Relation: derived from this entry. <!-- impl: wayfindinglib.analytics.result_quality.noise_growth.TARGET_NOISE_RATIO -->
- **Status:** Partly measured. The forward model is tested on made-up stacks (equal weights, a moonlit case, star-width reweighting, the known penalty of equal weights). "Take N more" stays a design estimate (WAY-D30): real subsets of M 57 came out 9 to 31 percent below the prediction (STK-36).
- **Data you need:** The stack runs of one target with their frame rows (`stacking.frame_weight`), their measured noise (`stacking.empty_aperture_noise_stack_unit`) and expected noise (`stacking.expected_empty_aperture_noise_stack_unit`), and the sky rate and star width of each frame.
- **Procedure:**
  1. Build `StackFrame` values from the frames of a run and identify the weighting mode from the recorded weights.
  2. Forecast the noise with `forecast_stack_noise` and compare it with the run's measured noise.
  3. Fit the floor over the target's runs with `fit_noise_floor`.

  ```python
  from wayfindinglib.analytics.result_quality.noise_growth import (
      WeightingMode,
      fit_noise_floor,
      forecast_stack_noise,
      frames_to_cut_noise,
  )

  # frames: list[StackFrame] built from the frame rows; steps: list[NoiseStep] from the run history
  print(forecast_stack_noise(frames, WeightingMode.NOISE).noise)
  print(frames_to_cut_noise(frames, frames, WeightingMode.NOISE).frames_needed)
  print(fit_noise_floor(steps).floor_detected)
  ```

  `forecast_stack_noise` <!-- impl: wayfindinglib.analytics.result_quality.noise_growth.forecast_stack_noise --> is the forward model, `frames_to_cut_noise` <!-- impl: wayfindinglib.analytics.result_quality.noise_growth.frames_to_cut_noise --> is "take N more", and `fit_noise_floor` <!-- impl: wayfindinglib.analytics.result_quality.noise_growth.fit_noise_floor --> is the floor fit.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The ratio of measured to forecast noise for subsets of one target, and whether the fitted constant is above 0 only for targets with a known systematic.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The errors are unpaired unless a row says it is paired (only the leave-one-night-out rows are). A recorded weighting mode may be absent (Siril does not report the weights of its noise mode), and the forecast then says so.
- **To tune for your setup:** Nothing to tune except the thresholds of WAY-D31 and WAY-D32.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/result_quality/test/test_noise_growth.py`.
- **Raw record:** None kept.

### WAY-40

**Geometry and sensor-profile limits of the performance envelope**

- **Claim:** The plate scales, the guide camera's field, the fastest credible guide speed (the sidereal rate) and the camera's clip ceiling and saturation level follow from the optics, the pixel size and the camera profile, with no fitted number.
- **Settings it supports:**
  - envelope limit `imaging_plate_scale`. Relation: derived from this entry.
  - envelope limit `guide_plate_scale`. Relation: derived from this entry.
  - envelope limit `max_credible_guide_error`. Relation: derived from this entry.
  - envelope limit `max_credible_guide_speed`. Relation: derived from this entry.
  - envelope limit `clip_ceiling`. Relation: derived from this entry.
  - envelope limit `saturation_threshold`. Relation: derived from this entry.
- **Status:** Partly measured. The arithmetic is exact and tested on made-up equipment. The camera-profile numbers carry their own provenance in the camera profile. No real night was re-run for this entry after the envelope moved to Foundation.
- **Data you need:** Your configured telescope, camera and guide equipment, and a camera profile.
- **Procedure:**
  1. Build the envelope for your active equipment.

     ```python
     from wayfindinglib.analytics.envelope_assembly import assemble_performance_envelope

     envelope = assemble_performance_envelope(config=config, butler=butler, astrometrics=astrometrics)
     print({
         name: (limit.value, limit.unit, limit.tier.value, limit.register_entry)
         for name, limit in envelope.thresholds.items()
     })
     ```

  2. Compare each value with the derivation text of the same limit.

  `assemble_performance_envelope` <!-- impl: wayfindinglib.analytics.envelope_assembly.assemble_performance_envelope --> gathers the inputs and `derive_performance_envelope` <!-- impl: wayfindinglib.analytics.performance_envelope.derive_performance_envelope --> works each limit out.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether each plate scale equals 206.265 times the pixel size in micrometers over the focal length in millimeters.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** A camera with no profile uses the generic stand-in, and the limit says so in its derivation.
- **To tune for your setup:** Nothing to tune; change the equipment configuration and the limits follow.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/test/test_performance_envelope.py` checks the arithmetic and the tier of each limit.
- **Raw record:** None kept.

### WAY-41

**Physical-budget limits: guiding error, trailing and excursions**

- **Claim:** The guiding error and the trailing length that widen a star by no more than the blur tolerance follow from the star width the equipment's own frames show; an error five times that guiding limit (or one guide pixel, if larger) is an event, not noise.
- **Settings it supports:**
  - envelope limit `guiding_rms_limit`. Relation: derived from this entry.
  - envelope limit `trailing_limit`. Relation: derived from this entry.
  - envelope limit `guide_excursion_limit`. Relation: derived from this entry.
- **Status:** Partly measured. With the earlier star width (5.6 arcsec) the default gave 1.09 arcsec per axis, inside the 0.8 to 1.4 arcsec range of 19 real guide logs on good nights. The star width now comes from the stored Moffat measurement `frame.star_width_moffat_arcsec`, so the numbers will change and have not been re-run on the real library.
- **Data you need:** At least 20 light frames of your equipment with a stored star width and long enough exposures (three guide cycles).
- **Procedure:**
  1. Build the envelope for your active equipment.

     ```python
     from wayfindinglib.analytics.envelope_assembly import assemble_performance_envelope

     envelope = assemble_performance_envelope(config=config, butler=butler, astrometrics=astrometrics)
     print({
         name: (limit.value, limit.unit, limit.tier.value, limit.register_entry)
         for name, limit in envelope.thresholds.items()
     })
     ```

  2. Compare each value with the derivation text of the same limit.

  `assemble_performance_envelope` <!-- impl: wayfindinglib.analytics.envelope_assembly.assemble_performance_envelope --> gathers the inputs and `derive_performance_envelope` <!-- impl: wayfindinglib.analytics.performance_envelope.derive_performance_envelope --> works each limit out.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether the guiding limit sits inside the guiding error your good nights show.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The star width is a median over all long frames, so a poor-seeing season widens every limit.
- **To tune for your setup:** Change the blur tolerance only with a stated reason; check the result against your own guide logs.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/test/test_performance_envelope.py` (blur tolerance scaling, excursion limit); `wayfindinglib/api/test/test_envelope_assembly_control.py` (star width read from stored measurements).
- **Raw record:** None kept.

### WAY-42

**Own-baseline limits of the performance envelope**

- **Claim:** A night is unusual for the equipment when it is beyond the median of the equipment's earlier nights times exp(plus or minus 3 robust spreads of the logarithms), or, for fractions that are zero on most nights, above their 90th percentile; none is given before 5 nights.
- **Settings it supports:**
  - envelope limit `guide_snr_low_limit`. Relation: derived from this entry.
  - envelope limit `guiding_rms_high_limit`. Relation: derived from this entry.
  - envelope limit `guide_star_mass_low_limit`. Relation: derived from this entry.
  - envelope limit `night_star_width_high_limit`. Relation: derived from this entry.
  - envelope limit `night_star_roundness_low_limit`. Relation: derived from this entry.
  - envelope limit `guide_lost_fraction_high_limit`. Relation: derived from this entry.
  - envelope limit `guide_excursion_fraction_high_limit`. Relation: derived from this entry.
  - envelope limit `capture_abort_fraction_high_limit`. Relation: derived from this entry.
- **Status:** Partly measured. The method was checked on this observatory's recorded nights when it was built. Night star width and roundness now come from stored measurements (Moffat width, inverse of the star elongation), so their baselines have not been re-run.
- **Data you need:** Five or more earlier nights of one equipment fingerprint with guide samples, runs and measured frames.
- **Procedure:**
  1. Build the envelope for your active equipment.

     ```python
     from wayfindinglib.analytics.envelope_assembly import assemble_performance_envelope

     envelope = assemble_performance_envelope(config=config, butler=butler, astrometrics=astrometrics)
     print({
         name: (limit.value, limit.unit, limit.tier.value, limit.register_entry)
         for name, limit in envelope.thresholds.items()
     })
     ```

  2. Compare each value with the derivation text of the same limit.

  `assemble_performance_envelope` <!-- impl: wayfindinglib.analytics.envelope_assembly.assemble_performance_envelope --> gathers the inputs and `derive_performance_envelope` <!-- impl: wayfindinglib.analytics.performance_envelope.derive_performance_envelope --> works each limit out.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether a limit with fewer than 5 nights has no value and says how many nights it has.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** A change of equipment starts an empty history by design.
- **To tune for your setup:** Raise the night count if your nights vary a great deal.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/test/test_performance_envelope.py`; `wayfindinglib/analytics/test/test_envelope_assembly.py`; `wayfindinglib/analytics/test/test_capture_history.py`.
- **Raw record:** None kept.

### WAY-43

**Shortest exposure whose star width counts**

- **Claim:** Guiding error only shows in an exposure that spans several guide cycles, so frames shorter than three of the equipment's own guide cycles are left out of the star-width limits.
- **Settings it supports:**
  - envelope limit `minimum_star_measurement_exposure`. Relation: derived from this entry.
- **Status:** Partly measured. Found on real data: bright stars shot for 1 to 4 seconds read 10 to 16 arcsec wide because they were saturated, and moved the median from 5.6 to 8.0 arcsec.
- **Data you need:** The equipment's recorded guide cycle (from guide samples) and its light frames.
- **Procedure:**
  1. Build the envelope for your active equipment.

     ```python
     from wayfindinglib.analytics.envelope_assembly import assemble_performance_envelope

     envelope = assemble_performance_envelope(config=config, butler=butler, astrometrics=astrometrics)
     print({
         name: (limit.value, limit.unit, limit.tier.value, limit.register_entry)
         for name, limit in envelope.thresholds.items()
     })
     ```

  2. Compare each value with the derivation text of the same limit.

  `assemble_performance_envelope` <!-- impl: wayfindinglib.analytics.envelope_assembly.assemble_performance_envelope --> gathers the inputs and `derive_performance_envelope` <!-- impl: wayfindinglib.analytics.performance_envelope.derive_performance_envelope --> works each limit out.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether short saturated frames are absent from the star-width sample count.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** No limit until the equipment has guided.
- **To tune for your setup:** Change the number of guide cycles only after looking at your own short frames.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/test/test_envelope_assembly.py` (short exposures left out).
- **Raw record:** None kept.

### WAY-44

**The envelope's frame inputs come from stored measurements**

- **Claim:** Star width, star roundness and clipping inputs are read from the stored frame measurements (`frame.star_width_moffat_arcsec`, `frame.star_elongation`, `frame.saturated_pixel_fraction`, `frame.background_level_adu`), each pinned to one definition version, and joined to the frame by target and path below the lights folder. A row of another version, a per-color-plane row, or a not-measured row counts as missing.
- **Settings it supports:**
  - The metric pins in `PINNED_FRAME_METRICS`. Relation: derived from this entry.
- **Status:** Partly measured. The join and the pins are tested on made-up rows. The step has not been run on the real library.
- **Data you need:** A library whose frames were measured by the frame measurement job.
- **Procedure:**
  1. Call `collect_capture_frames` for your telescope and camera.
  2. Count frames whose `measurement_rows` is empty: those were not measured or were measured under another definition.

  `collect_capture_frames` <!-- impl: wayfindinglib.analytics.capture_history.collect_capture_frames -->.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** How many frames are missing each metric.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The newest row of each frame and metric is used; if that newest row has another definition version, an older matching row is not used.
- **To tune for your setup:** Remeasure frames after a metric definition changes.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/test/test_capture_history.py`.
- **Raw record:** None kept.

### WAY-45

**Guiding-analysis rules**

- **Claim:** The per-night guiding statements (recalibrate, check the guide signal, update the optics, guiding against the limit, exposure length limited) compare guide-log values with the envelope limits and carry their values, limits and a confidence.
- **Settings it supports:** None. The entry backs the guiding rules; their constants are the design estimates WAY-D45 and WAY-D46, and their limits come from WAY-40 to WAY-43.
- **Status:** Partly measured. Run on this observatory's real guide logs when built; the confidence labels are new and the exposure-length rule rests on the guide log as a proxy for star shape.
- **Data you need:** Guide logs and Ekos session records of the night, and the envelope.
- **Procedure:**
  1. Run `control.history.query(kind="guiding", ...)` for a night.
  2. Read each recommendation's `comparedTo` and `confidence`.

  `recommend_for_guiding` <!-- impl: wayfindinglib.session_analysis.guiding.post_processing.recommend_for_guiding.recommend_for_guiding -->.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether each warning names the limit that fired and the register entry of that limit.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The rule confidence steps down one label when the signal was poor or the equipment only partly matches.
- **To tune for your setup:** Nothing to tune except the limits above.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/session_analysis/guiding/test/`.
- **Raw record:** None kept.

### WAY-46

**Capture-analysis rules**

- **Claim:** The per-night capture statements (frames missing from the library, missing measurements, aborts, sensor temperature spread, a clipped spectroscopy star, star width and roundness against earlier nights) cite the stored measurement rows they rest on; clipping is decided from the frames' stored saturated-pixel fractions, not from a stacking verdict.
- **Settings it supports:** None. The entry backs the capture rules; their constants are the design estimates WAY-D45, WAY-D47 and WAY-D48, and their limits come from WAY-42.
- **Status:** Partly measured. The old clipping rule agreed with the stacking summary on 67 of 69 exposure groups (the two differences were Moon frames). The stored-measurement version has not been re-run on the real library.
- **Data you need:** Light frames with stored measurements and Ekos records.
- **Procedure:**
  1. Run `control.history.query(kind="capture", ...)` for a night.
  2. Check that each recommendation's `evidence` rows exist in the measurement store.

  `recommend_for_capture` <!-- impl: wayfindinglib.session_analysis.capture.post_processing.recommend_for_capture.recommend_for_capture -->.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether clipped spectroscopy groups match your own look at the frames.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The stacking summary's recommended exposure is no longer quoted; the exposure rule belongs to the result-quality rule table.
- **To tune for your setup:** Nothing to tune except WAY-D45, WAY-D47 and WAY-D48.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/session_analysis/capture/test/`; `wayfindinglib/session_analysis/test/test_no_science_verdicts.py`.
- **Raw record:** None kept.

### WAY-47

**Sky-position rules**

- **Claim:** A part of the sky is called poor when a measure is worse there than in the rest of the same nights by more than the blur tolerance and by 3 robust spreads, on at least 5 nights.
- **Settings it supports:** None. The entry backs the sky rules; their constants are the design estimates WAY-D40 and WAY-D42.
- **Status:** Design estimate. The comparison is observational, and altitude is tied to time of night, so a poor altitude band can be a poor hour.
- **Data you need:** Five or more nights with a known pointing.
- **Procedure:**
  1. Run `control.history.query(kind="sky")`.
  2. Read each poor region's message and `comparedTo`.

  `recommend_for_sky` <!-- impl: wayfindinglib.session_analysis.sky.post_processing.recommend_for_sky.recommend_for_sky -->.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether a flagged region persists across targets and hours.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Not a designed test; confidence is capped at design estimate.
- **To tune for your setup:** Nothing to tune except WAY-D40 and WAY-D42.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/session_analysis/sky/test/`.
- **Raw record:** None kept.

### WAY-50

**The stored night plan and its revision check**

- **Claim:** The night plan is an `ObservationSession` that carries a checklist, a framing and a revision number. Every plan write adds 1 to the revision. A write that names an older revision is refused with the revision now stored, so two windows cannot overwrite each other. A session stored before these fields existed loads with an empty checklist, no framing and revision 0.
- **Settings it supports:** None. The entry backs a storage rule, not a tuned value.
- **Status:** Partly measured. Tested on made-up sessions, on a session JSON rewritten to the old shape, and on a stale write. It has not been run with two real app windows.
- **Data you need:** A night plan with at least one queue entry.
- **Procedure:**
  1. Create an empty plan with `create_plan`, then change it with `edit_queue(..., expected_revision=0)`.
  2. Repeat the same call with `expected_revision=0`. It must raise `PlanRevisionConflictError`, whose details name revision 1.
  3. Read the plan with `get_plan` and check the revision is 1.

  ```python
  change = planning.edit_queue(session.id, add_checklist=[{"rule": "example"}], expected_revision=0)
  print(change.revision, [item.kind for item in change.changes])
  ```

  `ObservationSession` <!-- impl: wayfindinglib.models.session.observation_session.ObservationSession --> holds the plan, `PlanChange` <!-- impl: wayfindinglib.models.session.observation_session.PlanChange --> is what a write returns, and `DiskButler.put_session_if_revision` <!-- impl: wayfindinglib.drivers.butler.DiskButler.put_session_if_revision --> makes the check and the write one database statement.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether a stale write leaves the stored plan untouched, and whether an old stored session still loads.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Execution saves with `DiskButler.put_session_from_execution` <!-- impl: wayfindinglib.drivers.butler.DiskButler.put_session_from_execution -->, which merges: in one database transaction it reads the stored session again and keeps what Planning wrote (checklist, framing, revision and queue edits), taking only Execution's own fields from its copy. It does not bump the revision, so a Planning edit that loaded the plan before an Execution save and writes after it still overwrites that save's execution fields (statuses, telemetry) once; the next Execution checkpoint writes them again from its own copy. A plan edit that changes nothing (every change is an "already present" skip) stores nothing, keeps the revision and sends no `plan:changed` event. The backend must send the plan-changed event from `PlanChange`; the sequencer methods `sequencer:add`, `sequencer:remove`, `sequencer:reorder` and `sequencer:modify` edit the queue through the plan service and so send it too. `plan:tick_checklist_item` with a `ticked_at` that is not an ISO 8601 time returns the `invalid_argument` error.
- **To tune for your setup:** Nothing to tune.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/api/test/test_planning.py` (`test_a_stale_revision_is_refused_and_names_the_current_one`, `test_the_database_write_refuses_a_revision_that_moved`, `test_a_session_stored_before_the_night_plan_fields_still_loads`).
- **Raw record:** None kept.

### WAY-51

**Allowed capture values from the camera profile**

- **Claim:** A plan may use a gain setting, offset and binning only if the camera profile measured that combination. For a camera with no measured combination the request is accepted and marked "setting not measured", never refused. Exact duplicate exposure requests merge and their counts add; requests that differ only in delay are refused.
- **Settings it supports:** None. The entry backs a rule about what is accepted.
- **Status:** Partly measured. Tested on stand-in profiles. It has not been run on the ASI533MM or Nikon profiles in the real configuration.
- **Data you need:** A camera profile with `sensor_noise` entries, from `resolve_camera_profile`.
- **Procedure:**
  1. Resolve the profile of your camera. One matcher, `camera_names_match` <!-- impl: wayfindinglib.analytics.result_quality.allowed_capture_values.camera_names_match -->, accepts every spelling of the profile's camera name.
  2. Build the allowed values and check one measured and one unmeasured combination.

  ```python
  from astrometricslib import resolve_camera_profile
  from wayfindinglib.analytics.result_quality.allowed_capture_values import allowed_capture_values

  allowed = allowed_capture_values(resolve_camera_profile("ZWO ASI533MM Pro"))
  print(allowed.check(gain_setting=100, offset=10, binning=1))
  ```

  `allowed_capture_values` <!-- impl: wayfindinglib.analytics.result_quality.allowed_capture_values.allowed_capture_values --> builds the answer and `AllowedCaptureValues.check` <!-- impl: wayfindinglib.analytics.result_quality.allowed_capture_values.AllowedCaptureValues.check --> applies it.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** That each setting you really use is accepted, and that the refusal names the measured settings.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** A filter outside the equipment list is refused only when the equipment lists filters. Today it lists none, so the filters come from library frames and an unseen filter is noted, not refused.
- **To tune for your setup:** Measure gain and read noise at each setting you use so the profile lists it.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/result_quality/test/test_allowed_capture_values.py`.
- **Raw record:** None kept.

### WAY-52

**The sequence summary for Ekos**

- **Claim:** The night plan can be shown as rows in the order of the Ekos Capture module (filter, exposure, count, binning, gain, offset). Each field names its Ekos screen. Dither size goes to the Ekos Guide options and dither frequency to each capture job.
- **Settings it supports:** None.
- **Status:** Partly measured. The order and screen names come from the Ekos Capture module as we use it; the rows are tested on a made-up plan. The text of the Ekos screen names has not been compared with the current Ekos release.
- **Data you need:** A night plan with a queue entry that has exposures and, optionally, dithering.
- **Procedure:**
  1. Read the plan with `get_plan`.
  2. Build the summary and compare the rows with your Ekos Capture screen.

  ```python
  from wayfindinglib.models.planning.sequence_summary import SequenceSummary

  summary = SequenceSummary.from_session(planning.get_plan(session.id))
  print([field.name for field in summary.rows[0].fields])
  ```

  `SequenceSummary` <!-- impl: wayfindinglib.models.planning.sequence_summary.SequenceSummary --> is the model.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether each value lands on the Ekos screen the row names.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** A value the plan leaves open is `None` and means "leave the camera's own value".
- **To tune for your setup:** Nothing to tune.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/api/test/test_planning.py` (`test_framing_is_stored_and_the_sequence_summary_follows_ekos_order`).
- **Raw record:** None kept.

### WAY-53

**Framing with camera rotation**

- **Claim:** `calculate_panels(rotation_deg=...)` turns the mosaic grid about its center by the camera rotation in degrees east of north, as Ekos and KStars give it. At 0 degrees the result is unchanged. At 90 degrees the two grid axes swap. Each panel center turned back by the rotation recovers the unrotated grid.
- **Settings it supports:** None.
- **Status:** Partly measured. The geometry is checked by the round trip and the axis swap on a made-up grid. It has not been compared with a plate-solved frame taken at a known rotation.
- **Data you need:** A target position and, to check the sign, one plate-solved frame taken with a known camera rotation.
- **Procedure:**
  1. Compute panels at rotation 0 and at a second rotation.
  2. Turn the second set back by the rotation and compare.

  ```python
  panels = planning.calculate_panels("10:00:00", "+30:00:00", 2, 3, 10.0, rotation_deg=30.0)
  print([(panel.panel_id, panel.ra_deg, panel.dec_deg) for panel in panels])
  ```

  `calculate_panels` <!-- impl: wayfindinglib.tasks.planning_tasks.mosaic_tasks.calculate_panels --> does the work.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether the panels fall where a solved frame at that rotation says they should. The sign of the rotation is the first thing to confirm.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The grid is laid out on a flat patch of sky (small-field approximation). The first version shows the framing as numbers only.
- **To tune for your setup:** Nothing to tune.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/tasks/planning_tasks/test/test_mosaic_tasks.py` (rotation tests).
- **Raw record:** None kept.

### WAY-54

**Checklist ticks**

- **Claim:** The checklist keeps each item's rule, capture setting and every time the user ticked it. A tick is a stored fact. Whether a ticked item comes back is not stored; a later advisory read works it out from the measurements taken since the last tick.
- **Settings it supports:** None.
- **Status:** Partly measured. Adding, de-duplicating and ticking are tested on made-up plans. The return rule itself is built by a later batch.
- **Data you need:** A night plan with a checklist item.
- **Procedure:**
  1. Add an item with `edit_queue(add_checklist=[...])`.
  2. Tick it with `tick_checklist_item` and read the plan back.

  ```python
  change = planning.tick_checklist_item(session.id, item_id, expected_revision=change.revision)
  print(change.session.checklist[0].tick_history)
  ```

  `ChecklistItem` <!-- impl: wayfindinglib.models.session.observation_session.ChecklistItem --> holds the item.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** That a repeat tick keeps the older times and that the same rule and setting is not added twice.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Nothing here decides whether the fix worked.
- **To tune for your setup:** Nothing to tune.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/api/test/test_planning.py` (`test_checklist_items_are_added_once_and_ticked`).
- **Raw record:** None kept.

### WAY-55

**Target-change detection from Ekos mount events**

- **Claim:** A change of target is a slew that ends in tracking. Its position comes from the nearest recorded mount position, is converted from altitude and azimuth to sky coordinates, and is matched to the nearest library target.
- **Settings it supports:** None. The entry backs a method, not a tuned value (the constants are the design estimates named under Limits).
- **Status:** Design estimate. Only made-up logs have been checked. No real recorded night has been run through it yet.
- **Data you need:** One or more Ekos sessions with logged slews and mount positions, and library targets near where the mount pointed.
- **Procedure:**
  1. Ask the control history for the target changes.
  2. Compare each reported change with the targets you know you imaged and when.

  ```python
  changes = control.history.query(kind="target_changes")
  for change in changes:
      print(change)
  ```

  `find_target_changes` <!-- impl: wayfindinglib.tasks.control_tasks.target_changes.find_target_changes -->, and `TargetChange` <!-- impl: wayfindinglib.models.session.night_list.TargetChange --> is the reply shape.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether every real change of target is listed, and whether a centering step or meridian flip is wrongly listed as a new target.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Synthetic logs only. The constants are design estimates WAY-D55 to WAY-D57.
- **To tune for your setup:** Change the three constants named in WAY-D55 to WAY-D57 and rerun the example on a night you know.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/tasks/control_tasks/test/` checks the detection on made-up logs.
- **Raw record:** None kept.

### WAY-56

**Monitor mode: no motion or capture command reaches a driver**

- **Claim:** While control is delegated, no motion or capture command reaches a device driver through the library, the backend RPC or the MCP tools. Device settings and the app's own INDI connect and disconnect stay allowed.
- **Settings it supports:** None. The entry backs a method, not a tuned value (the constants are the design estimates named under Limits).
- **Status:** Measured. The tests list the commands from the tool manifests and check each one is refused.
- **Data you need:** None beyond the repository.
- **Procedure:**
  1. Run the three test files named below.
  2. Check that a newly added actuating tool fails them until it is given sample arguments.

  Every method of the RPC table is classified as read-only, local write, stop only, allowed or command-tested. A method in none of the lists fails `test_every_rpc_method_is_classified`. `sequencer:begin` is command-tested. `guiding:stop` and `telescope:alignment_stop` are classed as stop only (they end a loop and move or expose nothing); the systems engineer is to confirm this decision. The guarded commands are listed in `wayfindinglib/test/monitor_mode_support.py`.
- **Acceptance criterion:** Every actuating command is refused in monitor mode and none reaches a driver.
- **What to look at:** Whether the test enumerates the commands from the manifest, so a new tool cannot slip past it.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The tests use a fake driver. They do not drive a real mount.
- **To tune for your setup:** Nothing to tune. To add an actuating tool, add its sample arguments to the two test helper files.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/test/test_monitor_mode_sends_no_commands.py`, `backend/tests/test_monitor_mode_rpc.py` and `mcp_servers/wayfinding_core/test/test_monitor_mode_tools.py`.
- **Raw record:** None kept.

### WAY-57

**The night list: what each has-data flag means**

- **Claim:** The night list (history kind `nights`) says for each night whether it has guiding, capture and sky data. Sky data means at least one frame with a stored background measurement.
- **Settings it supports:** None. The entry backs a method, not a tuned value (the constants are the design estimates named under Limits).
- **Status:** Design estimate. Only made-up nights have been checked.
- **Data you need:** A library with Ekos sessions and measured frames on several nights.
- **Procedure:**
  1. Ask the control history for the night list.
  2. Compare each flag with what you know is stored for that night.

  ```python
  nights = control.history.query(kind="nights")
  for night in nights:
      print(night)
  ```

  `build_night_list` <!-- impl: wayfindinglib.tasks.control_tasks.night_history.build_night_list --> builds the list and `NightListEntry` <!-- impl: wayfindinglib.models.session.night_list.NightListEntry --> is one row.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether a night with measured frames shows sky data and a night without them does not.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Synthetic nights only.
- **To tune for your setup:** Nothing to tune.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/tasks/control_tasks/test/` checks the flags on made-up nights.
- **Raw record:** None kept.

### WAY-60

**The target dataset report: its inputs, its joins and its statements**

- **Claim:** For one target, the report summarizes each capture setting, lists the warnings and recommendations in priority order, says explicitly what could not be checked, gives a starting point when there are no frames, checks earlier "take N more" forecasts, and brings back the checklist items the user ticked when the measurements taken since still trip the rule. It reads only stored measurements, the calibration library, the stacker's set-aside records, the stored night plans and the Ekos session records. It lists the newest value of each context metric for each capture setting. It stores nothing. A report that would be longer than 40,000 characters loses its context section first, then the long evidence lists of its statements, and says so.
- **Settings it supports:** None. The entry backs a method, not a tuned value. The constants are the design estimates WAY-D60 and WAY-D61. The size of the reply is WAY-D77.
- **Status:** Partly measured. The joins (a stack tied to its setting through its frame weights, a set-aside frame tied to its quarantine rows, a dark tied to the light frames) and every statement are checked on made-up libraries, and the report is shown to store nothing. No real target has been reported on yet (batch 7 does that on a copy).
- **Data you need:** A target with measured frames (`QualityRecords.measure_frames`), at least one stack, and a calibration library. For the sky model and the equipment baseline, at least three nights of frames from the same camera and settings.
- **Procedure:**
  1. Build the report for a target.
  2. Read the statements in order, and open the rows each cites with `QualityRecords.query(row_ids=...)`.
  3. Compare a statement with what you know of the target.

     ```python
     from wayfindinglib import Wayfinder

     report = Wayfinder().planning.get_advisory("quality", target="M 57")
     print(report.needs_attention)
     for statement in report.statements:
         print(statement.kind.value, statement.rule, statement.confidence.value, statement.message)
     ```

  `build_target_dataset_report` <!-- impl: wayfindinglib.tasks.planning_tasks.target_report_tasks.build_target_dataset_report --> builds the report, and `TargetDatasetReport` <!-- impl: wayfindinglib.models.planning.target_report.TargetDatasetReport --> is its shape.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether every statement names its setting and cites rows that exist; whether a rule that could not be checked says what is missing; whether the Moon share, where shown, is labeled model-derived; whether the stack's rows were tied to the right setting (the notes say when a stack holds more than one).
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The items `guiding_limits_exposure` and `best_window` of the decision catalog are not made; both are listed as deferred rules with their reasons (the guiding analysis lives in Control and Planning may not import Control; the nights-by-visibility model is not built). A report over the reply budget keeps all its statements but may cut their evidence lists to 20, 5 or 1 references (WAY-D77); each statement says how many it lost, and the rows stay readable with `QualityRecords.query`. A stack of several capture settings is tied to the setting with most of its frames, so its rows are judged against that setting only. The reference for the efficiency of a setting is the equipment baseline when three nights exist, else the physical reference, else none (and the report says so). The sky model needs five nights and 20 frames of the equipment.
- **To tune for your setup:** Change the design-estimate rows WAY-D60 to WAY-D69 and WAY-D77 first, then the constants they name.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/tasks/planning_tasks/test/test_target_report_tasks.py` checks the whole report on a made-up library, `wayfindinglib/analytics/result_quality/test/test_report_rules.py` checks each rule at its limit, `wayfindinglib/tasks/planning_tasks/test/test_target_report_context.py` checks that every context metric can be shown, and `test_target_report_tasks.py` checks that a busy target (3 capture settings, 500 frames, every metric) gives a report that fits the 40,000-character reply budget.
- **Raw record:** None kept.

### WAY-70

**Forecast check: planned, given the frames taken, and measured**

- **Claim:** For each pair of neighboring library stack runs of a target, the change in stack noise is forecast three ways and compared: as planned (the new frames look like the past frames, in the number and exposure actually taken), given the frames actually taken (their own sky rates and star widths, with the weighting mode the stacker really used), and as measured (the ratio of the two stacks' normalized empty-aperture noise). Each carries a tolerance of one standard error of the aperture noise, 1.17 divided by the square root of the aperture count, added in quadrature for two unpaired stacks. Two numbers agree when they differ by no more than two tolerances. The outcome is as forecast, short for a known reason, short for no known reason, or not checkable with the reason.
- **Settings it supports:** None. The entry backs a method, not a tuned value. The agreement width is the design estimate WAY-D70.
- **Status:** Partly measured. The check is tested on made-up histories with known noise and weights (equal weights, a moonlit night, star-width weights, an unknown mode, missing values, a paired error). No real target has been replayed yet (batch 7 does that on a copy). The star-width weights use the effective star width in arcseconds as a stand-in for Siril's weighted width.
- **Data you need:** The library stack runs of one target: `stacking.empty_aperture_noise_normalized`, `stacking.empty_aperture_noise_stack_unit` and `stacking.expected_empty_aperture_noise_stack_unit` of each stack, the `stacking.frame_weight` rows of each run, and the sky rate, read noise and star width of every member frame. At least two stack runs.
- **Procedure:**
  1. Read every row of the target with all runs kept, and turn the stack and weight rows into run records.
  2. Turn the frame rows into frame observations.
  3. Run the check.

     ```python
     from astrometricslib import Astrometrics
     from wayfindinglib.analytics.result_quality.efficiency import frame_observations_from_measurements
     from wayfindinglib.analytics.result_quality.forecast import forecast_check, stack_runs_from_measurements

     astro = Astrometrics()
     stack_rows = astro.quality.query(target_id="M 57", level="stack", library_only=True, latest_only=False)
     weight_rows = astro.quality.query(
         target_id="M 57", metric="stacking.frame_weight", library_only=True, latest_only=False
     )
     frame_rows = astro.quality.query(target_id="M 57", level="frame", library_only=True)
     runs = stack_runs_from_measurements(stack_rows + weight_rows)
     observations = frame_observations_from_measurements(frame_rows, astro.quality.capture_settings())
     for check in forecast_check(runs, observations):
         print(check.outcome.value, check.forecast_when_planned, check.forecast_given_frames_taken, check.measured)
     ```

  `forecast_check` <!-- impl: wayfindinglib.analytics.result_quality.forecast.forecast_check --> makes the checks, and `stack_runs_from_measurements` <!-- impl: wayfindinglib.analytics.result_quality.forecast.stack_runs_from_measurements --> builds the run records. `ForecastCheck` <!-- impl: wayfindinglib.models.planning.target_report.ForecastCheck --> is the reply shape.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether the measured change agrees with the forecast given the frames taken on nights whose cause you know (a moonlit night should be short for a known reason), and how often a check is not checkable because a run stored no weights.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The errors are unpaired unless the caller passes a paired error. A run that made more than one stack shares its member list between them. A weighting mode that Siril did not report cannot be identified, and the check says so. The star-width weights use a stand-in for the weighted width (WAY-D70). A noise floor is named only by the separate fit over two or more steps, never from one shortfall.
- **To tune for your setup:** Change `AGREEMENT_TOLERANCES` after replaying your own nights (WAY-D70).
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/result_quality/test/test_forecast.py`.
- **Raw record:** None kept.

### WAY-71

**Tonight's sky: the user's own sky model, used to predict the sky of a target at a time**

- **Claim:** The sky rate of a new frame tonight is predicted by the sky model fitted to the sky rates of the user's own frames (WAY-D29), at the target's position and the planning time, for the equipment setting the frames will use. The prediction is labeled as a model, gives the model's held-out error, and says when the Moon is up but the model has no Moon term. Extinction is not modeled separately.
- **Settings it supports:** None. The entry backs a method; the model's own constants are WAY-D29.
- **Status:** Design estimate. The model is checked on made-up frames only. No photometric night has checked it, and its error on a real new night is not known.
- **Data you need:** At least five nights and 20 frames of the same equipment setting, each with a sky rate, a time and a target position.
- **Procedure:**
  1. Read the frames of the library with `LibraryInputs`.
  2. Fit the model for each equipment setting.
  3. Predict the sky of a target tonight and compare it with the first frames of the night.

     ```python
     from astrometricslib import Astrometrics
     from wayfindinglib.analytics.result_quality.library_inputs import LibraryInputs

     # butler and config are the storage layer and the configuration of the running app.
     inputs = LibraryInputs(Astrometrics(), butler, config)
     models, unavailable = inputs.all_sky_models()
     print({key: model.held_out_relative_error for key, model in models.items()}, unavailable)
     ```

  `LibraryInputs.all_sky_models` <!-- impl: wayfindinglib.analytics.result_quality.library_inputs.LibraryInputs.all_sky_models --> gathers the samples and fits one model per equipment key (`LibraryInputs.sky_model_for` <!-- impl: wayfindinglib.analytics.result_quality.library_inputs.LibraryInputs.sky_model_for --> fits one), and `summarize_sky` <!-- impl: wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks.summarize_sky --> builds the label the ranking shows.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The difference between the predicted and the measured sky rate of the first frames of a night, against the held-out error the model reports.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** A frame's position is its target's position. Extinction is folded into the airmass term. An equipment setting with fewer than five nights has no model, and its targets are ranked from the sky of their own past frames, with a note.
- **To tune for your setup:** Nothing beyond WAY-D29.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/result_quality/test/test_library_inputs.py` and `wayfindinglib/analytics/result_quality/test/test_sky_model_and_observing.py` and `wayfindinglib/tasks/planning_tasks/test/test_tonight_ranking_tasks.py`.
- **Raw record:** None kept.

### WAY-72

**Tonight's ranking: gain in stack noise per hour of clock time**

- **Claim:** A target is ranked by how much one clock hour of exposures would cut the noise of its stack. The hour holds fewer frames than its seconds, because each frame costs a download, every third frame a dither settle, and the hour one autofocus run (overheads from the user's Ekos sessions, else defaults). The new frames follow the target's past frames, with tonight's modeled sky. A target whose fitted floor says doubling the data would cut the noise by under 10% is ranked at zero with the reason. A target with no measured frames is marked new and is not ranked. Imaging, photometry and spectroscopy are ranked in separate lists. Solar-system targets, comets, calibration folders and asteroid searches are never ranked. A thin dataset that leads a list is called out. A ranking over the reply budget leaves out its lowest candidates and says how many (WAY-D77).
- **Settings it supports:** None. The entry backs a method, not a tuned value. The constants are the design estimates WAY-D71 and WAY-D72.
- **Status:** Design estimate. The ranking is tested on a made-up library. Its gains inherit the "take N more" estimate (WAY-D30), which real subsets of M 57 fell 9% to 31% short of (STK-36), and the sky model of WAY-71. No real night has been ranked and then imaged.
- **Data you need:** The library targets with their frame measurements, the stack runs of each, the user's Ekos session records (for the overheads), the site profile and the telescope's altitude limits.
- **Procedure:**
  1. Rank the targets at a planning time.
  2. Image the top target for an hour and stack it with the old frames.
  3. Compare the real fall in noise with the ranked gain, using the forecast check (WAY-70).

     ```python
     from datetime import UTC, datetime

     from astrometricslib import Astrometrics
     from wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks import rank_tonight

     ranking = rank_tonight(
         datetime(2026, 10, 10, 6, 0, tzinfo=UTC), astrometrics=Astrometrics(), site_profile=site
     )
     for ranked_list in ranking.lists:
         for candidate in ranked_list.candidates:
             print(ranked_list.use.value, candidate.rank, candidate.target_id, candidate.gain_per_hour_fraction)
     ```

  `rank_tonight` <!-- impl: wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks.rank_tonight --> makes the ranking, and `TonightRanking` <!-- impl: wayfindinglib.models.planning.tonight_ranking.TonightRanking --> is the reply shape.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether the hour's real noise gain on a ranked target agrees with its ranked gain, and whether the order of two targets holds when both are imaged for an hour.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** The gain is the fall in the noise of the whole stack; sharpness is not part of it. Photometry and spectroscopy gains are stack-noise gains, not precision or signal-to-noise gains. A target seen only through its past stack ranks by the exposure of its main capture setting. The star width does not change with altitude in the forecast.
- **To tune for your setup:** Change the three constants named in WAY-D71 to match your sequence, then compare the estimate with the real time of a night's sequence (WAY-D33).
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/tasks/planning_tasks/test/test_tonight_ranking_tasks.py`.
- **Raw record:** None kept.

### WAY-73

**Trust in one star's science result: spectrum classification and period search**

- **Claim:** A star's spectrum is judged by the relative RMS of its best match and the gaps to the second-best match and to the next class, against the limits of the spectroscopy pipeline (SPE-16 and SPE-18). A period or repeating-dip search is judged by its verdict, its false-alarm probability as the pipeline stored it, the checks made on data it was not found from, and the number of cycles seen (at least two). Each statement gives the number behind it and cites the per-star result by star, run and statistic. Whether more time would separate two classes is never guessed: it is an explicit missing-data statement until the class gap is calibrated against the signal-to-noise ratio. A star with neither result gets one missing-data statement that names what is missing.
- **Settings it supports:**
  - code constants `POOR_MATCH_RMS` and `AMBIGUOUS_RMS_GAP` in `analytics/result_quality/star_result_rules.py`. Relation: derived from the spectroscopy entries SPE-16 and SPE-18, whose limits they copy; a test compares them. <!-- impl: wayfindinglib.analytics.result_quality.star_result_rules.POOR_MATCH_RMS --> <!-- impl: wayfindinglib.analytics.result_quality.star_result_rules.AMBIGUOUS_RMS_GAP -->
- **Status:** Design estimate. The statements are tested on made-up star records. The metric names of the per-star statistics (for example `spectroscopy.self_determined_spectral_type_rms`) name fields of the star record, not registered metrics, because a per-star result has no row in the measurement store.
- **Data you need:** A star in the stellar catalog with a spectrum (`SpectroscopyResult`) or a period search (`PhotometryResult.periodogram`), recorded for the target.
- **Procedure:**
  1. Ask for the trust statements of a star.
  2. Compare each with the raw number it quotes and with your own look at the spectrum or light curve.

     ```python
     from astrometricslib import Astrometrics
     from wayfindinglib.tasks.planning_tasks.result_judgment_tasks import judge_star_result

     for statement in judge_star_result("Alcor", "HD 116656", astrometrics=Astrometrics()):
         print(statement.kind.value, statement.rule, statement.message)
     ```

  `judge_star_result` <!-- impl: wayfindinglib.tasks.planning_tasks.result_judgment_tasks.judge_star_result --> reads the star, and `judge_star_record` <!-- impl: wayfindinglib.analytics.result_quality.star_result_rules.judge_star_record --> judges it.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether a spectrum you know is wrong (a known-type star matched to the wrong type) draws the poor-match or ambiguity warning, and whether a period you trust passes without warnings.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Asteroid candidates are not judged (the asteroid-linking rule is deferred). Alias checks of periods are deferred. The statements add no probability of their own.
- **To tune for your setup:** Change `MINIMUM_CYCLES_FOR_PERIOD` (WAY-D73). The spectral limits are tuned in their own entries.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/analytics/result_quality/test/test_star_result_rules.py` and `wayfindinglib/tasks/planning_tasks/test/test_result_judgment_tasks.py`.
- **Raw record:** None kept.

### WAY-74

**Night review: where each target's time went, its warnings, and the guiding**

- **Claim:** For one observing night, each target imaged is given a loss account in two parts (frames set aside, and the efficiency lost in the frames kept) against the user's own baseline for the equipment, or against the telescope's physical limit when there are fewer than three nights. The goal comes from the target's kind (WAY-D74). The warnings come from the same plain rules and the same `needs_attention` function as the target report. The Moon's share of the sky loss comes from the sky model and is labeled model-derived. A frame is counted as set aside only when a stored row says it was moved to the `_excluded` folder. The review also gives the focus drift (luminance frames and the successful autofocus runs of the stored Ekos records of the night; the focus statement counts in `needsAttention`) and an exploratory polar-alignment estimate (field rotation grouped by guiding run and target, WAY-D28). The review reads only what the last sync copied.
- **Settings it supports:** None. The entry backs a method, not a tuned value. The set-aside marker is the design estimate WAY-D75.
- **Status:** Design estimate. The review is tested on a made-up library. No real night has been reviewed with it, and the causes of loss have not been checked on nights with a known cause (a night with logged clouds, a night with a bright Moon).
- **Data you need:** The frame measurements of the night, and of the nights before it for the baseline, the capture settings, the configured observer location (for the Moon share), and the recorded guiding runs.
- **Procedure:**
  1. Ask for the review of a night.
  2. Check that the captured time, the set-aside time and the effective time add up, and that each lost hour has a cause or is marked "cause unknown".
  3. Repeat on a night with a known cause.

     ```python
     quality = control.history.query(kind="night_quality", session_id="2026-10-02", register_job=False)
     for entry in quality.targets:
         print(entry.target_id, entry.frames_taken, entry.needs_attention, entry.loss_account)
     ```

  `build_night_quality` <!-- impl: wayfindinglib.tasks.control_tasks.night_quality_task.build_night_quality --> makes the review, and `NightQuality` <!-- impl: wayfindinglib.models.session.night_quality.NightQuality --> is the reply shape.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether the largest share of the lost time on a night with a known cause is that cause, and whether frames the stacker moved to `_excluded` appear as set-aside time.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Set-aside frames are not stored one by one by the stacker today, so a night may show less set-aside time than it had; a note says so. Stack-level rules read the newest stack of the target, which may be newer than the night. A review over the reply budget cuts the evidence lists of its statements and says so (WAY-D77). Transparency is not included. The polar-alignment estimate is exploratory and drives no rule. Warnings that repeat across nights are read with `control.history.query(kind="recurring_issues")`, not in this reply.
- **To tune for your setup:** Nothing to tune in the review. The baseline needs three nights of the same equipment setting.
- **Regenerate with:** No script; run the example above.
- **Guarded by tests:** `wayfindinglib/tasks/control_tasks/test/test_night_quality_task.py` and `wayfindinglib/api/test/test_night_quality_control.py`.
- **Raw record:** None kept.

## Design estimates

These values have not been measured. Each is a reasoned choice; the last column says how you could measure it on your data.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| WAY-D01 | Seeing used by the physical efficiency reference when the site's typical seeing is not set | None: the reference uses the telescope's diffraction limit alone | `SiteProfile.typical_seeing_fwhm_arcsec` <!-- impl: wayfindinglib.models.equipment_and_site.site_profile.SiteProfile --> | Every frame looks worse than the reference, so the efficiency reads low and losses are overstated. | Enter your typical good-night star width in arcseconds, or take the lower decile of your own star widths over many nights, and compare the efficiency with and without it. |
| WAY-D05 | Clipping limit and aimed peak of the overexposure rule | A peak of 1.0 of the room clips; a proposed exposure aims for 0.8 | `CLIPPING_PEAK_FRACTION` <!-- impl: wayfindinglib.analytics.result_quality.rules.CLIPPING_PEAK_FRACTION --> and `TARGET_PEAK_FRACTION` <!-- impl: wayfindinglib.analytics.result_quality.rules.TARGET_PEAK_FRACTION --> | A proposed exposure leaves too little signal (aim too low) or still clips on a better night (aim too high). | Take one bright star at several exposures and find the exposure where its peak sits at 0.8 of the room; compare with the proposal. |
| WAY-D06 | Least ratio of sky noise to read noise in the underexposure rule | 3 | `MINIMUM_SKY_TO_READ_NOISE_RATIO` <!-- impl: wayfindinglib.analytics.result_quality.rules.MINIMUM_SKY_TO_READ_NOISE_RATIO --> | Frames are called short when read noise costs less than 5% of the noise, or are called long enough when it costs more. | Stack the same field at two exposures with equal total time and compare the stack noise with `QualityRecords.measure_stack`. |
| WAY-D07 | Star elongation above which the trailed-stars rule warns | 1.2 (long axis over short axis) | `TRAILED_STAR_ELONGATION_LIMIT` <!-- impl: wayfindinglib.analytics.result_quality.rules.TRAILED_STAR_ELONGATION_LIMIT --> | Round enough stars are flagged, or visibly oval stars are not. | Read `frame.star_elongation` over your good nights, take the upper decile, and compare it with 1.2. |
| WAY-D08 | Share of stack noise variance a master flat may add, and the share to aim for | Limit 0.03, aim 0.01 | `FLAT_VARIANCE_SHARE_LIMIT` <!-- impl: wayfindinglib.analytics.result_quality.rules.FLAT_VARIANCE_SHARE_LIMIT --> and `FLAT_VARIANCE_SHARE_TARGET` <!-- impl: wayfindinglib.analytics.result_quality.rules.FLAT_VARIANCE_SHARE_TARGET --> | More flats are asked for when they would not show, or a fixed pattern is left in the stack. | Stack the target with a flat of 10 frames and one of 40, and compare `stacking.empty_aperture_noise_stack_unit` and `stacking.flat_noise_in_aperture_stack_unit`. |
| WAY-D09 | Fewest frames and least temperature change for the focus drift fit | 5 frames per segment, 20 in all, 1.0 degree C. The error of the slope comes from a block bootstrap (whole segments when there are 3 or more, runs of 5 frames inside a segment otherwise); the drift test is on the squared slope (one-sided, 2.3%). The fitted slope is the quadrature slope; it matches a straight-line px per degree C slope only far from best focus, and is larger than the straight-line slope over the allowed 5% range | `MINIMUM_FRAMES_PER_SEGMENT` <!-- impl: wayfindinglib.analytics.result_quality.focus_drift.MINIMUM_FRAMES_PER_SEGMENT --> , `MINIMUM_FRAMES_TOTAL` <!-- impl: wayfindinglib.analytics.result_quality.focus_drift.MINIMUM_FRAMES_TOTAL --> and `MINIMUM_TEMPERATURE_CHANGE_DEGC` <!-- impl: wayfindinglib.analytics.result_quality.focus_drift.MINIMUM_TEMPERATURE_CHANGE_DEGC --> | A slope is fitted from noise, or a real drift is reported as "not enough data". | Fit nights of different length and see where the slope stops changing. |
| WAY-D10 | Efficiency budget, bootstrap and significance of the focus drift fit | Seeing efficiency 0.9 (width up about 5%), 200 resamples, 2σ. The error of the slope comes from a block bootstrap (whole segments when there are 3 or more, runs of 5 frames inside a segment otherwise); the drift test is on the squared slope (one-sided, 2.3%). The fitted slope is the quadrature slope; it matches a straight-line px per degree C slope only far from best focus, and is larger than the straight-line slope over the allowed 5% range | `MINIMUM_SEEING_EFFICIENCY` <!-- impl: wayfindinglib.analytics.result_quality.focus_drift.MINIMUM_SEEING_EFFICIENCY --> , `BOOTSTRAP_RESAMPLES` <!-- impl: wayfindinglib.analytics.result_quality.focus_drift.BOOTSTRAP_RESAMPLES --> and `SIGNIFICANCE_SIGMAS` <!-- impl: wayfindinglib.analytics.result_quality.focus_drift.SIGNIFICANCE_SIGMAS --> | The refocus interval is too short (more autofocus time) or too long (soft stars). | Compare the stack noise of frames kept within 5% and within 10% of the best width. |
| WAY-D11 | Airmass exponent of the seeing term in the focus drift fit | 1.2 (seeing grows as airmass to the power 0.6, squared) | `SEEING_SQUARED_AIRMASS_EXPONENT` <!-- impl: wayfindinglib.analytics.result_quality.focus_drift.SEEING_SQUARED_AIRMASS_EXPONENT --> | Seeing change is read as focus drift. | Fit the exponent on a night with a focus held fixed by frequent autofocus. |
| WAY-D20 | Wavelength and Airy factor of the diffraction limit | 550 nm; 1.029 lambda / D | `REFERENCE_WAVELENGTH_NM`, `AIRY_FWHM_FACTOR` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.REFERENCE_WAVELENGTH_NM --> | A narrowband filter or a central obstruction changes the limit by 10 to 30 percent, so the reference is a little off. | Compare the sharpest star width of your best night with the limit. |
| WAY-D21 | Physical reference width | Diffraction, site seeing and pixel blur added as squared widths | `physical_reference` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.physical_reference --> | Adding in quadrature slightly underestimates the real width, so the reference is a little too sharp and efficiency reads low. With the site seeing unset, the physical reference has no star width and the seeing factor is "not included" (diffraction plus pixel blur alone is not a width a real night reaches). | Compare with the best widths in your own library. |
| WAY-D22 | Equipment baseline | 10th percentile of per-night medians; at least 3 nights. The baseline also stores the sample standard deviation of its nightly median star widths (`STAR_WIDTH_NIGHTLY_SPREAD_KEY` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.STAR_WIDTH_NIGHTLY_SPREAD_KEY -->); the seeing-limit constraint for detail goals fires only when the median width exceeds the baseline by more than this spread (physical reference: 10% of the reference width) | `BASELINE_PERCENTILE`, `MINIMUM_NIGHTS_FOR_BASELINE` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.BASELINE_PERCENTILE --> | Too few nights make the baseline noisy; a lasting fault hides in it. | Vary the percentile and the night count and watch how eta of one night moves. |
| WAY-D23 | Seeing exponent for astrometric precision when not sky-limited | 2 | `SEEING_EXPONENT_ASTROMETRY_OTHER` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.SEEING_EXPONENT_ASTROMETRY_OTHER --> | Efficiency for astrometry reads slightly high in read-noise-limited frames. | Fit the position error against width on frames of one field. |
| WAY-D24 | Noise-regime thresholds | Sky variance at least 9 read-noise variances; source variance at least 1 sky variance | `SKY_LIMITED_VARIANCE_RATIO`, `SOURCE_LIMITED_VARIANCE_RATIO` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.SKY_LIMITED_VARIANCE_RATIO --> | The regime label (and the depth view) is wrong near a boundary. Eta itself does not change. | Check the label on frames with known sky and read noise. |
| WAY-D25 | Goals with no sky and read-noise factors | Bright-star photometry, period detection and slitless spectral resolution (a slitless spectrum is source-limited) | `_NO_TIME_FACTORS_GOALS` <!-- impl: wayfindinglib.analytics.result_quality.efficiency.assess_efficiency --> | Efficiency is not given for these goals, which hides real sky losses for fainter photometry targets. | Compare photometric precision with sky rate on your variable-star frames. |
| WAY-D26 | Airmass formula constants | Kasten and Young (1989): 0.50572, 6.07995, -1.6364 | `airmass_from_altitude` <!-- impl: wayfindinglib.analytics.result_quality.observing_values.airmass_from_altitude --> | Published values; negligible. | Compare with the airmass in the FITS header. |
| WAY-D27 | Dew risk spread | 3 degrees Celsius above the dew point | `DEW_SPREAD_LIMIT_C` <!-- impl: wayfindinglib.analytics.result_quality.observing_values.DEW_SPREAD_LIMIT_C --> | Dew may form earlier or later than flagged. | Note the spread on nights when dew formed. |
| WAY-D28 | Polar-alignment fit thresholds and model | Hour-angle span 45 degrees; 5 samples per run; cos(dec) at least 0.17. Guided field rotation theta(H) = theta_0 + (a sin H - b cos H) / cos(dec), so the rate is largest near the pole; the lower bound for a short run is the slope times cos(dec) | `MINIMUM_HOUR_ANGLE_SPAN_DEG` <!-- impl: wayfindinglib.analytics.result_quality.observing_values.MINIMUM_HOUR_ANGLE_SPAN_DEG --> | The two parts of the error may be mixed. The cos(dec) cut is not for lack of signal: the signal grows toward the pole, but the small-angle model and the plate-solved rotation fail there. The value is exploratory and drives no rule. | Compare with a drift-alignment measurement of the same night; the synthetic test recovers an injected error at declination 33 and 60 degrees. |
| WAY-D29 | Sky model | Minimum 5 nights and 20 frames; Moon fitted with at least 2 moonlit nights. Moon term = phase function of Krisciunas and Schaefer (1991), 10^(-0.4(3.84 + 0.026 abs(alpha) + 4e-9 alpha^4)) scaled to 1 at full Moon (about 0.09 at quarter phase), times their scattering shape, times the Moon's own extinction 10^(-0.4 k X_moon) with k = 0.2 mag per airmass (design estimate), times the target airmass. No twilight term. Held-out error is the median over nights of each night's RMS relative error | `fit_sky_model` <!-- impl: wayfindinglib.analytics.result_quality.sky_model.fit_sky_model --> | Predictions of sky brightness on a new night may be off by the held-out error the fit reports. Frames taken in twilight read brighter than predicted. The value of k changes from night to night. | Read `held_out_relative_error` of the model and compare it with the nightly scatter; compare the fitted Moon coefficient with a clear-night sky rate at known phases. |
| WAY-D30 | "Take N more" | Noise falls to 0.707 of today's; new frames copy the target's past frames | `frames_to_cut_noise` <!-- impl: wayfindinglib.analytics.result_quality.noise_growth.frames_to_cut_noise --> | The real gain was 9 to 31 percent below the prediction on M 57 (STK-36). | Replay a target's nights as the plan describes. |
| WAY-D31 | Weighting-mode rules | Penalty 1.05 (a copy of the stacker's value); weight match within 5 percent | `MINIMUM_PENALTY_TO_WEIGHT`, `WEIGHT_MATCH_TOLERANCE` <!-- impl: wayfindinglib.analytics.result_quality.noise_growth.WEIGHT_MATCH_TOLERANCE --> | A mode may be misread; a test checks the copy against the stacker. | Compare predicted and recorded weights on your runs. |
| WAY-D32 | Floor detection and stop signal | 2 standard errors, one-sided (a false alarm of about 2.3%); stop when doubling cuts noise by less than 10%. The fit is made on noise divided by the stack's reference flux (`stacking.noise_reference_flux_stack_unit`), with the errors scaled to the model's predicted variance in a second pass; with 2 steps it has zero degrees of freedom and says so | `FLOOR_SIGMAS`, `MINIMUM_GAIN_FRACTION` <!-- impl: wayfindinglib.analytics.result_quality.noise_growth.FLOOR_SIGMAS --> | A real floor may be missed, or a false stop raised. | Test on a target with a known fixed pattern. |
| WAY-D33 | Capture overhead defaults and detection | Download 5 s, dither settle 20 s, autofocus 120 s; pause 600 s; 10 gaps minimum | `DEFAULT_DOWNLOAD_SECONDS` <!-- impl: wayfindinglib.analytics.result_quality.capture_overheads.DEFAULT_DOWNLOAD_SECONDS --> | Wall-clock time and the hourly gain of a target are off by the overhead error. | Compare the estimate with the real time of a night's sequence. |
| WAY-D34 | Agreement of measured stack noise with the model | 2 standard errors, one-sided (a false alarm of about 2.3%). "Cause unknown" adds the expected-noise model's own error in quadrature: 11% as noise (half of the 9% to 31% spread of real M 57 subsets against the prediction, STK-36), 22% as variance (`EXPECTED_NOISE_MODEL_RELATIVE_ERROR` <!-- impl: wayfindinglib.analytics.result_quality.aggregates.EXPECTED_NOISE_MODEL_RELATIVE_ERROR -->) | `MODEL_AGREEMENT_SIGMAS` <!-- impl: wayfindinglib.analytics.result_quality.aggregates.MODEL_AGREEMENT_SIGMAS --> | "Cause unknown" is raised too early or too late. | Check on stacks whose noise is known to match the model. |
| WAY-D40 | Blur tolerance: most a star may widen from guiding or trailing | 0.10 | `DEFAULT_BLUR_TOLERANCE_FRACTION` <!-- impl: wayfindinglib.analytics.performance_envelope.DEFAULT_BLUR_TOLERANCE_FRACTION --> | Limits too loose or too tight for your sharpness goal. | Compare the resulting guiding limit with the guide error on nights whose stars you judged good. |
| WAY-D41 | Excursion multiple: guide error beyond this many times the guiding limit is an event | 5 | `EXCURSION_SIGMA_MULTIPLE` <!-- impl: wayfindinglib.analytics.performance_envelope.EXCURSION_SIGMA_MULTIPLE --> | Real noise is counted as events, or events as noise. | Plot the guide error histogram of a good night and see where the tail begins. |
| WAY-D42 | Baseline spread multiple and minimum number of nights | 3 robust spreads, 5 nights | `BASELINE_SPREAD_MULTIPLIER` <!-- impl: wayfindinglib.analytics.performance_envelope.BASELINE_SPREAD_MULTIPLIER -->, `MINIMUM_BASELINE_SESSIONS` <!-- impl: wayfindinglib.analytics.performance_envelope.MINIMUM_BASELINE_SESSIONS --> | Too many or too few nights called unusual. | Hold out nights you know were bad and count how many are caught. |
| WAY-D43 | Percentile for fractions that are zero on most nights | 90th | `BASELINE_FRACTION_PERCENTILE` <!-- impl: wayfindinglib.analytics.performance_envelope.BASELINE_FRACTION_PERCENTILE --> | One bad night in ten is always flagged. | Count flagged nights over many nights. |
| WAY-D44 | Guide cycles an exposure must span for guiding error to show | 3 | `MINIMUM_GUIDE_CYCLES_PER_EXPOSURE` <!-- impl: wayfindinglib.analytics.performance_envelope.MINIMUM_GUIDE_CYCLES_PER_EXPOSURE --> | Short frames pull the star width up or good frames are dropped. | Plot star width against exposure length. |
| WAY-D45 | Smallest samples: 20 frames for star width, 100 guide samples, 10 frames per night, 20 exposures per night | 20, 100, 10, 20 | `MINIMUM_IMAGE_QUALITY_SAMPLES` <!-- impl: wayfindinglib.analytics.performance_envelope.MINIMUM_IMAGE_QUALITY_SAMPLES -->, `MINIMUM_SAMPLES_PER_SESSION` <!-- impl: wayfindinglib.analytics.performance_envelope.MINIMUM_SAMPLES_PER_SESSION -->, `MINIMUM_FRAMES_PER_NIGHT` <!-- impl: wayfindinglib.analytics.performance_envelope.MINIMUM_FRAMES_PER_NIGHT -->, `MINIMUM_ATTEMPTS_PER_NIGHT` <!-- impl: wayfindinglib.analytics.performance_envelope.MINIMUM_ATTEMPTS_PER_NIGHT --> | A handful of frames sets a limit. | Bootstrap the median at smaller sample sizes. |
| WAY-D46 | Share of clean start times that makes an exposure length reliable | 0.5 | `RELIABLE_CLEAN_FRACTION` <!-- impl: wayfindinglib.session_analysis.guiding.processing.measure_exposure_feasibility.RELIABLE_CLEAN_FRACTION --> | Advice fires too early or too late. | Compare with the share of frames you kept at each length. |
| WAY-D47 | Match window between an Ekos exposure and a frame, and the dark-frame temperature tolerance | 30 s, 3 degC | `MATCH_WINDOW_SECONDS` <!-- impl: wayfindinglib.session_analysis.capture.pre_processing.assess_capture_input_quality.MATCH_WINDOW_SECONDS -->, `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` <!-- impl: astrometricslib.DEFAULT_DARK_TEMPERATURE_TOLERANCE_C --> | Frames mismatched, or darks that do not suit. | Compare Ekos and frame start times; compare dark residuals at several temperature offsets. |
| WAY-D48 | Saturated pixels that make a star count as clipped, and the share of frames that makes a length clip | 4 px, half | `SATURATED_BLOB_MINIMUM_PIXELS` <!-- impl: astrometricslib.SATURATED_BLOB_MINIMUM_PIXELS -->, `SATURATED_FRAME_FRACTION` <!-- impl: astrometricslib.SATURATED_FRAME_FRACTION --> | Hot pixels or cosmic rays read as clipped stars. | Compare with a visual check of the brightest star's peak. |
| WAY-D50 | Dither frequency screen name in the sequence summary | "Ekos Capture module, sequence queue, job settings, Dither" | `EKOS_CAPTURE_DITHER_SCREEN` <!-- impl: wayfindinglib.models.planning.sequence_summary.EKOS_CAPTURE_DITHER_SCREEN --> | The user looks for the setting on the wrong Ekos screen. | Compare the text with the screen names in your Ekos release. |
| WAY-D51 | Ekos screen name for a capture job's settings | "Ekos Capture module, sequence queue, job settings" | `EKOS_CAPTURE_SCREEN` <!-- impl: wayfindinglib.models.planning.sequence_summary.EKOS_CAPTURE_SCREEN --> | Same as above. | Same as above. |
| WAY-D52 | Ekos screen name for dither size | "Ekos Guide module, Options, Dither" | `EKOS_GUIDE_OPTIONS_SCREEN` <!-- impl: wayfindinglib.models.planning.sequence_summary.EKOS_GUIDE_OPTIONS_SCREEN --> | Same as above. | Same as above. |
| WAY-D53 | A filter first seen on a camera is accepted with a note, not refused | Note only, when the filter list comes from library frames | `AllowedCaptureValues.check` <!-- impl: wayfindinglib.analytics.result_quality.allowed_capture_values.AllowedCaptureValues.check --> | A mistyped filter is accepted. | List the filters in the equipment configuration so unlisted ones are refused. |
| WAY-D54 | Rotation sign: increasing row points `rotation_deg` east of north, increasing column a further 90 degrees clockwise | Convention, not measured | `calculate_panels` <!-- impl: wayfindinglib.tasks.planning_tasks.mosaic_tasks.calculate_panels --> | Mosaic panels mirror the real field. | Solve one frame taken at a known rotation and compare its center with the panel center. |
| WAY-D55 | Radius within which a pointing matches a library target | 0.5 degrees | `MATCH_RADIUS_DEGREES` <!-- impl: wayfindinglib.tasks.control_tasks.target_changes.MATCH_RADIUS_DEGREES --> | A target is missed or a neighbor is matched (M 81 and M 82 are 0.6 degrees apart). | Compare the unsolved mount pointing with the solved frame centers over many slews. |
| WAY-D56 | Distance that makes a slew end count as a new position | 0.25 degrees | `NEW_POSITION_DEGREES` <!-- impl: wayfindinglib.tasks.control_tasks.target_changes.NEW_POSITION_DEGREES --> | Centering steps or meridian flips are listed as new targets. | List the slew-end distances of centering steps on your mount. |
| WAY-D57 | Time window to find a mount position near a state change | 120 seconds | `POSITION_WINDOW_SECONDS` <!-- impl: wayfindinglib.tasks.control_tasks.target_changes.POSITION_WINDOW_SECONDS --> | A change of target has no position and is dropped. | Measure the gap between a tracking start and the nearest logged position. |
| WAY-D58 | Largest live-status reply | 30,000 characters | `MAXIMUM_STATUS_CHARACTERS` <!-- impl: wayfindinglib.tasks.control_tasks.live_session_status_task.MAXIMUM_STATUS_CHARACTERS --> | The transport cuts the reply (it cuts at 40,000), or a useful reply is trimmed too early. | Compare the reply size with the transport limit on a busy night. |
| WAY-D60 | Share of a setting's frames set aside that makes the frames-lost rule warn | 0.05 (one frame in twenty) | `FRAME_LOSS_WARNING_FRACTION` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.FRAME_LOSS_WARNING_FRACTION --> | A real loss goes unmentioned, or a single trailed frame is called a warning. | Count the frames the stacker set aside on your nights and compare with the loss in effective time. |
| WAY-D61 | "Take N more" in the report: fewest usable frames, and rounding of a proposed exposure | 10 frames; round up to a multiple of 10 s | `MINIMUM_FRAMES_FOR_FORECAST` <!-- impl: wayfindinglib.tasks.planning_tasks.target_report_tasks.MINIMUM_FRAMES_FOR_FORECAST -->, `EXPOSURE_ROUNDING_SECONDS` <!-- impl: wayfindinglib.tasks.planning_tasks.target_report_tasks.EXPOSURE_ROUNDING_SECONDS --> | A forecast from too few frames copies a thin sky history; a proposed exposure is longer than needed. The count itself is the design estimate WAY-D30. | Replay a target's nights as the plan describes and compare the forecast with the stack that followed. |
| WAY-D62 | The warning that more frames will not help, from the stop signal over the target's library stack runs | Raised when doubling the data is predicted to cut the noise by under 10% (WAY-D32), from two or more runs | `stop_statement` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.stop_statement --> | A real noise floor is missed with few runs, or a false stop is raised. | Test on a target with a known fixed pattern, before and after the fix. |
| WAY-D63 | What counts as a matching calibration frame | Darks: camera, gain, offset, binning, exposure within 0.5 s, sensor temperature within 3 °C (WAY-D47). Flats: camera, filter, gain, offset, binning. Biases are not asked for | `find_calibration_gaps` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.find_calibration_gaps --> | A usable dark is called a gap, or a poor match is accepted. | Stack with a dark that differs in one setting at a time and compare the stack noise. |
| WAY-D64 | Share of captured time lost to a bright sky that makes the sky-cost rule recommend darker nights | 0.10 | `SKY_COST_MINIMUM_SHARE` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.SKY_COST_MINIMUM_SHARE --> | A small loss is called a problem, or a large one is missed. The Moon's part is model-derived (WAY-D29). | Compare the predicted Moon share with the effective time of moonlit and moonless nights of one target. |
| WAY-D65 | Altitude cuts, frames needed on each side and loss that makes the altitude-cost rule fire | Cuts 30, 40 and 50 degrees; 5 frames per side; 0.15 | `ALTITUDE_CUTS_DEG` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.ALTITUDE_CUTS_DEG -->, `ALTITUDE_MINIMUM_FRAMES_PER_SIDE` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.ALTITUDE_MINIMUM_FRAMES_PER_SIDE -->, `ALTITUDE_MINIMUM_RELATIVE_LOSS` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.ALTITUDE_MINIMUM_RELATIVE_LOSS --> | Altitude is mixed up with time of night, so part of the loss may be the sky or the seeing of that hour. | Plot frame efficiency against altitude on one night. |
| WAY-D66 | Sharpness trade-off: share of frames kept, least gain and fewest frames | Keep 70%; the stars must narrow by at least 5%; 20 frames | `SHARPNESS_KEEP_FRACTION` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.SHARPNESS_KEEP_FRACTION -->, `SHARPNESS_MINIMUM_GAIN` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.SHARPNESS_MINIMUM_GAIN -->, `SHARPNESS_MINIMUM_FRAMES` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.SHARPNESS_MINIMUM_FRAMES --> | The noise change is a forecast from sky and read noise, and the width of the stack is not the median of its frames. | Stack the sharpest 70% of a target's frames and compare the stack's star width and noise with the forecast. |
| WAY-D67 | Starting point for a target with no frames | Sky variance at least 9 read-noise variances (WAY-D24), using the moonless sky while the target is above 30 degrees; the clipping cap is not computed without a zero point that is not exploratory. The setting is chosen by goal (the spectroscopy filter for a spectrum, any other filter otherwise). For a spectrum the clipping cap has its own "not computed" reason (the zero-order image of the brightest star). The camera limits (clip ceiling, saturation threshold, photometric linearity limit) are read from the camera profile with their sources | `build_starting_point` <!-- impl: wayfindinglib.analytics.result_quality.starting_point.build_starting_point -->, `STARTING_POINT_MINIMUM_ALTITUDE_DEG` <!-- impl: wayfindinglib.tasks.planning_tasks.target_report_tasks.STARTING_POINT_MINIMUM_ALTITUDE_DEG --> | The first exposure is too short (read noise) or too long (a bright star clips). It is always labeled a starting point. | Take the first frames at the proposed exposure and read `frame.sky_noise_e` against `frame.read_noise_e`. |
| WAY-D68 | Choices that decide which numbers a setting is judged against | Science goal from `science_goal_for_target` (WAY-D74; spectral when more than half the light frames are spectral); efficiency reference is the equipment baseline, else the physical reference; a stack belongs to the setting of most of its frames; the seeing constraint fires for an extended object or slitless spectrum when the median star width is above the reference width, and for a point source when it is within 10% of the physical reference (`SEEING_AT_REFERENCE_TOLERANCE`). The statement's limit (the reference width) cites WAY-D21 (physical) or WAY-D22 (baseline) | `assign_stacks_to_settings` <!-- impl: wayfindinglib.tasks.planning_tasks.target_report_inputs.assign_stacks_to_settings -->, `LibraryInputs.reference_for` <!-- impl: wayfindinglib.analytics.result_quality.library_inputs.LibraryInputs.reference_for -->, `seeing_limit_statements` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.seeing_limit_statements -->, `SEEING_AT_REFERENCE_TOLERANCE` <!-- impl: wayfindinglib.analytics.result_quality.report_rules.SEEING_AT_REFERENCE_TOLERANCE --> | A galaxy that is also a star field, or a photometry target, gets the wrong goal exponent; a stack of mixed settings is judged against one. A point source is called seeing-limited when better focus or guiding would still sharpen it. | Compare the efficiency of a target under each goal; list the stacks whose frames hold more than one setting. |
| WAY-D69 | Checklist return and the priority boost rule | A ticked item returns when its rule still fires on a row measured after the tick, on a night that began on or after the tick's night; a row-less statement returns while it still fires. The boost fires on one or more warnings and on a confirmed asteroid or a variable candidate | `build_target_dataset_report` <!-- impl: wayfindinglib.tasks.planning_tasks.target_report_tasks.build_target_dataset_report -->, `place` <!-- impl: wayfindinglib.tasks.planning_tasks.scheduling.place --> | A fix is called failed when the data is older than the fix, or a package is raised for a minor warning. | Tick an item, take frames, and check that it returns only after a new measurement trips the rule. |
| WAY-D70 | Agreement width of the forecast check, and the star-width stand-in | 2σ (σ is one tolerance, one standard error); the weighted star width of Siril is replaced by the effective star width in arcseconds. The forecast given the frames taken uses the weights the run recorded, with no mode guessed; the star-width stand-in is used only for the planned forecast. Errors are unpaired although the two stacks reuse apertures, which is conservative. One-sided test, false alarm about 2.3% | `AGREEMENT_TOLERANCES` <!-- impl: wayfindinglib.analytics.result_quality.forecast.AGREEMENT_TOLERANCES --> and `stack_frames_from_observations` <!-- impl: wayfindinglib.analytics.result_quality.forecast.stack_frames_from_observations --> | A shortfall is called as forecast (width too large) or a normal scatter is called a shortfall (width too small); star-width weights are forecast a little wrong. | Replay a target's nights, count how many checks fall outside two tolerances when the stack is known to be as forecast, and compare the weights Siril wrote with the forecast ones. |
| WAY-D71 | Assumptions of the hour in the ranking | A dataset under 2 hours is thin; a dither every 3 frames; one autofocus run per hour. Tonight's ranking and the report's "take N more" both price dithers and autofocus with these two values, so they price an hour the same way | `THIN_DATASET_SECONDS` <!-- impl: wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks.THIN_DATASET_SECONDS -->, `DITHER_EVERY_FRAMES` <!-- impl: wayfindinglib.analytics.result_quality.capture_overheads.DITHER_EVERY_FRAMES --> and `AUTOFOCUS_RUNS_PER_HOUR` <!-- impl: wayfindinglib.analytics.result_quality.capture_overheads.AUTOFOCUS_RUNS_PER_HOUR --> | The hour holds more or fewer frames than the sequence takes, so every gain per hour is off by the same factor. A thin target is not called out, or a deep one is. | Count the frames of a real hour of your sequence and compare them with the ranking's frames in the hour. |
| WAY-D72 | What the gain of the ranking means | One minus the noise of the stack after one clock hour over its noise now, with the new frames copying the target's past frames and tonight's modeled sky | `score_candidate` <!-- impl: wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks.score_candidate --> | A target is ranked high that gains in noise but not in the sharpness or the precision the user cares about. | Compare the ranked order with the order of the real gains after an hour on two targets. |
| WAY-D73 | Fewest cycles for a period claim | 2 | `MINIMUM_CYCLES_FOR_PERIOD` <!-- impl: wayfindinglib.analytics.result_quality.star_result_rules.MINIMUM_CYCLES_FOR_PERIOD --> | A period seen for less than two cycles is trusted, or a good one is called short. | Test the period search on a light curve with a known period cut to different lengths. |
| WAY-D74 | Science goal read from the target's kind | Spectral frames: slitless spectra; a photometry record: bright-star photometry; Messier, NGC or IC: extended object; any other name: faint point source. One function serves the report, the ranking and the night review. Tonight's ranking lists follow the goal: slitless spectrum, spectroscopy; bright-star photometry or period detection, photometry; others, imaging | `science_goal_for_target` <!-- impl: wayfindinglib.analytics.result_quality.science_goal.science_goal_for_target --> | Seeing is counted as lost time for an extended object, or not counted for a star, so the loss account is off. | Compare the goal with what you imaged each target for. |
| WAY-D75 | Text that marks a frame as set aside | A row whose action contains `_excluded`, read by one definition, `set_aside_reasons` <!-- impl: wayfindinglib.analytics.result_quality.library_inputs.set_aside_reasons -->, for the report and the review. The stored row does not say clouded or trailed, so the reason shown is the row's own words, and a note says when no frame is recorded as set aside. The report no longer lists the `_excluded` folders, so its counts need the stacker to write `action_taken` and can be lower than a folder listing | `SET_ASIDE_MARKER` <!-- impl: wayfindinglib.analytics.result_quality.library_inputs.SET_ASIDE_MARKER --> | Set-aside frames are missed, so the lost time is shown lower. | List the `_excluded` folders of a night and compare the frames with the rows that carry the marker. |
| WAY-D76 | Efficiency reference for the night review | The user's own baseline when there are at least three nights of the setting; otherwise the telescope's diffraction limit with the site's seeing. Both references are reported when both exist, with the efficiency against the one not in use. The physical reference uses the telescope named by the capture setting. The reviewed night is left out of its own baseline. The site is read, never stored (a note says when none is stored) | `LibraryInputs.reference_for` <!-- impl: wayfindinglib.analytics.result_quality.library_inputs.LibraryInputs.reference_for --> | A lasting fault hides in the baseline; a physical limit makes every frame look worse than it can be. | Compare the loss of one night against both references. |
| WAY-D77 | Largest reply of the target report, tonight's ranking and the night review, and what is cut when it is too long | 40,000 characters of compact JSON (the reply limit of the MCP tools). The report drops its context section first. Then each statement of the report or the review keeps 20, then 5, then 1 references. The ranking leaves out its lowest candidates. The budget is the limit less 10% headroom (`REPLY_HEADROOM_FRACTION` <!-- impl: wayfindinglib.analytics.result_quality.reply_budget.REPLY_HEADROOM_FRACTION -->, design estimate), and a reply is measured the way the MCP tool registry measures it (`model_dump()` and compact `json.dumps(default=str)`). The three reads use one helper, `fit_reply` <!-- impl: wayfindinglib.analytics.result_quality.reply_budget.fit_reply -->; the notes a cut adds are inside the measured reply. Moving the trimming to the MCP edge is issue #96 | `MAXIMUM_REPLY_CHARACTERS` <!-- impl: wayfindinglib.analytics.result_quality.reply_budget.MAXIMUM_REPLY_CHARACTERS -->, `EVIDENCE_STEPS` <!-- impl: wayfindinglib.analytics.result_quality.reply_budget.EVIDENCE_STEPS -->, `fit_report_to_budget` <!-- impl: wayfindinglib.tasks.planning_tasks.target_report_tasks.fit_report_to_budget -->, `fit_ranking_to_budget` <!-- impl: wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks.fit_ranking_to_budget -->, `fit_night_quality_to_budget` <!-- impl: wayfindinglib.tasks.control_tasks.night_quality_task.fit_night_quality_to_budget --> | A long reply is cut by the transport without warning, or a statement loses references the reader needed, or a target is left out of the ranking. Each cut is stated in the reply. | Build the reply for your busiest target, library or night, count its characters, and compare what was cut with what you would have opened. |
