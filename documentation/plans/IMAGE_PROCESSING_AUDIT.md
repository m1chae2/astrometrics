# Image Processing Code Audit

This audit looks for tangled code in the image-processing part of `astrometricslib`: the stacking
pipeline (`pipelines/stacking`, including post-processing), `pipelines/shared` and `drivers`. That is
about 70,000 lines of non-test code in 142 modules. It was made on 2026-10-27 on branch
`134-group-weights-nikon` at 84f25715, after the #134 work landed.

The user set four criteria: structure, coupling, duplication and data flow. Each finding below gives
its evidence, a severity (how much it hurts safe change and reading), and a fix size. An EASY fix keeps
behavior the same, stays within one or two files, renames no public API or file, and is covered by
existing tests. Everything else is a PLAN item for the user to decide.

## Status

2026-10-27: measured, reviewed by three read-only reviewers (stacking orchestration, post-processing,
shared and drivers), and checked against the code. The EASY fixes in section 4 are being made on this
branch. The PLAN items in section 5 wait for the user.

## 1. Measurements

| Measure | Result |
| :--- | :--- |
| Average cyclomatic complexity (radon) | 5.0 (grade B) |
| Functions with complexity D or worse | 33; 11 at E or F |
| Files over 1,000 lines | 21 |
| Functions over 150 lines | 21 |
| Functions with 10 or more parameters | 27 |
| Import cycles | 1 (`shared/star_catalog_queries` with `shared/star_overlay`) |
| Drivers importing pipelines | 0 (not enforced by a contract) |
| Private names imported across modules | 15 |
| Copied blocks of 8 or more lines (pylint) | 1 |
| Names with no caller outside tests, repository-wide | 15 |

The worst functions:

| Function | Lines | Complexity | Parameters |
| :--- | :--- | :--- | :--- |
| `drivers/siril_interface.py` `ImageProcessing.process_target` | 732 | 72 | 15 |
| `stacking/stage.py` `_stack_frames_guarded` | 502 | 67 | 16 |
| `stacking/stack_runner.py` `_stack_exposure_groups` | 419 | 89 | 6 |
| `stacking/post_processing/stack_noise.py` `_measure_noise_in_plane` | 334 | 58 | 11 |
| `stacking/subset_noise_run.py` `_run_odd_even_difference_noise` | 306 | 48 | 6 |
| `drivers/siril_interface.py` `ImageProcessing.build_directories` | 251 | 50 | 4 |

**What the numbers say.** Most of the code is fine: small pure functions, models where they matter,
clean layering, one copied block. The tangle sits in about six long functions that each run several
phases or modes on shared local state, and in a few files that hold several jobs. Most of it is in the
stacking flow and the noise measurement, the code that grew fastest in #134.

## 2. Findings, most severe first

| Id | Criterion | Where | Finding | Severity | Fix |
| :--- | :--- | :--- | :--- | :--- | :--- |
| ORC-1 | Structure, data flow | `stack_runner._stack_exposure_groups` | Ten phases in one function, with state in about 12 parallel lists indexed by position and a second index space (`used`) read as `[x[i] for i in used]` about 25 times. Mixing the two index spaces is the main risk to safe change. | High | PLAN P1 |
| ORC-2 | Structure, coupling | `stage._stack_frames_guarded` | 502 lines threading five gates through shared locals, then naming, archive, crop, records and preview in an order that matters but is implicit. Re-packs the `SirilOptions` model field by field three times. | High | PLAN P2 |
| SD-1, SD-2 | Structure, data flow | `drivers/siril_interface.py` (2,961 lines) | One class holds locks and pipes, script builders, staging, output parsing, the imaging stacker and the spectral stacker; `process_target` takes 15 loose parameters and forks the whole flow on `is_spectral`. | High | PLAN P3 |
| PP-1, PP-9 | Structure, data flow | `stack_noise._measure_noise_in_plane` | Three modes chosen by flags (`may_choose_apertures`, `subtracted_plane`) tested at seven points, plus WCS choice, plate-solve fallback (inlined twice) and aperture choice. | High | PLAN P4 |
| PP-2, PP-3, PP-5 | Structure, coupling | `subset_noise_run.py` (2,766 lines) | Five jobs in one file; the odd–even runner threads 13 positional arguments through a nested `finish()`; the half-stack-and-check sequence is copied in `odd_even_bias.py`, which imports two private helpers. | High | PLAN P5, EASY E4 |
| PP-4 | Duplication | `subset_noise_run.py:1060-1090`, `record_science.py:585, 1200` | The expected-noise row sequence (compute, scale, rows for noise, ratio, scale, sky) is written twice, for the library and the subset paths. A change to one must be mirrored. (A reviewer counted three copies; the library path's two sites are one split sequence.) | High | PLAN P6 |
| ORC-4 | Duplication | `stack_runner.py:699`, `exposure_groups.py:1693` | The group weight `count × exposure² / noise²` is written twice; the alignment reference and the brightness reference must be the same group, and two copies can drift. | Medium | EASY E2 |
| PP-6 | Data flow | `difference_noise.load_noise_sums` | A positional 3-tuple read by index (`[2]` is the aperture identity) at three places. | Medium | EASY E5 |
| PP-7, SD-7 | Coupling | `stack_image`, `empty_apertures`, `stack_noise`, `camera_profile_store` | 15 underscore names used across modules: they are in fact module API. | Medium | EASY E4 |
| PP-8 | Duplication | `flat_match`, `colour_calibration`, `star_tone`, `registration_shifts`, `stack_noise`, `odd_even_apertures`, `aperture_size_noise` | The MAD-to-sigma factor 1.4826 is written as a literal in many places beside the constant `stack_image.MAD_TO_SIGMA`, and the robust spread is computed inline six times. | Medium | EASY E6 |
| SD-4 | Duplication | `drivers/filter_detection.py:34`, `drivers/image.py:241` | Two filter-name tables. A reviewer reported that "Luminance" maps differently; checked: both give L (the table matches by substring), so it is duplication, not a bug. | Medium | EASY E3 |
| ORC-5 | Duplication | `exposure_groups.frame_exposure_seconds` and three private copies | Five readers of a frame's exposure that differ in small ways (dict support, which errors they catch). | Medium | PLAN P7 (small) |
| ORC-8 | Dead branch | `stage.stack_frames` `filter_type` | Production always passes the frames; the `filter_type` branch and its second filter-alias table serve tests only. | Medium | PLAN P7 |
| ORC-9, ORC-10, PP-10, SD-5 | Structure | `stage.py`, `exposure_groups.py`, `empty_apertures.py`, `frame_measurement.py` | Files of 1,500–2,400 lines holding several jobs each (for example FWHM sampling, archive handling and naming inside `stage.py`; folding, gains, combination and file merging in `exposure_groups.py`). | Medium | PLAN P8 |
| SD-3 | Data flow | `siril_interface.py:570-605`, `astrometry_net_driver.py:110-243` | Process-wide counters read and reset by callers as a side channel. | Medium | PLAN P9 |
| SD-9 | Duplication | about 18 sites in drivers and shared | Direct `fits.open` calls outside `fits_access.py` (all with `memmap=False`, so safe), including two header updates in place. | Medium | PLAN P10 |
| SD-6 | Structure | `provenance_store._init_db` | 245 lines of schema and migrations in one function. | Medium | PLAN P11 |
| SD-8 | Coupling | `shared/star_catalog_queries` with `shared/star_overlay` | The only import cycle, half-broken by a lazy import. | Medium | PLAN (small) |
| ORC-6 | Duplication | `stack_request.check_one_camera`, `camera_selection.ensure_single_camera` | Two single-camera checks run in sequence, with different error classes. | Low | PLAN P7 |
| ORC-7, SD-11 | Dead code | see section 4 | Functions with no caller anywhere, and no test or only their own test. | Low | EASY E1 |
| SD-10, SD-12, SD-13 | Dead code | `staleness.py` (3), `calibration_library.is_*_compatible` (2), `frame_grouping.select_frames_for_configuration`, `detection_confidence.significance_to_confidence` | No production caller, but tested, and one documented: may be planned API. | Low | Decision |
| SD-14 | Coupling | many drivers | Drivers read the configuration directly. | Low | Decision |
| PP-11, PP-12 | Structure | `metrics.py` (2,863 lines), `flat_match.py` (2,045 lines) | Large but not tangled: `metrics.py` is a declarative table and `flat_match.py` is a cohesive set of steps. | Low | Leave |

**Well structured.** `stack_runner.run_stack` and `stack_request.run_stack` are short readable
dispatchers. The pre-processing gates are small uniform pure functions. `stack_image.py` is the single
home of sky-to-pixel conversion. `aperture_size_noise.py`, `difference_noise.py` and
`odd_even_apertures.py` are small and single-purpose; `half_pattern.py` measures a different thing
from the odd–even difference, so there is no overlap. The aperture file writer is atomic with a lock.
No module in scope imports `wayfindinglib` or the backend, and every FITS read uses `memmap=False`.

## 3. Checks made on the reviewers' claims

| Claim | Check | Result |
| :--- | :--- | :--- |
| SD-4: "Luminance" headers map to no filter in `get_filter_type` | Called it on "Luminance", "LUMINANCE", "Lum", "L" | All give L. Corrected to duplication. |
| ORC-4: the group weight is written twice | Read both sites | Confirmed, the same formula. |
| PP-4: the row sequence is written three times | Read the call sites | Two copies; corrected. |
| PP-8: the literal equals the constant | `MAD_TO_SIGMA = 1.4826` | Confirmed. |
| SD: `query_star_summary_page` defined twice | Reviewer's own check | An abstract method and its implementation, not a duplicate. |

## 4. EASY fixes made on this branch

Each keeps outputs and stored rows the same. Status as of 2026-10-27 (all uncommitted).

- **E1, dead code. Done.** Removed `exposure_groups.estimate_group_gains` and `group_frame_noises`,
  `calibration_library.check_for_calibration_frames`, `image.get_pixel_coords`, `image.get_world_coords`,
  `provenance_store.record_was_attributed_to` and `target_records.refresh_target`. `_is_plausible_gain`
  stays: `measure_group_gains` still uses it. The tests of the two removed `exposure_groups` functions
  were kept and now call `measure_group_gains` and `measure_group_frames`, because they also cover those
  two live functions.
- **E2, one group weight. Done.** `group_weight(count, exposure_seconds, frame_noise)` in
  `exposure_groups.py`, used in `_prepare_group_inputs` and in `stack_runner._stack_exposure_groups`,
  with a test.
- **E3, one filter table. Done.** `AstrometricsImage.filter_type` calls `filter_detection.get_filter_type`;
  the driver table gains the explicit "LUMINANCE" key. A test checks the property against the function
  for a list of header values.
- **E4, module API without underscores. Done.** Public names (and `__all__` entries) for the
  `stack_image`, `empty_apertures`, `stack_noise.measure_noise_in_plane`, `subset_noise_run` and
  `camera_profile_store` names; callers and tests updated. `camera_profile_store` had no `__all__`, so it
  now has a full one, and its three `ruff: ignore` type-hint suppressions were replaced by hints. In
  `stack_noise` the local variable `setup_key` was renamed `stack_setup_key`, because it would hide the
  new public function of that name. No documentation named the old private names.
- **E5, named tuples. Done.** `difference_noise.NoiseSums` for `load_noise_sums` (read by name in
  `subset_noise_run.py`) and `half_pattern.LagZeroTerms` for `lag_zero_terms`.
- **E6, one robust spread. Done in `post_processing`; left elsewhere.** `stack_image.robust_sigma` added
  and used where the computation is exactly the same (`odd_even_apertures`, `stack_noise`,
  `difference_noise`, `aperture_size_noise`, `flat_match`, `colour_calibration`, `star_tone`,
  `stack_comparison`). Other 1.4826 literals in `post_processing` now use `MAD_TO_SIGMA`. Skipped:
  the literals in `processing/` and `pre_processing/` (`exposure_groups`, `group_alignment`,
  `flat_calibration`), because `stack_image` sits in `post_processing` and those earlier stages import
  nothing from it; `sensor_noise.MAD_TO_STANDARD_DEVIATION` is likewise left. They need a shared home
  for the constant first (see P8).
- **E7, small tidy-ups. Done.** One module-level `get_configuration` import in `stage.py` (the local
  imports in `_stack_frames_guarded` are gone; the ones in other functions stay, because a test patches
  `foundation.config.get_configuration` for `_record_configuration_stack`). The `ruff: ignore`
  comments in `stage.py` are replaced by type hints. `subset_stack.stack_stem` is used in
  `subset_noise_run.py` and `odd_even_bias.py`.
- **E8, a layering contract. Done.** The import-linter contract "astrometricslib drivers never import
  pipelines" is in `pyproject.toml` and is kept.

## 5. Refactor plan (for the user to decide)

Order matters: P1, P2 and P4 carry the most risk and the most benefit; each needs a real-data
before-and-after comparison on the copy (identical stored rows on a few targets), not only unit tests.

| # | Change | Shape | Size |
| :--- | :--- | :--- | :--- |
| P1 | Exposure-group stacking | A `GroupResult` model replaces the parallel lists; "used" becomes the groups without a left-out reason; the function splits into stack each group, align and screen, write the combined stack, describe the groups. | Large |
| P2 | The stacking stage | `_stack_frames_guarded` becomes a short pipeline over a run context; the gates move to a frame-screening module; FWHM sampling, registration records and calibration health move out of `stage.py`; `SirilOptions` and one quality-recording object are passed whole. | Large |
| P3 | The Siril driver | `siril_interface.py` becomes a package: script builders, session (lock, pipes, output parsing), staging, imaging stacker, spectral stacker; `process_target` takes one request model and runs short phase methods. | Large |
| P4 | Stack noise | Three entry points (library, subset, difference) sharing three steps: choose the WCS (one plate-solve helper), resolve the apertures, summarize the sums; no mode flags. | Large |
| P5 | Odd–even difference | Its own module with a `HalfStacks` helper shared with the bias script, and a result model in place of 13 arguments; growth and night planning stay in `subset_noise_run.py`. | Medium |
| P6 | Stack science rows | One function builds the expected-noise rows for the library and subset paths. After P5. | Medium |
| P7 | Small consolidations | One frame-exposure reader; one single-camera check; remove the `filter_type` test path from `stack_frames` and its second alias table (tests rewritten to pass frames). | Small |
| P8 | Large files by job | Split `exposure_groups.py`, `empty_apertures.py` and `frame_measurement.py` by job; move `FULL_SCALE_COUNTS` to a shared constants home. | Medium |
| P9 | Run counters | Siril lock-wait and plate-solve counters carried on a run object instead of module globals. | Small |
| P10 | FITS access | Header-and-data and header-update helpers in `fits_access.py`, then migrate the 18 direct callers. | Medium |
| P11 | Provenance schema | `_init_db` as an ordered list of migration steps. | Small |
| D1 | Decision: tested but unused functions | Keep `staleness.py`, the two calibration compatibility checks, `select_frames_for_configuration` and `significance_to_confidence` as planned API, or remove them. | — |
| D2 | Decision: configuration in drivers | Whether drivers may read the configuration, or take settings when built. | — |
