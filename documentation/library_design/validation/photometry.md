# Photometry Validation and Tuning

*Version 1.0 · 2026-10-15 · Status: current*

This file holds the evidence behind the photometry pipeline's tuned values: what was measured, on what data, with which library calls, and what came out. It is written for a reviewer who wants to repeat a measurement on their own camera and sky and tune the pipeline to match. The method itself (what each step does and why) is in the photometry READMEs under `astrometricslib/pipelines/photometry/`. Terms such as light curve, comparison ensemble, CV, MAD and AUC are defined in the [glossary](../Astrometrics_Library_Architecture.md#appendix-a-glossary). The status labels (Measured, Partly measured, Design estimate) are defined in [index.md](index.md).

Every procedure below reads a target that the library already holds. A run of the photometry stage goes through the public entry point `Astrometrics().processing.process_target` <!-- impl: astrometricslib.api.processing.ProcessingPipelines.process_target -->, and its run-level checks (gates) are read back with `PhotometryQualitySummary.gate` <!-- impl: astrometricslib.models.quality_summary.PipelineQualitySummaryBase.gate -->. Steps that need a lower-level function say so.

## Measured and partly measured values

### PHO-01

**The comparison ensemble keeps injected signals and removes shared changes (synthetic sessions)**

- **Claim:** Dividing by a fixed, vetted, inverse-variance weighted comparison ensemble keeps a 1 percent dip at its full depth through a steady rise in airmass, turns away a varying comparison candidate, and removes a frame-to-frame transparency change. Its scatter agrees with the propagated error.
- **Settings it supports:** The method as a whole, run at its defaults: code constant `BRIGHTEST_SKIPPED_FRACTION` = 0.02 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.BRIGHTEST_SKIPPED_FRACTION -->, `FAINTEST_BAND_FRACTION` = 0.30 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.FAINTEST_BAND_FRACTION -->, `EXCESS_SCATTER_SIGMA` = 3.0 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.EXCESS_SCATTER_SIGMA -->, `MINIMUM_COMPARISON_STARS` = 5 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MINIMUM_COMPARISON_STARS --> and `DEFAULT_MAXIMUM_COMPARISON_STARS` = 20 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.DEFAULT_MAXIMUM_COMPARISON_STARS -->, all in `astrometricslib/pipelines/photometry/processing/comparison_ensemble.py` (changing them means editing the code; the cap is also the `maximum_comparison_stars` argument of `VariabilityAnalyzer` <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer -->). It also supports the choice to leave the ensemble airmass correction off (`ensemble_airmass_correction=False` on `VariabilityAnalyzer`) and never to fit a star's own flux against airmass. The test does not tune the individual values.
- **Status:** Partly measured (synthetic sessions only; no real field has checked the current selection).
- **Data you need:** None from the sky. To repeat it on real data, you need one session of 30 or more frames of a field with at least 20 unsaturated stars, taken while the airmass changes by 0.5 or more, ideally with a star of known transit or eclipse depth.
- **Procedure:**
  1. Build a session of light curves with known fluxes, a common airmass extinction, and a box dip in one star. The test's generator does this (internal test helper, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.photometry.test.test_comparison_ensemble_injection.simulate_session -->
  2. Normalize and detrend with the analyzer (internal class, not part of the public API; may change), then measure the dip depth as one minus the mean flux inside the dip over the mean flux outside it.

```python
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer
from astrometricslib.pipelines.photometry.test.test_comparison_ensemble_injection import (
    DIP_DEPTH,
    DIP_STAR,
    recovered_dip_depth,
    simulate_session,
)

session = simulate_session(dips={DIP_STAR: DIP_DEPTH}, seed=0)
analyzer = VariabilityAnalyzer()
analyzer.stellar_objects = session.stars
analyzer.timestamp_to_path = {stamp: f"frame_{i:03d}.fits" for i, stamp in enumerate(session.timestamps)}
analyzer.normalize_light_curves()
analyzer.detrend_light_curves_airmass()
print(recovered_dip_depth(session.stars[DIP_STAR]), DIP_DEPTH)
print(analyzer.comparison_set.scatter_mag, analyzer.comparison_set.expected_error_mag)
```
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.normalize_light_curves --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.detrend_light_curves_airmass --> <!-- impl: astrometricslib.pipelines.photometry.test.test_comparison_ensemble_injection.recovered_dip_depth -->
  3. For the end-to-end version, the test renders drifting FITS frames, runs `VariabilityAnalyzer.process` on them, and measures the dip the same way. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.process -->
  4. On real data, run the photometry stage and read the comparison set's record from the `comparison_ensemble` gate and from each star's `sessionSummaries` (`comparisonScatterMag`, `comparisonRejectedCount`).
- **What to look at:** The recovered depth over the injected depth (1.0 is perfect; a value near 0.5 means the trend removal ate half the dip). Whether the varying candidate appears in `comparison_set.rejected_ids`. The ensemble scatter over the propagated error, in magnitudes (near 1 is good).
- **Our result:** Synthetic sessions, 36 frames at a 10 minute cadence (6 hours), airmass rising from 1 to 2, 25 percent extinction over the run.

  | Check | Result |
  |---|---|
  | 1 percent, 2 hour dip, from light curves (seeds 0 to 2) | recovered at 1.01 to 1.02 percent |
  | Same dip, from rendered pixel frames | recovered at 1.00 percent |
  | Earlier method on the same data: median of raw fluxes, quadratic airmass fit to each star's own flux | 0.51 to 0.53 percent from light curves, 0.30 percent from frames |
  | Comparison candidate carrying a 5 percent sinusoid | rejected from the set |
  | Comparison scatter against propagated error | equal within 20 percent |
  | 3 percent frame-to-frame transparency change | removed from every curve |

  In plain terms, the dip comes back to within 3 percent of its depth.
- **Limits:** Synthetic noise is white and Gaussian, and the recorded error equals the true noise; real fields have flat-field and centroid errors that the errors leave out (see [PHO-02](#pho-02)). The test asserts a tolerance of 10 percent; the exact recovered depths above were read from a run of the test, not asserted by it. The earlier-method figures come from code that no longer exists, so they cannot be re-run.
- **To tune for your setup:** If your fields have few stars, lower `MINIMUM_COMPARISON_STARS` only with care: the `comparison_ensemble` gate fails below it. If your brightest stars saturate often, raise `BRIGHTEST_SKIPPED_FRACTION`. Re-run the test file after any change.
- **Automated by:** `astrometricslib/pipelines/photometry/test/test_comparison_ensemble_injection.py` (`.venv/bin/pytest astrometricslib/pipelines/photometry/test/test_comparison_ensemble_injection.py`).

### PHO-02

**Comparison-star scatter against the predicted error on three real fields**

- **Claim:** On a clear night the comparison stars scatter only a little more than the CCD equation predicts, while a night with patchy cloud or tracking loss gives a ratio about ten times larger. A limit of 5 separates the two. The predicted per-point errors run about one fifth smaller than the real scatter.
- **Settings it supports:** Code constant `MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED` = 5.0 in `astrometricslib/pipelines/photometry/post_processing/run_gates.py` (changing it means editing the code). <!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED -->
- **Status:** Partly measured (three fields; the limit sits between them, but no false-alarm rate was measured).
- **Data you need:** At least three single-night photometry sessions of fields with 20 or more unsaturated stars, from one camera with a known gain, including at least one night you know was clear and, if possible, one you know had passing cloud.
- **Procedure:**
  1. Run the photometry stage on each target through the public API and read two gates.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
for target_id in ("M 13", "M 81", "NGC 2403"):
    result = astrometrics.processing.process_target(target_id, stages=["photometry"])
    summary = result.quality.photometry
    ensemble = summary.gate("comparison_ensemble")
    errors = summary.gate("flux_uncertainty")
    print(target_id, ensemble.status, ensemble.measured_value, ensemble.detail)
    print(target_id, errors.status, errors.measured_value, errors.detail)
```
<!-- impl: astrometricslib.Astrometrics --> <!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.photometry_run_gates -->
  2. The `comparison_ensemble` detail quotes the ensemble scatter in magnitudes next to the scatter the errors predict. Their ratio is the number the limit judges.
  3. For a night with a large ratio, look at each session's `ensembleMedianFlux` over the pictures and at the faint stars' normalized scatter, to tell cloud or tracking loss from a bad comparison set.
- **What to look at:** The ratio of measured to predicted ensemble scatter (no unit). Near 1 means the errors describe the noise; 1.2 to 2 is normal for real data with flat-field and centroid errors; much larger means something the ensemble cannot divide out. The `flux_uncertainty` gate's median per-point error, in magnitudes.
- **Our result:** ASI 533MM Pro data, 2026-10-11, with the measured gain of [PHO-03](#pho-03).

  | Field | Ratio of ensemble scatter to predicted | Notes |
  |---|---|---|
  | M 13 (41 frames) | 1.27 (1.3) | ensemble scatter 0.0142 mag against 0.0112 mag predicted, so the predicted errors are about 21 percent too small; `flux_uncertainty` passed with a median per-point error of 0.0205 mag |
  | M 81 | 1.66 (1.7) | |
  | NGC 2403 | 18 | 0.0655 mag against 0.0036 mag; the comparison stars' brightness fell by 30 percent over 16 minutes and recovered (922, 636 and 946 ADU/s); faint stars' normalized fluxes scattered 0.08 to 0.09 mag at every brightness, and the brightest 50 stars were anti-correlated with the ensemble |

  The NGC 2403 pattern is patchy cloud or tracking loss, which dividing by the ensemble cannot remove. The gate had passed that run before the limit existed; the limit was then set between these three fields.
- **Limits:** Three fields from one camera. The gap between 1.3 and 1.7 on clear nights is explained by noise sources the CCD equation leaves out (flat-field residuals and centroid error), but nothing here shows how it splits between them. With the gain assumed to be 1 e-/ADU, `flux_uncertainty` is `not_checked` and quotes its error as a guide only.
- **To tune for your setup:** Collect the ratio on several nights you know were clear. Set `MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED` a comfortable margin above the clear-night values and below any known cloudy night. A camera without a measured gain gives ratios of the wrong size; measure the gain first ([PHO-03](#pho-03)).
- **Automated by:** No script; read from the run gates by hand on 2026-10-11. `astrometricslib/scripts/report_gate_limits.py` reports the spread of saved photometry values across all targets (`python -m astrometricslib.scripts.report_gate_limits`).

### PHO-03

**Gain and read noise of the ASI 533MM Pro**

- **Claim:** In the stored 16-bit files the camera's gain at gain setting 0 is 0.783 electrons per ADU and its read noise is 3.8 electrons. A datasheet gain quoted for the sensor's own 14-bit ADU (3.6 e-/ADU) is wrong for these files.
- **Settings it supports:** Config key `[Observatory.Camera.ZWO ASI533MM Pro] gain_e_per_adu` (default 0.783, kind `measured`) and `[Observatory.Camera.ZWO ASI533MM Pro] read_noise_e` (default 3.8, kind `measured`) in the configuration file (`astrometricslib/astrometrics.config.example.toml` ships them). The pipeline reads them through the camera profile. <!-- impl: astrometricslib.models.camera_profile.CameraProfile --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.detector_noise.resolve_detector_noise -->
- **Status:** Partly measured (one signal level; no full photon-transfer curve).
- **Data you need:** For your camera, at one gain setting, offset and sensor temperature: 15 or more pairs of flat frames taken back to back at one exposure (a signal near half of full scale), and 5 or more pairs of bias frames (zero-length exposures).
- **Procedure:** No library function measures gain; the steps use NumPy and Astropy directly.
  1. For each flat pair, take the central region of both frames, subtract the bias level, and form their difference. Half the variance of the difference is the random noise of one frame, because the difference cancels the fixed pixel-to-pixel pattern.
  2. For each bias pair, half the variance of the difference is the read variance in ADU squared.
  3. The gain is the mean signal divided by (the single-frame variance minus the read variance), all in ADU. The read noise in electrons is the read noise in ADU times the gain.

```python
import numpy as np
from astropy.io import fits

def single_frame_variance(path_a, path_b, box=500):
    """Half the variance of the difference of two frames, in their central box."""
    a = fits.getdata(path_a, memmap=False).astype(float)
    b = fits.getdata(path_b, memmap=False).astype(float)
    cy, cx = a.shape[0] // 2, a.shape[1] // 2
    window = (slice(cy - box, cy + box), slice(cx - box, cx + box))
    return 0.5 * np.var(a[window] - b[window]), 0.5 * (a[window].mean() + b[window].mean())

flat_pairs, bias_pairs = [], []  # lists of (path, path) for your own frames
read_variance = np.mean([single_frame_variance(a, b)[0] for a, b in bias_pairs]) if bias_pairs else 0.0
bias_level = np.mean([single_frame_variance(a, b)[1] for a, b in bias_pairs]) if bias_pairs else 0.0
gains = []
for a, b in flat_pairs:
    variance, mean = single_frame_variance(a, b)
    gains.append((mean - bias_level) / (variance - read_variance))
```
  4. Write the results into the camera's section of the configuration file with `kind = "measured"` and a `source` sentence.
- **What to look at:** The gain in electrons per stored ADU and its pair-to-pair spread. The read noise in electrons, compared with the datasheet value at the same gain setting.
- **Our result:** ASI 533MM Pro, 2026-10-11, gain setting 0, offset 10, 0.55 s flats, -10 C, central 1000 by 1000 pixels, two sets of 15 flat pairs at about 28,000 ADU: gain 0.783 and 0.782 e-/ADU, pair-to-pair spread 0.0004. Five bias pairs: read noise 4.88 ADU, which is 3.8 e-, the same as the ZWO datasheet at gain setting 0; a median-based estimate gives 3.3 e-. The sensor is 14-bit and the files are 16-bit in steps of 4, so the gain in stored ADU is a quarter of the value in sensor ADU. With the datasheet's 3.6 e-/ADU the error bars come out about 2 times too small.
- **Limits:** One signal level, so no photon-transfer curve was fitted. A flat panel that flickers between frames adds variance and lowers the gain, so 0.783 is more likely too low than too high. The values hold only at gain setting 0.
- **To tune for your setup:** Measure your own camera at the gain setting you observe with. If your files store sensor ADU scaled up (as here), use the gain in stored ADU. Then re-run [PHO-02](#pho-02): the ensemble ratio should move toward 1.
- **Automated by:** No script; measured by hand on 2026-10-11.

### PHO-04

**Frame alignment on real fields: rotation, corner displacement and centroid offsets**

- **Claim:** A field can turn enough during a night that a shift alone leaves far stars outside the centroid box's reach. Fitting a rotation and scale change brings them back. The corner displacement the fit reports is small on steady nights and near 2 pixels on a field that turned; the per-star offsets left after the fit are centroid noise and do not track the rotation.
- **Settings it supports:** Code constants `MAXIMUM_CORNER_DISPLACEMENT_PX` = 1.0 <!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.MAXIMUM_CORNER_DISPLACEMENT_PX --> and `MAXIMUM_CENTROID_FALLBACK_FRACTION` = 0.20 <!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.MAXIMUM_CENTROID_FALLBACK_FRACTION --> in `astrometricslib/pipelines/photometry/post_processing/run_gates.py`; `ALIGNMENT_ANCHOR_REACH_PX` = 4.0 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_REACH_PX -->, `MAX_CENTROID_SHIFT_PX` = 1.5 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.MAX_CENTROID_SHIFT_PX --> and `ALIGNMENT_REFINEMENT_PASSES` = 3 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_REFINEMENT_PASSES --> in `astrometricslib/pipelines/photometry/pre_processing/frame_photometry.py` (changing any of them means editing the code). It also supports the gate's choice to judge the corner displacement and not the per-star offset.
- **Status:** Partly measured (three fields; the limits are not set from a measured false-alarm rate).
- **Data you need:** Single-night sessions of 30 or more frames from fields of different density (a globular cluster, a galaxy field, a sparse field), including a night long enough for field rotation to show (several hours, or a mount with a known polar-alignment error).
- **Procedure:**
  1. Run the photometry stage and read the `registration_drift` gate. Its detail reports the global drift, the corner displacement (95th percentile and worst picture), the median and 95th-percentile per-star offset, and the fallback share.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
result = astrometrics.processing.process_target("NGC 2403", stages=["photometry"])
drift = result.quality.photometry.gate("registration_drift")
print(drift.status, drift.measured_value, drift.limit, drift.detail)
```
  2. For one session in detail, run the analyzer on the session's frames and read its summary (internal functions, not part of the public API; may change). `frame_paths` is the list of one session's FITS files.

```python
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

frame_paths = []  # one session's FITS files, first picture first
analyzer = VariabilityAnalyzer()
analyzer.process(frame_paths)
summary = analyzer.centroid_shift_summary()
if summary is not None:
    print(summary.worst_corner_displacement_px, summary)
```
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.centroid_shift_summary --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.CentroidShiftSummary --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.FrameAlignment.corner_displacement_px -->
  3. To compare reaches, change `ALIGNMENT_ANCHOR_REACH_PX` (or call `calculate_frame_alignment` directly on a frame and its anchors) and repeat step 2. An independent check fits the same rotation and scale to several hundred bright stars matched by hand. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.calculate_frame_alignment -->
- **What to look at:** Corner displacement, in pixels (95th percentile over the pictures): under 1 pixel means a star stays on nearly the same pixels. Fallback share (0 to 1): the share of measurements whose own centroid was refused. Median and 95th-percentile per-star offset, in pixels: reported only, because centroid noise dominates it.
- **Our result:** ASI 533MM Pro, 2026-10-11.

  | Field | Corner displacement, 95th percentile | Notes |
  |---|---|---|
  | M 13 | 0.2 px | turned 0.05 px at the corner over its night |
  | M 81 | 0.9 px | mostly the same small scale change in every picture (the star finder's centroids on the first picture against the re-centering box on later ones) |
  | NGC 2403 | 1.8 px (1.9 px worst picture) | the field turned 0.05 degrees over the night |

  - Per-star offsets after the fit: 95th percentile 1.0 to 1.3 px and median 0.3 to 0.5 px on all three fields, unchanged by the rotation fit.
  - On NGC 2403 a shift alone left 12 percent of the measurements falling back to an off-star position; with the rotation fitted, 4 percent.
  - On M 81 the corner displacement was 0.9 px with the usual 1.5 px reach and 1.3 to 1.6 px with a reach of 3 to 4 px; an independent fit on 800 bright stars gave about 0.6 px. The wide first-pass box was pulled off by neighbors or the galaxy by several pixels on some M 81 stars; the median shift ignored them.
  - Before the gate judged the corner displacement, `registration_drift` failed M 13 on a 95th-percentile per-star offset of 1.13 px (limit 1). A replay on the frames showed centroid noise on faint and crowded stars, not rotation. M 13 now passes.
- **Limits:** Three fields and one camera. The 1 pixel and 20 percent limits are design estimates set with these values in view, not from a false-alarm rate. The 0.6 px independent fit was done by hand.
- **To tune for your setup:** If your steady nights show a corner displacement near 1 pixel, the scale term between the two centroid methods may dominate; check it on a night with no rotation before raising `MAXIMUM_CORNER_DISPLACEMENT_PX`. For larger star images (FWHM above about 4 px), the 11 px re-centering box is too small; widen `CENTROID_BOX_HALF_WIDTH_PX` and re-run this procedure.
- **Automated by:** No script; measured by hand on 2026-10-11. The gate logic is pinned by `astrometricslib/pipelines/photometry/test/post_processing/test_run_gates.py` and `astrometricslib/pipelines/photometry/test/pre_processing/test_centroid_shift_summary.py`.

### PHO-05

**Alignment anchors chosen by signal-to-noise on a sparse field (synthetic)**

- **Claim:** In a sparse field whose detections are mostly noise peaks, choosing anchors by peak signal-to-noise recovers the frame's shift to 0.05 px, while the older rule (detections ranked 50 to 100) does not.
- **Settings it supports:** Code constants in `astrometricslib/pipelines/photometry/pre_processing/frame_photometry.py` (changing them means editing the code): `ALIGNMENT_ANCHOR_MINIMUM_PEAK_SNR` = 20.0 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_MINIMUM_PEAK_SNR -->, `ALIGNMENT_ANCHOR_COUNT` = 50 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_COUNT -->, `ALIGNMENT_ANCHOR_SKIPPED_BRIGHTEST_FRACTION` = 0.05 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_SKIPPED_BRIGHTEST_FRACTION --> and `ALIGNMENT_ANCHOR_MINIMUM_QUALIFYING` = 10 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_MINIMUM_QUALIFYING -->. The test supports the rule, not the exact values.
- **Status:** Partly measured (one synthetic case).
- **Data you need:** None from the sky. To repeat it on real data, you need two frames of a sparse field (a few tens of stars) with a known small offset between them, for example from a deliberate mount nudge.
- **Procedure:**
  1. Render a reference frame and a shifted frame of 12 stars on a noisy sky (`make_photometry_frame` in `astrometricslib/test/synthetic/`). <!-- impl: astrometricslib.test.synthetic.make_photometry_frame -->
  2. Detect sources on the reference frame, choose anchors with `select_alignment_anchors`, and measure the shift with `calculate_frame_alignment` (internal functions, not part of the public API; may change). Repeat with the detections ranked 50 to 100 for the older rule. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.select_alignment_anchors -->
  3. On real data, read `VariabilityAnalyzer.alignment_anchor_rule` and `alignment_anchor_count` after `process`, and compare the measured shift with the known offset.
- **What to look at:** The measured shift against the injected one, in pixels; the anchor rule used and the anchor count.
- **Our result:** Synthetic sparse field, 12 real stars, more than 50 detections. Injected shift (2.70, -1.30) px. The signal-to-noise rule kept the 12 stars and measured (2.70, -1.30) px; the older rule measured (-4.7, 2.1) px.
- **Limits:** One synthetic field; real sparse fields have hot pixels and gradients the generator does not add.
- **To tune for your setup:** If a real sparse field falls back to the older rule (the `registration_drift` detail names it), lower `ALIGNMENT_ANCHOR_MINIMUM_PEAK_SNR` and re-run this test and [PHO-04](#pho-04).
- **Automated by:** `astrometricslib/pipelines/photometry/test/pre_processing/test_frame_photometry.py` (`test_sparse_field_anchors_recover_an_injected_shift_where_the_old_rule_fails`).

### PHO-06

**Variability indices and the candidate rule on synthetic fields with known variables**

- **Claim:** Measuring each star against the field's noise model separates injected variables from constant stars far better than the raw CV (AUC 0.84). The chosen score (chi-square and excess scatter, with Stetson J as a veto) reaches an AUC of 0.94, flags no constant star, and finds 64 percent of injected variables of 0.01 to 0.3 mag. Using J as a veto keeps its protection against isolated bad points without lowering the ranking.
- **Settings it supports:** Code constants in `astrometricslib/pipelines/photometry/processing/variability_indices.py` (changing them means editing the code): `EXCESS_SCATTER_THRESHOLD` = 1.5 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.EXCESS_SCATTER_THRESHOLD -->, `CALIBRATION_SIGMAS` = 2.326 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.CALIBRATION_SIGMAS -->, `MINIMUM_STARS_PER_NOISE_BIN` = 10 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MINIMUM_STARS_PER_NOISE_BIN -->, `MAXIMUM_NOISE_BINS` = 10 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MAXIMUM_NOISE_BINS --> and `MINIMUM_STARS_FOR_NOISE_MODEL` = 20 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MINIMUM_STARS_FOR_NOISE_MODEL -->. The form of the score itself. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.variability_score -->
- **Status:** Partly measured (synthetic fields only).
- **Data you need:** None from the sky. The real-sky counterpart is [PHO-08](#pho-08) and [PHO-09](#pho-09).
- **Procedure:**
  1. Build a field with `make_variability_field` (internal test helper, not part of the public API; may change): 400 constant stars spread evenly over 6 magnitudes, 48 frames, and 5 percent of stars (20) carrying a sinusoid (two thirds) or an eclipsing dip (one third) with an amplitude drawn log-uniformly from 0.01 to 0.3 mag. <!-- impl: astrometricslib.test.synthetic.variability_field.make_variability_field -->
  2. Run the real normalization, detrending and flagging, then score each index with the AUC.

```python
from astrometricslib.pipelines.photometry.post_processing.variability_skill import area_under_curve
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer
from astrometricslib.test.synthetic.variability_field import make_variability_field

field = make_variability_field(seed=100)
analyzer = VariabilityAnalyzer()
analyzer.stellar_objects = field.stars
analyzer.timestamp_to_path = {stamp: f"frame_{i:03d}.fits" for i, stamp in enumerate(field.timestamps)}
analyzer.normalize_light_curves()
analyzer.detrend_light_curves_airmass()
candidates = {star.id for star in analyzer.identify_variable_stars()}
scores = [(star.photometry.variability_score, truth) for star, truth in zip(field.stars, field.is_variable)
          if star.photometry.variability_score is not None]
print(area_under_curve([s for s, t in scores if t], [s for s, t in scores if not t]))
```
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.identify_variable_stars --> <!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.area_under_curve -->
  3. Repeat over seeds 100 to 123 and average. The test module's `measure_auc_table` and `measure_flag_rates` do this. <!-- impl: astrometricslib.pipelines.photometry.test.test_variability_injection.measure_auc_table --> <!-- impl: astrometricslib.pipelines.photometry.test.test_variability_injection.measure_flag_rates -->
- **What to look at:** The AUC of each index (the chance a random injected variable outranks a random constant star; 0.5 is chance, 1 is perfect). The share of constant stars flagged (should be near 0) and of injected variables flagged, by amplitude.
- **Our result:** 24 synthetic fields (seeds 100 to 123), each 400 stars over 6 magnitudes with scatter from 0.0035 mag at the bright end (set by an unrecorded 0.3 percent systematic term) to 0.06 mag at the faint end (photon and background noise), 48 frames. Re-run on 2026-10-15 with identical results.

  | Index | AUC (mean, sd between fields) |
  |---|---|
  | Coefficient of variation | 0.835, 0.039 |
  | Reduced chi-square against the propagated errors | 0.809, 0.066 |
  | Reduced chi-square against the scaled errors | 0.940, 0.044 |
  | Stetson J (scaled errors) | 0.879, 0.042 |
  | Excess scatter | 0.942, 0.042 |
  | Smallest ratio of the three indices to their thresholds | 0.880, 0.043 |
  | Chosen score (chi-square and excess scatter, J as a veto) | 0.940, 0.044 |

  - Dividing by the noise model matters more than the choice of index: chi-square against the propagated errors ranks below the CV, because those errors are too small for the bright stars; against the scaled errors it ranks with excess scatter.
  - At the thresholds, the rule flagged none of the 9,120 constant stars and 64 percent of the injected variables: 90 percent of those of 0.1 to 0.3 mag, 63 percent of 0.03 to 0.1 mag, 40 percent of 0.01 to 0.03 mag; 76 percent of sinusoids and 38 percent of eclipsing dips (a narrow eclipse puts few points in the dip).
  - In 8 fields where 5 percent of the points scatter five times more than the others, the rule with J flagged none of 3,040 constant stars, and the rule without J flagged 24 (0.8 percent).
- **Limits:** Synthetic data, not the sky. The systematic term and the outlier share are the generator's choices; a real field has other error sources, so these AUCs describe the method on a field whose noise is understood. `measure_flag_rates` counts a star as flagged when its score is above 1 and does not apply the q-value test. The per-amplitude and per-shape shares and the 8-field outlier comparison are not printed by the module; they were computed by hand from the same runs. The generator's sinusoid amplitude is a semi-amplitude (peak to peak twice that), unlike [PHO-09](#pho-09), whose depths are peak to peak.
- **To tune for your setup:** Change the generator's `systematic_fraction`, `frame_count` and `outlier_fraction` to match your own light curves (compare its scatter curve with your run's `noiseModelCurve`), then re-run the table. Raise `EXCESS_SCATTER_THRESHOLD` or `CALIBRATION_SIGMAS` if constant stars start to pass.
- **Automated by:** `astrometricslib/pipelines/photometry/test/test_variability_injection.py`; print the table with `.venv/bin/python -m astrometricslib.pipelines.photometry.test.test_variability_injection`.

### PHO-07

**False-alarm probabilities are uniform for constant stars (synthetic)**

- **Claim:** For constant stars with correct, independent Gaussian errors, the chi-square false-alarm probability is uniform: 1 percent of stars fall below 0.01. The Benjamini and Hochberg q-value then keeps a field of 300 constant stars to at most 3 candidates.
- **Settings it supports:** Code constant `FALSE_DISCOVERY_LEVEL` = 0.01 in `astrometricslib/pipelines/photometry/processing/variability_indices.py` (changing it means editing the code). <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.FALSE_DISCOVERY_LEVEL --> The functions behind it. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.false_alarm_probability --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.benjamini_hochberg --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.field_error_scale -->
- **Status:** Partly measured (synthetic white noise only; the effect of red noise is not measured).
- **Data you need:** None from the sky. On real data, a field of 200 or more stars whose catalogs list few variables.
- **Procedure:**
  1. Draw 4,000 light curves of 40 points with 1 percent Gaussian noise and errors equal to that noise. Compute each one's probability (internal functions, not part of the public API; may change).

```python
import numpy as np
from astrometricslib.pipelines.photometry.processing import variability_indices as vi

generator = np.random.default_rng(41)
probabilities = [
    vi.false_alarm_probability(vi.reduced_chi_square(1.0 + generator.normal(0.0, 0.01, 40), np.full(40, 0.01)), points=40)
    for _ in range(4000)
]
print(np.mean(np.asarray(probabilities) < 0.01))
```
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.reduced_chi_square -->
  2. For the field test, build 300 such stars and run `assess_variability` on them; count `candidates`. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.assess_variability -->
  3. On real data, take the run's `falseAlarmProbability` values for stars no catalog lists and plot their histogram; it should be flat if the errors are right and the noise is white.
- **What to look at:** The share of probabilities below 0.01 (should be near 0.01) and below 0.5 (near 0.5). The candidate count in a constant field.
- **Our result:** 4,000 synthetic light curves: 1.0 percent below 0.01. Field of 300 constant stars: at most 3 candidates (the test's bound).
- **Limits:** Both numbers assume noise independent from point to point. Slow systematics (red noise) make the probabilities too small, and no test measures how much. The 0.01 level is a design value, not tuned.
- **To tune for your setup:** If your unlisted stars' probabilities pile up near 0, your errors are too small or your noise is red; fix the gain ([PHO-03](#pho-03)) before loosening anything. Lower `FALSE_DISCOVERY_LEVEL` for fewer false flags at the cost of missed variables.
- **Automated by:** `astrometricslib/pipelines/photometry/test/processing/test_variability_indices.py` (`test_constant_stars_with_correct_errors_give_uniform_probabilities`, `test_a_field_of_constant_stars_flags_nobody_at_the_false_discovery_level`).

### PHO-08

**The candidate rule on a real field (M 13)**

- **Claim:** On a real globular-cluster field the variability score ranks catalogued variables above other stars only weakly (AUC 0.61, against 0.94 on synthetic fields), and the rule accepts 5 of 37 listed variables and 11 of 403 unlisted stars. The limit is the detection threshold, not the statistic ([PHO-09](#pho-09)).
- **Settings it supports:** Code constant `MINIMUM_DISCRIMINATION_AUC` = 0.7 in `astrometricslib/pipelines/photometry/post_processing/variability_skill.py` <!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.MINIMUM_DISCRIMINATION_AUC -->, and the candidate rule's `FALSE_DISCOVERY_LEVEL` = 0.01 (see [PHO-07](#pho-07)). Changing either means editing the code.
- **Status:** Partly measured (one field).
- **Data you need:** One session of 30 or more frames of a field with at least 10 stars that SIMBAD, Gaia DR3 or VSX list as variable and 30 or more that they do not, with the catalog answers stored on the stars (`backfill_variable_star_flags.py` fills in Gaia DR3 and VSX).
- **Procedure:**
  1. Store the catalogs' variability answers on the stars, then run the photometry stage.

```shell
python -m astrometricslib.scripts.backfill_variable_star_flags --target "M 13" --apply
```
  2. Read the `variability_discrimination` gate, then compute the AUC of each index by hand from the saved stars.

```python
from astrometricslib import Astrometrics
from astrometricslib.models.known_variability import KnownVariability
from astrometricslib.pipelines.photometry.post_processing.variability_skill import area_under_curve, required_auc

astrometrics = Astrometrics()
result = astrometrics.processing.process_target("M 13", stages=["photometry"])
astrometrics.targets.save()
print(result.quality.photometry.gate("variability_discrimination"))
stars = astrometrics.stars.query(target_id="M 13", has_photometry=True, detail="objects", limit=None).objects
scored = [s for s in stars if s.photometry and s.photometry.variability_score is not None]
listed = [s for s in scored if s.known_variability == KnownVariability.KNOWN_VARIABLE]
unlisted = [s for s in scored if s.known_variability == KnownVariability.NOT_LISTED]
for name in ("variability_score", "reduced_chi_square", "stetson_j", "coefficient_of_variation"):
    auc = area_under_curve([getattr(s.photometry, name) for s in listed], [getattr(s.photometry, name) for s in unlisted])
    print(name, auc)
print("beats chance above", required_auc(len(listed), len(unlisted)))
```
<!-- impl: astrometricslib.api.stars.StellarCatalog.query --> <!-- impl: astrometricslib.models.known_variability.KnownVariability --> <!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.required_auc -->
  3. Count the candidates in the photometry result's `variableCandidates` and split them by catalog status.
- **What to look at:** The AUC of the score and of each index (0.5 is chance); the AUC that beats chance at the 5 percent level for your group sizes; the share of listed and unlisted stars the rule accepts.
- **Our result:** M 13, ASI 533MM, 41 frames, 2026-10-11. 458 stars with indices; 37 listed as variable by SIMBAD, Gaia DR3 or VSX; 403 not listed.

  | Statistic | AUC, listed against unlisted |
  |---|---|
  | Variability score | 0.61 |
  | False-alarm probability | 0.61 |
  | Reduced chi-square | 0.61 |
  | Stetson J | 0.57 |
  | CV (earlier flag) | 0.41 |
  | Least AUC that beats chance at 5 percent | 0.58 |

  The rule accepted 16 stars: 5 listed variables and 11 not listed. Listed variables are 8 percent of the field and 31 percent of the accepted stars; the rule found 5 of 37 listed variables (14 percent) and 11 of 403 unlisted stars (2.7 percent). The `variability_discrimination` gate failed (0.61 is below 0.7).
- **Limits:** One field, crowded at its core, where the photometry is poor. Most listed variables in M 13 are small-amplitude. A catalog listing is not proof of variability at this camera's precision, and some unlisted stars that the rule accepts may be real variables.
- **To tune for your setup:** Run this on two or three of your own fields. If the AUC stays near 0.6 with a good noise model, the detection threshold limits you ([PHO-09](#pho-09)); improving the photometry helps more than moving the thresholds. Whether to loosen the rule is a choice between missed signals and false flags.
- **Automated by:** No script for the per-index AUCs; computed by hand on 2026-10-11. The gate itself runs in every photometry run (`photometry_run_gates`). `astrometricslib/scripts/backfill_variable_star_flags.py` stores the catalog answers (`python -m astrometricslib.scripts.backfill_variable_star_flags --limit 2000`, then `--apply`).

### PHO-09

**Smallest signal the candidate rule finds on real light curves (injection on M 13)**

- **Claim:** On M 13 the rule flags half of the injected signals at about 0.04 mag peak to peak on the brightest quarter of stars, and only at 0.16 mag on the faintest quarter. It finds nothing below 0.02 mag and flags none of the untouched stars.
- **Settings it supports:** The candidate rule as a whole: `FALSE_DISCOVERY_LEVEL` = 0.01, `EXCESS_SCATTER_THRESHOLD` = 1.5 and `CALIBRATION_SIGMAS` = 2.326 (see [PHO-06](#pho-06)). It gives the measured counterpart of the `detectable_amplitude` gate's estimate, whose limit is code constant `MAXIMUM_DETECTABLE_AMPLITUDE_MAG` = 0.3 in `astrometricslib/pipelines/photometry/post_processing/run_gates.py`. <!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.MAXIMUM_DETECTABLE_AMPLITUDE_MAG -->
- **Status:** Partly measured (one field; the signals are added to light curves, not pixels).
- **Data you need:** A target already run through the photometry stage, with at least 60 stars whose light curves have 25 or more points and stored catalog answers ([PHO-08](#pho-08), step 1).
- **Procedure:**
  1. Run the script on your target. It reads the stars read-only, leaves out stars the rule already flags and stars the catalogs list as variable, and in each of many draws multiplies about 8 percent of the field's light curves (and their errors) by a sinusoid or a box transit of one depth from 0.005 to 0.16 mag. It then runs the candidate rule on the whole field.

```shell
python -m astrometricslib.scripts.validate_photometry_injection --target "M 13" --realizations 80 --seed 1 --json m13_injection.json
```
  2. The same steps from Python:

```python
from astrometricslib.scripts.validate_photometry_injection import format_report, load_field, run_injection

stars, known_variable_ids = load_field("M 13", minimum_points=25)
result = run_injection(stars, known_variable_ids, realizations=80, seed=1)
print(format_report(result, "M 13"))
```
<!-- impl: astrometricslib.scripts.validate_photometry_injection.run_injection --> <!-- impl: astrometricslib.scripts.validate_photometry_injection.load_field --> <!-- impl: astrometricslib.scripts.validate_photometry_injection.inject_signal -->
- **What to look at:** The completeness (share of injected stars flagged) by depth and brightness quarter; the depth at which it reaches 50 percent; the share of untouched stars flagged, with its 68 percent interval.
- **Our result:** M 13, 2026-10-11, 80 draws, seed 1, 408 stars able to host a signal. Brightest quarter: flagged half the time at about 0.04 mag (85 percent of sinusoids and 84 percent of transits at 0.04 mag). Faintest quarter: flagged that often only at 0.16 mag (82 percent of sinusoids, 69 percent of transits). Nothing found below 0.02 mag at any brightness. 0 of 29,760 untouched stars flagged. Most catalogued variables in a field like this vary by less than 0.04 mag, which accounts for finding 5 of 37 in [PHO-08](#pho-08). The counts were saved outside the repository (`~/astrometrics_db_backups/20261010_validation_step0/photometry_injection_m13.json`).
- **Limits:** The injection is made on the light curves, so it tests the candidate rule and the noise of the photometry, not the aperture photometry or the comparison-star normalization. The ensemble does not respond to an injected signal, as it would to a real variable among the comparison stars. Hosts are the field's unflagged stars, and the injected stars shift the field's noise model and thresholds slightly. Depths are peak to peak for the sinusoid and the drop for the transit. One field.
- **To tune for your setup:** Run the script on your own targets. If the 50 percent depth is well above the variables you care about, improve the photometry (gain, flats, aperture) before moving the thresholds; then re-run. If untouched stars start to be flagged after a change, it went too far.
- **Automated by:** `astrometricslib/scripts/validate_photometry_injection.py` (`python -m astrometricslib.scripts.validate_photometry_injection --target "M 13"`; options `--realizations`, `--seed`, `--minimum-points`, `--json`), tested by `astrometricslib/scripts/test/test_validate_photometry_injection.py`.

### PHO-10

**The CV-only cutoff against the catalogs, over the whole library**

- **Claim:** The fallback rule (flag a star whose CV is above the field's median plus 7.4 MAD) flags about as large a share of unlisted stars as of catalogued variables, and the CV separates the two no better than chance. The multiplier is not what limits the search; the photometry's own scatter is.
- **Settings it supports:** Code constant `DEFAULT_VARIABILITY_SIGMA_THRESHOLD` = 7.4 in `astrometricslib/pipelines/photometry/processing/variability_analyzer.py` (changing it means editing the code). <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.DEFAULT_VARIABILITY_SIGMA_THRESHOLD --> It also bears on the low-confidence margin and the `detectable_amplitude` fallback, which use the same cutoff.
- **Status:** Measured.
- **Data you need:** A library of many targets with light curves (100 or more stars per target with 8 or more points) and stored catalog answers ([PHO-08](#pho-08), step 1).
- **Procedure:**
  1. Run the script. It reads every star's stored CV, builds the cutoff per target for each multiplier from 3 to 16 (including 7.4), and counts the stars of each catalog status above it. It also gives the AUC of CV between listed and unlisted stars, overall and between stars of similar brightness in the same target.

```shell
python -m astrometricslib.scripts.measure_variability_cutoff --output cutoff.json
```
  2. The cutoff for one field, from Python (internal function, not part of the public API; may change):

```python
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    DEFAULT_VARIABILITY_SIGMA_THRESHOLD,
    adaptive_cv_cutoff,
)

cvs = [0.01, 0.012, 0.015, 0.02, 0.05]  # the field's CVs
print(adaptive_cv_cutoff(cvs, DEFAULT_VARIABILITY_SIGMA_THRESHOLD).cutoff)
```
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.adaptive_cv_cutoff --> <!-- impl: astrometricslib.scripts.measure_variability_cutoff.evaluate --> <!-- impl: astrometricslib.scripts.measure_variability_cutoff.measure_separation -->
  3. For the scatter per target, run `report_gate_limits.py`, which reports the spread of each target's `lightCurveScatterRmsMag`.
- **What to look at:** For each multiplier, the share of catalogued variables flagged (the cutoff's recall) against the share of unlisted stars flagged (an upper bound on its false-alarm rate). The brightness-matched AUC (0.5 is chance).
- **Our result:** The library on 2026-10-09: 46,259 stars with light curves in 32 targets, 2,374 listed as variable by SIMBAD, Gaia DR3 or VSX. At 7.4 the cutoff flagged 5.2 percent of all stars, 3.8 percent of the catalogued variables, and 5.2 percent of unlisted stars. The brightness-matched AUC was 0.46, including for strong-amplitude types. The median CV of a light curve was 8 percent (about 0.08 mag) even among the brightest fifth of stars. Per target, the recorded median scatter ranged from 0.009 to 0.9 mag, with a median over targets of 0.29 mag. The multiplier was chosen to flag about the top 3 percent of stars; it flags 5.2 percent.
- **Limits:** The stored CV is a within-session scatter while the script builds the cutoff per target. Most light curves in the library predate the current comparison ensemble. The median CV among the brightest fifth and the per-target scatter range are not printed by the script; they were read from the same database by hand.
- **To tune for your setup:** Run the script on your library. If the AUC is near 0.5, changing the multiplier will not help; improve the photometry. If it is well above 0.5, pick the multiplier from the table at the recall and false-flag share you want.
- **Automated by:** `astrometricslib/scripts/measure_variability_cutoff.py` (`python -m astrometricslib.scripts.measure_variability_cutoff`; options `--minimum-stars`, `--minimum-points`, `--output`), tested by `astrometricslib/scripts/test/test_measure_variability_cutoff.py`; `astrometricslib/scripts/report_gate_limits.py` for the per-target scatter.

### PHO-11

**Block shuffling keeps period-search false alarms honest under red noise (synthetic)**

- **Claim:** Shuffling single points destroys slow correlated noise, so pure red noise looks significant almost every time. Shuffling blocks of three correlation lengths keeps the false-alarm rate at or below its stated level.
- **Settings it supports:** Code constants `_BLOCK_LENGTH_IN_CORRELATION_LENGTHS` = 3 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._BLOCK_LENGTH_IN_CORRELATION_LENGTHS -->, `_CORRELATION_GONE_BELOW` = 0.2 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._CORRELATION_GONE_BELOW --> and `_LONGEST_BLOCK_FRACTION` = 0.25 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._LONGEST_BLOCK_FRACTION --> in `astrometricslib/pipelines/photometry/processing/periodicity_search.py` (changing them means editing the code).
- **Status:** Partly measured (simulated red noise only).
- **Data you need:** None from the sky. On real data, light curves of stars you believe constant, observed on three or more nights.
- **Procedure:**
  1. Simulate AR(1) noise (each point keeps a share `phi` of the one before) on three nights of 25 points, 10 minutes apart. Search each curve and count how often the false-alarm probability is at or below 0.05 (internal functions, not part of the public API; may change).

```python
import numpy as np
from astrometricslib.pipelines.photometry.processing import periodicity_search as search

times = np.concatenate([night + np.arange(25) * (10 / 1440) for night in range(3)])
generator = np.random.default_rng(0)
calls = 0
for _ in range(60):
    steps = generator.standard_normal(times.size)
    values = np.zeros(times.size)
    for i in range(1, times.size):
        values[i] = 0.95 * values[i - 1] + steps[i]
    flux = 1.0 + 0.01 * values
    result = search.lomb_scargle_search(times, flux, shuffle_count=99)
    calls += result.false_alarm_probability <= 0.05
print(calls / 60, search.correlation_block_length(flux))
```
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.lomb_scargle_search --> <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.correlation_block_length --> <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.null_flux -->
  2. Pass `block_length=1` to get the plain point shuffle, and compare.
- **What to look at:** The share of pure-noise curves called significant at the 5 percent level; it should be near 5 percent, and at or below it is the safe side.
- **Our result:** 60 simulated AR(1) light curves per case, 99 noise-only versions each, share called significant at 5 percent:

  | Block length | 1 lag | 2 lags | 3 lags | 4 lags |
  |---|---|---|---|---|
  | `phi` = 0.70 | 28% | 7% | 2% | 2% |
  | `phi` = 0.95 | 37% | 10% | 3% | 2% |

  A plain point shuffle called 97 percent (`phi` = 0.70) and 100 percent (`phi` = 0.95) significant. Three lags is slightly conservative, which is the safe side; one or two lags is too permissive.
- **Limits:** Simulated AR(1) noise only. The table was recorded in the code comment of `periodicity_search.py`; the tests check only bounds (point shuffle at least 70 percent at `phi` = 0.95; block shuffle at most 20 and 30 percent with 30 simulations). The photometry README quotes "about 5% to 10%" for the block shuffle, which matches the 2-lag column, not the 3 lags the code uses.
- **To tune for your setup:** If constant stars in your data get "possible" verdicts more often than 5 percent of the time, raise `_BLOCK_LENGTH_IN_CORRELATION_LENGTHS` and re-run the test.
- **Automated by:** `astrometricslib/pipelines/photometry/test/processing/test_periodicity_null.py` (bounds only); no script reproduces the table.

### PHO-12

**Period and dip searches on pure noise and on injected signals (synthetic)**

- **Claim:** On white noise, neither search calls a light curve "detected" more often than its 1 percent cutoff allows. A 0.4 day cycle of 1 percent amplitude in 1 percent noise, and repeating dips of a few percent, are found.
- **Settings it supports:** Code constants in `astrometricslib/pipelines/photometry/processing/periodicity_search.py` (changing them means editing the code): `_FALSE_ALARM_FOR_DETECTION` = 0.01 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._FALSE_ALARM_FOR_DETECTION -->, `_FALSE_ALARM_FOR_POSSIBLE` = 0.05 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._FALSE_ALARM_FOR_POSSIBLE -->, `_SHUFFLE_COUNT` = 300 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._SHUFFLE_COUNT --> and `_CYCLES_FOR_DETECTION` = 3.0 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._CYCLES_FOR_DETECTION -->.
- **Status:** Partly measured (synthetic white noise only).
- **Data you need:** None from the sky. On real data, a star with a known period and amplitude observed on three or more nights (an eclipsing binary or a short-period pulsator).
- **Procedure:**
  1. Run the script's period checks (it also runs the spectral checks; `--skip-standards` leaves out the real-star part). The noise check uses 200 points over 2.5 days with 1 percent Gaussian noise.

```shell
python -m astrometricslib.scripts.validate_spectral_and_period_analysis --trials 100 --skip-standards
```
  2. The period part alone, from Python:

```python
from astrometricslib.scripts.validate_spectral_and_period_analysis import validate_period_searches

passed = validate_period_searches(25)
```
<!-- impl: astrometricslib.scripts.validate_spectral_and_period_analysis.validate_period_searches --> <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.box_search -->
  3. On a real star, use the public `Astrometrics().stars.analyze_periodicity(star_id)` and compare the saved period and verdict with the known ones. <!-- impl: astrometricslib.api.stars.StellarCatalog.analyze_periodicity -->
- **What to look at:** The share of pure-noise curves called "detected" (should stay near 1 percent; the script fails above 3 percent). The share of injected signals detected at each amplitude or depth.
- **Our result:** Run for this register on 2026-10-15, `validate_period_searches(25)` (25 light curves per case, 200 points over 2.5 days, 1 percent Gaussian noise, 300 shuffles per search; about 6 minutes).

  | Case | Share called "detected" |
  |---|---|
  | Cycle search, pure noise | 0 of 25 |
  | Dip search, pure noise | 0 of 25 |
  | 0.4 day sinusoid, semi-amplitude 1, 2 and 4 percent | 100 percent each |
  | 0.04 day dips every 0.5 day, depth 2, 4 and 8 percent | 100 percent each |

  The script's noise checks passed. Before this run, no result of the period part had been recorded.
- **Limits:** White noise and a regular cadence only; real light curves have red noise ([PHO-11](#pho-11)) and gaps between nights. With 25 trials per case a rate of 0 means fewer than about 4 percent at 68 percent confidence. The hold-out and alias checks and the family-wise correction are not part of this script.
- **To tune for your setup:** Change the cadence, span and noise in `validate_period_searches` to match your own light curves and re-run. If noise is called detected too often, raise the shuffle count or tighten `_FALSE_ALARM_FOR_DETECTION`.
- **Automated by:** `astrometricslib/scripts/validate_spectral_and_period_analysis.py` (`python -m astrometricslib.scripts.validate_spectral_and_period_analysis`; options `--trials`, `--skip-standards`); fixed cases in `astrometricslib/pipelines/photometry/test/processing/test_periodicity_search.py`.

### PHO-13

**Scatter of constant stars with an earlier median ensemble (NGC 2244)**

- **Claim:** With an earlier ensemble (the median of 200 stars ranked 100th to 300th by brightness), constant stars on a 3-hour NGC 2244 sequence scattered by less than 0.012 mag. The current comparison set has not been measured on this field.
- **Settings it supports:** None of the current values; it is kept as the only real-field scatter figure for a differential ensemble on this camera. The current selection is `select_comparison_set`. <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.select_comparison_set -->
- **Status:** Partly measured (earlier method; not repeated with the current comparison set).
- **Data you need:** One session of 25 or more frames over 2 or more hours of an open cluster with many stars of similar brightness.
- **Procedure:**
  1. Run the photometry stage on the target with the current code.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
result = astrometrics.processing.process_target("NGC 2244", stages=["photometry"], photometry={"filter_type": "Luminance"})
print(result.quality.photometry.gate("comparison_ensemble").detail)
```
  2. Read the comparison scatter in magnitudes from the gate detail, and the scatter of the field's constant stars from the run's `noiseModelCurve` (`result.quality.photometry.photometry_metrics.noise_model_curve`). <!-- impl: astrometricslib.models.quality_summary.PhotometryQualitySummary -->
- **What to look at:** The scatter of non-variable stars, in magnitudes, at each brightness.
- **Our result:** NGC 2244, 29 frames, 10,800 s total exposure: non-variable stars scattered by less than 0.012 mag (about 1.1 percent) with the earlier median ensemble. Date of measurement not recorded.
- **Limits:** The method that produced it no longer exists. The figure is evidence about the data and the shape of the method, not about current performance.
- **To tune for your setup:** Use [PHO-02](#pho-02) for the current set.
- **Automated by:** `astrometricslib/scripts/run_empirical_validation.py` runs the photometry stage on NGC 2244, M 81 and NGC 2903 (`python -m astrometricslib.scripts.run_empirical_validation --camera "ZWO ASI 533MM Pro"`); it does not print the scatter, which was read by hand.

### PHO-14

**Cross-session star matching on an 8-session target (M 81)**

- **Claim:** Tracking each session on its own and then matching stars across sessions by sky position gives correct per-session measurements and one light curve per star. A repeat run gives the same result.
- **Settings it supports:** The `tolerance_arcsec` argument of `_match_and_merge_across_sessions` (default 5.0 arcsec, in `astrometricslib/pipelines/photometry/batch.py`; changing it means editing the code). <!-- impl: astrometricslib.pipelines.photometry.batch._match_and_merge_across_sessions --> <!-- impl: astrometricslib.pipelines.photometry.batch._merge_light_curves -->
- **Status:** Partly measured (one target; the match radius was not varied).
- **Data you need:** One target observed on several nights (5 or more sessions over weeks or months), each session plate-solved.
- **Procedure:**
  1. Run the photometry stage on the target twice and compare the stored stars.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
counts = []
for _ in range(2):
    result = astrometrics.processing.process_target("M 81", stages=["photometry"])
    astrometrics.targets.save()
    counts.append(result.quality.photometry.photometry_metrics.cross_session_match_count)
print(counts, result.results["photometry"]["crossSessionMatchCount"])
```
  2. Count the distinct stars before and after matching, and check that a second run adds, removes or changes no rows.
  3. Check the `session_plate_solve` gate: a session that cannot be solved is left out of matching.
- **What to look at:** The number of merges; whether a repeat run is identical; the share of frames with a zero flux for a star (should be near 0); whether false flags fall on the faintest stars, as noise predicts.
- **Our result:** M 81, 258 frames in 8 sessions (2023-05 to 2026-05). 11,392 session-scoped stars matched down to 8,422 (2,970 merges); a repeat run added, removed or changed 0 rows. One session was left out because one of its frames referenced another target's stack. Before per-session tracking, 85 to 94 percent of frames read zero flux for a star, and 75 percent of the brightest fifth of stars were flagged variable by the earlier CV flag; after it, 223 of 258 frames were processed, the brightest fifth's flag rate fell to 14 percent, and the faintest fifth's was 46 percent, close to the single-session NGC 2903 baseline of 47 percent. Distinct stars rose from 1,353 to 11,392 before matching.
- **Limits:** One target. The flag rates use the earlier CV-only flag, not the current candidate rule. Dates of the runs were not recorded.
- **To tune for your setup:** If your plate solutions are poorer than about 1 arcsec, or your fields are crowded, change `tolerance_arcsec` and repeat; too large a radius merges neighbors, too small leaves the same star split.
- **Automated by:** `astrometricslib/scripts/run_empirical_validation.py` (photometry part); the repeat-run comparison was done by hand. Matching is pinned by `astrometricslib/pipelines/photometry/test/test_merge_errors_and_times.py`.

## Design estimates

These tuned values have not been measured. They are reasoned choices.

| Setting | Value | Where | How you could measure it on your data |
|---|---|---|---|
| Aperture radius; sky ring radii | 4 px; 7 to 12 px | arguments of `measure_aperture_photometry` in `pipelines/photometry/pre_processing/frame_photometry.py` | Run [PHO-02](#pho-02) at several radii and keep the one with the smallest ensemble scatter for your star width. |
| Re-centering box; passes; largest move | 11 px; 3; 1.5 px | `CENTROID_BOX_HALF_WIDTH_PX`, `CENTROID_REFINEMENT_PASSES`, `MAX_CENTROID_SHIFT_PX` in `frame_photometry.py` | Read the fallback share of the `registration_drift` gate ([PHO-04](#pho-04)) at several values on your own frames. |
| Anchor limits | 20 times the sky noise; skip brightest 5 percent; 50 anchors; at least 10 | `ALIGNMENT_ANCHOR_*` constants in `frame_photometry.py` | Repeat [PHO-05](#pho-05) on real frames with a known offset. |
| Anchor spread for a rotation fit; fewest anchors for a shift | 7 percent of the corner distance; 5 | `MINIMUM_ANCHOR_SPREAD_FRACTION`, `MINIMUM_ANCHORS_FOR_SHIFT` in `frame_photometry.py` | Compare the corner displacement of [PHO-04](#pho-04) with and without the fit on a cluster-core field. |
| Comparison band; vetted candidates | brightest 2 percent skipped, band to 30 percent; 100 | `BRIGHTEST_SKIPPED_FRACTION`, `FAINTEST_BAND_FRACTION`, `MAXIMUM_VETTING_CANDIDATES` in `comparison_ensemble.py` | Run [PHO-02](#pho-02) with several band edges and keep the smallest ensemble scatter over predicted. |
| Minimum and cap of comparison stars | 5 and 20 | `MINIMUM_COMPARISON_STARS`, `DEFAULT_MAXIMUM_COMPARISON_STARS` in `comparison_ensemble.py` | Re-run [PHO-02](#pho-02) with `VariabilityAnalyzer(maximum_comparison_stars=...)` and watch the ensemble error stop falling. |
| Vetting consistency limit; usable-frame share | 3 sigma; one half | `EXCESS_SCATTER_SIGMA`, `MINIMUM_USABLE_FRAME_FRACTION` in `comparison_ensemble.py` | Plant a known variable among the comparison candidates of a real session, as [PHO-01](#pho-01) does on synthetic ones. |
| Frame outlier clip; per-star outlier clip | 3 sigma (at least 5 frames); 5 sigma | `VariabilityAnalyzer._reject_outlier_frames`, `_reject_outlier_measurements_for_star` | Count clipped points on constant stars against the share a normal distribution predicts. |
| Share of frames rejected before the run is flagged | 25 percent (and at least 5 frames) | `MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG`, `MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG` in `run_gates.py` | Run `report_gate_limits.py` over the library and set the limit above the rejection share of known-good nights. |
| Noise model sizes | 20 stars; 10 per bin; 10 bins | `MINIMUM_STARS_FOR_NOISE_MODEL`, `MINIMUM_STARS_PER_NOISE_BIN`, `MAXIMUM_NOISE_BINS` in `variability_indices.py` | Vary them in [PHO-06](#pho-06) with a field size like yours and watch the AUC. |
| Thresholds | excess scatter 1.5; chi-square and J at median + 2.326 robust sigma | `EXCESS_SCATTER_THRESHOLD`, `CALIBRATION_SIGMAS` in `variability_indices.py` | Checked only on synthetic fields ([PHO-06](#pho-06)); run [PHO-09](#pho-09) on your field at several values. |
| False discovery level | 0.01 | `FALSE_DISCOVERY_LEVEL` in `variability_indices.py` | Count untouched stars flagged in [PHO-09](#pho-09) at several levels. |
| Fewest usable stars for a scatter population | 10 | `MINIMUM_STARS_FOR_SCATTER_POPULATION` in `run_gates.py` | Run [PHO-06](#pho-06) with very small fields and see where the AUC collapses. |
| Discrimination AUC floor | 0.7 | `MINIMUM_DISCRIMINATION_AUC` in `variability_skill.py` | Collect the gate's AUC over many fields ([PHO-08](#pho-08)) and compare runs you trust with runs you do not. |
| Largest acceptable detectable amplitude | 0.3 mag peak to peak | `MAXIMUM_DETECTABLE_AMPLITUDE_MAG` in `run_gates.py` | Compare the gate's estimate with the 50 percent depth of `validate_photometry_injection.py` ([PHO-09](#pho-09)) on the same field. |
| Corner displacement; centroid fallback share; tracking drift | 1 px; 20 percent; 20 px | `MAXIMUM_CORNER_DISPLACEMENT_PX`, `MAXIMUM_CENTROID_FALLBACK_FRACTION` in `run_gates.py`; `UNSTABLE_TRACKING_DRIFT_PX` in `pre_processing/assess_input_quality.py` | Collect the `registration_drift` values over many nights ([PHO-04](#pho-04)) and set each limit above the nights you trust. |
| Low coverage; low confidence margin | 0.5; 1 MAD | `LOW_COVERAGE_THRESHOLD` in `assess_input_quality.py`; `LOW_CONFIDENCE_MARGIN` in `post_processing/assess_output_quality.py` | Re-measure stars near the limit on a second night and count how often their verdict flips. |
| Between-session flag | significance above 3; amplitude at least 0.02 mag | `DEFAULT_LONG_TERM_SIGNIFICANCE_THRESHOLD`, `MINIMUM_LONG_TERM_AMPLITUDE_MAG` in `processing/variability_analyzer.py` | Count flags on stars a catalog calls constant over a multi-session target ([PHO-14](#pho-14)). |
| Period checks: alias rival share; agreeing nights | 90 percent of the best peak; more than half | `_RIVAL_ALIAS_POWER_SHARE`, `_AGREEING_SHARE` in `processing/period_checks.py` | Run `holdout_cycle` and `alias_check` on a star of known period observed on several nights. |
| Stars searched for a period | target plus 10 brightest | `MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH` in `batch.py` | A cost choice; time `search_periods_and_save` on your largest field. |
| Shuffles when a search is slow | 150 above 400,000 point-periods | `_REDUCED_SHUFFLE_COUNT`, `_SLOW_SEARCH_WORK_LIMIT` in `periodicity_search.py` | Repeat [PHO-12](#pho-12) at 150 shuffles. |
| Frequency grid cap; refined peaks | 20,000 points; 20 peaks, 3 coarse steps | `_MAXIMUM_SEARCH_GRID_POINTS`, `_CANDIDATE_PEAK_COUNT`, `_REFINE_HALF_WIDTH_COARSE_STEPS` in `periodicity_search.py` | Inject a known period into a long, multi-month light curve and check it is recovered. |
