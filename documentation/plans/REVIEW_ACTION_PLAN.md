# Review Action Plan

This plan lists the items from the 2026-10-09 architecture and scientific-rigor review of the
repository. Section 1 sets the order of work. Each item names the problem, how to confirm it
is real, how to measure that it is fixed, and what stops it from coming back. Section 5 describes the shared prevention mechanisms
that the per-item "Prevent" lines refer to.

The code is the source of truth. Line numbers drift, so each item names files and functions.

How to read an item:

- **Issue.** What is wrong and why it matters.
- **Confirm.** A check a person or an agent can run to prove the issue exists before changing
  anything. A fix that cannot reproduce its issue first is a guess.
- **Success.** The measurable condition that closes the item.
- **Prevent.** The mechanism that keeps the issue from returning. Mechanism names such as
  "golden-data test" or "contract test" refer to section 5.

## 1. Priorities and order of work

Scientific correctness comes first. Architecture work is limited to two aims: usability, meaning
a person or a Python caller can do the task without surprises, and reliability, meaning one
implementation of each function rather than copies in several places. Items that serve neither
aim are deferred and listed at the end of this section.

### 1.1 Science, by tier

**Tier 1: produces wrong results today.** Fix each with its truth-known test (section 5.3) in the
same change.

| Item | What is wrong | Size |
|---|---|---|
| S1 | SIP distortion lost on WCS write-back | one line plus a test |
| S2 | integer-pixel apertures, no per-star centroid | small |
| S3 | cross-session rescaling erases long-term variability | small |
| S4 | single calibration frames used raw; one-light stacks skip calibration | small |
| S5 | missing `DATE-OBS` becomes the current time | small |
| S7 | wavelength tuner index offset | small |
| S13 | exposure-group gain measured on saturated pixels | medium |
| S6 | RA wrap at 0h in asteroid chaining | small |

**Tier 2: standards gaps that change conclusions.** S8 (uncertainties) and S9 (mid-exposure
BJD_TDB times) come first because S10, S18, and the period searches depend on them.

| Item | What is missing | Depends on |
|---|---|---|
| S8 | per-point flux uncertainties, gain and read noise | — |
| S9 | mid-exposure BJD_TDB timestamps | — |
| S10 | vetted fixed comparison set; ensemble-based detrending | S8 |
| S11 | rejection floor or asymmetric bounds at small N | — |
| S12 | temperature and binning in the dark-matching key | — |
| S15 | spectral aperture, sky estimate, response mask width, extinction | — |

**Tier 3: honesty of reported confidence.** S14, S16, S17, S18, S19, S20, S21. Do these after
tiers 1 and 2; S18 only after S8.

**Tier 4: standards a journal referee would ask for.** From the 2026-10-11 gap review (section
1.5). The cheap items come first because each reuses numbers the pipeline already computes.

| Item | What is missing | Size | Depends on |
|---|---|---|---|
| S22 | a false-alarm probability for each variability flag | small | S8 |
| S23 | spectroscopy noise that follows the pixels each stack kept | small | — |
| S24 | per-frame covariance of position error, and an ADES report | medium | — |
| S25 | apertures scaled to each frame's star width, with an aperture correction | medium | — |
| S26 | colour-matched comparison stars, or an airmass-times-colour term | medium | S10 |
| S27 | injection-recovery grids: transit depth by magnitude, mover rate by star density | medium | S8 |

### 1.2 Architecture, filtered by the two aims

Do first, because they block the science work or are small:

- **A15** (agent sessions can run tests). Nothing in tier 1 can be confirmed without it.
- **D3** (the copy rule) and **A11**, **A10**, **A12** (the copies that exist). These are the
  "one implementation" aim directly.
- **A1** (a served read tool connects to hardware). Reliability, and a one-line block today.
- **A6** (driver injection in astrometricslib). Needed so S1 and S14 tests run without
  astrometry.net and SIMBAD.

Do as part of the science work when a touched file makes it cheap:

- **A7** (ambient configuration reads), **A8** (typed returns), **A9** (argument names). Each is
  a usability item for Python callers and should be fixed in the files the science items open,
  not as a sweep.
- **A2** (cross-process cancel). Reliability; fix when the job framework is next touched.

Deferred. These concern the agent-control surface, the plan document, or UI typing and do not
serve the two aims strongly enough to go before the science work:

- **D1** and **D2**. Keep the current read-mostly MCP policy for now (D1 option 1) and defer the
  process-model decision. A1 removes the one live hazard in the meantime.
- **A3**, **A4**, **A5**, **A13**, **A14**.

### 1.3 Suggested sequence

1. A15, then run the full suite to get a baseline.
2. Build the synthetic generators of section 5.3 (photometry frame, spectral frame). Every later
   science fix adds one test to them.
3. Tier 1 science items, one change each, each with its test and its golden-data pin
   (section 5.4).
4. A6, then S14's astrometry tests.
5. S8 and S9 together, since both change `PhotometryResult` and the camera profile.
6. S10, S11, S12, S15.
7. D3 written into CLAUDE.md, then A11, A10, A12 as one "remove the copies" pass with the
   dead-code check (section 5.7) turned on.
8. Tier 3 science items.
9. Deferred items as time allows.

### 1.4 Progress

Each line names the check that now holds the item, per section 5.5. A line with no check is not
done.

| Item | State | Check |
|---|---|---|
| A15 | done | `.claude/hooks/session-start.sh` builds `.venv`; validated by a cloud-session run |
| Generators | done | `astrometricslib/test/synthetic/test_synthetic_generators.py` |
| Golden suite | done | `astrometricslib/test/golden/test_golden_m13.py` (skips without Git LFS frames) |
| S1 | done | `pipelines/astrometry/test/test_runner_wcs_write_back.py`; the same bug was removed from `session_identification.py` and `spectroscopy/batch.py` |
| S2 | done | `pipelines/photometry/test/pre_processing/test_frame_photometry.py` (sub-pixel, drift, centroid tests) |
| S3 | done | `pipelines/photometry/test/test_between_session_variability.py` |
| S4 | done | `drivers/test/test_siril_calibration_master_scripts.py`; gate `calibration_frame_count` |
| S5 | done | `test_frame_photometry.py` and `test_variability_analyzer_timestamps.py`; `test_layer_boundaries.py` blocks `datetime.now` under `pipelines/` |
| S6 | done | `pipelines/asteroid_detection/test/test_detection_ra_wrap.py` |
| S7 | done | `pipelines/spectroscopy/utilities/test/test_calibration_tuner_sample_distances.py` |
| S13 | done | `pipelines/stacking/test/test_exposure_group_linearity.py`; gate `exposure_group_linearity` |
| A6 | done | `api/test/test_driver_injection.py`; `test/test_driver_construction.py` blocks direct construction under `pipelines/` |
| S8 | done | `pipelines/photometry/test/pre_processing/test_flux_uncertainty.py`; gate `flux_uncertainty` |
| S9 | done | `pipelines/photometry/test/pre_processing/test_observation_times.py`; gate `capture_timestamps` names the time basis |
| S10 | done | `pipelines/photometry/test/test_comparison_ensemble_injection.py`; gate `comparison_ensemble` |
| S11 | done | `utilities/test/test_rejection_thresholds.py`; `scripts/rejection_small_n_check.py` measures the rejected fractions |
| S12 | done | `drivers/test/test_calibration_matching.py`; gate `calibration_metadata` |
| S14 | done | `drivers/test/test_astrometry_net_fit_statistics.py`, `test_gaia_proper_motion.py`, `test_scale_hint_binning.py`, `test_gaia_search_depth.py` |
| S15 | done | `pipelines/spectroscopy/test/test_extraction_aperture_stability.py`, `test_sky_estimate_bias.py`, `test_response_balmer_mask.py`, `test_extinction_correction.py` |
| S20 | done | `test_layer_boundaries.py` blocks unseeded randomness under `pipelines/` |
| RA wrap follow-ups | done | `test_pointing_correction_ra_wrap.py`; `test_gaia_cache_ra_wrap.py`; helper in `pipelines/shared/angles.py` |
| S16 | done | `pipelines/spectroscopy/test/test_threshold_single_definition.py`; `models/test/test_spectral_match_fields.py`; gate `spectral_classification` fails on class-level ambiguity |
| S17 | done | `pipelines/asteroid_detection/test/test_session_chaining.py`, `test_residual_criterion.py`, `test_mpc_report.py`; gate `track_residuals` |
| S18 | done | `pipelines/photometry/test/test_variability_injection.py` (synthetic AUC 0.94); gate `variability_discrimination` fails below 0.7 |
| S19 | open | the recompute script still re-reads stored arrays; an independent re-measurement is not yet written |
| S21 | done | `astrometricslib/test/test_architecture_doc_claims.py` imports every `impl` link in the architecture document |
| Golden photometry sequence | done | `test/golden/test_golden_m13.py` pins the five-frame shifts and per-star scatter |
| Tuner follow-ups | done | `utilities/test/test_calibration_tuner_dip_detection.py` (noisy frame tunes in under 10 s) |
| Photometry follow-ups | done | `test/pre_processing/test_centroid_shift_summary.py`; gate `registration_drift` judges per-star offsets |
| S14 follow-ups | done | `pipelines/shared/test/test_session_fit_statistics.py`; `drivers/test/test_catalog_store_regions.py`; the residual limit is still uncalibrated on real solves |
| Spectroscopy checkpoints | done | four `StageQualityCheckpoint`s per spectrum (raw frame, calibrated spectrum, processing, output); `pipelines/spectroscopy/test/test_stage_quality.py`; gate `processing_quality` |
| Atmospheric refraction | done | `test/pre_processing/test_atmospheric_refraction.py` (Ciddor 1996 index to 1e-8), `test_differential_refraction.py`; checkpoint 0 metrics `dar_*`, `target_altitude_degrees` |
| Wavelength zero point | done | `test/pre_processing/test_wavelength_zero_point.py` (25 Å error recovered within 2 Å; measured after refraction); checkpoint 1 metrics; gate `wavelength_scale` |
| Per-sample errors and equivalent widths | done | `test/pre_processing/test_intensity_variance.py` (reported error / scatter 0.96 to 1.01 over 200 realizations), `test/processing/test_equivalent_width.py` (z-score sd 1.06); checkpoint 1 and 2 metrics, all report-only |
| Dereddening and line indices | done | `test/processing/test_dereddened_classification.py` (reddened G0V reads K2V, dereddened G0V); line-index type is a rough second opinion (50% within 2 subtypes, leave-one-out) |
| Gaia XP comparison | done | `test/post_processing/test_compare_to_gaia_xp.py` (5%/1000 Å tilt recovered to 0.1); checkpoint 3 metrics; gate `gaia_xp_agreement` |
| Measured line spread | done | `test/pre_processing/test_measured_line_spread.py` (injected sigma within 5%); checkpoint 1 `chromatic_defocus_ratio`; `scripts/spectral_focus_sweep.py`, `scripts/compare_instrument_responses.py` |
| S12 / S13 real-data corrections | done | `drivers/test/test_calibration_matching.py::test_a_library_saved_before_temperatures_does_not_pool_them`; `pipelines/stacking/test/test_exposure_groups.py::test_a_different_sky_does_not_pass_for_a_gain` (see 1.5) |
| Truncated calibration frames | done | `drivers/test/test_calibration_matching.py::test_a_truncated_frame_is_not_added_to_the_library` |
| Gaia from VizieR first | done | `pipelines/astrometry/test/processing/test_gaia_service_fallback.py` |
| Old star records load | done | `models/test/test_spectral_match_fields.py::test_a_star_saved_before_the_new_verdicts_still_loads` |
| S22 | done | `pipelines/photometry/test/processing/test_variability_indices.py` (tail checked against `gammaincc` and a Monte Carlo; 1.0 percent of constant stars below 0.01); candidate rule needs a Benjamini-Hochberg q-value below 0.01 |
| Solution refit to Gaia | done | `pipelines/astrometry/test/processing/test_wcs_refinement.py` (known solution recovered to 0.03 arcsec through 15 percent wrong detections); `test_plate_solve_fit_statistics.py::test_the_gaia_refit_gets_every_detection_not_only_the_identified_ones`; the `astrometric_residual` gate judges the RMS against Gaia. Real stacks: offsets 2.75, 0.59 and 0.10 arcsec became 0.01, 0.06 and 0.00 |
| S23 | done | `pipelines/spectroscopy/test/pre_processing/test_intensity_variance.py::test_the_box_variance_follows_the_frames_each_pixel_kept` (known variance recovered to 1 percent; the model without the map reports under 70 percent); real stacks: variance too small by 21 to 32 percent (errors by 11 to 18 percent) on Alnath, Vega and Arcturus |
| S24 | done | `pipelines/asteroid_detection/test/test_residual_criterion.py::test_each_axis_is_judged_against_its_own_error`; `test_position_error_estimate.py::test_the_estimate_recovers_a_correlation_between_the_axes`; `test_mpc_report.py` (ADES rows, header, assumed-error count). The ADES file is not validated with the MPC's own tool |
| Step 3, photometry on real data | done for 3 of 3 usable targets | M 13: AUC 0.61 (37 listed variables; 0.58 needed), the rule accepts 5 of them; M 81: AUC 0.68 to 0.73 on only 6 listed variables (0.70 needed, too few to say); NGC 2403: AUC 0.48, chance, on a night of patchy cloud that the `comparison_ensemble` gate now fails (ratio 18; `test_run_gates.py::test_ensemble_gate_goes_red_when_the_comparison_stars_scatter_far_above_their_errors`). Found and fixed: a merged star had no mean or scatter, so the run crashed (`test_between_session_variability.py`). Algol cannot be used: 10 frames over 10 minutes, out of eclipse. Found and fixed: `registration_drift` failed on all three (95th percentile per-star centroid offset 1.13, 1.24 and 1.29 px against a limit of 1). Replaying the gate on the raw frames showed centroid noise on faint and crowded stars on M 13 and M 81 (the offsets did not grow with distance from the centre and a fitted rotation did not reduce them) and a real field rotation on NGC 2403 (0.05 degrees over the night, 1.9 px at a corner). Frames are now lined up with a fitted shift, rotation and scale (`FrameAlignment`, `test_frame_alignment.py`), which cut NGC 2403's fallback share from 5.5 to 3.7 percent on its own and by more when the rotation is larger, and the gate judges the corner displacement (limit 1 px: M 13 0.2, M 81 0.9, NGC 2403 1.9) and reports the per-star offsets without judging them. Not a stacking issue, so the stacks do not change |
| Camera gain measured | done | gain 0.783 e-/ADU and read noise 3.8 e- in the ASI 533MM Pro profile and the example config (`drivers/test/test_camera_profile_store.py`); the example's 3.6 was in 14-bit ADU. Real check of S8 on M 13: predicted ensemble error 0.0112 mag against measured scatter 0.0142 mag |
| S27, photometry | done | `scripts/validate_photometry_injection.py` with `scripts/test/test_validate_photometry_injection.py`; M 13: half of the signals found at about 0.04 mag (brightest quarter) to 0.16 mag (faintest), none below 0.02 mag, 0 of 29,760 untouched stars flagged. The asteroid grid was re-run on 2026-10-11 after the direction check: the recovery table is unchanged and 0 of 40 mover-free fields confirmed a track (1 before). Star density as an axis and a purity fraction are still not done |
| S25, S26 | open | none yet. S25 would not have fixed the NGC 2403 night: r = 4 and r = 10 apertures lost the same 20 percent while the seeing was steady |

Decisions taken during the work, for the owner to confirm:

- `AMBIGUOUS_RMS_GAP` is 0.02 RMS, the smallest adjacent-rung spacing of the template ladder.
  Real stars show a median best-to-second gap of about 0.006, so most are ambiguous at subtype
  level; the gate therefore fails only on ambiguity between spectral classes, and the subtype
  count goes in its detail. Set the constant to about 0.0025 to recover the old behaviour.
- Airmass extinction uses the mean Kitt Peak curve (2 km, dry site). Only the curve's shape
  enters a difference correction, but a wetter or lower site has more blue extinction.
- The stored instrument response still carries coefficients fitted with one blur width and the
  old 60 Å Balmer mask. Simulation puts its error at up to 0.5 percent (0.3 percent red-to-blue
  tilt) if the original width was about 45 Å. The fit now blurs with the measured line-spread
  profile; re-derive on the Vega stack with `scripts/derive_instrument_response.py` (the stack
  is not in the repository).
- Variability thresholds (chi-square and Stetson J at the field's 99th percentile, excess
  scatter 1.5, AUC floor 0.7) are design choices measured only on synthetic fields.
- Spectroscopy limits that are designed, not measured on real spectra: the refraction span
  (20 Å) and minimum altitude (20°), the zero-point offset (20 Å) and the `wavelength_scale`
  gate (11 Å), the Gaia XP tilt (3 %/1000 Å), RMS (5 %) and shift (11 Å), and the chromatic
  defocus ratio (1.3). The zero-point line threshold (6 sigma) is measured on synthetic noise.
  Real XP spectra of different A0V stars already differ by up to 36 Å in shift from one
  template, so the 11 Å XP shift limit may sit at the method's floor; run
  `scripts/compare_spectra_with_gaia_xp.py` on a real night to set it.
- The Gaia BP-RP to E(B-V) factor (1.339) and the A(G) to E(B-V) factor (2.740) were taken from
  Casagrande and VandenBerg (2018) from memory and need checking against the paper.
- The stored instrument response was fitted before the refraction correction existed;
  re-derive it once a Vega stack and an observatory site are available.
- `SpectroscopyPipeline(use_measured_line_spread=True)` stays off until the focus sweep has
  been run on a real bright star; M 13 (a cluster) cannot settle chromatic defocus.

### 1.5 Real-data validation and the gap review, 2026-10-10 and 2026-10-11

The first three tiers were built and tested on synthetic data and on five M 13 frames. Running
them on the library (ASI 533MM stacks of M 13, M 81 and NGC 2403, and the astrometry stage) found
problems that no synthetic test had:

- **S13 measured the sky, not the stars.** The gain equalled the two nights' sky-per-second ratio
  to three decimals (M 81 1.297 against 1.298), because the middle of a deep-sky image is sky.
  Independent star photometry gave 1.40. The gain is now measured on sky-subtracted pixels well
  above the sky. On NGC 2403 it agrees with star photometry (1.137 against 1.127); on M 81 the
  60 s group is now left out as nonlinear (1.139 in the middle, 1.203 at the bright end). Why M 81's
  bright stars read high is open.
- **S12 did not reach an old library.** `calibration_frames.json` was saved before temperatures were
  part of the key, and the selector then used darks of every temperature without a flag. A dark
  with no temperature in its key is now placed by its own header.
- **S16 broke 68 stored stars.** Old records had no `isPoorMatch`, so loading them stopped every
  pipeline that records stars for their targets. The field is now optional.
- **Gaia lookups failed on 22 of 23 targets.** The ESA archive was timing out on the G < 18 query.
  VizieR answers the same query in 10 s and is now asked first.
- **A truncated bias frame** (13 of 48 MB) was in the index. Frames shorter than their header
  says are now refused.
- **A metric had the wrong name.** `astrometric_residual_rms_arcsec` held the catalog match
  separation, and the gate report labelled it the plate-solve residual.

Measured and holding: S1 (SIP terms present, up to 1.8 arcsec at a corner), S11 (rejected pixels
2.1, 4.0 and 5.0 percent down to 1.6, 1.7 and 1.8 percent), and the astrometric solutions
themselves (median scatter about 0.24 arcsec against Gaia, one eighth of a pixel).

Decisions of 2026-10-11:

- **No search for fast movers.** The detector keeps its 300 arcsec per hour (5 arcsec per minute)
  cap. Faster objects would be mostly satellites, and Starlink alone puts thousands of them in
  the sky. A trail fit or shift-and-add search is out of scope, and the asteroid README should
  say why.
- **No absolute flux calibration.** Spectra stay in relative flux (a response curve from Vega and
  an airmass correction). Classification does not need physical units. The review's claim that
  dispersion is linear was wrong: the pipeline already uses the grating equation and fits the
  grating distance.
- **Tier 4 goes ahead**, cheap items first (S22, S23, S24), then S25 to S27.

Found while doing tier 1, now tracked as follow-ups:

- The global-shift centroid in `frame_photometry.py` counted noise as light and under-measured
  drift by about half. Fixed with S2.
- `find_zero_order_position` returns `None` on every M 13 spectroscopy frame, because the target
  is an extended cluster. The spectral frame check therefore cannot run on cluster targets.
- M 13 frame 020 carries a faint satellite or plane trail (4 px wide, about 50 ADU over a 320
  ADU sky) that lifts noise peaks over the detection cut-off. No raw-frame flag catches it; a
  high-count rule or a line detector is needed.
- The tuner's dip detector uses an absolute prominence in the spectrum's own units and can find
  hundreds of dips on a noisy ADU-scale spectrum, after which the three-line search does not
  finish.
- `test_periodicity_search.py::test_a_wide_span_with_fine_cadence_does_not_stall_the_period_search`
  is a wall-clock test (30 s) that takes about 31 s on a 4-core container with unchanged code.

## 2. Decisions to make first

Three of your instructions contradict the repository's own documents. Items D1 and D2 affect only the
agent-control surface and can wait. D3 governs section 4 and should be made now.

### D1. How much control an AI agent gets through MCP

**Issue.** The goal of agent parity with the desktop app conflicts with `.claude/AGENTS.md`
section 5, `GEMINI.md`, and the `drop` rows for every `actuate` and `safe-stop` tool in
`mcp_servers/inventory/tool_dispositions.py`. All three say an agent never commands a device,
changes configuration, or runs code. The code follows the documents.

**Confirm.** Run `python -m mcp_servers.inventory` and count served tools with class `actuate` or
`safe-stop` in any profile. The count is zero.

**Decision.** Choose one of:

1. Keep the read-mostly policy and drop the parity goal. Update the goal statement in CLAUDE.md.
2. Add an `operator` profile that serves actuation tools, and rewrite the three documents. This
   requires D2 first.

**Success.** CLAUDE.md, `.claude/AGENTS.md`, `GEMINI.md`, and the disposition table state the same
policy, and `mcp_servers/inventory/test/test_served_tools.py` pins it.

### D2. Which process owns the hardware when an agent acts

**Issue.** The library MCP servers build their own `Astrometrics()` and `Wayfinder()` in their own
processes (`mcp_servers/astrometrics_core/definition.py`, `mcp_servers/wayfinding_core/definition.py`).
The backend holds the INDI worker, the sequencer thread, job futures, caches, and the event
socket (`backend/container.py`). The Wayfinding architecture's Single Command Authority rule
(`documentation/library_design/Wayfinding_Library_Architecture.md`, "Single Command Authority")
allows one actuating system per device. Two processes with two INDI clients breaks that rule.
Parity for control is impossible under this process model.

**Confirm.** Start the backend, then run `wayfindinglib-core` with `observatory_guiding_status`
and `include=["live"]`. Watch the INDI server's client list. A second client connects.

**Decision.** Choose one of:

1. Control tools go through the running backend over `/api/rpc`, the same path the desktop app
   uses. The library MCP servers keep only computation and catalog reads. `backend_call_rpc`
   already exists in `mcp_servers/backend/definition.py` as the transport.
2. The backend becomes a thin shell and both clients talk to one long-lived library service that
   owns the hardware. This is a larger change.

**Success.** Exactly one process opens an INDI client connection while the desktop app and an
MCP client are both active, and a test proves it with a fake INDI server that counts connections.

### D3. Whether adapters may copy library logic

**Issue.** Your instruction allows the backend, UI, and MCP servers to duplicate library logic for
performance. `ARCHITECTURE_PLAN.md` section 1 says adapters hold no domain rules, section 5 lists
copies to delete, and CLAUDE.md section 2 says business logic lives in services. These cannot
both hold.

**Decision.** Adopt the rule that section 5.3 of `ARCHITECTURE_PLAN.md` already applies to the
planetarium
projection math: a copy is allowed only with a measured performance reason recorded in the
file's description block, and a reference test that compares the copy's output with the library
function at fixed inputs (`ui/tests/test_projectionReference.test.ts` is the model). Write this
rule into CLAUDE.md section 2.

**Success.** Every duplicate listed in item A12 either has a reference test and a recorded reason,
or is deleted. A grep for the rule's marker comment finds every allowed copy.

## 3. Scientific items

Ordered by effect on results. "Golden-data test" and "injection test" are defined in section 5.

### S1. SIP distortion is lost when the WCS is written to the file

**Issue.** `_write_solved_wcs_to_fits_header` in `astrometricslib/pipelines/astrometry/runner.py`
calls `wcs.to_header()` without `relax=True`. astropy then omits the `A_*`/`B_*` coefficients and
strips `-SIP` from `CTYPE`. Every later reader of the file header, including photometry,
spectroscopy, and asteroid detection, gets a tangent-plane-only solution with distortion errors at
the field edges. This is also the only `fits.open` in the library without `memmap=False`.

**Confirm.** Solve a frame, then `fits.getheader(path)` and look for `A_ORDER`. It is absent
while `context.wcs.sip` is not `None`.

**Success.** `A_ORDER` is present after write-back, and a test computes pixel-to-sky at a corner
with the in-memory WCS and the re-read header and finds them equal to 0.01 arcsec.

**Prevent.** Golden-data test on the M 13 sample frame that pins the corner position. The
`memmap=False` rule becomes a ruff `TID251`-style check or a grep test.

### S2. Aperture photometry uses integer pixel centers and never re-centroids

**Issue.** `_measure_aperture_flux` in `pipelines/photometry/pre_processing/frame_photometry.py`
rounds `x` and `y` before building the aperture. Per-star positions are the reference position
plus one global shift from the median of about 50 centroids. The architecture document says each
star is re-located by its brightness-weighted centroid. With a 4 px aperture and 4 px FWHM
(full width at half maximum, the star's apparent size), a 0.5 px offset loses 1 to 2 percent of
flux and the loss changes with drift, which is the amplitude of the signals the pipeline searches
for.

**Confirm.** Injection test: place a Gaussian star of known flux at (100.5, 100.5) and at (100.0,
100.0) on a flat background and measure both. The fluxes differ by more than 1 percent.

**Success.** The injection test recovers flux to 0.2 percent at any sub-pixel position across a
10 px drift. Positions pass through unrounded, and each star is re-centroided per frame within a
small box.

**Prevent.** The injection test runs in CI. Gate `registration_drift` records the per-star
centroid shift distribution, not only the global one.

### S3. Cross-session merge erases long-term variability

**Issue.** `_merge_photometry_results` in `pipelines/photometry/batch.py` rescales each new
session's `fluxes_normalized` and `fluxes_detrended` so their median equals the canonical
session's median before concatenating. `identify_long_term_variable_candidates` in
`processing/variability_analyzer.py` then measures only within-session scatter, while its
docstring claims to find changes over weeks and months.

**Confirm.** Injection test: build two sessions of one constant star and one star 0.3 mag fainter
in the second session, merge, and run the long-term candidate search. The faint star is not
flagged.

**Success.** The injection test flags the star. The merge keeps the ensemble-relative level of each
session and records per-session zero points separately.

**Prevent.** Injection test in CI, and the `detectable_amplitude` gate extended to a
between-session amplitude.

### S4. Single calibration frames are used raw, and single-light stacks skip calibration

**Issue.** In `drivers/siril_interface.py`, a lone flat is loaded and saved as the master without
bias subtraction, so the bias pedestal stays in the flat and vignetting is under-corrected. A lone
bias or dark becomes the master with no rejection. A one-light "stack" skips calibration entirely
and is still recorded as the target's stacked image. No minimum frame count exists.

**Confirm.** Run a stack with one flat and inspect the generated Siril script. There is no
`calibrate flat` line.

**Success.** A single flat is bias-subtracted. Masters from fewer than a configured minimum (3 is
the usual floor) raise `InvalidArgumentError` or carry a blocking quality flag that
`assess_flats` and the stack quality summary report. A one-light run is calibrated or refused.

**Prevent.** Unit tests on the script generator for N = 1, 2, 3. The flat and dark quality gates
record frame counts.

### S5. Missing `DATE-OBS` becomes the current time

**Issue.** `frame_photometry.py` and `variability_analyzer.py` fall back to `datetime.now()` when
`DATE-OBS` is absent or `fromisoformat` cannot parse it. The frame then enters the light curve
with a fabricated time.

**Confirm.** Strip `DATE-OBS` from a test frame and run photometry. The frame has today's date.

**Success.** The frame is rejected with a reason recorded in the `capture_timestamps` gate.
Parsing goes through astropy `Time`, which accepts the FITS formats `fromisoformat` does not.

**Prevent.** A test with a missing and a malformed `DATE-OBS`. Ruff ban on `datetime.now()` in
`pipelines/` (a pipeline has no business reading the wall clock).

### S6. Right ascension does not wrap at 0h in asteroid chaining

**Issue.** `_tangent_plane_offset_arcsec` and the chaining bounding box in
`pipelines/asteroid_detection/detection.py` subtract raw RA without wrapping at 360°. Fields that
straddle RA = 0h break chains and the stationarity test.

**Confirm.** Injection test with a mover crossing RA = 359.99° to 0.01°. No chain forms.

**Success.** The test recovers the mover. Offsets use `astropy.coordinates` separation or an
explicit wrap.

**Prevent.** The injection test in CI. A library-wide rule in the pipeline README: angular
differences go through one shared helper.

### S7. Wavelength tuner index offset

**Issue.** `calibration_tuner.py` maps a dip index `i` to pixel offset `current_start_px + i`. The
pipeline (`pipelines/spectroscopy/pipeline.py`, `_process_single_star`) has already dropped
leading samples that fall off the image or below `sensor_min_wavelength`. When any leading sample
was dropped, every dip is mis-placed by that count and the fitted grating distance is biased.

**Confirm.** Run the tuner on a Vega frame with the zero order placed so the first samples fall
below the sensor minimum, and compare the fitted distance with a run where no samples are dropped.

**Success.** The tuner uses `distances_from_zero_order_px` that the pipeline already records, and
a test with a synthetic spectrum (known lines at known pixels, with leading samples off-image)
recovers the lines to 0.5 px.

**Prevent.** The synthetic-spectrum test in CI (section 5.3).

### S8. Photometry carries no uncertainties

**Issue.** `PhotometryResult` (`models/stellar_source.py`) has no error field. `CameraProfile`
has no gain or read noise. The CCD equation (source Poisson noise plus sky, read, and dark noise,
with gain in electrons per ADU) is never applied. Period searches substitute one scatter value
from neighbor differences. Every downstream statistic (variability index, periodogram
normalization, transit SNR) therefore has no per-point weight.

**Confirm.** Grep `read_noise` and `gain` in `models/camera_profile.py` and
`pipelines/photometry/`. No matches.

**Success.** `CameraProfile` gains `gain_e_per_adu` and `read_noise_e`. `PhotometryResult` carries
`flux_errors`. An injection test with a star of known flux and known noise recovers the predicted
σ to 10 percent. Lomb-Scargle and BLS receive `dy`.

**Prevent.** A model test that `PhotometryResult` arrays all have equal length including errors.
The variability gates report the median σ.

### S9. Times are exposure start in naive UTC

**Issue.** Timestamps are `DATE-OBS` as written, with no half-exposure shift and no conversion to
BJD_TDB (barycentric dynamical time, the standard time scale for light curves). A 300 s exposure
gives a 150 s phase error. Light-travel time across Earth's orbit adds up to 8 minutes over a
multi-month baseline, which shifts periods and defeats the alias and hold-out checks. The same
applies to asteroid report times.

**Confirm.** Inspect a stored `PhotometryResult.timestamps` entry against the frame's `DATE-OBS`
and `EXPTIME`. They are equal.

**Success.** Each sample stores `time_bjd_tdb` computed with astropy from mid-exposure, the
observer location, and the target coordinates. A test checks one value against a published
reference (for example the astropy documentation example) to 1 s.

**Prevent.** The `capture_timestamps` gate records the time scale name. A model field named
`time_bjd_tdb` cannot be filled with UTC by accident if a test compares it with the UTC value.

### S10. Ensemble normalization and detrending

**Issue.** In `variability_analyzer.py` the comparison signal is the median of raw fluxes of up to
100 stars of very different brightness, with membership changing per frame. The median of a
widely spread set has the noise of one star, so there is no root-N averaging, and membership
changes produce steps. Comparison stars are not vetted for constancy; the known-variable labels
in `post_processing/known_variability_labels.py` are not used to exclude them. Airmass detrending
then fits a quadratic of each star's own flux against airmass and divides it out. Airmass is
monotonic over half a night, so the quadratic can absorb a transit or half a pulsation cycle.

**Confirm.** Injection test: constant field, one star with a 1 percent 2-hour box dip during a
monotonic airmass run. After detrending, the dip depth is less than half its injected value.

**Success.** The injection recovers the dip to 10 percent of its depth. Normalization is a
weighted sum of fluxes from a fixed, vetted comparison set (bright, unsaturated, not labeled
variable, scatter below a threshold). Detrending fits the ensemble, not the target.

**Prevent.** Injection test in CI. The `comparison_ensemble` gate reports the comparison set size,
its scatter, and that it is fixed across the session.

### S11. Stack rejection is too aggressive at small N

**Issue.** `chauvenet_sigma` in `utilities/rejection_thresholds.py` is correct, but
`siril_interface.py` passes the same k as both the low and high bound of Winsorized clipping. At
N = 5 that is 1.64σ, so about 10 percent of good samples are rejected by construction with σ
estimated from five values. Standard practice loosens rejection at small N and sets a tighter
high bound than low because satellites and cosmic rays are positive.

**Confirm.** Read the `-rejmap` output of a 5-frame stack of a clean field. The rejected fraction
is near 10 percent.

**Success.** A floor on k (2.5 is common) or asymmetric bounds for N below about 15, with the
rejected fraction of a clean 5-frame test stack below 2 percent.

**Prevent.** Golden-data test on five M 13 sample frames pinning the rejected fraction.

### S12. Dark matching ignores temperature and binning

**Issue.** In `drivers/calibration_library.py`, darks at every temperature pool into one master.
The 3 °C check reads only the first dark's `CCD-TEMP`. Gain and offset mismatches fall back to any
dark and are soft flags. Binning and readout mode are not part of the calibration key.

**Confirm.** Add a dark at a different `CCD-TEMP` to a library and build the master. It is
included without a warning.

**Success.** The calibration key includes temperature (binned to the tolerance), binning, and
readout mode. A mismatch excludes the frame or blocks the master, and the quality summary names
the reason.

**Prevent.** Unit tests on the key builder for each dimension.

### S13. Exposure-group gain breaks linearity

**Issue.** `processing/exposure_groups.py` rescales each group by the median ratio over the
reference's brightest 1 percent of pixels. Measured values of 0.65 to 0.88 between groups of one
camera indicate near-full-well nonlinearity or zero clipping, not a gain. Applied to every pixel,
this changes faint-star fluxes by up to 35 percent in the combined stack.

**Confirm.** Compute the ratio on mid-range pixels (20th to 80th percentile) for the same groups
and compare with the stored gain.

**Success.** The gain is measured on mid-range pixels and the pipeline refuses to combine groups
whose bright-end and mid-range ratios differ by more than a few percent, recording that in the
stack quality summary.

**Prevent.** Golden-data test with two synthetic exposure groups of known ratio.

### S14. Astrometry residuals, epochs, and hints

**Issue.** The "residual RMS" that the plate-solve gate judges is the RMS of nearest-neighbor
distances to SIMBAD and Gaia within a 10 arcsec cutoff (`star_identifier.py`), not the fit
residual, and solve-field's own statistics are discarded. Gaia DR3 positions (epoch 2016.0) are
used without proper-motion propagation. The scale hint ignores `XBINNING`, so binned frames fall
to blind solving. The Gaia cone search is truncated at 10,000 rows with no magnitude cut.

**Confirm.** Compare the gate's residual with the `*.corr` or `*.rdls` output of solve-field for
the same frame.

**Success.** The gate uses solve-field's match residuals. Gaia positions are propagated to the
observation epoch with `SkyCoord.apply_space_motion`. The hint uses binning. The cone search has
a magnitude limit.

**Prevent.** Golden-data test pinning the residual on the M 13 frame. A test with a high
proper-motion star.

### S15. Spectroscopy extraction and response

**Issue.** In `pipelines/spectroscopy/pre_processing/spectrum_extractor.py`, the extraction
half-width is `round(2.5σ)` with σ re-fitted per column, which flips the aperture by a pixel
column to column and injects steps of about 3 percent. Sky is the lower of two band medians, which
is biased low. In `instrument_response.py`, the response fit masks only ±60 Å around Balmer lines
while the measured line spread is 100 to 150 Å, so Vega's line wings are divided into every
target. No airmass extinction correction exists, though the differential effect across 4200 to
8000 Å over a 0.3 airmass change is about 0.1 mag, the same as the classifier's subtype
separation.

**Confirm.** Synthetic-spectrum test: a flat continuum with Poisson noise; extract and look at
the column-to-column step pattern against the per-column aperture width.

**Success.** A fixed or smoothed aperture; sky as the mean of both bands or a sigma-clipped
estimate; the response mask set from the measured line spread; an extinction curve applied with
airmass. The synthetic test recovers a flat continuum to 1 percent.

**Prevent.** Synthetic-spectrum test in CI (section 5.3).

### S16. Classification confidence and duplicated thresholds

**Issue.** `spectral_classifier.py` reports `confidence = 1 - rms` and a softmax with a hand-set
temperature. Neither is a probability, but the UI and gates read them as one. The thresholds for
"poor match", "differs from catalog", and "ambiguous" are defined in `spectral_classifier.py`,
`models/stellar_source.py`, and `compare_to_catalog.py` with different values and different units
(a probability gap versus an RMS gap).

**Confirm.** Grep `POOR_MATCH_RMS_THRESHOLD`, `NO_GOOD_MATCH_RMS`, and the ambiguity margins.

**Success.** One definition per threshold in `models/stellar_source.py`. Confidence is reported as
the RMS gap between the best and second-best template, in RMS units, with the word "probability"
removed from fields and UI labels.

**Prevent.** A test that imports each threshold from one place and fails on a second definition.

### S17. Asteroid chaining across sessions

**Issue.** `asteroid_detection/runner.py` searches all of a target's frames across sessions, and
the chain match radius in `detection.py` grows to 1° for gaps of hours to months. The single
SkyBoT query in `ephemeris.py` is made at the mean epoch and mean position of all frames, which is
only valid for a sequence a few minutes long. Linearity is judged by R² alone, which is near 1 for
any three-point chain with a large displacement. No MPC 80-column or ADES output exists.

**Confirm.** Injection test: two static stars 0.5° apart on frames a month apart. A "mover" is
reported.

**Success.** Chaining runs per session, SkyBoT is queried per epoch, and a residual-in-arcsec
criterion against the per-frame astrometric error replaces R². The injection test reports nothing.
An ADES export exists if reporting is a goal.

**Prevent.** The null-field injection test in CI, extended with the two-session case.

### S18. Variability index has no discriminating power

**Issue.** The sole index is the coefficient of variation with a field-wide MAD cutoff. The
pipeline's own `variability_discrimination` gate reports an AUC of 0.46 to 0.49 against catalog
variables, which is chance. The honesty is good; the index is below standard.

**Confirm.** Read the gate output on the M 81 dataset.

**Success.** An RMS-versus-magnitude noise model plus at least one correlated-noise index
(Stetson J or χ² against the S8 errors) lifts the AUC above 0.7 on the labeled set.

**Prevent.** The gate already measures it; make the gate fail below a floor once S8 lands.

### S19. The headline recompute is not independent

**Issue.** `scripts/recompute_headline_numbers.py` re-reads the stored detrended arrays and runs
the same `np.std/np.mean` and the same astropy Lomb-Scargle as the pipeline. It verifies storage
integrity, not measurement correctness. The FWHM check is independent; the rest is not.

**Confirm.** Compare the CV code path in the script with
`_compute_star_coefficients_of_variation`.

**Success.** The script re-measures pixels for a sample of stars with a different aperture
method, re-derives normalization from a different comparison set, and uses a different period
estimator (phase dispersion minimization). Disagreement beyond a stated tolerance fails.

**Prevent.** Section 5.4: independent recomputation is a stated requirement for any "verified"
claim.

### S20. Unseeded randomness

**Issue.** `pipelines/astrometry/pre_processing/source_detection.py` uses an unseeded
`np.random.default_rng()` for the background subsample, and `pipelines/shared/image_scaling.py`
uses `np.random.randint` for preview scaling. Detection thresholds can differ run to run.

**Confirm.** Run detection twice on a large frame and diff the star lists.

**Success.** Identical lists across runs. Every RNG takes a seed from the configuration.

**Prevent.** Ruff ban on `np.random.` and `default_rng()` without a seed argument in `pipelines/`.

### S21. Architecture documents describe algorithms the code does not run

**Issue.** `documentation/library_design/Astrometrics_Library_Architecture.md` says stars are
re-centroided per frame (S2), that a 2-D Gaussian is fitted for astrometric centroids (the solver
receives DAOStarFinder centroids), that sky is subtracted only during spectral centroiding (the
code subtracts it from every reading), and that comparison stars are a rank slice (the code uses
the 100 brightest). A reader of the document believes rigor the code does not have.

**Confirm.** Items S2, S10, S15, and the agent findings above.

**Success.** Each algorithm claim in the document names the function that implements it, and a
test (section 5.5) checks the function exists.

**Prevent.** Claim-to-code links, checked in CI.

### S22. Variability flags have no false-alarm probability

**Issue.** `calibrate_thresholds` in `pipelines/photometry/processing/variability_indices.py` puts
cut-offs at the field median plus 2.326 times the scaled MAD (a normal-theory 99th percentile of
the field's own spread). No flag has a p-value. The period searches already have one (block
permutations, with a family-wise correction), so the two halves of the pipeline disagree about
what "significant" means. A referee will ask for a false-alarm probability, and a count of flags
without a false-discovery rate does not say how many are real.

**Confirm.** Grep `p_value` and `false_alarm` in `variability_indices.py`. None.

**Success.** Each star gets a chi-square p-value from its S8 errors (the tail of the chi-square
distribution at its degrees of freedom), and the run reports the Benjamini-Hochberg false
discovery rate at the chosen level. A candidate needs the existing score above 1 and a
false-discovery-corrected p below 0.01. A test on constant synthetic fields confirms that no more
than 1 percent of stars pass.

**Prevent.** The synthetic-field test in CI. The `variability_discrimination` gate reports the
number of stars that pass at the corrected level.

### S23. Spectroscopy noise ignores which pixels survived rejection

**Issue.** `build_pixel_noise_model` in `pipelines/spectroscopy/pre_processing/intensity_variance.py`
divides by one global frame count (`STACKCNT`). A pixel where rejection removed several frames
has fewer frames behind it and more noise than the model says. Siril writes the rejection map
(`_RejMap.fits`), but no code in spectroscopy or photometry reads it. (Photometry measures single
frames, so it is not affected.)

**Confirm.** Compare `intensity_errors` of a spectrum on a stack with many rejected pixels along
the trail with the scatter of the same pixels in the stack's input frames.

**Success.** The noise model reads the rejection map and uses, per pixel, the number of frames
that were kept. A synthetic stack with a known rejected count shows reported error over scatter
within 10 percent along the trail.

**Prevent.** The synthetic test in CI, and the `intensity_variance` checkpoint metric records the
fraction of trail pixels with fewer frames than the stack count.

### S24. Position errors have no covariance, and there is no ADES report

**Issue.** `pipelines/asteroid_detection` reduces each frame's position error to one number and
fits RA and Dec separately. Frame WCS is the stack WCS moved to the mount pointing, with no solve
per frame. `mpc_report.py` writes the 80-column format only, with no uncertainty. The Minor
Planet Center expects a position uncertainty for each observation.

**Success.** Each observation carries sigma in RA and Dec and their correlation, taken from the
residuals of the matched stars after the plate fit. An ADES (the MPC's XML or PSV format) writer
is added beside the 80-column one, with the uncertainties. A test round-trips an observation.

### S25. Apertures do not follow the seeing

**Issue.** `measure_aperture_photometry` uses a 4.0 pixel radius with a 7 to 12 pixel sky ring on
every frame. In differential photometry a fixed aperture loses about the same fraction of every
star's light in one frame, so the ratio to the comparison stars cancels most of it. What is left
is the part that depends on position in the field (a PSF that changes across the frame), blended
stars, and sky error on faint stars. It grows when seeing worsens over a night, and no aperture
correction is measured.

**Confirm.** Injection test with Gaussian stars of 3.0 and then 5.0 pixel FWHM across one series.
The ratio of a faint star to the ensemble changes by more than 0.5 percent.

**Success.** The radius follows each frame's measured star width (radius equal to a stated
multiple of the FWHM, with limits), and an aperture correction from the curve of growth of bright
isolated stars is applied and recorded. The injection test recovers flux to 0.5 percent across the
FWHM range.

**Prevent.** The injection test in CI. The `registration_drift` gate also records the per-frame
FWHM and the aperture used.

### S26. Comparison stars are not matched to the target's colour

**Issue.** `select_comparison_set` picks stars by brightness only. Blue stars lose more light than
red ones as airmass rises, so a target whose colour differs from the ensemble's will show a
spurious airmass slope. The optional `ensemble_airmass_correction` removes the median slope of the
comparison stars and uses no colour (its own docstring says it cannot tell a colour term from
variability). It is off in production.

**Success.** Gaia BP-RP is stored for each star (it is already in the Gaia download and absent
from the cache). The comparison set is chosen within a stated colour range of the target, or a
slope against airmass times colour is fitted on the comparison stars and removed. An injection
test with a colour-dependent extinction term recovers a known dip to 10 percent.

**Prevent.** The injection test in CI.

### S27. No completeness or false-discovery curves

**Issue.** The injection tests are pass or fail at one point. `validate_asteroid_detection.py`
has a grid of signal-to-noise by rate, but the star density is fixed, there is no purity figure,
and it was not re-run after the direction check was added. Photometry has no grid at all.

**Success.** A script writes, for photometry, the recovery fraction of injected dips and
sinusoids against depth and magnitude with the false-alarm rate on constant stars, and for
asteroids the recovery against rate and star density with purity. Each is written to a file that
the README quotes with its date.

**Prevent.** The script runs in the empirical validation workflow, and the README states which
numbers are measured and which are designed.

## 4. Architecture items

Ordered by risk, then by how much other work depends on them.

### A1. A served MCP tool connects to hardware

**Issue.** `ObservatoryGuiding.status(include=["live"])` (`wayfindinglib/api/control/guiding.py`)
calls `live_guiding.poll`, which calls `hardware_operations.mount_status`, which reaches
`IndiInterface.connect_to_telescope` (`wayfindinglib/drivers/indi_interface.py`). That method
sends `CONNECTION=ON` to every INDI device that is off. The same hazard already blocks
`observatory_mount_status` through `INTERIM_BLOCKS` in `tool_dispositions.py`, but
`observatory_guiding_status` is served to every profile. The poll also writes guide samples to the
logs database from the MCP process, beside the backend's own guiding loop.

**Confirm.** Inject a fake driver whose `connect_to_telescope` raises, call
`wayfinder.control.guiding.status(include=["live"])`, and observe the raise.

**Success.** No served tool of class `observe` or `compute` reaches `connect_to_telescope`. A
call-graph test (section 5.2) proves it for every served tool, not just this one.

**Prevent.** Call-graph contract test. The `INTERIM_BLOCKS` table is a manual list; the test
replaces it with a rule.

### A2. Cross-process job cancellation is silent

**Issue.** `processing:cancel` (`backend/services/processing/image_processing_service.py`) cancels
futures held by the backend process (`base_service.py`). A job that an MCP server started runs in
another process. The cancel marks the database row `cancelled` while the thread keeps running.
Both processes share one `JobStore` (`astrometricslib/foundation/jobs/store.py`), so the row looks
honest.

**Confirm.** Start `processing_stack` from `astrometricslib-core`, then call `processing:cancel`
with its job id from the backend. Watch Siril keep running after the row says cancelled.

**Success.** A cancel of a job owned by another process either reaches that process (a
cancellation flag in the store that the running job polls) or the RPC reply says the job belongs
to another process and was not stopped. `owner_pid` is already recorded; the reply must use it.

**Prevent.** A test in `foundation/jobs/test/` that starts a job in a subprocess and cancels it
from the parent.

### A3. The MCP `notify` action calls a method that does not exist

**Issue.** `app_controls` with `action="notify"` sends RPC method `events:broadcast`
(`mcp_servers/backend/definition.py`). `backend/public_interface.py` has no such method, so the
call always fails with method-not-found. The tests cover only bad arguments.

**Confirm.** Grep `events:broadcast` in `backend/`. No handler exists.

**Success.** `notify` sends a declared method and a test calls it against a live backend fixture.

**Prevent.** Contract test: every RPC method name string in `mcp_servers/` must appear in
`RPC_METHODS`. The same test catches the stale tool names in `suggest_remediation`
(`target_list`, `observatory_equipment_connect`, `terminal_inspect_api`).

### A4. The UI MCP server cannot start

**Issue.** `ui/mcp/dist/` is not built, so `astrometrics-ui` fails to connect. The plan's status
section records this.

**Confirm.** `ls ui/mcp/dist` fails.

**Success.** The developer-profile client config starts all five servers, and
`backend/tests/test_client_configs.py` (or a sibling) launches each server once and lists its
tools.

**Prevent.** Build `ui/mcp` in `build/linux/setup_venv.sh` and in the frontend CI workflow, and
add a server-launch smoke test.

### A5. astrometricslib's root still exports internals

**Issue.** `astrometricslib/__init__.py` exports 143 names. About 20 are internals that plan
section 6.1 said to remove: `FrameSelection`, `select_library_frames`, `close_interrupted_jobs`,
`DbLogHandler`, `ImageProcessing`, `background_job`, `check_choice`, `check_include`,
`reject_unused_arguments`, `resolve_target`, `to_epoch_seconds`, `parse_iso_time`,
`classify_and_sort_fits_files`, `derive_target_sessions`, `resolve_camera_profile`,
`preview_path_for`, and the two `SATURATED_*` constants. wayfindinglib imports all of them through
the root, so the import-linter contract "wayfindinglib uses only the public API" passes by letter
and fails by intent.

**Confirm.** Compare `astrometricslib.__all__` with the plan's section 6.1 "Exports" row.

**Success.** `astrometricslib.__all__` contains only the root object, sub-APIs, models,
exceptions, and driver base classes. `astrometricslib/test/test_public_surface.py` pins the list.
Each internal wayfindinglib needs becomes either a public method with a docstring or code that
moves into astrometricslib.

**Prevent.** The public-surface test already exists. Change it from "the list equals this" to
"every name is a model, an exception, an API class, or a driver base class", so adding a helper
fails the test regardless of whether someone also edits the allow-list.

### A6. Driver injection differs between the libraries

**Issue.** wayfindinglib injects drivers through property setters on `control` and a registry
(`wayfindinglib/drivers/interfaces/registry.py`). astrometricslib has no injection path: concrete
drivers are built inside pipelines (`pipelines/astrometry/processing/star_identifier.py`,
`pipelines/stacking/stage.py`, `pipelines/stacking/stack_runner.py`, `pipelines/astrometry/pipeline.py`).
Neither library exports its driver base classes, so a Python user cannot supply a mock plate
solver or mount without importing internals.

**Confirm.** Try to run `astrometrics.processing.process_target(stages=["astrometry"])` with a
fake `PlateSolveDriver` without touching `sys.modules`. There is no way to pass it.

**Success.** `Astrometrics(config, storage, *, plate_solve_driver=None, stacking_driver=None,
simbad_driver=None)` accepts drivers and threads them to the pipelines. Both roots export their
base classes. A test runs the astrometry stage with a fake solver and no network.

**Prevent.** Public-surface test includes the base classes. A ruff `TID251` ban on constructing
`AstrometryNetPlateSolveDriver`, `SirilStackingDriver`, and `AstroquerySimbadDriver` outside
`api/` and `drivers/`.

### A7. Ambient configuration reads

**Issue.** `get_configuration()` is called from 41 places in astrometricslib (23 in pipelines) and
14 in wayfindinglib. wayfindinglib's sub-API constructors default `config` to `None`;
astrometricslib's require it. A caller who passes a scratch configuration still gets the live one
in those 55 places, which is how a test or an agent can touch real data by accident.

**Confirm.** `grep -rn "get_configuration()" astrometricslib wayfindinglib --include='*.py' | grep -v test | grep -v scripts | wc -l`.

**Success.** The count outside `foundation/config.py` and program entry points is zero. Every
sub-API constructor requires `config`.

**Prevent.** Ruff `TID251` ban on `astrometricslib.get_configuration` with per-file exemptions
for the entry points only.

### A8. Public methods return untyped dictionaries

**Issue.** 6 astrometricslib and 15 wayfindinglib public methods return `dict`, and 4 return
`Any`. The UI's generated types cannot cover them, which is why `ActionRegistry` payloads are
hand-written.

**Confirm.** Parse the public API classes and list methods whose return annotation is `dict` or
`Any`.

**Success.** Zero. Each `kind` of `history.query` and `Jobs.query` has a Pydantic model, and
`build/codegen/generate_types.py` emits it.

**Prevent.** A test in each library's `test/test_public_surface.py` that fails on a `dict` or
`Any` return annotation on a public method.

### A9. Argument naming drift

**Issue.** Section 6.1 conventions are mostly applied, with these outliers: `target_id: str` on
`TargetCatalog.get`, `StellarCatalog.query`, and `Jobs.query`; `StellarCatalog.create(ra: str,
dec: str)`; `exposure: str` on `get_frame` and `render_fits`; `guiding.refit_spectrum(target_name)`;
`calculate_panels(center_ra, center_dec)`; `observation_session_id` beside `session_id`;
`duration_ms` beside `ra_pulse_duration_sec`; `record_divergence(detail: str)` using `detail` as
free text.

**Confirm.** The list above, by reading each signature.

**Success.** Every subject argument is `target: str | Target`, every angle is `*_deg`, every
duration is `*_seconds`, every session is `session_id`, and `detail=` only ever selects a level of
detail.

**Prevent.** A naming test that scans public signatures for the banned names `ra`, `dec`,
`target_id`, `target_name`, `*_ms`, `*_sec`, and `time_input`.

### A10. Duplicated models

**Issue.** `CalibrationEntry` and `CalibrationStats` exist in
`astrometricslib/models/calibration_inventory.py` and
`wayfindinglib/models/equipment_and_site/calibration.py` with different fields. Inside
astrometricslib, `CameraConfig` is defined in `foundation/config_schema.py` and
`utilities/spectroscopy_models.py`, and `InputQualityAssessment` and `OutputQualityAssessment`
are defined in both the photometry and spectroscopy quality modules.

**Confirm.** `grep -rn "^class CalibrationEntry\|^class CameraConfig\|^class InputQualityAssessment" astrometricslib wayfindinglib`.

**Success.** One definition per name, and `generate_types.py` emits each once.

**Prevent.** A test that collects every `BaseModel` subclass name across both libraries and
fails on a duplicate.

### A11. Domain rules left in the backend

**Issue.** Four places in `backend/services/` still hold library logic:

- `analysis/analysis_orchestrator.py` classifies frames as spectral by filter name and filename
  substrings, in three ways. The library has `astrometricslib.frame_is_spectral`. The plan's
  status says this copy was deleted; it was not.
- `observatory/telescope_service.get_observer_location` falls back to a hard-coded Denver site.
- `processing/ingestion_service.py` owns the per-folder calibration sync loop and infers
  progress by counting rsync output lines.
- `observatory/telescope_service.slew_coordinates` converts RA hours to degrees, and
  `_infer_target_at_coordinates` fixes a 1° tolerance.

Legacy code the plan marks done remains: the in-memory job fallback in `base_service._submit_job`,
the no-op `stellar_service.save_objects`, the unused `scripting_service.run_repl_command`,
duplicate `target:get` and `target:get_targets` registrations, and unused service methods
(`park_telescope`, `unpark_telescope`, `pause_imaging`, `abort_imaging`, `get_all_active_syncs`).

**Confirm.** Read the named functions. For dead code, grep each method name across `backend/`,
`ui/`, `electron/`, and `mcp_servers/` and find no caller.

**Success.** The four logic sites call the library. The dead code is gone. No import-linter
exception remains for the backend.

**Prevent.** Add an import-linter contract "backend imports only the public API of the
libraries" (today only ruff's `TID251` enforces it, with four per-file ignores). Add a dead-code
check (`vulture` or an RPC-handler reachability test) to the lint workflow.

### A12. Domain logic in the UI and Electron

**Issue.** Beyond the allowed planetarium projection (plan section 5.3), the UI holds: Julian Date
conversion in `PlanetariumDateTimeModal.tsx`; a copy of `SPECTRAL_CLASS_ALIASES` in
`astronomyManager/utils/starDisplayFormat.ts`; phase folding in
`astronomyManager/components/PhotometryViewer.tsx`; sexagesimal parsing in
`planetariumDisplay/utils/coordinateUtils.ts` and `common/utils/coordinateValidation.ts`;
limiting-magnitude math in `planetariumDisplay/layers/StarOverlay.ts`. Electron calls `/api/rpc`
from `emergency_park.js` and `python_terminal_manager.js` outside `backendApi.ts`, and sends
`SIGSTOP` to `siril-cli` and `solve-field` by `pkill` in `platforms/base.js`.

**Confirm.** Open each file. Each is a few dozen lines.

**Success.** Under D3, each copy has a reference test and a recorded reason or is replaced by a
backend field. The spectral alias table comes from the generated types. Pipeline pause and resume
become a declared RPC method that the backend implements, so Electron stops signalling processes
it does not own.

**Prevent.** Extend the ESLint `fetch`/`WebSocket` rule to `electron/`. Add a reference-test
requirement to CLAUDE.md under D3.

### A13. `ActionRegistry` payloads are hand-written

**Issue.** `ui/common/services/backendApi.ts` types about 21 responses as inline literals and 14
as `any`. `PlanetariumSource` duplicates the generated `SkySource`. Pydantic models that exist but
are not generated include `wayfindinglib.DeepCatalogStatus`, `EquipmentConfiguration`, and
`astrometricslib.CameraProfile`.

**Confirm.** Count `any` and `Record<string, any>` in `backendApi.ts`.

**Success.** Every `ActionRegistry` entry's response type is imported from
`ui/common/types/backendTypes.ts`. The count of `any` is zero. This depends on A8.

**Prevent.** `backend/tests/test_generated_types_current.py` already checks freshness. Add an
ESLint rule that forbids `any` in `backendApi.ts`.

### A14. The plan's status section is out of date

**Issue.** `ARCHITECTURE_PLAN.md` says sections 5.1, 6.3, and 8.1 are done. Items A5, A11, and the
`ASTROMETRICS_TESTING` read in `wayfindinglib/api/control/context.py` and `backend/container.py`
show they are not. A hand-written status section is a claim without a check.

**Confirm.** Items A5 and A11.

**Success.** Each "done" line in the status section names the test or lint rule that proves it.

**Prevent.** Generate the status from checks (section 5.5) or delete it and rely on CI.

### A15. Agent sessions cannot run the project's checks

**Issue.** CLAUDE.md requires `.venv/bin/python`, `ruff`, `pytest`, and `import-linter`. A cloud
session starts with no `.venv` and Python 3.13, while the code needs 3.14. The MCP servers fail to
connect for the same reason. An agent that cannot run tests reads code instead, which is how the
mismatches in A14 go unnoticed.

**Confirm.** `ls .venv` in a fresh cloud session.

**Success.** A fresh session runs `.venv/bin/pytest astrometricslib/test/test_public_surface.py`
and `lint-imports` without manual setup.

**Prevent.** A `SessionStart` hook that runs `build/linux/setup_venv.sh` (or a cached subset) and
installs Python 3.14. The `session-start-hook` skill documents how.

## 5. Preventing these issues in AI-assisted development

The findings share a pattern. An agent made a change that satisfied the local test or lint rule
and the plan's prose, while the scientific or architectural intent went unchecked. The agents
also left claims in documents that no check backs. The mechanisms below convert intent into
checks that run on every change. They work for people too, but they matter more for agents,
because an agent reads the repository's rules literally and optimizes for the checks it can see.

### 5.1 Executable contracts instead of prose rules

Every rule in CLAUDE.md and `ARCHITECTURE_PLAN.md` that an agent could violate should have a
check that fails. The repository already does this well for import direction. Gaps to close:

- import-linter contract for the backend's use of the libraries (A11);
- public-surface tests that classify names rather than list them (A5), and that reject `dict` and
  `Any` returns (A8) and banned argument names (A9);
- a duplicate-model test (A10);
- a test that every RPC method name string in `mcp_servers/` is declared (A3);
- ruff bans on `get_configuration()` (A7), `datetime.now()` in pipelines (S5), unseeded RNGs
  (S20), and direct driver construction (A6).

Rule of thumb for CLAUDE.md: a sentence that says "never" or "always" should name the check that
enforces it. A rule without a check is a request, and agents treat requests as negotiable when a
task is hard.

### 5.2 A call-graph contract for hardware reach

Item A1 shows that a tool-by-tool block list cannot keep up with refactors. Replace
`INTERIM_BLOCKS` with a test that builds each served tool, injects a driver whose connect methods
raise, calls the tool with representative arguments, and asserts no raise for any tool of class
`observe` or `compute`. The `SimulatorIndiInterface` can host this as a "connection-counting"
mode. The same test proves D2 once decided.

### 5.3 Truth-known tests as the definition of done for science code

A pipeline change is done when a test with known truth recovers that truth within a stated
tolerance. The repository already has injection tests for periodicity and asteroid detection. Add
the missing ones and make them a CLAUDE.md requirement:

- **Photometry.** A synthetic frame generator (Gaussian stars of known flux, flat sky, Poisson and
  read noise, optional drift and airmass trend). Tests: flux recovery at sub-pixel positions (S2),
  σ recovery (S8), dip recovery through detrending (S10), between-session amplitude (S3).
- **Spectroscopy.** A synthetic 2-D spectral frame (zero order, tilted trace with known
  dispersion, absorption lines at known wavelengths, sky bands). Tests: line positions after
  extraction and calibration (S7), continuum flatness (S15).
- **Stacking.** Five sample frames with injected cosmic rays and one satellite trail. Tests:
  rejected fraction (S11), linearity of the combined stack against a known flux ratio (S13).
- **Astrometry.** The M 13 sample frame with a pinned corner position after SIP write-back (S1)
  and a pinned residual (S14).

Keep the generators in `astrometricslib/test/synthetic/` and document each in a README so an
agent can extend rather than reinvent them.

### 5.4 Golden-data regression on real frames

The sample data under `documentation/notebooks/astrometrics/sample_data/M 13/` is enough for a
small golden-data suite: run each pipeline stage on it in CI and compare a short list of numbers
(star count, median FWHM, ten star fluxes, `CRVAL1/2`, rejected fraction, the three Balmer line
positions) with pinned values. Store the pinned values in a JSON file beside the test with a
comment naming the change that last moved each number and why. An agent that changes a number
must edit that file, and the diff shows the reviewer exactly which measurement moved. This is the
single most effective guard against silent regressions from refactors, because it checks the
result rather than the code path.

Separately, keep "independent recomputation" (S19) honest by requiring that the recompute script
use different code for each number it checks, and say so in its description block.

### 5.5 Claims that point to code

Two kinds of document drifted: the plan's status section (A14) and the architecture documents
(S21). The fix is the same. A claim about behavior names the function or test that implements it,
in a form a script can check. For the plan, replace each "done" line with the name of its check,
or generate the status section from the check results. For the architecture documents, add a
`<!-- impl: module.function -->` comment after each algorithm claim and a test that imports each
named function. A claim with no implementation link is a design goal and should say so.

The `code-documentation-style` skill already requires a README to end with "read the code" for
exact thresholds. Extend it: a README that describes an algorithm names the test that checks it.

### 5.6 An agent session that can run the checks

Item A15 is the root cause of several others. An agent that cannot run `pytest`, `ruff`, or
`lint-imports` falls back to reading, and reading is how the status section drifted. Add a
`SessionStart` hook that prepares `.venv` with Python 3.14 so every agent session starts able to
run the suite. Then add a `Stop` or pre-commit hook that runs `ruff check`, `lint-imports`, and
the affected test folder before an agent reports completion. The existing
`.claude/hooks/guard_destructive.py` shows the pattern.

### 5.7 Scope and dead-code hygiene

Plan rule 7 says a change that renames or removes something updates every caller. Items A11 and
A12 show agents leaving the old path in place. A dead-code check (`vulture` for Python, an
unused-export rule for TypeScript) in the lint workflow catches this mechanically. Add to
CLAUDE.md: a task that adds a replacement is not done until the thing it replaces is deleted and
the dead-code check passes.

### 5.8 A review checklist for science changes

Add `astrometricslib/REVIEW_CHECKLIST.md` and have CLAUDE.md require that an agent answer each
line in its summary when it touches `pipelines/`:

1. What physical quantity does each new number represent, and in what unit? (ADU or electrons,
   degrees or hours, UTC or BJD_TDB, air or vacuum wavelength.)
2. Where does the uncertainty of each measurement come from, and is it carried to the next stage?
3. What is the reference for the method (a paper, a textbook section, or a named standard
   routine), and does the docstring cite it?
4. Which truth-known test exercises the change, and what tolerance did it meet?
5. Which golden-data numbers moved, and why is each move correct?
6. Which README and which architecture document describe this behavior, and were they updated?
7. What does the change refuse to do (minimum frame counts, missing headers, mismatched
   calibration), and how does the quality gate report that refusal?

An agent that cannot answer a line should report the gap rather than fill it with a plausible
sentence. That instruction belongs in CLAUDE.md verbatim, because an agent's default is to fill
it.

### 5.9 Separate "measured" from "designed"

Several documents state intended behavior as fact. Require two words in every README and gate
description: **measured** for a number that a test or validation script produced, with the script
named, and **designed** for a target that nothing has yet verified. The `run_gates.py` modules
already distinguish passed, failed, and unchecked gates. Carry the same three states into prose.
