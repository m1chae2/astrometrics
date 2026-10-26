# Imaged Target Suggestions Plan

This plan makes "take N more frames" possible for the targets already imaged. It has three steps,
done in order:

1. **Part 1, the Nikon fix.** The saturation limits and the D5300's read noise.
2. **Part 2, exposure-group weights.** 2a: a stacking run records frame weights, gains and the
   predicted noise for stacks combined from several exposure lengths. 2b: the forecast and the
   "take N more" suggestion use them.
3. **One forced restack of every imaging target**, after parts 1 and 2 land.

The code is the source of truth. Line numbers drift, so this plan names files and functions.

## Status

Status on 2026-10-25: version 4, revised after its board review (appendix D); nothing built.
Versions 1 to 3 tried to measure or rebuild the record of the existing stacks without stacking
again; the board reviewed each the same day (appendices A to C). The user chose one forced restack
after parts 1 and 2 instead, and dropped the rebuild. Decisions D4 and D5 were made on 2026-10-25;
D1 to D3 wait. Ready to build once the user approves.

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
  Siril normalizes the mixed frames with a scale it does not save.

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
  - a group that holds folded frames of another exposure;
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

## 4. The forced restack

### 4.1 How it runs

`astrometricslib/scripts/run_all_target_processing.py --force-restack` already gives each target to
one camera (`assign_targets_to_cameras`), uses the primary optic, isolates failures per target, and
has `--dry-run`, `--target`, `--targets-from` and `--skip-target(s-from)`. It gains two options
(one tool with parameters): `--stack-only` (no astrometry, photometry or spectroscopy, and no
spectral stacks) and a progress file so an interrupted run resumes. No new script and no backend
call with `force`. A plain loop of `processing.stack(target, force=True)` would fail on the 13
targets with frames from both cameras and would mix optics on the 12 shot at both 300 and 405 mm.

Second-camera stacks of a target are not rebuilt; the plan accepts that.

### 4.2 Steps

1. Count the folded groups of every target, and preview the quarantine
   (`QualityDiagnostics.frame_quality`), since the restack may move frames library-wide.
2. Back up the databases with `scripts/backup_library_databases.py`. The restack deletes each
   target's older previous stack (`previous_stack.commit_archive`); the user accepted that loss
   (decision D5).
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
using the progress file to resume.

### 4.4 When "take N more" can appear

After the restack, a target's next normal stack with new frames opens the gate only if:

- the restack's record is complete (weights known, predicted noise present);
- the new frames form groups of 5 or more, or join existing exposure lengths;
- the flat and bias masters are the same, and the heaviest group does not change;
- no group is clipped or stacked with `-weight=noise`;
- for Nikon targets, the read noise was measured first.

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
| D1 | The noise goal after M 57 is measured again | Keep 1.9e-4, or set the goal from M 57's current-placement value; and whether a suggestion needs the noise above the goal by about 2 combined sigma before it is better than Tentative | Open: decide after part 2a's validation |
| D2 | D5300 calibration frames | Use the library's 111 biases and 125 flats if the flats include pairs at ISO 800, or take a short calibration session | Open: check the library first |
| D3 | Which issue holds this work | One issue under epic #93 with the pull requests and the restack, or separate issues | Open: one issue recommended |
| D4 | Where the calibration identities live | A side table with a schema migration, or a hash stored as a measurement | Decided 2026-10-25: side table |
| D5 | The older previous stacks the restack deletes | Back up the stacks folder first, or accept the loss | Decided 2026-10-25: accept the loss; only the databases are backed up |

## 7. Out of scope

- Rebuilding the record of existing stacks without stacking (versions 2 and 3, dropped).
- Second-camera stacks of a target.
- Night weights, subset stacks and half-stack noise.
- Filling the Image Planner's filter list from the configuration (`system:filters` reads a key the
  configuration does not have).
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
