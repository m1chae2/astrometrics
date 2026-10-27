# Imaged Target Suggestions Plan

This plan makes "take N more frames" possible for the targets already imaged. It has three steps,
done in order:

1. **Part 1, the Nikon fix.** The saturation limits and the D5300's read noise.
2. **Part 2, exposure-group weights.** 2a: a stacking run records frame weights, gains and the
   predicted noise for stacks combined from several exposure lengths. 2b: the forecast and the
   "take N more" suggestion use them.
3. **One forced restack of every ZWO ASI533MM imaging target**, after parts 1 and 2 land (decision D6).

The code is the source of truth. Line numbers drift, so this plan names files and functions.

## Status

Status on 2026-10-25: version 4, revised after its board review (appendix D); nothing built.
Versions 1 to 3 tried to measure or rebuild the record of the existing stacks without stacking
again; the board reviewed each the same day (appendices A to C). The user chose one forced restack
after parts 1 and 2 instead, and dropped the rebuild. Decisions D4 and D5 were made on 2026-10-25;
D1 to D3 wait. Ready to build once the user approves.

Status on 2026-10-26: part 1 and the Image Planner filters are built (f542a026, 5df591d0), and part
2a is built (73faef14). The validation on a copy is under way. Decision D6 (2026-10-26): the work
now covers the ZWO ASI533MM Pro only. The Nikon D5300's read noise, its forecasts and its restack
are deferred (decisions D2 and the Nikon parts of section 3.3 wait with it). Part 1's saturation
levels stay: they leave the ZWO unchanged and correct the Nikon's limit.

Later on 2026-10-26: the validation found four gaps that block a forecast check on M 27
(section 3.6). They are fixed before part 2b and before any rehearsal. D7, D8 and D9 were decided the
same day. Gaps 3 and 4 and the D7 fallback are built (uncommitted). Gap 3's cause is
confirmed on M 27's stored registration file: the 120 s frame Siril dropped has an all-zero
registration line, which looked like a second reference frame. Part 2a's covered area was also never
passed to the noise step; that is fixed. Next: the gap 1 diagnosis on the copy.

Still on 2026-10-26: part 2b is built (uncommitted, unit tests only; sections 3.3 and 3.6). The ZWO rehearsal and the restack are next.

Later still on 2026-10-26: the rehearsal on the copy found that one 300 s frame folded into M 27's 120 s group left every frame weight and the predicted noise unmeasured. The user decided D10 (option B): a folded group is modeled, so it gets weights and a predicted noise. D10 and the restack tooling (section 4) are built, uncommitted on `134-group-weights-nikon` after 30f53f77. The rehearsal ran on M 27, M 42 and M 81 (section 4.5). The rest of the ZWO targets on the copy come next. The real library is restacked only on the user's go-ahead.

Last on 2026-10-26: the restack script measures every frame before it stacks, and a stack that has no plate solution from a frame is plate-solved in memory (section 4.1). Capture settings round the pixel scale (stored settings are merged on first open). The half-difference model uses `k_i` (section 3.7). D11 is built (section 3.9), and the "take N more" wording has a practical limit (section 3.10, decision D12). All of it is uncommitted on `134-group-weights-nikon` after dacb635a, with unit tests and one copy run on M 13 (section 4.5). The full ZWO pass on the copy has not been rerun with these changes.

Then on 2026-10-26: decisions D13 and D14 (sections 3.11 and 4.1). The floor's random part now comes from the half-difference when one exists, and the model test is the fallback with a shared error budget; the loss account follows. The restack runs the half-difference for targets whose measured over predicted noise is 1.1 to 2.0. All of it is uncommitted on `134-group-weights-nikon` after 9ed5af01, with unit tests and one copy run on M 13 (section 4.5).

Late on 2026-10-26: the full ZWO rerun on the copy found three faults, now fixed and uncommitted (section 3.12); the rerun was stopped after four targets. The user decided new names (D15, being built with a schema version 5 migration) and asked for the algorithm to follow mission practice: decisions D16 to D24 were proposed (section 3.13) and decided the same day: the recommended option for each, D24 deferred. They are built after the rename.

Later on 2026-10-26: D15 to D23, D25 and the metrics of D1 are built (uncommitted on `134-group-weights-nikon` after 817c7da4; unit tests only, no real-data run): the renames and the quality-store schema 5 migration, the rerun fixes, the bias injection script (written, not yet run), the measured model error, the possible band, the odd–even difference for every stack, the aperture-size exponent, the per-aperture record with the line-pattern rows and the shape tests with their three rules, the master dark's predicted noise, the history per setup, the earlier-nights minimum of 8 frames, and the two depth metrics (the 5σ point-source depth and the 3σ surface-brightness limit over 10 arcsec by 10 arcsec). D24 stays deferred. Not built: the per-target depth-level dropdown of D1 (Quick look, Good, Deep), which waits for the user to set the level values from the depths of the copy's processed targets. The papers and the register carry the methods, the stated biases of the depth and the Design estimate rows STK-D30 to STK-D35 and WAY-D88 to WAY-D93.

## 1. Why suggestions from imaged targets are missing

"Take N more" is a forecast: add these frames and the noise falls this much. The library shows it
only after the forecast has worked once for that target (`_forecast_gate_open` in
`wayfindinglib/tasks/planning_tasks/target_report_tasks.py`). The check, `forecast_check` in
`wayfindinglib/analytics/result_quality/forecast.py`, compares two library stack runs. A pair is
checkable only when both stacks have a normalized noise with its error, both member lists are known,
every member frame has stored measurements (exposure, sky rate, read noise) and a known weight, and
the later stack holds frames the earlier one did not.

Three things stop this today:

- **No run records.** All 2,667 stack rows are `backfilled`. None holds the noise or the weights,
  and no frame-level rows exist. The noise metric arrived with PR #127 (2026-10-24), after the last
  stack was made. A normal stacking run records all of it, frame measurements included
  (`record_stage_frame_quality` in `astrometricslib/pipelines/stacking/frame_measurement_run.py`).
- **Exposure groups.** When a stack combines exposure groups and a Siril weighting is set,
  `stack_membership.determine_stack_members` marks every weight unknown, and
  `record_science.compute_expected_noise` refuses (`MULTIPLE_EXPOSURE_GROUPS_REASON`). This happens
  in a normal run. Most deep-sky stacks went through the group path (`groups/*_manifest.json`). A
  group stack also has no single reference frame (one zero-shift line per group), and the merged
  diagnostics keep only the first group's Siril data (`stack_runner._merge_diagnostics`).
- **The Nikon D5300.** Its read noise is not measured, and an assumed read noise does not count, so
  its frames cannot enter a forecast. Its saturation threshold (65,000 ADU, assumed) is above its
  measured clip ceiling (16,383 ADU), so clipped stars can become reference stars.

After the three steps each target has a complete run record from the restack, and gets "take N
more" after its next normal stack that adds new frames, provided that pair is checkable (section
4.4). A restack made before part 2 would leave the group stacks without weights, so it comes last.

## 2. Part 1: the Nikon fix

### 2.1 Two limits, not one

The code holds two rules, and they stay two:

- **Pixel-saturation level:** the threshold capped at the clip ceiling. Used where saturated pixels
  are counted: `resolve_saturation_threshold` (`pipelines/shared/quality/saturation.py`),
  `frame_measurement.py` around line 2240, `stage.py` around line 417, `frame_statistics.py`, the
  spectroscopy `pipeline.py` and `calibration_tuner.py`, and wayfindinglib `envelope_assembly.py`.
- **Usable-peak level:** also capped at the linearity limit. Used where a star must be photometric:
  `stack_noise._saturation_check`, `frame_measurement.py` around line 750, and
  `variability_analyzer.py` (which passes the linearity limit separately).

Add two `CameraProfile` properties (`astrometricslib/models/camera_profile.py`), each returning a
value with its kind, so "assumed" carries through. Every caller uses one of them. Folding the
linearity limit into one limit would lower the ZWO ASI533's saturation level from 65,000 to
60,000 ADU and change the meaning of `FRAME_SATURATED_PIXEL_FRACTION` without a version change.

- A clip ceiling of kind `assumed` caps the limits too, and the row says the limit is assumed.
- At 16,383 ADU the check is "at or above" the clip itself, on the reference frame only; the noise
  row says so.
- Clear the stored `saturated_pixel_fraction` of the Nikon frames: 1,963 frames hold values computed
  at 65,000 ADU, and `stage.py` measures the field only when it is empty.
- Update the stacking register entry for the saturation limits and the Implementation document.

### 2.2 The user's part

- Set the D5300 profile's `saturation_threshold_adu` to 16,383 ADU in the app's settings. The code
  cap already enforces it; the setting keeps the profile honest.
- Measure the D5300's gain and read noise with Measure noise in Camera Profiles
  (`calibration:measure_sensor_noise`) at the setting every Nikon imaging frame uses (ISO 800,
  offset 0, binning 1). It needs bias frames and pairs of flat frames at that ISO (decision D2). The
  mean and variance gain holds for a Bayer sensor; the register entry says so.

### 2.3 Tests

- Each property caps correctly, with and without a linearity limit and an assumed ceiling.
- A static test: no module outside `camera_profile.py` and the store reads
  `saturation_threshold_adu.value` directly.

## 3. Part 2: exposure-group weights

### 3.1 How the combine works

`stack_runner._stack_exposure_groups` stacks each exposure length on its own, with Siril's
weighting (`wfwhm`) inside the group. `exposure_groups.combine_exposure_group_images` then combines
the groups:

- each group image is divided by its exposure (counts per second);
- a gain per group, measured inside the combine, corrects Siril's rescaling:
  `(image / t_g - sky_g) / G_g + reference sky`;
- the groups are averaged with weights `W_g = N_g t_g^2 / s_g^2` (frames stacked, exposure, frame
  noise), set before the gain is applied; `s_g` comes from `estimate_background_noise` on three
  sampled raw frames per group (`FRAMES_SAMPLED_PER_GROUP`);
- the result is scaled back by the frame-count-weighted mean exposure `t̄`;
- where a group is saturated or empty the combine weights pixel by pixel, clipped groups drop sky
  pixels below a noise floor (`CLIPPING_FLOOR_SIGMAS`), and shifted borders are dropped;
- `split_frames_by_exposure` folds a group of fewer than 5 frames into the nearest exposure, and
  Siril normalizes the mixed frames with a scale it does not save. Decision D10 models that scale
  (section 3.7).

So frame `i` of group `g` enters the stack, in rate units, with the coefficient
`W_g ŵ_i / (ΣW · G_g)`, where `ŵ_i = w_i / Σw_g` is its Siril weight inside the group.

### 3.2 Part 2a: stacking (astrometricslib)

- **The combine returns a result object** with the gains it applied, the `measured` flag of each,
  the sky levels and the trusted and covered masks, instead of the image alone. Its callers (19 test
  references and `compare_group_steps_with_siril.py`) change with it.
- **Per-group Siril data.** Each `exposure_group_weights` entry keeps its own frame paths,
  registration facts and inclusion flags. `_merge_diagnostics` keeps joining the flat lists as
  today, because `stage.py` and `registration_record.py` read them.
- **Members** are the registered, included frames of each kept group, not every path in the group.
- **Frame weights.** `stacking.frame_weight` stores `W_g ŵ_i`, without the gain; the weights sum to
  `ΣW`. The gain stays its own value: the combine's applied gain is stored per group (the existing
  `stacking.group_gain_mid_range` row holds an earlier measurement, so the applied one is recorded
  beside it). `normalized_weights` and both forward models keep their own normalization, and the
  gain enters the variance, not the weight.
- **Predicted noise.** `compute_expected_noise` and `expected_noise.py` handle group stacks:
  `var_rate = Σ (W_g ŵ_i)^2 v_i / (G_g^2 t_g^2) / (ΣW)^2`, where `v_i` is frame `i`'s variance, and
  `σ = t̄ · STACK_UNITS_PER_ADU · sqrt(area · var_rate)`. The predicted sky is the reference group's
  sky per second times `t̄`. A group whose gain was not measured uses the combine's `G = 1`.
- **Unknown stays unknown**, with a reason:
  - a group that holds folded frames of another exposure (until D10, section 3.7);
  - a group stacked with `-weight=noise`;
  - a group whose gain the combine could not measure;
  - a group whose frame noise could not be read (today `frame_noises = [1.0] * n`, an invented
    value);
  - any group whose weights Siril did not report.
- **Clipped groups.** The predicted noise is refused when `clipped_exposure_groups` is not empty.
  `_describe_single_group` also fills that list, so a clipped single-group stack is refused too, on
  purpose.
- **Reference frame and header.** The reference frame and the stack's header come from the
  heaviest group (`reference_index`), whose identity line the combine aligns every group to. Today
  the header comes from the group with the most frames.
- **Apertures** keep out of any group's uncovered border, using the combine's masks.
- **Public rules.** Two pure functions join `wfwhm_weights` and `choose_stack_weight` in the public
  API (`astrometricslib/__init__.py`, `__all__` and the deferred exports), pinned in
  `test_public_surface.py`: the group frame-weight rule (in-group weights, exposures, frame noises,
  gains) and the fold rule over exposure lengths, which `split_frames_by_exposure` itself calls.
- **Calibration identities** (decision D4): a side table beside the measurements, modeled on
  `quality_capture_setting`, keyed by run id and stack subject. It holds the identities of the flat,
  bias and (for the Nikon) dark masters the members used, the optic, the Siril version and the
  stacking algorithm version. Schema version 2 goes to 3 with a migration and a backup
  (`quality_store.py`, rule 8). A `QualityRecords` method shaped like `capture_settings` reads it,
  and it gets an observe disposition in `mcp_servers/inventory/tool_dispositions.py` and the
  regenerated manifest. The noise definition version is already on every row and is compared
  directly.
- A separate pull request under the same issue, after 2a: the flux-scale reference stars avoid
  cores patched from other groups.

### 3.3 Part 2b: the forecast (wayfindinglib)

**Status 2026-10-26: built, uncommitted on `134-group-weights-nikon` after 9613ffc1. Unit tests only; no real-data run yet.** What differs from the text below, and what is left:

- The aperture-set identity is stored as a number (`stacking.aperture_set_number`, and `stacking.half_difference_aperture_set_number`), so older stacks have none and a pair with one is not checkable.
- A pair whose earlier stack is a single group and whose later stack has several is not checkable. This is stricter than this plan.
- The stop signal can come from one stack's floor.
- The camera profile has no cooled flag, so a changed dark master confounds a pair unless the camera name contains `zwo` or `asi`.
- The Nikon sensor-temperature band is not stored and is not checked.
- Nikon forecasts are not yet labeled exploratory.
- No row links a frame to its group or a group to its stack. The forecast places each frame by its exposure length with the stacker's fold rule.
- `CalibrationIdentities` is not exported at the `astrometricslib` top level.
- A planned new exposure length needs at least 5 frames. A smaller set is folded into a neighbor group. Since D10 (section 3.7) the stacker records weights for that group and the forecast scales each folded frame, so the plan is forecast normally.

- **A group weighting mode** for both the earlier run and the planned one: `wfwhm` inside each
  group and `W_g = N_g t_g^2 / s_g^2` per group, through the public group rule. `identify_weighting_mode`
  and `_check_pair` stop falling back to `wfwhm` over all frames.
- **A planned group's frame noise.** An existing group keeps its measured `s_g`. A new ZWO group's
  `s_g` is predicted from the sky rate and the read noise, in ADU (`StackFrame` is in electrons; the
  conversion is explicit). A planned group keeps the gain of the nearest group. If the new frames
  would change the heaviest group, the forecast says so and the pair is confounded (below).
- **New frames** go through the public fold rule: frames of an exposure length that would form a
  group of fewer than 5 are placed where the stacker would place them. The forecast cannot predict
  a group the stacker leaves out; it says so.
- **The suggestion uses the same model.** `target_report_tasks._more_frames_for_setting` and the
  take-more solver (`noise_growth.py`) use the group mode and the fold rule, so the number users see
  comes from the model the gate checked. A suggestion never asks for fewer than 5 frames of a new
  exposure length. Other users of these functions (`tonight_ranking_tasks.py`,
  `report_rules.find_sharpness_tradeoff`) are checked and updated.
- **The two forward models agree.** `noise_growth.forecast_noise_with_weights` (rate units) and
  `expected_noise.py` (stack units) give the same answer for the same frames, weights and gains; a
  test in wayfindinglib's tests checks it.
- **Confounded pairs** are kept out of the gate: the flat or bias master differs, the heaviest
  group differs, the Siril or stacking algorithm version differs, the noise definition version
  differs, or (for the Nikon) the dark master, sensor temperature band or optic differs. A pair with
  no calibration record counts as confounded. A new ZWO dark set or a new exposure length does not
  confound. The gate's own rule is unchanged.
- **New frames must be new.** If the later stack holds frames that were not in the earlier stack
  but are older than the earlier stack's made time, the pair is not checkable.
- **Nikon forecasts are exploratory.** The raw mosaic inflates `s_g` with the sky's colour step,
  and the stack's noise is measured on debayered planes. Nikon "take N more" carries an exploratory
  label with a review date until one Nikon group check passes, and uses the measured `s_g`.

### 3.4 Validation and register

All on a copy of the library (`build/agent_tools/realrun.sh`) or with synthetic data:

- **The Siril weight test.** Give each frame its own small known tile, set the `.seq` widths by
  hand, and stack with `-nonorm` and no rejection; each tile's level is that frame's weight.
  Include one excluded image. Confirm that Siril applies `wfwhm` with `-nonorm`. Record the Siril
  version.
- **The impulse test:** add a small known tile, well inside the rejection limit, to one frame per
  group, restack, and difference with the unmodified restack. The tile goes in a frame that is not
  sampled for `s_g`, away from stars and inside every group's covered area, and is measured against
  a ring around it. Both manifests must show the same `W_g` and `G_g`. Inject into the brightest-sky
  and the darkest-sky frame of a group, because Siril's unsaved normalization follows the sky.
  Pass: each recovered coefficient within 2% of the rule.
- **M 27 without its single 300 s frame** (that frame would be folded): 26 × 30 s, 33 × 60 s,
  22 × 120 s, where the group rule and equal weights differ by 18% to 64%. Pass: measured over
  predicted noise within the STK-36 band (5% plus the aperture error `1.17 / sqrt(n)`) of a
  single-group substack of the same target.
- **One Nikon group stack** (M 51) as an exploratory check.
- **M 57** gives its normalized noise under the current aperture placement, for STK-36. WAY-D83
  (the noise goal) is revisited (decision D1). The goal came from M 57's noise under the first
  placement, which no longer describes the code.
- Register entries for the group weight rule, the gain and `t̄` factors, the three-frame `s_g`, and
  each test. Siril's per-frame normalization scale is left out, as in the single-group path; the
  entry says so.

### 3.5 Tests

- A two-group run stores weights equal to the rule (`N_g` included, gain excluded) and a predicted
  noise equal to the formula.
- Folded, `-weight=noise`, unmeasured-gain and unread-noise groups give unknown weights with
  reasons.
- A group stack names the heaviest group's identity frame as its reference and header.
- A clipped group gives no predicted noise.
- The two forward models agree, with gains other than 1.
- A forecast and a suggestion from a group stack place new frames by the fold rule and never ask
  for fewer than 5 frames of a new length.
- Confound cases: a different flat master, a changed heaviest group, a new Nikon dark master; not a
  new ZWO dark set.
- CI uses synthetic `.seq` and manifest fixtures and the existing synthetic group harness
  (`test_exposure_group_linearity.py`); the real-data runs are register evidence.

### 3.6 Validation findings and the forecast gaps (2026-10-26)

The validation of part 2a on a copy (section 3.4) passed the Siril weight test and failed the
impulse test (the cause is Siril's unsaved normalization scale, decision D9). On M 27 the forecast
could not be checked, for the three reasons below. The numbers are from the copy's stored rows
(`/home/michael/astrometrics_scratch/134-validation/snapshots/`), run on commit 73faef14.

#### 3.6.1 Gap 1: the stack is about 3.5 times noisier than predicted, and flat noise is not why

The 60 s group of M 27 (33 frames, effective count 22.0, sky 933 ADU) measured an empty-aperture
noise of 3.10e-3 ± 0.36e-3 stack units. The prediction was 8.78e-4: sky 8.70e-4, read 1.22e-4.
The flat's own contribution is already stored (`stacking.flat_noise_in_aperture_stack_unit`):
1.17e-4. Added in quadrature it raises the prediction by 1%. The flat's pixel noise is not the gap.

One raw 60 s frame matches the model. Its measured noise, `s_g` in the group manifest, is
37.7 ADU, and the model gives 35.1 ADU (933 ADU of sky at 0.783 e-/ADU and 3.8 e- of read noise).
The 30 s and 120 s groups come out 0.81 and 0.92 of the model, within the coarse steps of `s_g`
(gap 4). So the model holds for a single frame, and the stack misses it.

In pixel units the stack's noise is 26.3 ADU. The frames' random noise averaged over 22 effective
frames is 37.7 / sqrt(22) = 8.0 ADU. What is left, sqrt(26.3² − 8.0²) = 25 ADU, is about 2.7% of
the sky. It does not average down with more frames: a floor. Siril's normalization scale (D9)
would raise the random part by at most 1.4 times and leave the floor about the same.

Possible sources, none confirmed yet:

- **Faint stars.** M 27 lies 3.5° from the galactic plane. The aperture search flagged 9% of the
  image and excluded 82%, which says the field is crowded. Stars too faint to detect still fill
  the apertures, and they are the same in every frame.
- **The calibration.** An analysis of the ZWO previews on 2026-10-02 found a 4% to 8% large-scale pattern from the old
  single flat. M 27's lights at offset 0 are calibrated with offset-10 flats, and biases at
  offset 0 are mostly exact zeros.
- **Fixed sensor patterns** (dark residuals). These are partly averaged, because the frames drift
  84 px across the session.

Why it matters: the forecast check compares the change in noise, not the absolute noise. With a
floor this large, adding frames lowers M 27's noise far less than the 1/sqrt(N) forecast, so
every check falls short "for no known reason" and the gate never opens. The honest suggestion for
such a target is "more frames will not help much; the floor is X". `noise_growth.fit_noise_floor`
already models a floor, but only from two or more stacks.

The fix, in order:

1. **Diagnose (on the copy, about half a day).** Prove the floor and find its kind, using tools
   that exist:
   - Noise against frame count inside the 60 s group: substacks of 8, 16 and 33 frames
     (`subset_stack.py`). A fit of `σ² = a / N_eff + b` gives the floor `b`. Pass: `b` above zero
     by 2 standard errors. If `b` is zero, the excess falls with N and the model misses a random
     term instead.
   - The same random-against-measured split on a high-latitude target (M 101 or M 81) and on M 57.
     A floor on M 27 only points at faint stars. A floor on every target points at the
     calibration.
   - An odd-minus-even difference of two half-stacks. It cancels anything fixed on the sky and
     keeps the random noise, which tests the random part directly. This reopens "half-stack
     noise", which section 7 left out of scope; it is used here as a check only.
2. **Model the floor in the forecast (part 2b, about 1 day).** The random part of a stack comes
   from the measured `s_g` of its groups and the recorded weights, in the same units as the
   measured noise, with no gain or read noise needed. The floor is
   `σ_floor² = σ_measured² − σ_random²`, kept only when it is above zero by 2 standard errors
   (the aperture error, `1.17 / sqrt(n)`). *Revised 2026-10-26 (D13, section 3.11): the error also
   holds the model's own error, because the prediction is not exact, and a half-difference
   measurement replaces the prediction when there is one.* The forecast becomes
   `σ_later² = σ_floor² + σ_random,later²`. The suggestion then states the floor, and stops
   offering frames when the floor makes up most of the noise. Decision D8.
   **Result of step 1 (2026-10-26, on the copy, with the gap 3 and 4 fixes and the D7 fallback in
   place).** The full record is `work/FLOOR_REPORT.md` in the copy folder. The floor is
   `sqrt(measured² − random²)`, with the random part taken from the measured `s_g` and the stored
   weights:

   | Stack | N_eff | Measured | Random from `s_g` | Floor | Floor / sky | Fit `b` |
   | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
   | M 27, 60 s (galactic latitude about −4°) | 22.0 | 3.10e-3 ± 0.36e-3 | 0.93e-3 | 2.95e-3 ± 0.38e-3 (25 ADU) | 2.6% | 3.4σ above 0 |
   | M 57, 60 s (+13°, offset 10, matched flats) | 40.8 | 1.25e-3 ± 0.15e-3 | 0.46e-3 | 1.17e-3 ± 0.16e-3 (9.9 ADU) | 2.4% | 3.3σ |
   | M 81, 60 s (+41°) | 22.1 | 1.13e-3 ± 0.13e-3 | 0.70e-3 | 0.89e-3 ± 0.17e-3 (7.5 ADU) | 1.3% | 1.5σ |
   | M 27, three groups | 29.9 | 2.25e-3 ± 0.26e-3 | 0.51e-3 | 2.19e-3 ± 0.27e-3 (19 ADU) | 3.9% | not run |

   On M 27, going from 5 to 22 effective frames lowers the noise by 22%; the model says 54%. The
   half-stack difference (`measure_half_difference_noise`) gives a random part of 1.1 to 1.4 times the
   `s_g` value, so it confirms the floors on M 27 and M 57 and leaves M 81's at about 2σ. A single
   frame still matches the model. The floor follows galactic latitude, and M 57 has one even with
   matched flats. This favors faint stars and crowding over the offset-0 calibration, with at most a
   common residual of about 1% of the sky. Three targets cannot separate a field cause from a
   multiplicative one by sky level. The floor is confirmed, so D8's one-stack floor goes ahead in
   part 2b.

   Found on the way:
   - The noise row did not name its sky-coordinate source. Fixed in `record_quality.py`.
   - **Apertures from a different stack can block the noise.** The first three-group restack of
     M 27 kept the apertures chosen on the 60 s stack. Their 10 reference stars saturate in the
     120 s reference frame, so no noise was stored ("only 0 of the reference stars can be used").
     With a new aperture set the stack measured, but its noise is then not comparable with the
     60 s stack's. Part 2b must treat a pair with different aperture sets as not checkable, and
     the restack should choose apertures again where the reference group changed. Fixed: a set is
     now kept per camera, optic and reference exposure, and its identity covers the reference stars.
     Part 2b still needs the identity as a structured value on the noise rows (today it is only in
     the reason text), an `aperture_identity` on `StackRunRecord`, and `_check_pair` treating a
     pair with different or missing identities as not checkable.
   - `stacking.frames_stacked_count` says 77 for the three-group stack, while its members and N_eff
     use 76 (the 120 s group stacked 17 of 18). The count (`stage.py`) subtracts only corrupt
     frames. Fixed: the count and the member rows now use one list (definition version 2).
3. **If step 1 points at the calibration,** the cure is the user's: new offset-matched flats,
   biases and darks (the offset test of 2026-10-02 is still open). The forecast's floor term
   reports the problem until then.

#### 3.6.2 Gap 2: frames at offset 0 have no noise entry

The ZWO profile has one `sensor_noise` entry: gain 0, offset 10, binning 1x1. Most ZWO lights are
at offset 0 (all 26 + 33 + 4 of M 27's 30 s, 60 s and 120 s frames at offset 0, and M 57's 30 s
frames). `CameraProfile.sensor_noise_for` matches exactly, with no fallback, so these frames get
no read noise or gain. Predicted noise is then missing, and a forecast check is not possible,
because every member frame needs a stored read noise.

The offset is a constant added to every pixel before digitizing. It changes neither the gain
(electrons per ADU) nor the read noise, as long as the noise is not cut off at zero. The offset-0
values cannot be measured on this camera anyway: offset-0 biases are 96% exact zeros, so the read
noise of a bias pair is cut off. A light frame with a sky well above zero is not cut off (M 27's
frames have no zeros, `frame_zero_fraction` = 0).

Options (decision D7, decided 2026-10-26: option A):

- **A. An offset fallback in the library** (recommended, about half a day). `sensor_noise_for`
  uses the entry of the same gain and binning at another offset when the frame is not clipped at
  zero (`CLIPPED_FRAME_ZERO_FRACTION`). The value keeps its kind (measured). Its source says
  "measured at offset 10, used at offset 0: the offset does not change gain or read noise". A
  clipped frame gets nothing, as today. Tests cover the fallback, the clipped refusal and the
  exact match winning.
- **B. A second config entry** for offset 0, entered by the user and copied from offset 10. It is
  simple, but the entry claims a measurement that was not made at that setting.
- **C. Offset 10 from now on.** Old offset-0 frames never take part in a forecast. M 27, M 57 and
  most ZWO targets would wait for new frames.

#### 3.6.3 Gap 3: the group stack has no plate solution, and plate solving is not the fix

Neither M 27 stack on the copy has a WCS in its header. The noise step then borrows the solution
of the registration reference frame (`stack_noise._reference_frame_wcs`), and every M 27 frame
has one. The 60 s stack worked that way. The three-group stack did not, because no reference
frame was named: its rows say "no single registration reference frame could be identified"
(`stack_membership.registration_reference_from_diagnostics`). That rule needs exactly one
zero-shift frame in the heaviest group (120 s), with the frame list and the registration lines
of the same length. That group stacked 17 of its 18 submitted frames, so the two lists likely
differ in length. That is not confirmed.

This is a part 2a bug, not a restack problem: a normal group stack hits it too, because the
quality rows are written before astrometry runs. So `--stack-only` keeps skipping astrometry.

The fix (about half a day):

1. Reproduce it with a unit test: a heaviest group whose frame list holds one more frame than its
   registration lines. Fix the lookup so it pairs frames by name, not by position.
2. A fallback for when no single frame can be named: take the solution of any member of the
   reference group. `_reference_frame_wcs` already checks a solution against the stack's stars,
   and `_move_guess_onto_reference_stars` corrects a shift of a few pixels.
3. Rerun the M 27 three-group stack on the copy. Pass: a measured noise is stored.

#### 3.6.4 Gap 4, found on the way: `s_g` moves in steps of about 4 ADU

`exposure_groups.estimate_background_noise` takes the median absolute deviation of neighboring
pixel differences. The ZWO stores its 14-bit values in steps of 4, so the median is a multiple
of 4 and `s_g` can only be 1.048 times a multiple of 4 ADU (M 27: 25.16 = 1.048 × 24, and 37.74 =
1.048 × 36 for both the 60 s and the 120 s group). At these levels one step is about 11% in `s_g`
and 22% in a group's weight `W_g`. The fix is an estimator with finer resolution, for example
interpolating the median inside its step, or a clipped standard deviation. It goes with a test on
quantized synthetic data (about half a day). It changes the weights, so it lands before the
restack.

#### 3.6.5 Order

Part 2b with the floor term is built (2026-10-26, uncommitted, unit tests only; see section 3.3). The floor rule is the one-stack rule above, kept as a fraction of today's random variance, and the aperture-set identity is now a stored number. The ZWO rehearsal and the restack are not done.

Fix gaps 3 and 4 and option D7 first (library, part 2a follow-up), then run the gap 1 diagnosis on
the copy, then build part 2b with the floor term, then the ZWO rehearsal, then the restack. The
rehearsal needs all four.

### 3.7 Decision D10: folded exposure groups (2026-10-26)

**The finding.** On a copy restack of M 27, one 300 s frame was folded into the 120 s group. Part 2a left all frame weights and the predicted noise unmeasured for such a group. A count on the copy found folded groups in 12 of the 31 ZWO targets: M 27, M 81, M 45, M 42, NGC 1893, NGC 2403, M 101, NGC 4438, M 13, Albireo, the Moon and Alnath.

**The options.** A: leave the frames of a length with fewer than 5 frames out of the stack. B: model a folded group so it gets weights and a predicted noise. C: accept no forecast. The user chose B.

**What is built (uncommitted).**

- **Library.** Siril normalizes each frame of a group to the group's registration reference frame. Frame `i` enters with the scale `k_i = t_ref / t_i`, and its noise grows by `k_i`. The `k_i` come from exposure ratios (design estimate: Siril fits the scale from stars, which also absorbs transparency). An unfolded group keeps `k_i = 1` exactly, so older results do not change. The reference is the group's one zero-shift kept frame, read from Siril's registration, not assumed to have the main length. A frame with no usable exposure (0 s; M 13 and Polaris) or a reference that cannot be named is still refused.
- **New row.** `stacking.group_reference_exposure_s` (version 1, unit s, STK-38) on the group's own subject; for a single-group stack, on the stack file. `stacking.frame_weight` (STK-07) no longer refuses folded groups.
- **Normalization scale (D9).** Reference-group membership is decided by the reference frame's path. The expected ratio divides by the group's exposure `t_g`, which the combine divides by, not by the reference frame's exposure. The single-group path needs no change.
- **Forecast.** Each frame has a noise factor `k_i sqrt((B t_i + RN^2) / (B t_len + RN^2))` relative to a frame of the group's main length `t_len`, because the stored `s_g` is the median of sampled raw frames and stands for a main-length frame (exact only when no sampled frame was folded). Folded new frames are forecast normally. A new length still needs at least 5 frames. The heaviest-group confound and the weight lookup compare group lengths, not mean exposures, because the mean moves when frames fold in. A single group of mixed lengths needs no per-frame factor: the stack is `t_ref` times the rate stack, and the forecast compares noise relative to the stars' flux, which scales the same way. The model agrees with the library's predicted noise to 1e-9 for folded groups (unit test).
- **Known limit (closed).** The subset job refuses a folded subset whose reference cannot be named. The half-difference model ratio took no `k_i`, so it treated a folded frame as unfolded. It now multiplies each frame's variance by `k_i` squared, read from the half stack that holds the frame. The subset stacks already used `k_i` through their membership, so the limit was only partly true. Unit tests only; no real folded target has been measured with it.

### 3.8 Evidence for D10

See section 4.5 for the numbers. The register entries are STK-07, STK-38, WAY-D30 and WAY-D70.

### 3.9 Decision D11: checking the forecast with frames already taken (decided 2026-10-26)

**Decision.** The user chose B on 2026-10-26, with these choices:

- The pair is the stack of all nights but the last, against the library stack (one subset stack per
  target with two or more nights).
- A count opened by such a pair is marked exploratory and ranked below counts confirmed by a real
  later stack. The first real stack confirms it or closes the gate again.
- The forced restack makes the subset stack for each target with two or more nights, so D11 is built
  before the real restack.

The rest of this section is the analysis the decision was made from.

**The question.** "Take N more" stays hidden until a forecast check agrees (section 4.4). Today a
check needs two library stacks of the same setting, the later one holding newer frames. After the
forced restack no target has such a pair, so the count appears only after a new night is imaged and
stacked. Raised by the user on 2026-10-26 after the M 13 copy restack.

**What already exists.** The library can stack a subset of a target's frames without touching the
library stack (`ProcessingPipelines.stack_subset`). `measure_noise_growth` stacks the first night,
the first two nights and so on (a one-night stack uses the first quarter, half and three quarters of
its frames). `measure_night_contributions` stacks the frames without each night in turn. Each
subset's noise is measured in the library stack's own apertures and stored at the stack level with
the subset's label as the variant. A subset stack stores no frame weights, no predicted noise and no
calibration record. The forecast check reads library stacks only. (Before the work below, the half-difference
prediction also ignored `k_i`; section 3.7.)

**What a subset pair would test, and what it would not.** A pair of "earlier nights" and "all nights"
asks the same question as a real later stack: given the frames added, did the noise fall as
forecast? It differs from a real later stack in three ways:

- The two stacks share frames, the calibration masters and the apertures. Errors common to both, a
  flat-field pattern above all, cancel more than they would against new data. The check is easier to
  pass than a real one.
- It is in-sample: the same frames were used to judge the model (the floor of D8 came from such
  subsets).
- It does not test what a new night brings: other sky, focus, temperature or calibration.

**Options.**

- A: no change. The count waits for a new night and a normal stack.
- B: a subset pair of nights may open the gate. For a target with two or more nights in the restacked
  stack, the check compares the stack of all nights but the last with the library stack. A
  one-night target still waits for a new night. The suggestion says it rests on a check made from
  frames already taken, and the first real new stack is checked as before, as a held-out
  confirmation.
- C: as B, and a one-night target may also use a frame-order subset (the first half against all).
  This is the weakest form: the same night, the same conditions, shared frames.
- D: show the forecast from the subset pair as information only, labeled not checked, and keep the
  count hidden until a real check agrees.

**Work for B as planned (design estimate, about one to two days).** The subset stack records what the check
needs: frame weights, predicted noise with `k_i`, the aperture-set row and the calibration record. The
forecast check reads a subset stack as the earlier member of a pair and applies the same confound
rules (its frames are by construction older than the library stack's newest night). The register
gains an entry for the subset check, labeled in-sample. A real check that later disagrees closes the
gate again, as today.

**Recommendation.** B, with the label. It turns on the count right after the restack for the targets
imaged on two or more nights, and keeps the stronger real check as the confirmation. C adds little
and is weak; D gives the user a number the report itself does not trust.

**As built (uncommitted).**

- **API shape.** The user chose an option on the existing call, not a new one: `ProcessingPipelines.stack_subset(..., record_forecast_rows=True)`. There is no dedicated "earlier nights" call. The caller picks the frames and the label; the restack script picks all nights but the last and uses the label `EARLIER_NIGHTS_VARIANT` (`"earlier-nights"`).
- **Rows a subset records.** One frame weight per frame, on the subject `<frame id>#<label>` so it never hides the library stack's newest weight of that frame. The exposure-group rows, under `_subsets/<label>/groups/`. The predicted noise (with `k_i` and the normalization scale), the aperture-set row (in the library stack's apertures) and a calibration record under the subset's own subject. Without the option a subset stores what it stored before.
- **Pairing rule.** The forecast check pairs the subset with the library run that holds all of its frames and was made closest in time (the older run at a tie). A later library stack that also holds the frames is passed over. If no library run holds them, the closest in time is used and the check says why it cannot be made. With several subset runs for one library run, the newest is kept.
- **Later-night rule.** The rule "added frames newer than the earlier stack's noise measurement" cannot hold for a subset, because all frames are older than both stacks. The rule for a pair with a subset is: every frame the library stack added is from a later observing night than the subset's newest frame. A frame with no start time makes the pair not checkable.
- **Gate.** A check records its basis (`ForecastBasis`: later stack or earlier-nights subset). `ForecastGate` has three states: closed, exploratory and confirmed (section 4.4). The take-more statement carries a sentence saying the count is exploratory.
- **Ranking.** Tonight's ranking puts an exploratory target after the others only when the gains tie to the whole percent (`GAIN_TIE_DIGITS = 2`). This is a deliberately soft reading, because the ranking orders by gain and not by frame count. The ranked reason says the count is exploratory.
- **Evidence.** Section 4.5 (M 13, in-sample, one target, one run). Register: STK-38 for the rows, WAY-D70 for the pair and the gate.

### 3.10 Decision D12: take-more wording with a floor (user-approved 2026-10-26)

**The finding.** With a noise floor, the count that reaches the target can be very large for a small last gain. On the M 13 copy the floor is 0.49 of the noise variance. The old report asked for 1,748 frames (about 20.9 h) to cut the noise by 29%. Doubling the stack's time cuts about 14%.

**What is built (uncommitted).**

- A count is given only when the target needs at most `PRACTICAL_TIME_FACTOR` (4.0) times the stack's present exposure time. The time is the sum of the exposure of every frame, not the weighted time.
- Past that limit, or when the target cannot be reached, the statement gives what one doubling buys (frames, time and noise cut), the most any number of frames can give, and a pointer to the report's calibration recommendations when there are any. The package rows ask for the doubling frames.
- When doubling cuts less than `MINIMUM_DOUBLING_GAIN_FRACTION` (0.05), the floor warning is given and no frames are suggested.
- M 13, 44 frames of 30 s: doubling cuts about 14%, 4 times about 21%, 10 times about 26%, 40 times about 29%, and at most about 30%. Now the report asks for 44 frames (about 32 min) for about 14%.

All values are design estimates (register WAY-D30).

### 3.11 Decisions D13 and D14: the floor from the half-difference (decided 2026-10-26)

**The finding.** The one-stack floor took the predicted random noise as exact. The model has its own error (the M 57 subsets fell 9% to 31% below the prediction, STK-36), so the floor test was too ready to find a floor, and the loss account's "cause unknown" test already counted that error. The two disagreed. Also, the half-difference of two half stacks measures the random noise directly, so the model is not needed when it exists.

**D13, what is built (uncommitted).**

- **One error budget (the fallback).** `total_variance_ratio_error` is the aperture error and twice the model's relative error times the variance ratio, added in quadrature. `EXPECTED_NOISE_MODEL_RELATIVE_ERROR` is 0.11, half of the 9% to 31% spread (a design estimate, register row WAY-D34). `MODEL_AGREEMENT_SIGMAS` (2) moved to `noise_growth.py`. The one-stack floor's model test and the loss account use them, so they agree. `FLOOR_SIGMAS` now serves only the multi-stack fit.
- **The floor from a direct measurement.** When a usable half-difference exists (the same aperture set as the stack's noise row, made after it, both noises measured), the floor's random part is the half-difference's full-stack-equivalent noise. It is rebuilt as the difference over `sqrt(N_eff x F)`, or from the model ratio, and compared in normalized noise. The test is F = (measured / random)^2, significant when ln F is above 2 times hypot(e1, e2), with e = 2 x 1.17 / sqrt(n) for each side. Only measurement errors count (no model error). The test is one-sided. Why this holds: with no fixed pattern, the sum and the difference of two independent halves have equal variance and their aperture scatters are independent, so the ratio is an F-test, judged on a log scale. The model test stays the fallback, because its error (from another target) is a stand-in. A half-difference floor wins over a multi-stack fit. Names: `compare_stack_noise_with_half_difference`, `HalfDifferenceTest`, `measure_stack_noise_floor_from_half_difference`, `StackNoiseFloor.basis` and `sigmas`, `HalfDifferenceRecord`, `half_difference_noise_inputs`. The stored rows are `stacking.half_difference_*` (version 1). The statements name the basis ("measured from the odd and even half stacks" or "found from the noise model").
- **The loss account follows.** `MeasuredNoiseCheck.basis` and `from_half_difference`: with a usable half-difference the measured noise is compared with the measured random part through the same test, and an excess is reported as a pattern fixed on the sky. Without one, the model with the total error. The floor and the loss account read the same record, so they cannot disagree (a unit test over ratios 1 to 8 and 30, 100 and 400 apertures).
- **Unit-test cases.** M 27 (3.097e-3 against 1.048e-3) keeps its floor on both bases. M 81 (1.126e-3 against 0.921e-3) has none. M 57 (1.057e-3 against 0.596e-3) has a floor on the model basis.

**D14, the restack step.** See section 4.1. The half-difference runs only when measured over predicted noise is between `HALF_DIFFERENCE_LOWER_RATIO` (1.1) and `HALF_DIFFERENCE_UPPER_RATIO` (2.0). Below 1.1 the stack agrees with the model. Above 2.0 the floor is clear either way (M 27 is at 2.95). The limits are design estimates (WAY-D70). The evidence is M 13 (section 4.5, register WAY-76).

### 3.12 Names, the rerun fixes and the noise breakdown (decided 2026-10-26)

**D15, names.** The user renamed three ideas so a reader can tell what each one is:

| Before | After | What it is |
| :--- | :--- | :--- |
| half-difference | odd–even difference | The difference of the odd-frame and even-frame half stacks. What is fixed on the sky cancels; what is left is the stack's random noise, measured instead of predicted. |
| noise floor | fixed-pattern noise | Noise that stays at the same place on the sky in every frame, so more frames do not reduce it: fixed-pattern² = measured² − random². "Floor" is kept only for the limit it puts on the gain. |
| forecast gate | recommended additional frames (with a status) | The count is "recommended additional frames". Its status (not checked, provisional, confirmed) decides whether the count is shown and whether it carries "(provisional)". Reports do not name the status. |
| measured over predicted noise | measured-to-predicted noise ratio | Stored as `stacking.measured_to_predicted_noise_ratio` when the stack's noise is measured. |

The stored metric names move from `stacking.half_difference_*` to `stacking.odd_even_difference_*` in quality-store schema version 5. The photometry and spectroscopy "noise floor" (a different idea) keeps its name.

**The noise components.** The papers get one table of what the stack's empty-sky noise is made of and how the library gets each part:

| Component | Term | Over many frames | Source | How the library gets it |
| :--- | :--- | :--- | :--- | :--- |
| Sky pedestal | $I_{\text{sky}}$ | Constant; subtracted | Sky glow, airglow, light pollution | Background level of each frame |
| Sky photon noise | $\sigma_{\text{sky}}$ | Averages down as $1/\sqrt{N}$ | Random arrival of sky photons | Predicted from the frame's sky rate and gain |
| Read noise | $\sigma_{\text{read}}$ | Averages down | Camera electronics | Camera profile |
| Dark-current noise | $\sigma_{\text{dark}}$ | Averages down | Thermal electrons | Dark frames and temperature |
| Fixed-pattern noise | $\sigma_{\text{fixed}}$ | Does not shrink | Faint sky structure, unresolved objects, flat-field residuals, gradients | Measured² minus the random part (odd–even difference, or the prediction) |

**Fixes from the copy rerun (built, uncommitted).** The rerun of 2026-10-26 found three faults:

- Frames measured before the D7 fallback kept stale "no profile entry" sky rates, because the frame fingerprint did not change (M 57 lost its predicted noise). The three noise metrics' versions were raised, and a frame is measured again when its camera profile entry appears.
- A stack that may not choose apertures took the deepest stored set, which on M 81 was a stale 60 s set; its stars were saturated in the 300 s reference frame. The library stack now records its set, and the others use it.
- The reference-star match moved a solution by a shift only. M 81's earlier-nights stack sits on a frame whose header rotation is about 0.2 degrees off. The match now searches and fits a rotation up to 0.5 degrees.

Open from the rerun: M 81's frame check took 21 minutes (the new profile-gap query is the suspect), M 27's forecast finds no row for its folded 120 s group, and M 13's odd–even difference moved 5% between runs on the same frames and apertures, which moved its fixed-pattern noise from 2.2σ to 1.9σ. Found later the same day: M 27's group row is matched by the mean of the stacked members, but stored as the mean of all frames submitted (fixed by matching the stored reference exposure first). M 13's shift is sampling scatter: the new star match placed the apertures a few hundredths of a pixel differently (mean shift instead of median), and with 99 apertures such a shift changes the estimate by about 2% typically and up to 4.5% (a test on M 13's stack); the split and the half stacks were identical. A negligible fitted shift and rotation will be applied as none, so reruns reproduce. M 81's slow frame check was not reproduced; every piece is fast alone, so contention on the copy is the likely cause, to be checked on the next pass.

### 3.13 Decisions D16 to D24: the noise algorithm and mission practice (decided 2026-10-26)

**Why.** The noise measurement and forecast use methods that space missions use and have tested at scale. Planck (ESA) measured its noise with half-ring difference maps, the same idea as the odd–even difference [P1], [P2], and checked its noise model against them [P3]. Gaia (ESA) adds an "excess noise" in quadrature when a source scatters more than its errors explain, and reports its significance, with values above 2 taken as significant [P5], [P6], [P7]. Hubble deep-field work found that resampling (drizzling) correlates neighboring pixels, so pixel noise understates noise over an area [P8], [P9], and the Hubble surveys measure it with empty apertures of several sizes [P10], [P11], [P12]. TESS (NASA) planned with a fixed systematic floor that flight data later showed too high for bright stars [P13], [P14]. None of these changes needs new frames.

Each decision below gives the options, a recommendation and a question. The user took every recommendation on 2026-10-26 (D24 deferred); the answers to the questions are in the decisions table (section 6).

**D16, bias in the odd–even difference.** Planck found its half-difference biased low by about a percent, from correlations between the halves and from clipping that removes the noise's positive tail [P3], [P4]. Ours has both: the halves share the registration reference frame, and Siril rejects outlier pixels (1.6% on M 13). A low random part makes fixed-pattern noise look larger. First run on the copy, 2026-10-27 (M 13, 3 repeats, cdb80809): the script reported 0.74 ± 0.04, but its reference (the measured noise of the simulated full stack) keeps the real frames' smoothed sky and so M 13's real fixed-pattern noise, which the difference cancels. Against the known injected noise (the model's 1.47e-4 normalized) the difference recovered 1.44e-4, 1.62e-4 and 1.63e-4 (mean 1.57e-4, ratio about 1.06): no sign of a low bias, so the correction stays 1.0. The script is to compare against the injected noise instead. The same day, the copy pass found that 9 of 11 aperture sets stored sky positions made through the registration reference frame's header, off by 11–50″, so the depth zero point matched no Gaia star (decision 2026-10-27, option B): a set's sky positions now come only from a plate solve of the stack, an unsolved set is marked untrusted, and the next library stack replaces an untrusted set.

- A: accept and document the bias.
- B: measure it once with an injection test: stack frames of pure simulated noise, with each frame's predicted noise, through the same steps, and store the ratio of the measured difference to the known noise as a correction (a register entry).
- C: stack the halves without pixel rejection, so no clipping bias arises, at the cost of noisier halves.
- Recommendation: B. Question: is a one-time injection test on the copy acceptable as the evidence, or should it rerun per camera setting?

**D17, the model's error measured, not borrowed.** `EXPECTED_NOISE_MODEL_RELATIVE_ERROR` (0.11) comes from one target as a stand-in. Every odd–even difference compares the prediction with a measured random part (M 13 within a few percent, NGC 7331 about 5%), as Planck checked its noise model [P3].

- A: keep 0.11.
- B: pool the ratio of odd–even random noise to predicted noise over the library's targets, per camera and setting, and use its scatter as the model's error once at least 5 targets have one; until then keep 0.11.
- Recommendation: B. Question: is 5 targets per setting enough to replace the stand-in?

**D18, a graded fixed-pattern answer.** The 2σ cut makes the advice jump: M 13 went from "about 13%" to "about 29%" when its fixed-pattern significance fell from 2.2σ to 1.9σ. Gaia reports its excess noise with its significance and leaves the cut visible [P5].

- A: keep the hard cut.
- B: between 1σ and 2σ, give the gain as a range, from "with this fixed-pattern noise" to "with none", and say the fixed-pattern noise is possible but not proven. Above 2σ and below 1σ, as now.
- C: give one expected gain, weighting the two cases by the evidence.
- Recommendation: B. Question: should a range-only case still count as a recommendation, or as information?

**D19, run the odd–even difference whenever it can run.** D14 skips it above a ratio of 2.0, so M 27, the largest excess (4.13), has no measured random part and no fixed-pattern size.

- A: keep D14 (1.1 to 2.0).
- B: run it for every stack with at least 8 frames per half, at the cost of about one stack each.
- Recommendation: B, which reverses D14. Question: is the extra stacking time in a full restack acceptable (about one stack per target)?

**D20, correlated noise from the aperture size.** Hubble surveys measure empty-aperture noise at several aperture sizes and fit the noise against the number of pixels as a power law; an exponent near 1 (in variance) means independent pixels, near 2 means fully correlated pixels [P10], [P12]. Registration resampling correlates our pixels too, and a fixed pattern adds noise on larger scales.

- A: one aperture size, as now.
- B: measure the same aperture positions at three or four radii, store the fitted exponent, and report it; change the prediction only when the register shows a steady correction.
- C: as B, and correct the prediction for resampling at once.
- Recommendation: B. Question: none beyond approval.

**D21, where the fixed-pattern noise is.** One number cannot say whether the excess is a gradient, a flat residual, faint nebulosity or banding. With the two half stacks in hand, each aperture's sum carries random plus fixed noise and its difference carries random noise only.

- A: one number, as now.
- B: compare sum and difference per aperture and test four shapes: a trend with distance from the center (flat or vignetting residual), a plane (gradient), a rise near the target (faint real light, likely on M 27), and row or column averages that vary more than random noise (line pattern noise). The loss account names the shape that fits.
- Recommendation: B. Question: should a named shape change the advice (for example "retake flats" for a radial trend), or only the explanation?

**D22, calibration noise as a known part.** The master flat's and master dark's own noise is the same in every frame, so it is fixed-pattern noise that the library can predict: the master flat's noise is already measured, and the master dark's follows from its frame count.

- A: leave it inside the measured remainder.
- B: predict it as a named part of the fixed-pattern noise, so the loss account reports how much of the excess the masters explain and what is left.
- Recommendation: B. Question: none beyond approval.

**D23, fixed-pattern history per setup.** TESS's planned floor gave way to measured values [P14]. Our new targets start with no fixed-pattern noise until measured.

- A: none, as now.
- B: keep each target's fixed-pattern share by camera, optic and flat set, and start a new target of the same setup from the median share, reset when the flats change.
- Recommendation: B, after D17 to D21, once enough targets have a measured share. Question: should a share learned from other targets be marked as such in the report?

**D24, gain and read noise from the odd–even differences.** The measured random noise at known sky levels across targets gives the effective gain and read noise, in the manner of the photon transfer method [P15], without a calibration session.

- A: keep the camera profile, with the D7 offset fallback.
- B: fit them from the odd–even differences as a check on the profile, reported but not used.
- Recommendation: defer; a real photon transfer session at offset 0 is the better source when the user takes one.

**References for sections 3.12 and 3.13.**

- [P1] Planck Collaboration, "Planck 2013 results. II. Low Frequency Instrument data processing," arXiv:1303.5063. <https://arxiv.org/pdf/1303.5063>
- [P2] Planck Collaboration, "Planck 2013 results. VI. High Frequency Instrument data processing," arXiv:1303.5067. <https://arxiv.org/pdf/1303.5067>
- [P3] Planck Collaboration, "Planck 2015 results. XI. CMB power spectra, likelihoods, and robustness of parameters," arXiv:1507.02704. <https://arxiv.org/pdf/1507.02704>
- [P4] Planck Collaboration, "Planck 2018 results. V. CMB power spectra and likelihoods," arXiv:1907.12875. <https://arxiv.org/pdf/1907.12875>
- [P5] Gaia astrometric excess noise in DR2 and the search for X-ray binaries, arXiv:2009.07277. <https://arxiv.org/pdf/2009.07277>
- [P6] Gaia Collaboration, "Gaia Data Release 2: The astrometric solution," arXiv:1804.09366. <https://arxiv.org/pdf/1804.09366>
- [P7] Astrometric excess noise in Gaia EDR3, arXiv:2201.00833. <https://arxiv.org/pdf/2201.00833>
- [P8] S. Casertano et al., "WFPC2 observations of the Hubble Deep Field-South," arXiv:astro-ph/0010245. <https://arxiv.org/pdf/astro-ph/0010245>
- [P9] A catalogue of galaxies in the HDF-South, arXiv:astro-ph/0005204. <https://arxiv.org/pdf/astro-ph/0005204>
- [P10] The MUSYC wide K-band imaging survey (empty-aperture method after Labbé et al. 2003), arXiv:0803.0763. <https://arxiv.org/pdf/0803.0763>
- [P11] R. E. Skelton et al., "3D-HST WFC3-selected photometric catalogs," arXiv:1403.3689. <https://arxiv.org/pdf/1403.3689>
- [P12] 3D-DASH: the widest near-infrared Hubble survey, arXiv:2206.01156. <https://arxiv.org/pdf/2206.01156>
- [P13] P. W. Sullivan et al., "The Transiting Exoplanet Survey Satellite: simulations of planet detections and astrophysical false positives," arXiv:1506.03845. <https://arxiv.org/pdf/1506.03845>
- [P14] Predicting the exoplanet yield of the TESS prime and extended missions, arXiv:2202.03656. <https://arxiv.org/pdf/2202.03656>
- [P15] J. R. Janesick, *Photon Transfer*, SPIE Press, 2007 (not yet checked against the text).

These are plan references. When a decision is built, its method moves into the papers with numbered IEEE references, as the documentation conventions require.

## 4. The forced restack

### 4.1 How it runs

`astrometricslib/scripts/run_all_target_processing.py --force-restack` already gives each target to
one camera (`assign_targets_to_cameras`), uses the primary optic, isolates failures per target, and
has `--dry-run`, `--target`, `--targets-from` and `--skip-target(s-from)`. It gains
these options (one tool with parameters), all built (uncommitted): `--stack-only` (the public
`processing.stack` for each target, no astrometry, photometry, spectroscopy or spectral stacks; run
one target at a time), `--progress-file`, `--retry-failed`, `--camera`, `--review-list` and
`--review-only`. **Measure first.** `--stack-only` first measures every imaging frame of the target (`ProcessingPipelines.record_frame_measurements`, no limit), because a stack measures at most `STACKING_STAGE_MEASUREMENT_LIMIT` (40) new frames and its predicted noise needs the sky of every frame. Frames measured before and unchanged are skipped, so a resumed run does not repeat the work. A target that another run is measuring is failed, not stacked.

**Earlier nights.** After the stack, a target whose stack holds two or more observing nights also gets the earlier-nights subset (section 3.9), made with frames chosen in the script. `--no-earlier-nights` turns it off. A failed subset is reported in the run's message and does not fail the target.

**Half-difference (decision D14, section 3.11).** After the stack and the earlier-nights subset, `--stack-only` reads the stack's newest measured and predicted noise rows. When measured over predicted is between `HALF_DIFFERENCE_LOWER_RATIO` (1.1) and `HALF_DIFFERENCE_UPPER_RATIO` (2.0), it calls `ProcessingPipelines.measure_half_difference_noise`. The step prints one line per target (run or skipped, with the ratio). A failure is added to the run's message and does not fail the target. `--no-half-difference` turns it off.

**Plate-solve fallback.** The stack-noise step needs sky coordinates for the stack. When no stack header, registration reference frame or member frame gives a solution, the stack is plate-solved in memory with the astrometry pipeline. The stack header is not written. The solve is also tried when a frame's solution fails the check against the stored reference stars (seen on M 13's earlier-nights subset, whose frames carry a capture-program solution). A failed solve keeps the not-measured result with both reasons, and it can take up to about 10 minutes (a hinted and a blind try of up to 300 s each).

No new script and no backend call with `force`. A plain loop of `processing.stack(target, force=True)` would fail on the 13
targets with frames from both cameras and would mix optics on the 12 shot at both 300 and 405 mm.

Second-camera stacks of a target are not rebuilt; the plan accepts that.

**Progress file.** One JSON line per finished target (`target`, `camera`, `optic_mm`, `status`, `error`, `time`, `run_started_at`). Status `success` and `skipped` count as done and are not run again. A `failed` target is retried only with `--retry-failed`. A `refused` target is checked again on every run. An unreadable line is skipped with a warning.

**Dry run.** `--dry-run` writes nothing (no progress line, no review list, no scratch cleanup). In stack-only mode it counts imaging frames. Over the ZWO targets it counted 31 targets and 1,019 imaging frames, which matches the estimate in section 4.3; six targets have spectral frames only.

**Nikon guard.** `camera_refusal_reason` refuses a D5300 target unless the profile has a noise entry for ISO 800, offset 0 and binning 1, and a saturation level at or below the clip ceiling. Today it refuses: there is no such entry, and saturation 65000 is above the 16383 clip ceiling. A Nikon restack stays refused until the profile is fixed.

**ZWO command shape** (through `build/agent_tools/realrun.sh`, which runs only scripts inside the scratch folder; a wrapper there runs the repository script):

```text
run_all_target_processing.py --force-restack --stack-only --camera zwo \
    --progress-file PROGRESS.jsonl --review-list REVIEW.csv --target "M 27"
```

**Review list.** A CSV with the columns `target`, `camera`, `optic_mm`, `status`, `refusal_reason`, `weights_known`, `predicted_noise_present`, `aperture_set_present`, `earlier_nights_present` (yes, no, or n/a for a stack of one night), `half_difference` (`run`, `skipped (ratio X)`, `failed` or `n/a`), `frames_moved_count` and `frames_moved`. It covers rows made since the run started. `--review-only` writes it from an existing progress file and processes nothing. **Known gap:** a run with `--no-half-difference` shows `failed` for the targets in range, because the column cannot tell a skipped step from a failed one.

### 4.2 Steps

1. Count the folded groups of every target, and preview the quarantine
   (`QualityDiagnostics.frame_quality`), since the restack may move frames library-wide.
2. Back up the databases with `scripts/backup_library_databases.py`. The restack deletes each
   target's older previous stack (`previous_stack.commit_archive`); the user accepted that loss
   (decision D5). The quality store's schema version 4 changes stored data on its first open: it rounds the stored pixel scales and merges capture settings that become equal, so the backup comes first.
3. Stop the backend (`build/linux/run_backend.sh stop`). The stack guard does not stop two library
   stacks of the same target, and a running sync can take the frame-measurement turn so frames go
   unmeasured.
4. Run a dry run (`--dry-run`) over every target to count frames and catch errors.
5. The script refuses to start the Nikon targets unless the D5300 profile has a noise entry for
   ISO 800, offset 0, binning 1, and a saturation level at or below its clip ceiling. The ZWO targets
   run regardless.
6. Run it, skipping "Test Target Name", the Sun, Venus and Jupiter.
7. Write a list per target: weights known, predicted noise present, any refusal and its reason,
   and the frames the quarantine moved.
8. Restart the backend and refresh suggestions.

### 4.3 Time

The restack measures every chosen imaging frame once: about 4,363 frames (3,344 Nikon at 6000 by
4000 pixels, 1,019 ZWO at 3008 by 3008). At about 6 s per 9-megapixel frame with four workers, the
Nikon frames alone are likely more than 10 hours, and a frame cut short by the 60 s budget
(`FRAME_MEASUREMENT_TIME_BUDGET_SECONDS`) is measured again on the next run. Plan for two nights,
using the progress file to resume. The half-difference step adds about one library stack's time for each target in range (1.1 to 2.0 times the predicted noise); `--no-half-difference` removes it.

### 4.4 When "take N more" can appear

After the restack, a target's next normal stack with new frames opens the gate only if:

- the restack's record is complete (weights known, predicted noise present);
- the new frames form groups of 5 or more, or join existing exposure lengths;
- the flat and bias masters are the same, and the heaviest group does not change;
- no group is clipped or stacked with `-weight=noise`;
- for Nikon targets, the read noise was measured first.

A target imaged on two or more nights can open the gate earlier, as exploratory (decision D11, section 3.9). Its earlier-nights subset is paired with the library stack. The check needs the same confound rules, and every frame the library stack added must be from a later observing night than the subset's newest frame. If the check agrees, the count is shown with a sentence saying it is exploratory. The first real later stack then confirms it or closes the gate. A target imaged on one night still waits for a new night.

### 4.5 Rehearsal on the copy (2026-10-26)

Copy at `/home/michael/astrometrics_scratch/134-validation`; code uncommitted on `134-group-weights-nikon` after 30f53f77; one run each, in-sample. Details are in register entries STK-38 and WAY-D70.

- **M 27.** 82 imaging frames in three groups (30 s x 26, 60 s x 33, and a 120 s group of 23 frames holding one 300 s frame). After D10: 79 of 79 frame weights, the 300 s frame stacked with raw weight 6.9, and a predicted noise. Before D10 the same restack stored none.
- **M 42** (one group, 21 x 30 s and one 300 s). Siril did not stack the 300 s frame, so the folded path was not exercised. Weights 21 of 21.
- **M 13 (two nights; added after the first rehearsal).** Before the plate-solve fallback, M 13 and M 101 restacked with "no WCS for this stack" and stored no noise. After it, M 13's stack solved (18 stars, 0.767 arcsec rms) and so did its earlier-nights subset (23 stars, 0.941 arcsec). Headers wrote M 13's pixel scale as 1.915279 and as 1.9152790075, which split one 30 s setting into 40 and 4 frames; rounding the scale makes them one setting. The earlier-nights subset has 4 frames (effective 2.13): normalized noise 0.778e-3, measured over predicted 0.98, normalization scale 1.234. The library stack has 44 frames (effective 35.9): normalized noise 0.206e-3, measured over predicted 1.40, scale 1.472. The scale difference is explained by the reference frames (below). The check came out "as forecast", and the gate opened as exploratory. In-sample, one target, one run. The take-more numbers for M 13 are in section 3.10. A rerun of M 101 after the fix is not recorded.
- **M 81.** Siril did not stack the three folded frames. Weights 42 of 42. The predicted noise is not measured, because four 300 s frames have no stored sky measurement on the copy (not a D10 issue).

- **M 13 half-difference (decisions D13 and D14; copy, one target, in-sample, 2026-10-26; code uncommitted after 9ed5af01; register WAY-76).** Odd half 22 frames and even half 23, both holding the registration reference frame `M_13_Light_Luminance_025.fits` (weight fractions 0.084 and 0.088), 99 apertures. The full-stack-equivalent random noise is 1.4295e-4 normalized, against the model's 1.4298e-4 (0.02% apart). The measured full-stack noise is 2.056e-4. F = 2.07, ln F = 0.73 with an error of 0.33, so about 2.2σ. The floor is about 51% to 52% of the variance (0.000588 stack units). With the model test (11% model error) the same stack shows no floor. The restack rerun ran the half-difference by itself (ratio 1.38). The report now says: 44 more 30 s frames (about 32 min) cut about 13%, at most about 29%, with the floor measured from the odd and even half stacks; the loss account reports the excess as a pattern fixed on the sky.
- **M 13 normalization scales.** The library stack's scale is 1.472 (reference frame `M_13_Light_Luminance_025`) and the earlier-nights subset's is 1.234 (a first-night reference frame whose stars are about 19% fainter: reference fluxes 2.76 against 2.25, in stack units over scale). The different reference frames explain the difference. The forecast check works in normalized noise, so the scale cancels.

**Left to do.**

1. Restack the rest of the ZWO targets on the copy, one target at a time, and read the review list.
2. Compare the M 27 floor across stacks in normalized noise (noise over the stars' flux) before trusting the floor from the three-group stack. Stack-unit numbers of different stacks are not comparable.
3. Rerun the full ZWO pass on the copy with the changes of 2026-10-26 (measure first, the plate-solve fallback, pixel-scale merging, the earlier-nights subsets). It has not been rerun. (The subset job's `k_i` is done, section 3.7.)
4. Find why four M 81 300 s frames have no sky measurement.
5. Restack the real library, with the databases backed up and the backend stopped, only on the user's go-ahead.

## 5. Order of work and effort

| Step | Work | Effort |
| :--- | :--- | :--- |
| Part 1 | Two limit properties used everywhere, clearing stale Nikon fractions, tests, register; the user's settings change and Measure noise | About half a day, plus about 15 minutes of the user's time |
| Part 2a | Combine result object, per-group data, weights and gains, predicted noise, unknown cases, reference and header, apertures, public rules, calibration side table and migration; Siril weight test, impulse test, M 27, M 51 and M 57 on a copy; register | About 4 to 5 days |
| Part 2a, follow-up | Flux-scale stars avoid patched cores | About half a day |
| Part 2b | Group mode, planned group noise, fold rule, suggestion path, model agreement, confound and new-frame rules, Nikon label | About 1.5 to 2 days |
| Restack | Script options, backup, dry run, two overnight runs, review list | Two evenings and two nights |

Each part is its own pull request under one issue. Documentation in each: the Implementation
document, the stacking and wayfinding registers, and the READMEs of the changed folders.

## 6. Decisions

| # | Decision | Options | Status |
| :--- | :--- | :--- | :--- |
| D1 | The noise goal after M 57 is measured again | Keep 1.9e-4, or set the goal from M 57's current-placement value; and whether a suggestion needs the noise above the goal by about 2 combined sigma before it is better than Tentative | Decided 2026-10-26: depth in magnitudes per kind of object, as NASA and ESA projects state it. Two stored metrics per stack: the 5σ point-source depth (in an aperture matched to the stack's star size) and the 3σ surface-brightness limit over 10″ × 10″ boxes (using D20's aperture-size exponent for the box noise), both from the empty-aperture noise and the photometric zero point. The diminishing-returns stop (D12) stays. The user sets the per-type goals from the copy's processed targets once the depths exist; 1.9e-4 stays until then. The goal is chosen per target in the Image Processing Display, in the Frame Analysis panel's Data tab as a "Depth goal" row under Total Exposure, with a dropdown of plain depth levels for a novice (Quick look, Good as the default, Deep), each defined per kind of object in magnitudes; next to it the image's current level and the time to reach the next one, or the diminishing-returns warning. The two depth metrics are built, uncommitted (`stacking.point_source_depth_5sigma_mag`, `stacking.surface_brightness_limit_3sigma_10arcsec_mag_per_arcsec2`); the kind of object comes from the catalog. Level values decided 2026-10-27 (option C, time-based): each level is the depth this setup reaches in a set time at the measured rate (NGC 7331 on the copy, 19.7 mag and 25.1 mag/arcsec² in 4.1 h, scaled by 1.25·log10 of the time ratio): Quick look 30 min (18.6 mag point source, 24.0 mag/arcsec²), Good 2 h (19.3, 24.7), Deep 8 h (20.1, 25.5). Clusters use the point-source depth, galaxies and nebulae the surface-brightness limit, bright-star fields get no depth goal. Copy depths of 19 targets on 587ce984: galaxies 23.9–25.1, nebulae 22.8–24.0 mag/arcsec², clusters 18.2–19.2 mag. The dropdown is being built. The kind of object (decided 2026-10-27) comes from each target's stored SIMBAD object type, filled when the target is resolved and by a backfill; when the type is unknown the point-source depth is used, the lowest-fidelity kind, since every field has stars. The depth level is the only control (user, 2026-10-27): no per-target override of the kind; an NGC or IC object that SIMBAD types as a star (NGC 6888 as its Wolf-Rayet star) is treated as unknown, so it keeps a point-source goal |
| D2 | D5300 calibration frames | Use the library's 111 biases and 125 flats if the flats include pairs at ISO 800, or take a short calibration session | Open: check the library first |
| D3 | Which issue holds this work | One issue under epic #93 with the pull requests and the restack, or separate issues | Decided in practice: one issue, #134, with pull request #135 on branch `134-group-weights-nikon` |
| D4 | Where the calibration identities live | A side table with a schema migration, or a hash stored as a measurement | Decided 2026-10-25: side table |
| D5 | The older previous stacks the restack deletes | Back up the stacks folder first, or accept the loss | Decided 2026-10-25: accept the loss; only the databases are backed up |
| D6 | Which camera this work covers | Both cameras, or the ZWO ASI533MM Pro only | Decided 2026-10-26: ZWO ASI533MM Pro only; Nikon deferred |
| D7 | Read noise and gain for offset-0 frames (section 3.6.2) | A: library fallback to another offset for unclipped frames; B: a copied config entry; C: offset 10 only from now on | Decided 2026-10-26: A, the library fallback for frames not clipped at zero |
| D8 | A noise floor in the forecast (section 3.6.1) | Estimate the floor from one stack (measured minus random) and forecast with it; or keep 1/sqrt(N) and wait for two stacks to fit a floor | Decided 2026-10-26: the one-stack floor, built only once the diagnosis (section 3.6.1, step 1) confirms a floor |
| D9 | Siril's unsaved per-frame normalization scale (impulse test) | A: ignore and document; B: measure the stack's scale from its reference stars (stack flux over registration reference frame flux) and apply it to the predicted noise and the floor's random part; C: use the half-stack difference as the random part; D: stop Siril from scaling (`-norm=add`) | Decided 2026-10-26: B, with the stored frame weight kept as the star-width share and the per-frame bias (under about 3% on ratios) documented. Built and checked on the copy 2026-10-26: scales 1.193, 1.385 and 1.339 (M 27, M 57, M 81) agree with the half-stack factors 1.14, 1.37 and 1.34 within 0.4σ; corrected floors 2.92e-3, 0.87e-3 and 0.65e-3 |
| D11 | Whether a subset pair from frames already taken may open the forecast gate (section 3.9) | A: no change; B: a pair of nights may open it, labeled, with the first real stack as confirmation; C: as B plus a frame-order subset for one-night targets; D: show the subset forecast as information only | Decided 2026-10-26: B. The pair is all nights but the last against the library stack; its count is marked exploratory and ranked below a real check (within a gain tie); the forced restack makes the subset stacks. Built, uncommitted (section 3.9) |
| D10 | How folded exposure groups are handled (section 3.7) | A: leave frames of a length with fewer than 5 frames out of the stack; B: model a folded group so it gets weights and a predicted noise; C: accept no forecast | Decided 2026-10-26: B. Built, uncommitted. Found on a copy restack of M 27: one 300 s frame folded into its 120 s group left all weights and the predicted noise unmeasured. 12 of 31 ZWO targets have folded groups |
| D12 | What "take N more" says when the noise floor makes the count very large (section 3.10) | A: keep the count however large; B: give a count only within a practical limit of the stack's exposure time, otherwise what one doubling buys | Decided 2026-10-26: B, with the limit at 4 times the stack's exposure time and a floor of a 5% cut for one doubling. Built, uncommitted |
| D13 | Where the floor's random part comes from, and the loss account (section 3.11) | A: the prediction taken as exact (before); B: the prediction with the model's own error in the test (the shared budget); C: B as the fallback, and the half-difference measurement when a usable one exists, with the loss account reading the same record | Decided 2026-10-26: C. Built, uncommitted |
| D14 | When the restack runs the half-difference (section 4.1) | A: never; B: for every target; C: only when measured over predicted noise is between 1.1 and 2.0 | Decided 2026-10-26: C. Built, uncommitted. D19 proposes B |
| D15 | Names (section 3.12) | Odd–even difference, fixed-pattern noise, recommended additional frames, measured-to-predicted noise ratio; stored names migrated in schema version 5 | Decided 2026-10-26. Built, uncommitted |
| D16 | Bias in the odd–even difference (section 3.13) | A: document; B: injection-measured correction; C: halves without pixel rejection | Decided 2026-10-26: B; the injection test runs once per camera on the copy, and again when the stacker's rejection settings change. Script and module built, uncommitted; not yet run, so the divisor stays 1.0 |
| D17 | The model's error (section 3.13) | A: keep 0.11; B: pooled from odd–even differences, per setting, from 5 targets | Decided 2026-10-26: B, from 5 targets per setting. Built, uncommitted |
| D18 | Fixed-pattern noise between 1σ and 2σ (section 3.13) | A: hard 2σ cut; B: a range of gains; C: one evidence-weighted gain | Decided 2026-10-26: B; a range still counts as a recommendation, labeled as a range. Built, uncommitted |
| D19 | When the odd–even difference runs (section 3.13) | A: D14 (1.1 to 2.0); B: every stack with 8 frames per half | Decided 2026-10-26: B (replaces D14); the extra stack per target is accepted. Built, uncommitted |
| D20 | Correlated noise from the aperture size (section 3.13) | A: one size; B: three or four radii, store the exponent; C: B and correct the prediction now | Decided 2026-10-26: B. Built, uncommitted |
| D21 | Where the fixed-pattern noise is (section 3.13) | A: one number; B: per-aperture sum against difference, four shape tests | Decided 2026-10-26: B; a significant shape changes the advice (radial trend: retake flats; gradient: check gradient removal; banding: line pattern removal); a rise near the target is explained, not advised on. Built, uncommitted |
| D22 | Calibration noise as a known part (section 3.13) | A: inside the remainder; B: predicted as a named part of the fixed-pattern noise | Decided 2026-10-26: B. Built, uncommitted |
| D23 | Fixed-pattern history per setup (section 3.13) | A: none; B: median share by camera, optic and flat set | Decided 2026-10-26: B, after D17 to D21; a learned share is marked as learned from other targets. Built, uncommitted |
| D24 | Gain and read noise from odd–even differences (section 3.13) | A: camera profile; B: fitted as a check | Decided 2026-10-26: deferred; not built |
| D25 | The smallest earlier-nights stack that may make a count provisional | A: the general subset minimum (4 frames); B: 8 frames | Decided 2026-10-26: B. The restack does not make a smaller one, and wayfindinglib refuses its check. Built, uncommitted. M 13's 4-frame first night had opened a provisional count alone. The rule id `more_frames` keeps its name (ids are not shown to readers) |

## 7. Out of scope

- Rebuilding the record of existing stacks without stacking (versions 2 and 3, dropped).
- Second-camera stacks of a target.
- Night weights, subset stacks and half-stack noise.
- Any change to the forecast gate's rule.

## Appendix A. Review board changes, version 1 (2026-10-25)

In appendices A and B, "where it landed" names the sections of the version that followed the
review. Version 4 dropped the rebuild, so the rows about it no longer apply.

Reviewers: observational astronomer (approve with changes), software engineer (reject as written),
technical architect (approve with changes).

| Finding | Raised by | Check | Where it landed |
| :--- | :--- | :--- | :--- |
| "Take N more" needs a passed forecast check between two complete runs | Software engineer | Confirmed | Section 1 |
| The goal's M 57 anchor comes from the first aperture placement | Astronomer | Confirmed | Section 3.3, D2 |
| The saturation check never fires for the Nikon | Astronomer | Confirmed | Part 1 |
| `_crop_offset` returns (row, column) | Astronomer, software engineer | Confirmed | Version 3 part 3 (dropped in version 4) |
| A recovered offset is never checked on a solved stack | Astronomer | Accepted | Version 3 part 3 (dropped in version 4) |
| The stack depth cannot come from the frame count | All three | Confirmed | Version 3 part 3 (dropped in version 4) |
| A refused stack must not write apertures | Astronomer | Accepted | Version 3 part 3 (dropped in version 4) |
| A remeasure would count as an extra stack run | Software engineer | Accepted | Sections 4.3, 5.2 |
| The made time must not come from `createdAt` | Software engineer | Confirmed | Section 5.1 |
| The stacking skip path should use the same helper | Technical architect | Confirmed | Section 4.1 |
| No guard against a running restack; reruns add rows | Software engineer | Accepted | Version 3 part 3 (dropped in version 4) |
| Refuse a stack made by another algorithm version | Technical architect | Accepted | Version 3 part 3 (dropped in version 4) |
| `save_noise_sums` was not called | Software engineer | Accepted | Version 3 part 3 (dropped in version 4) |
| Row notes go through the provenance path | Technical architect, software engineer | Accepted | Version 3 part 3 (dropped in version 4) |
| Public interface work; the tool writes and is withheld | Technical architect, software engineer | Accepted | Section 7 |
| The inventory counted the wrong folder | Astronomer, software engineer | Confirmed | Section 1 |
| Bright-star and Moon stacks refuse with a reason | Astronomer | Accepted | Section 4.4 |
| The backfill comment and `live_rows_exist` | Technical architect | Accepted | Section 5.3 |
| Cut the `kind` argument; postpone the batch helper | Technical architect | Accepted | Section 4.1 |
| Require about 2 combined sigma above the goal before a suggestion is better than Tentative | Astronomer | Deferred | D2 |

## Appendix B. Review board changes, version 2 (2026-10-25)

Reviewers: observational astronomer (reject as written), software engineer (reject as written),
technical architect (approve with changes). Every critical and major claim was checked against the
code or the files on disk.

| Finding | Raised by | Check | Where it landed |
| :--- | :--- | :--- | :--- |
| Exposure-group stacks get unknown weights and no expected noise, even in a normal run | All three | Confirmed (`determine_stack_members`, `record_science`, the manifests) | Part 2 |
| The inventory sorted stacks by `.seq` count instead of the group manifest | All three | Confirmed | Section 1 |
| NGC 2024 gained no frames; no target gets a forecast right after the rebuild | All three | Confirmed (37 of 39 previous frames, none new) | Section 1 |
| Frames carry the commanded pointing, so the plate-solution order check cannot tell frames apart | Astronomer, software engineer | Confirmed (STK-36) | Version 3 part 3 (dropped in version 4) |
| Take the member set from `_inputs.json`, not today's frame list | All three | Accepted | Version 3 part 3 (dropped in version 4) |
| Previous and current stacks share a subject id | Software engineer, technical architect | Accepted | Section 4.1 (previous stacks out of scope) |
| The guard would deadlock the skip path | Software engineer, technical architect | Accepted | Section 4.1 |
| Three functions sort runs by measurement time | Astronomer, software engineer | Accepted | Section 5.2 |
| The made time had two sources | Technical architect | Accepted | D1 |
| Use a repeatable run id instead of parsing provenance text | Software engineer, technical architect | Accepted | Section 4.3 |
| Duplicate runs fake a noise floor | Software engineer | Accepted | Section 5.2 |
| Version 4 does not prove a stack would be skipped; use the fingerprint and stored settings | Astronomer, software engineer | Accepted | Version 3 part 3 (dropped in version 4) |
| The skip path's signature and the expected noise before frame rows exist | Software engineer | Accepted | Sections 4.1, 4.2 step 1 |
| `wfwhm_weights` was never checked against Siril | Astronomer | Confirmed (stacking register) | Section 3.3 |
| A calibration change can pass the forecast check; new frames must be newer than the stack | Astronomer, software engineer | Accepted | Section 5.4 |
| The clip-ceiling minimum and the Nikon refusal contradicted each other | Astronomer, software engineer | Accepted (keep the minimum) | Section 2.1 |
| Validate on a single-exposure stack with pass criteria and negative controls | Astronomer | Accepted | Section 8 |
| No code refuses read-noise-limited stacks | Astronomer | Accepted | Section 4.4 |
| The star match must allow wide Nikon stars | Astronomer | Accepted | Version 3 part 3 (dropped in version 4) |
| The frame count for the measurement run | Software engineer | Accepted (about 1,650 member frames) | Section 7 |
| Cut the "making pipeline version" field | Technical architect | Accepted | Section 4.3 |
| Build the member-order rebuild as a `RegistrationRecord` beside `registration_record.py` | Technical architect | Accepted | Version 3 part 3 (dropped in version 4) |
| Writing a registration record for old stacks | Technical architect | Postponed | D5 |

Not adopted: the software engineer's dedicated metric or schema migration for the made time is
option (b) of D1, left to the user.

## Appendix C. Review board changes, version 3 (2026-10-25)

Reviewers: observational astronomer (part 1 approve, part 2 reject as written, part 3 approve with
changes), software engineer (parts 1 and 2 approve with changes, part 3 reject as written),
technical architect (approve with changes). Every critical and major claim was checked against the
code or the database. After the review the user dropped part 3 (the rebuild) for one forced restack,
so part 3's findings landed as "dropped with part 3".

| Finding | Raised by | Check | Where it landed |
| :--- | :--- | :--- | :--- |
| The predicted noise of a group stack must be computed in rate units and scaled by the mean exposure; the ADU model would store M 27's value up to 64% too high | Astronomer | Accepted | Section 3.2 |
| The weight rule leaves out the group gain and the mean-exposure factor | Astronomer, software engineer | Confirmed (`combine_exposure_group_images`) | Section 3.2 |
| Groups of fewer than 5 frames are folded, with a scale Siril does not save | Astronomer | Confirmed (`split_frames_by_exposure`) | Sections 3.2, 3.3 |
| The forecast cannot recognize group weights and falls back to `wfwhm` over all frames | Astronomer | Accepted | Section 3.3 |
| Clipped groups drop sky pixels where apertures sit; borders differ by group | Astronomer | Accepted | Section 3.2 |
| The M 57 validation cannot tell the rules apart; use an impulse test and M 27 | Astronomer | Accepted | Section 3.4 |
| The Siril weight test as written cannot recover the weights | Astronomer | Accepted | Section 3.4 |
| A group stack has no single reference frame, so the saturation and reference checks skip it | Software engineer, astronomer | Confirmed (`registration_reference_from_diagnostics` needs one zero-shift line) | Section 3.2 |
| wayfindinglib cannot reach the group rule through the public API; split part 2 into 2a and 2b | Technical architect | Accepted | Sections 3.2, 3.3 |
| The group refusal is in `compute_expected_noise`; the two forward models must agree | Technical architect | Confirmed | Sections 3.2, 3.3 |
| Section 5 belonged to no pull request; the confound rule read astrometricslib's `_inputs.json` from wayfindinglib | Technical architect, software engineer | Accepted (fingerprint stored as a row) | Sections 3.2, 3.3 |
| The confound rule was too broad (every dark, temperature and exposure) | Astronomer | Accepted (flat and bias masters, combine path, noise version) | Section 3.3 |
| Part 1 reinvented a rule the code already has | Technical architect | Confirmed (`saturation_threshold_can_be_reached`) | Section 2.1 |
| Assumed clip ceilings; the check fires only at the clip | Software engineer, astronomer | Accepted | Section 2.1 |
| Group stacks with `-weight=noise` keep unknown weights | Technical architect | Accepted | Section 3.2 |
| Effort: part 2 is 3 to 4 days | Software engineer | Accepted | Section 5 |
| The stored Siril facts cannot check the frame order (background empty on 5,888 frames; star counts from first runs match M 57 on 18 of 71 lines) | Software engineer, astronomer | Confirmed | Dropped with part 3 |
| Group members, folded groups without inclusion lines, left-out groups | Software engineer | Accepted | Dropped with part 3 |
| Duplicate runs, the made time, the lock, the registration record | All three | Accepted | Dropped with part 3 |
| Part 3 effort is 4 to 6 days | Software engineer | Accepted | Led to the user's decision to restack instead |

## Appendix D. Review board changes, version 4 (2026-10-25)

Reviewers: observational astronomer, software engineer and technical architect, all approve with
changes. Every critical and major claim was checked against the code or the database.

| Finding | Raised by | Check | Where it landed |
| :--- | :--- | :--- | :--- |
| Gain-divided weights are renormalized downstream; store the share without the gain and the gain on its own | Astronomer | Confirmed (`normalized_weights`, `expected_stack_noise`) | Section 3.2 |
| The combine's applied gains, skies and masks are never returned | Software engineer | Accepted | Section 3.2 |
| Per-group Siril data does not reach the merged diagnostics | Software engineer | Confirmed (`_merge_diagnostics` keeps group 0) | Section 3.2 |
| One combined saturation limit would change the ZWO and redefine a metric; keep two limits | Technical architect, software engineer, astronomer | Accepted | Section 2.1 |
| Stale Nikon saturated fractions survive the restack | Technical architect, software engineer | Accepted | Section 2.1 |
| M 27's 300 s frame is folded, so M 27 cannot be validated as written | Astronomer | Accepted | Section 3.4 |
| The suggestion path used a different model from the gate | Astronomer | Accepted | Section 3.3 |
| The group weight leaves out `N_g`; a planned group's `s_g` and a change of reference group were not specified | Astronomer, software engineer | Accepted | Sections 3.1, 3.3 |
| Nikon frames: mosaic-inflated `s_g`, debayered noise, uncooled darks, two optics | Astronomer | Accepted | Sections 3.2, 3.3 |
| Validation had no pass criteria and the tile placement was not specified | Astronomer | Accepted | Section 3.4 |
| More confound triggers: the heaviest group, the Siril and algorithm versions | Astronomer | Accepted | Section 3.3 |
| Invented frame noise (`[1.0] * n`) | Software engineer | Accepted | Section 3.2 |
| The fingerprint "as a row" had no form; a hash is not a measurement | Technical architect, software engineer | Accepted | D4, section 3.2 |
| Export the group rule and the fold rule as public functions; 2a and 2b disagreed on the gain | Technical architect | Accepted | Sections 3.2, 3.3 |
| The guard does not stop two library stacks of one target; a sync can take the measurement turn | Software engineer | Confirmed (`target_stack_guard.py`) | Section 4.2 |
| A plain `stack(force=True)` loop fails on two-camera targets and mixes optics; the batch script exists | Software engineer, technical architect | Confirmed (`check_one_camera`, `run_all_target_processing.py`) | Section 4.1 |
| The restack deletes older previous stacks | Software engineer | Accepted | D5, section 4.2 |
| The restack measures about 4,363 frames, likely two nights | Software engineer | Accepted | Section 4.3 |
| Header and reference from `reference_index`; members from registered, included frames | Software engineer, astronomer | Accepted | Section 3.2 |
| Clipped single-group stacks are also refused | Astronomer | Accepted, on purpose | Section 3.2 |
| Effort: 2a about 4 to 5 days, 2b about 1.5 to 2 days | Software engineer | Accepted | Section 5 |
| Move the patched-core rule to a separate pull request | Technical architect | Accepted | Sections 3.2, 5 |
| Wrong decision numbers in sections 2.2 and 3.4 | All three | Accepted | Sections 2.2, 3.4 |
| One public forward model instead of two plus a test | Technical architect | Not adopted: the two models live in different libraries; the agreement test guards them | Section 3.3 |
