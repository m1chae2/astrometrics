# Photometry Validation and Tuning

*Version 1.0 · 2026-10-15 · Status: current*

This file holds the evidence behind the photometry pipeline's tuned values: what was measured, on what data, with which library calls, and what came out. It is written for a reviewer who wants to repeat a measurement on their own camera and sky and tune the pipeline to match. The method itself (what each step does and why) is in the photometry READMEs under `astrometricslib/pipelines/photometry/`. Terms such as light curve, comparison ensemble, CV, MAD and AUC are defined in the [glossary](../../Glossary.md). The status labels (Measured, Partly measured, Design estimate) are defined in [index.md](index.md).

Every procedure below reads a target that the library already holds. A run of the photometry stage goes through the public entry point `Astrometrics().processing.process_target` <!-- impl: astrometricslib.api.processing.ProcessingPipelines.process_target -->, and its run-level checks (gates) are read back with `PhotometryQualitySummary.gate` <!-- impl: astrometricslib.models.quality.quality_summary.PipelineQualitySummaryBase.gate -->. Steps that need a lower-level function say so.

## Measured and partly measured values

### PHO-01

**The comparison ensemble keeps injected signals and removes shared changes (synthetic sessions)**

- **Claim:** Dividing by a fixed, vetted, inverse-variance weighted comparison ensemble keeps a 1% dip at its full depth through a steady rise in airmass, turns away a varying comparison candidate, and removes a frame-to-frame transparency change. Its scatter agrees with the propagated error.
- **Settings it supports:**
  - The method as a whole, run at its defaults. Code constants `BRIGHTEST_SKIPPED_FRACTION` = 0.02 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.BRIGHTEST_SKIPPED_FRACTION -->, `FAINTEST_BAND_FRACTION` = 0.30 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.FAINTEST_BAND_FRACTION -->, `EXCESS_SCATTER_SIGMA` = 3.0 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.EXCESS_SCATTER_SIGMA -->, `MINIMUM_COMPARISON_STARS` = 5 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MINIMUM_COMPARISON_STARS --> and `DEFAULT_MAXIMUM_COMPARISON_STARS` = 20 <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.DEFAULT_MAXIMUM_COMPARISON_STARS -->, all in `astrometricslib/pipelines/photometry/processing/comparison_ensemble.py` (changing them means editing the code; the cap is also the `maximum_comparison_stars` argument of `VariabilityAnalyzer` <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer -->). The test does not tune the individual values. Relation: sanity check only.
  - The choice to leave the ensemble airmass correction off (`ensemble_airmass_correction=False` on `VariabilityAnalyzer`) and never to fit a star's own flux against airmass. Relation: derived from this entry.
- **Status:** Partly measured. Synthetic sessions only; no real field has checked the current selection.
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
  1. For the end-to-end version, the test renders drifting FITS frames, runs `VariabilityAnalyzer.process` on them, and measures the dip the same way. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.process -->
  2. On real data, run the photometry stage and read the comparison set's record from the `comparison_ensemble` gate and from each star's `sessionSummaries` (`comparisonScatterMag`, `comparisonRejectedCount`).

- **Acceptance criterion:** The recovered depth is near 1.0 of the injected depth, and the ensemble scatter over the propagated error is near 1.
- **What to look at:** The recovered depth over the injected depth (1.0 is perfect; a value near 0.5 means the trend removal ate half the dip). Whether the varying candidate appears in `comparison_set.rejected_ids`. The ensemble scatter over the propagated error, in magnitudes (near 1 is good).
- **Our result:** Synthetic sessions, 36 frames at a 10 minute cadence (6 hours), airmass rising from 1 to 2, 25% extinction over the run.

  | Check | Result |
  | --- | --- |
  | 1%, 2 hour dip, from light curves (seeds 0 to 2) | recovered at 1.01 to 1.02% |
  | Same dip, from rendered pixel frames | recovered at 1.00% |
  | Earlier method on the same data: median of raw fluxes, quadratic airmass fit to each star's own flux | 0.51 to 0.53% from light curves, 0.30% from frames |
  | Comparison candidate carrying a 5% sinusoid | rejected from the set |
  | Comparison scatter against propagated error | equal within 20% |
  | 3% frame-to-frame transparency change | removed from every curve |

  In plain terms, the dip comes back to within 3% of its depth.
- **Verdict:** Not judged.
- **Limits:** Synthetic noise is white and Gaussian, and the recorded error equals the true noise; real fields have flat-field and centroid errors that the errors leave out (see [PHO-02](#pho-02)). The test asserts a tolerance of 10%; the exact recovered depths above were read from a run of the test, not asserted by it. The earlier-method figures come from code that no longer exists, so they cannot be re-run.
- **To tune for your setup:** If your fields have few stars, lower `MINIMUM_COMPARISON_STARS` only with care: the `comparison_ensemble` gate fails below it. If your brightest stars saturate often, raise `BRIGHTEST_SKIPPED_FRACTION`. Re-run the test file after any change.
- **Regenerate with:** `.venv/bin/pytest astrometricslib/pipelines/photometry/test/test_comparison_ensemble_injection.py` runs the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/test_comparison_ensemble_injection.py`.
- **Raw record:** None kept.

### PHO-02

**Comparison-star scatter against the predicted error on three real fields**

- **Claim:** On a clear night the comparison stars scatter only a little more than the CCD equation predicts, while a night with patchy cloud or tracking loss gives a ratio about ten times larger. A limit of 5 separates the two. The predicted per-point errors run about one fifth smaller than the real scatter.
- **Settings it supports:**
  - Code constant `MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED` = 5.0 in `astrometricslib/pipelines/photometry/post_processing/record_quality.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED -->
- **Status:** Partly measured. Three fields; the limit sits between them, but no false-positive rate was measured.
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
<!-- impl: astrometricslib.Astrometrics --> <!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.photometry_run_gates -->
  1. The `comparison_ensemble` detail quotes the ensemble scatter in magnitudes next to the scatter the errors predict. Their ratio is the number the limit judges.
  2. For a night with a large ratio, look at each session's `ensembleMedianFlux` over the pictures and at the faint stars' normalized scatter, to tell cloud or tracking loss from a bad comparison set.

- **Acceptance criterion:** The ratio of ensemble scatter to predicted error stays below the gate limit of 5. Near 1 means the errors describe the noise.
- **What to look at:** The ratio of measured to predicted ensemble scatter (no unit). Near 1 means the errors describe the noise; 1.2 to 2 is normal for real data with flat-field and centroid errors; much larger means something the ensemble cannot divide out. The `flux_uncertainty` gate's median per-point error, in magnitudes.
- **Our result:** ASI 533MM Pro data, 2026-10-11, with the measured gain of [PHO-03](#pho-03).

  | Field | Ratio of ensemble scatter to predicted | Notes |
  | --- | --- | --- |
  | M 13 (41 frames) | 1.27 (1.3) | ensemble scatter 0.0142 mag against 0.0112 mag predicted, so the predicted errors are about 21% too small; `flux_uncertainty` passed with a median per-point error of 0.0205 mag |
  | M 81 | 1.66 (1.7) | |
  | NGC 2403 | 18 | 0.0655 mag against 0.0036 mag; the comparison stars' brightness fell by 30% over 16 minutes and recovered (922, 636 and 946 ADU/s); faint stars' normalized fluxes scattered 0.08 to 0.09 mag at every brightness, and the brightest 50 stars were anti-correlated with the ensemble |

  The NGC 2403 pattern is patchy cloud or tracking loss, which dividing by the ensemble cannot remove. The gate had passed that run before the limit existed; the limit was then set between these three fields.
- **Verdict:** Not judged.
- **Limits:** Three fields from one camera. The gap between 1.3 and 1.7 on clear nights is explained by noise sources the CCD equation leaves out (flat-field residuals and centroid error), but nothing here shows how it splits between them. With the gain assumed to be 1 e-/ADU, `flux_uncertainty` is `not_checked` and quotes its error as a guide only.
- **To tune for your setup:** Collect the ratio on several nights you know were clear. Set `MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED` a comfortable margin above the clear-night values and below any known cloudy night. A camera without a measured gain gives ratios of the wrong size; measure the gain first ([PHO-03](#pho-03)).
- **Regenerate with:** No script; read from the run gates by hand on 2026-10-11. `astrometricslib/scripts/report_gate_limits.py` reports the spread of saved photometry values across all targets (`python -m astrometricslib.scripts.report_gate_limits`). Each run now also stores the ratio as the metric `photometry.comparison_scatter_over_predicted_worst` (worst session; <!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.worst_comparison_scatter_ratio -->) and the median per-point error as the value of `photometry.gate_flux_uncertainty`. Read them with `QualityStore.query(metric=...)` instead of `summary.gate(...)`.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### PHO-03

**Gain and read noise of the ASI 533MM Pro**

- **Claim:** In the stored 16-bit files the camera's gain at gain setting 0 is 0.783 electrons per ADU and its read noise is 3.8 electrons. A datasheet gain quoted for the sensor's own 14-bit ADU (3.6 e-/ADU) is wrong for these files.
- **Settings it supports:**
  - Config key `[Observatory.Camera.ZWO ASI533MM Pro] sensor_noise` in the configuration file: an entry for gain setting 0, offset 10, binning 1x1 holding `gain_e_per_adu` (0.783, kind `measured`) and `read_noise_e` (3.8, kind `measured`) (`astrometricslib/astrometrics.config.example.toml` ships it). The values are now keyed by camera setting: a frame at another gain, offset or binning does not use them. The pipeline reads them through the camera profile. Relation: derived from this entry. <!-- impl: astrometricslib.models.camera_profile.CameraProfile --> <!-- impl: astrometricslib.pipelines.photometry.pre_processing.detector_noise.resolve_detector_noise -->
- **Status:** Partly measured. One signal level; no full photon-transfer curve.
- **Data you need:** For your camera, at one gain setting, offset and sensor temperature: 15 or more pairs of flat frames taken back to back at one exposure (a signal near half of full scale), and 5 or more pairs of bias frames (zero-length exposures).
- **Procedure:** `astrometrics.processing.calibration.measure_sensor_noise(camera_id, gain, offset, binning)` runs these steps on the flats and bias frames in the calibration library (see [STK-35](stacking.md#stk-35); it reproduced these values on 2026-10-22). The steps, and the code that does them by hand with NumPy and Astropy, are:
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

  1. Write the results into the camera's section of the configuration file with `kind = "measured"` and a `source` sentence.

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The gain in electrons per stored ADU and its pair-to-pair spread. The read noise in electrons, compared with the datasheet value at the same gain setting.
- **Our result:** ASI 533MM Pro, 2026-10-11, gain setting 0, offset 10, 0.55 s flats, -10 C, central 1000 by 1000 pixels, two sets of 15 flat pairs at about 28,000 ADU: gain 0.783 and 0.782 e-/ADU, pair-to-pair spread 0.0004. Five bias pairs: read noise 4.88 ADU, which is 3.8 e-, the same as the ZWO datasheet at gain setting 0; a median-based estimate gives 3.3 e-. The sensor is 14-bit and the files are 16-bit in steps of 4, so the gain in stored ADU is a quarter of the value in sensor ADU. With the datasheet's 3.6 e-/ADU the error bars come out about 2 times too small.
- **Verdict:** Not judged.
- **Limits:** One signal level, so no photon-transfer curve was fitted. A flat panel that flickers between frames adds variance and lowers the gain, so 0.783 is more likely too low than too high. The values hold only at gain setting 0.
- **To tune for your setup:** Measure your own camera at the gain setting you observe with. If your files store sensor ADU scaled up (as here), use the gain in stored ADU. Then re-run [PHO-02](#pho-02): the ensemble ratio should move toward 1.
- **Regenerate with:** `astrometrics.processing.calibration.measure_sensor_noise(camera_id, gain, offset, binning)` (add `save=True` to write the camera profile). The 2026-10-11 values were measured by hand with the code above.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### PHO-04

**Frame alignment on real fields: rotation, corner displacement and centroid offsets**

- **Claim:** A field can turn enough during a night that a shift alone leaves far stars outside the centroid box's reach. Fitting a rotation and scale change brings them back. The corner displacement the fit reports is small on steady nights and near 2 pixels on a field that turned; the per-star offsets left after the fit are centroid noise and do not track the rotation.
- **Settings it supports:**
  - Code constants `MAXIMUM_CORNER_DISPLACEMENT_PX` = 1.0 <!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.MAXIMUM_CORNER_DISPLACEMENT_PX --> and `MAXIMUM_CENTROID_FALLBACK_FRACTION` = 0.20 <!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.MAXIMUM_CENTROID_FALLBACK_FRACTION --> in `astrometricslib/pipelines/photometry/post_processing/record_quality.py` (changing them means editing the code). Relation: sanity check only.
  - `ALIGNMENT_ANCHOR_REACH_PX` = 4.0 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_REACH_PX -->, `MAX_CENTROID_SHIFT_PX` = 1.5 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.MAX_CENTROID_SHIFT_PX --> and `ALIGNMENT_REFINEMENT_PASSES` = 3 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_REFINEMENT_PASSES --> in `astrometricslib/pipelines/photometry/pre_processing/frame_photometry.py` (changing them means editing the code). Relation: sanity check only.
  - The gate's choice to judge the corner displacement and not the per-star offset. Relation: derived from this entry.
- **Status:** Partly measured. Three fields; the limits are not set from a measured false-positive rate.
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

  1. For one session in detail, run the analyzer on the session's frames and read its summary (internal functions, not part of the public API; may change). `frame_paths` is the list of one session's FITS files.

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
  1. To compare reaches, change `ALIGNMENT_ANCHOR_REACH_PX` (or call `calculate_frame_alignment` directly on a frame and its anchors) and repeat step 2. An independent check fits the same rotation and scale to several hundred bright stars matched by hand. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.calculate_frame_alignment -->

- **Acceptance criterion:** A corner displacement under 1 pixel means a star stays on nearly the same pixels.
- **What to look at:** Corner displacement, in pixels (95th percentile over the pictures): under 1 pixel means a star stays on nearly the same pixels. Fallback share (0 to 1): the share of measurements whose own centroid was refused. Median and 95th-percentile per-star offset, in pixels: reported only, because centroid noise dominates it.
- **Our result:** ASI 533MM Pro, 2026-10-11.

  | Field | Corner displacement, 95th percentile | Notes |
  | --- | --- | --- |
  | M 13 | 0.2 px | turned 0.05 px at the corner over its night |
  | M 81 | 0.9 px | mostly the same small scale change in every picture (the star finder's centroids on the first picture against the re-centering box on later ones) |
  | NGC 2403 | 1.8 px (1.9 px worst picture) | the field turned 0.05° over the night |

  - Per-star offsets after the fit: 95th percentile 1.0 to 1.3 px and median 0.3 to 0.5 px on all three fields, unchanged by the rotation fit.
  - On NGC 2403 a shift alone left 12% of the measurements falling back to an off-star position; with the rotation fitted, 4%.
  - On M 81 the corner displacement was 0.9 px with the usual 1.5 px reach and 1.3 to 1.6 px with a reach of 3 to 4 px; an independent fit on 800 bright stars gave about 0.6 px. The wide first-pass box was pulled off by neighbors or the galaxy by several pixels on some M 81 stars; the median shift ignored them.
  - Before the gate judged the corner displacement, `registration_drift` failed M 13 on a 95th-percentile per-star offset of 1.13 px (limit 1). A replay on the frames showed centroid noise on faint and crowded stars, not rotation. M 13 now passes.
- **Verdict:** Not judged.
- **Limits:** Three fields and one camera. The 1 pixel and 20% limits are design estimates set with these values in view, not from a false-positive rate. The 0.6 px independent fit was done by hand.
- **To tune for your setup:** If your steady nights show a corner displacement near 1 pixel, the scale term between the two centroid methods may dominate; check it on a night with no rotation before raising `MAXIMUM_CORNER_DISPLACEMENT_PX`. For larger star images (FWHM above about 4 px), the 11 px re-centering box is too small; widen `CENTROID_BOX_HALF_WIDTH_PX` and re-run this procedure.
- **Regenerate with:** No script; measured by hand on 2026-10-11. Each run now also stores the 95th-percentile corner displacement and the fallback share (worst session) as `photometry.corner_displacement_p95_worst_px` and `photometry.centroid_fallback_worst_fraction`.
- **Guarded by tests:** The gate logic is pinned by `astrometricslib/pipelines/photometry/test/post_processing/test_record_quality.py` and `astrometricslib/pipelines/photometry/test/pre_processing/test_centroid_shift_summary.py`.
- **Raw record:** None kept.

### PHO-05

**Alignment anchors chosen by signal-to-noise on a sparse field (synthetic)**

- **Claim:** In a sparse field whose detections are mostly noise peaks, choosing anchors by peak signal-to-noise recovers the frame's shift to 0.05 px, while the older rule (detections ranked 50 to 100) does not.
- **Settings it supports:**
  - Code constants in `astrometricslib/pipelines/photometry/pre_processing/frame_photometry.py` (changing them means editing the code): `ALIGNMENT_ANCHOR_MINIMUM_PEAK_SNR` = 20.0 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_MINIMUM_PEAK_SNR -->, `ALIGNMENT_ANCHOR_COUNT` = 50 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_COUNT -->, `ALIGNMENT_ANCHOR_SKIPPED_BRIGHTEST_FRACTION` = 0.05 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_SKIPPED_BRIGHTEST_FRACTION --> and `ALIGNMENT_ANCHOR_MINIMUM_QUALIFYING` = 10 <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.ALIGNMENT_ANCHOR_MINIMUM_QUALIFYING -->. The test supports the rule, not the exact values. Relation: sanity check only.
- **Status:** Partly measured. One synthetic case.
- **Data you need:** None from the sky. To repeat it on real data, you need two frames of a sparse field (a few tens of stars) with a known small offset between them, for example from a deliberate mount nudge.
- **Procedure:**
  1. Render a reference frame and a shifted frame of 12 stars on a noisy sky (`make_photometry_frame` in `astrometricslib/test/synthetic/`). <!-- impl: astrometricslib.test.synthetic.make_photometry_frame -->
  2. Detect sources on the reference frame, choose anchors with `select_alignment_anchors`, and measure the shift with `calculate_frame_alignment` (internal functions, not part of the public API; may change). Repeat with the detections ranked 50 to 100 for the older rule. <!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.select_alignment_anchors -->
  3. On real data, read `VariabilityAnalyzer.alignment_anchor_rule` and `alignment_anchor_count` after `process`, and compare the measured shift with the known offset.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The measured shift against the injected one, in pixels; the anchor rule used and the anchor count.
- **Our result:** Synthetic sparse field, 12 real stars, more than 50 detections. Injected shift (2.70, -1.30) px. The signal-to-noise rule kept the 12 stars and measured (2.70, -1.30) px; the older rule measured (-4.7, 2.1) px.
- **Verdict:** Not judged.
- **Limits:** One synthetic field; real sparse fields have hot pixels and gradients the generator does not add.
- **To tune for your setup:** If a real sparse field falls back to the older rule (the `registration_drift` detail names it), lower `ALIGNMENT_ANCHOR_MINIMUM_PEAK_SNR` and re-run this test and [PHO-04](#pho-04).
- **Regenerate with:** No script; the test runs the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/pre_processing/test_frame_photometry.py` (`test_sparse_field_anchors_recover_an_injected_shift_where_the_old_rule_fails`).
- **Raw record:** None kept.

### PHO-06

**Variability indices and the candidate rule on synthetic fields with known variables**

- **Claim:** Measuring each star against the field's noise model separates injected variables from constant stars far better than the raw CV (AUC 0.84). The chosen score (chi-square and excess scatter, with Stetson J as a veto) reaches an AUC of 0.94, flags no constant star, and finds 64% of injected variables of 0.01 to 0.3 mag. Using J as a veto keeps its protection against isolated bad points without lowering the ranking.
- **Settings it supports:**
  - Code constants in `astrometricslib/pipelines/photometry/processing/variability_indices.py` (changing them means editing the code): `EXCESS_SCATTER_THRESHOLD` = 1.5 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.EXCESS_SCATTER_THRESHOLD -->, `CALIBRATION_SIGMAS` = 2.326 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.CALIBRATION_SIGMAS -->, `MINIMUM_STARS_PER_NOISE_BIN` = 10 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MINIMUM_STARS_PER_NOISE_BIN -->, `MAXIMUM_NOISE_BINS` = 10 <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MAXIMUM_NOISE_BINS --> and `MINIMUM_STARS_FOR_NOISE_MODEL` = 20. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MINIMUM_STARS_FOR_NOISE_MODEL -->
  - The form of the score itself. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.variability_score -->
- **Status:** Partly measured. Synthetic fields only.
- **Data you need:** None from the sky. The real-sky counterpart is [PHO-08](#pho-08) and [PHO-09](#pho-09).
- **Procedure:**
  1. Build a field with `make_variability_field` (internal test helper, not part of the public API; may change). The field has 400 constant stars spread evenly over 6 magnitudes and 48 frames. 5% of the stars (20) carry a sinusoid (two thirds) or an eclipsing dip (one third) with an amplitude drawn log-uniformly from 0.01 to 0.3 mag. <!-- impl: astrometricslib.test.synthetic.variability_field.make_variability_field -->
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
scores = [
    (star.photometry.variability_score, truth)
    for star, truth in zip(field.stars, field.is_variable)
    if star.photometry.variability_score is not None
]
print(area_under_curve([s for s, t in scores if t], [s for s, t in scores if not t]))
```
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer.identify_variable_stars --> <!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.area_under_curve -->
  1. Repeat over seeds 100 to 123 and average. The test module's `measure_auc_table` and `measure_flag_rates` do this. <!-- impl: astrometricslib.pipelines.photometry.test.test_variability_injection.measure_auc_table --> <!-- impl: astrometricslib.pipelines.photometry.test.test_variability_injection.measure_flag_rates -->

- **Acceptance criterion:** The share of constant stars flagged is near 0.
- **What to look at:** The AUC of each index (the chance a random injected variable outranks a random constant star; 0.5 is chance, 1 is perfect). The share of constant stars flagged (should be near 0) and of injected variables flagged, by amplitude.
- **Our result:** 24 synthetic fields (seeds 100 to 123), each 400 stars over 6 magnitudes with scatter from 0.0035 mag at the bright end (set by an unrecorded 0.3% systematic term) to 0.06 mag at the faint end (photon and background noise), 48 frames. Re-run on 2026-10-15 with identical results.

  | Index | AUC (mean, sd between fields) |
  | --- | --- |
  | Coefficient of variation | 0.835, 0.039 |
  | Reduced chi-square against the propagated errors | 0.809, 0.066 |
  | Reduced chi-square against the scaled errors | 0.940, 0.044 |
  | Stetson J (scaled errors) | 0.879, 0.042 |
  | Excess scatter | 0.942, 0.042 |
  | Smallest ratio of the three indices to their thresholds | 0.880, 0.043 |
  | Chosen score (chi-square and excess scatter, J as a veto) | 0.940, 0.044 |

  - Dividing by the noise model matters more than the choice of index: chi-square against the propagated errors ranks below the CV, because those errors are too small for the bright stars; against the scaled errors it ranks with excess scatter.
  - At the thresholds, the rule flagged none of the 9,120 constant stars and 64% of the injected variables: 90% of those of 0.1 to 0.3 mag, 63% of 0.03 to 0.1 mag, 40% of 0.01 to 0.03 mag. It flagged 76% of sinusoids and 38% of eclipsing dips (a narrow eclipse puts few points in the dip).
  - In 8 fields where 5% of the points scatter five times more than the others, the rule with J flagged none of 3,040 constant stars, and the rule without J flagged 24 (0.8%).
- **Verdict:** Not judged.
- **Limits:** Synthetic data, not the sky. The systematic term and the outlier share are the generator's choices; a real field has other error sources, so these AUCs describe the method on a field whose noise is understood. `measure_flag_rates` counts a star as flagged when its score is above 1 and does not apply the q-value test. The per-amplitude and per-shape shares and the 8-field outlier comparison are not printed by the module; they were computed by hand from the same runs. The generator's sinusoid amplitude is a semi-amplitude (peak to peak twice that), unlike [PHO-09](#pho-09), whose depths are peak to peak.
- **To tune for your setup:** Change the generator's `systematic_fraction`, `frame_count` and `outlier_fraction` to match your own light curves (compare its scatter curve with your run's `noiseModelCurve`), then re-run the table. Raise `EXCESS_SCATTER_THRESHOLD` or `CALIBRATION_SIGMAS` if constant stars start to pass.
- **Regenerate with:** `.venv/bin/python -m astrometricslib.pipelines.photometry.test.test_variability_injection` prints the table.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/test_variability_injection.py`.
- **Raw record:** None kept.

### PHO-07

**False-alarm probabilities are uniform for constant stars (synthetic)**

- **Claim:** For constant stars with correct, independent Gaussian errors, the chi-square false-alarm probability is uniform: 1% of stars fall below 0.01. The Benjamini and Hochberg q-value then keeps a field of 300 constant stars to at most 3 candidates.
- **Settings it supports:**
  - Code constant `FALSE_DISCOVERY_LEVEL` = 0.01 in `astrometricslib/pipelines/photometry/processing/variability_indices.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.FALSE_DISCOVERY_LEVEL -->
  - The functions behind it. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.false_alarm_probability --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.benjamini_hochberg --> <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.field_error_scale -->
- **Status:** Partly measured. Synthetic white noise only; the effect of red noise is not measured.
- **Data you need:** None from the sky. On real data, a field of 200 or more stars whose catalogs list few variables.
- **Procedure:**
  1. Draw 4,000 light curves of 40 points with 1% Gaussian noise and errors equal to that noise. Compute each one's probability (internal functions, not part of the public API; may change).

```python
import numpy as np
from astrometricslib.pipelines.photometry.processing import variability_indices as vi

generator = np.random.default_rng(41)
probabilities = [
    vi.false_alarm_probability(
        vi.reduced_chi_square(1.0 + generator.normal(0.0, 0.01, 40), np.full(40, 0.01)), points=40
    )
    for _ in range(4000)
]
print(np.mean(np.asarray(probabilities) < 0.01))
```
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.reduced_chi_square -->
  1. For the field test, build 300 such stars and run `assess_variability` on them; count `candidates`. <!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.assess_variability -->
  2. On real data, take the run's `falseAlarmProbability` values for stars no catalog lists and plot their histogram; it should be flat if the errors are right and the noise is white.

- **Acceptance criterion:** The share of probabilities below 0.01 is near 0.01, and the share below 0.5 is near 0.5.
- **What to look at:** The share of probabilities below 0.01 (should be near 0.01) and below 0.5 (near 0.5). The candidate count in a constant field.
- **Our result:** 4,000 synthetic light curves: 1.0% below 0.01. Field of 300 constant stars: measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** Both numbers assume noise independent from point to point. Slow systematics (red noise) make the probabilities too small, and no test measures how much. The 0.01 level is a design estimate, not tuned.
- **To tune for your setup:** If your unlisted stars' probabilities pile up near 0, your errors are too small or your noise is red; fix the gain ([PHO-03](#pho-03)) before loosening anything. Lower `FALSE_DISCOVERY_LEVEL` for fewer false alarms per field at the cost of missed variables.
- **Regenerate with:** No script; the tests run the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/processing/test_variability_indices.py` (`test_constant_stars_with_correct_errors_give_uniform_probabilities`, `test_a_field_of_constant_stars_flags_nobody_at_the_false_discovery_level`). The tests assert at most 3 candidates in a field of 300 constant stars.
- **Raw record:** None kept.

### PHO-08

**The candidate rule on a real field (M 13)**

- **Claim:** On a real globular-cluster field the variability score ranks cataloged variables above other stars only weakly (AUC against catalog labels 0.61, against 0.94 on synthetic fields), and the rule accepts 5 of 37 listed variables and 11 of 403 unlisted stars. The limit is the detection threshold, not the statistic ([PHO-09](#pho-09)).
- **Settings it supports:**
  - Code constant `MINIMUM_DISCRIMINATION_AUC` = 0.7 in `astrometricslib/pipelines/photometry/post_processing/variability_skill.py` (changing it means editing the code). Relation: challenged by this entry. <!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.MINIMUM_DISCRIMINATION_AUC -->
  - The candidate rule's `FALSE_DISCOVERY_LEVEL` = 0.01 (see [PHO-07](#pho-07); changing it means editing the code). Relation: sanity check only.
- **Status:** Partly measured. One field.
- **Data you need:** One session of 30 or more frames of a field. The field needs at least 10 stars that SIMBAD, Gaia DR3 or VSX list as variable and 30 or more that they do not, with the catalog answers stored on the stars (`backfill_variable_star_flags.py` fills in Gaia DR3 and VSX).
- **Procedure:**
  1. Store the catalogs' variability answers on the stars, then run the photometry stage.

```shell
python -m astrometricslib.scripts.backfill_variable_star_flags --target "M 13" --apply
```

  1. Read the `variability_discrimination` gate, then compute the AUC of each index by hand from the saved stars.

```python
from astrometricslib import Astrometrics
from astrometricslib.models.known_variability import KnownVariability
from astrometricslib.pipelines.photometry.post_processing.variability_skill import (
    area_under_curve,
    required_auc,
)

astrometrics = Astrometrics()
result = astrometrics.processing.process_target("M 13", stages=["photometry"])
astrometrics.targets.save()
print(result.quality.photometry.gate("variability_discrimination"))
stars = astrometrics.stars.query(target_id="M 13", has_photometry=True, detail="objects", limit=None).objects
scored = [s for s in stars if s.photometry and s.photometry.variability_score is not None]
listed = [s for s in scored if s.known_variability == KnownVariability.KNOWN_VARIABLE]
unlisted = [s for s in scored if s.known_variability == KnownVariability.NOT_LISTED]
for name in ("variability_score", "reduced_chi_square", "stetson_j", "coefficient_of_variation"):
    auc = area_under_curve(
        [getattr(s.photometry, name) for s in listed], [getattr(s.photometry, name) for s in unlisted]
    )
    print(name, auc)
print("beats chance above", required_auc(len(listed), len(unlisted)))
```
<!-- impl: astrometricslib.api.stars.StellarCatalog.query --> <!-- impl: astrometricslib.models.known_variability.KnownVariability --> <!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.required_auc -->
  1. Count the candidates in the photometry result's `variableCandidates` and split them by catalog status.

- **Acceptance criterion:** An AUC against catalog labels of at least 0.7 (the `variability_discrimination` gate limit, `MINIMUM_DISCRIMINATION_AUC`).
- **What to look at:** The AUC of the score and of each index (0.5 is chance); the AUC that beats chance at the 5% level for your group sizes; the share of listed and unlisted stars the rule accepts.
- **Our result:** M 13, ASI 533MM, 41 frames, 2026-10-11. 458 stars with indices; 37 listed as variable by SIMBAD, Gaia DR3 or VSX; 403 not listed.

  | Statistic | AUC against catalog labels, listed against unlisted |
  | --- | --- |
  | Variability score | 0.61 |
  | False-alarm probability | 0.61 |
  | Reduced chi-square | 0.61 |
  | Stetson J | 0.57 |
  | CV (earlier flag) | 0.41 |
  | Least AUC that beats chance at 5% | 0.58 |

  The rule accepted 16 stars: 5 listed variables and 11 not listed. Listed variables are 8% of the field and 31% of the accepted stars; the rule found 5 of 37 listed variables (14%) and 11 of 403 unlisted stars (2.7%). The `variability_discrimination` gate failed (0.61 is below 0.7).
- **Verdict:** Fail. The AUC against catalog labels was 0.61, below the limit of 0.7, and the `variability_discrimination` gate failed.
- **Limits:** One field, crowded at its core, where the photometry is poor. Most listed variables in M 13 are small-amplitude. A catalog listing is not proof of variability at this camera's precision, and some unlisted stars that the rule accepts may be real variables.
- **To tune for your setup:** Run this on two or three of your own fields. If the AUC stays near 0.6 with a good noise model, the detection threshold limits you ([PHO-09](#pho-09)); improving the photometry helps more than moving the thresholds. Whether to loosen the rule is a choice between missed signals and false alarms per field.
- **Regenerate with:** No script for the per-index AUCs; computed by hand on 2026-10-11. The gate itself runs in every photometry run (`photometry_run_gates`). `astrometricslib/scripts/backfill_variable_star_flags.py` stores the catalog answers (`python -m astrometricslib.scripts.backfill_variable_star_flags --limit 2000`, then `--apply`).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### PHO-09

**Smallest signal the candidate rule finds on real light curves (injection on M 13)**

- **Claim:** On M 13 the rule flags half of the injected signals at about 0.04 mag peak to peak on the brightest quarter of stars, and only at 0.16 mag on the faintest quarter. It finds nothing below 0.02 mag and flags none of the untouched stars.
- **Settings it supports:**
  - The candidate rule as a whole: `FALSE_DISCOVERY_LEVEL` = 0.01, `EXCESS_SCATTER_THRESHOLD` = 1.5 and `CALIBRATION_SIGMAS` = 2.326 (see [PHO-06](#pho-06)). It gives the measured counterpart of the `detectable_amplitude` gate's estimate, whose limit is code constant `MAXIMUM_DETECTABLE_AMPLITUDE_MAG` = 0.3 in `astrometricslib/pipelines/photometry/post_processing/record_quality.py`. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.MAXIMUM_DETECTABLE_AMPLITUDE_MAG -->
- **Status:** Partly measured. One field; the signals are added to light curves, not pixels.
- **Data you need:** A target already run through the photometry stage, with at least 60 stars whose light curves have 25 or more points and stored catalog answers ([PHO-08](#pho-08), step 1).
- **Procedure:**
  1. Run the script on your target. It reads the stars read-only and leaves out stars the rule already flags and stars the catalogs list as variable. In each of many draws it multiplies about 8% of the field's light curves (and their errors) by a sinusoid or a box transit of one depth from 0.005 to 0.16 mag. It then runs the candidate rule on the whole field.

```shell
python -m astrometricslib.scripts.validate_photometry_injection --target "M 13" --realizations 80 --seed 1 --json m13_injection.json
```

  1. The same steps from Python:

```python
from astrometricslib.scripts.validate_photometry_injection import format_report, load_field, run_injection

stars, known_variable_ids = load_field("M 13", minimum_points=25)
result = run_injection(stars, known_variable_ids, realizations=80, seed=1)
print(format_report(result, "M 13"))
```
<!-- impl: astrometricslib.scripts.validate_photometry_injection.run_injection --> <!-- impl: astrometricslib.scripts.validate_photometry_injection.load_field --> <!-- impl: astrometricslib.scripts.validate_photometry_injection.inject_signal -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The completeness (share of injected stars flagged) by depth and brightness quarter; the depth at which it reaches 50%; the false-positive rate (the share of untouched stars flagged), with its 68% interval.
- **Our result:** M 13, 2026-10-11, 80 draws, seed 1, 408 stars able to host a signal. Brightest quarter: flagged half the time at about 0.04 mag (85% of sinusoids and 84% of transits at 0.04 mag). Faintest quarter: flagged that often only at 0.16 mag (82% of sinusoids, 69% of transits). Nothing found below 0.02 mag at any brightness. 0 of 29,760 untouched stars flagged. Most cataloged variables in a field like this vary by less than 0.04 mag, which accounts for finding 5 of 37 in [PHO-08](#pho-08). The counts were saved outside the repository (`~/astrometrics_db_backups/20261010_validation_step0/photometry_injection_m13.json`).
- **Verdict:** Not judged.
- **Limits:** The injection is made on the light curves, so it tests the candidate rule and the noise of the photometry, not the aperture photometry or the comparison-star normalization. The ensemble does not respond to an injected signal, as it would to a real variable among the comparison stars. Hosts are the field's unflagged stars, and the injected stars shift the field's noise model and thresholds slightly. Depths are peak to peak for the sinusoid and the drop for the transit. One field.
- **To tune for your setup:** Run the script on your own targets. If the 50% depth is well above the variables you care about, improve the photometry (gain, flats, aperture) before moving the thresholds; then re-run. If untouched stars start to be flagged after a change, it went too far.
- **Regenerate with:** `astrometricslib/scripts/validate_photometry_injection.py` (`python -m astrometricslib.scripts.validate_photometry_injection --target "M 13"`; options `--realizations`, `--seed`, `--minimum-points`, `--json`).
- **Guarded by tests:** `astrometricslib/scripts/test/test_validate_photometry_injection.py`.
- **Raw record:** Saved outside the repository: `~/astrometrics_db_backups/20261010_validation_step0/photometry_injection_m13.json`.

### PHO-10

**The CV-only cutoff against the catalogs, over the whole library**

- **Claim:** The fallback rule (flag a star whose CV is above the field's median plus 7.4 MAD) flags about as large a share of unlisted stars as of cataloged variables, and the CV separates the two no better than chance. The multiplier is not what limits the search; the photometry's own scatter is.
- **Settings it supports:**
  - Code constant `DEFAULT_VARIABILITY_SIGMA_THRESHOLD` = 7.4 in `astrometricslib/pipelines/photometry/processing/variability_analyzer.py` (changing it means editing the code). <!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.DEFAULT_VARIABILITY_SIGMA_THRESHOLD --> It also bears on the low-confidence margin and the `detectable_amplitude` fallback, which use the same cutoff. Relation: challenged by this entry.
- **Status:** Measured. 46,259 stars with light curves in 32 targets of the library.
- **Data you need:** A library of many targets with light curves (100 or more stars per target with 8 or more points) and stored catalog answers ([PHO-08](#pho-08), step 1).
- **Procedure:**
  1. Run the script. It reads every star's stored CV, builds the cutoff per target for each multiplier from 3 to 16 (including 7.4), and counts the stars of each catalog status above it. It also gives the AUC of CV between listed and unlisted stars, overall and between stars of similar brightness in the same target.

```shell
python -m astrometricslib.scripts.measure_variability_cutoff --output cutoff.json
```

  1. The cutoff for one field, from Python (internal function, not part of the public API; may change):

```python
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    DEFAULT_VARIABILITY_SIGMA_THRESHOLD,
    adaptive_cv_cutoff,
)

cvs = [0.01, 0.012, 0.015, 0.02, 0.05]  # the field's CVs
print(adaptive_cv_cutoff(cvs, DEFAULT_VARIABILITY_SIGMA_THRESHOLD).cutoff)
```
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.adaptive_cv_cutoff --> <!-- impl: astrometricslib.scripts.measure_variability_cutoff.evaluate --> <!-- impl: astrometricslib.scripts.measure_variability_cutoff.measure_separation -->
  1. For the scatter per target, run `report_gate_limits.py`, which reports the spread of each target's `lightCurveScatterRmsMag`.

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** For each multiplier, the share of cataloged variables flagged (the cutoff's completeness, or recall) against the share of unlisted stars flagged (an upper bound on its false-positive rate). The brightness-matched AUC against catalog labels (0.5 is chance).
- **Our result:** The library on 2026-10-09: 46,259 stars with light curves in 32 targets, 2,374 listed as variable by SIMBAD, Gaia DR3 or VSX. At 7.4 the cutoff flagged 5.2% of all stars, 3.8% of the cataloged variables, and 5.2% of unlisted stars. The brightness-matched AUC was 0.46, including for strong-amplitude types. The median CV of a light curve was 8% (about 0.08 mag) even among the brightest fifth of stars. Per target, the recorded median scatter ranged from 0.009 to 0.9 mag, with a median over targets of 0.29 mag. The multiplier was chosen to flag about the top 3% of stars; it flags 5.2%.
- **Verdict:** Not judged.
- **Limits:** The stored CV is a within-session scatter while the script builds the cutoff per target. Most light curves in the library predate the current comparison ensemble. The median CV among the brightest fifth and the per-target scatter range are not printed by the script; they were read from the same database by hand.
- **To tune for your setup:** Run the script on your library. If the AUC is near 0.5, changing the multiplier will not help; improve the photometry. If it is well above 0.5, pick the multiplier from the table at the completeness (recall) and false-positive rate you want.
- **Regenerate with:** `astrometricslib/scripts/measure_variability_cutoff.py` (`python -m astrometricslib.scripts.measure_variability_cutoff`; options `--minimum-stars`, `--minimum-points`, `--output`); `astrometricslib/scripts/report_gate_limits.py` for the per-target scatter.
- **Guarded by tests:** `astrometricslib/scripts/test/test_measure_variability_cutoff.py`.
- **Raw record:** None kept.

### PHO-11

**Block shuffling keeps period-search false alarms honest under red noise (synthetic)**

- **Claim:** Shuffling single points destroys slow correlated noise, so pure red noise looks significant almost every time. Shuffling blocks of three correlation lengths keeps the false-positive rate at or below its stated level.
- **Settings it supports:**
  - Code constant `_BLOCK_LENGTH_IN_CORRELATION_LENGTHS` = 3 in `astrometricslib/pipelines/photometry/processing/periodicity_search.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._BLOCK_LENGTH_IN_CORRELATION_LENGTHS -->
  - Code constants `_CORRELATION_GONE_BELOW` = 0.2 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._CORRELATION_GONE_BELOW --> and `_LONGEST_BLOCK_FRACTION` = 0.25 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._LONGEST_BLOCK_FRACTION --> in the same file (changing them means editing the code). Relation: sanity check only.
- **Status:** Partly measured. Simulated red noise only.
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
  1. Pass `block_length=1` to get the plain point shuffle, and compare.

- **Acceptance criterion:** The share of pure-noise curves called significant at the 5% level is near 5%. At or below it is the safe side.
- **What to look at:** The share of pure-noise curves called significant at the 5% level; it should be near 5%, and at or below it is the safe side.
- **Our result:** 60 simulated AR(1) light curves per case, 99 noise-only versions each, share called significant at 5%:

  | Block length | 1 lag | 2 lags | 3 lags | 4 lags |
  | --- | --- | --- | --- | --- |
  | `phi` = 0.70 | 28% | 7% | 2% | 2% |
  | `phi` = 0.95 | 37% | 10% | 3% | 2% |

  A plain point shuffle called 97% (`phi` = 0.70) and 100% (`phi` = 0.95) significant. Three lags is slightly conservative, which is the safe side; one or two lags is too permissive.
- **Verdict:** Pass. At three correlation lengths the share called significant was 2% to 3%, at or below 5%.
- **Limits:** Simulated AR(1) noise only. The table was recorded in the code comment of `periodicity_search.py`; the tests check only bounds (point shuffle at least 70% at `phi` = 0.95; block shuffle at most 20 and 30% with 30 simulations). The photometry README quotes "about 5% to 10%" for the block shuffle, which matches the 2-lag column, not the 3 lags the code uses.
- **To tune for your setup:** If constant stars in your data get "possible" verdicts more often than 5% of the time, raise `_BLOCK_LENGTH_IN_CORRELATION_LENGTHS` and re-run the test.
- **Regenerate with:** No script reproduces the table.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/processing/test_periodicity_null.py` (bounds only).
- **Raw record:** None kept.

### PHO-12

**Period and dip searches on pure noise and on injected signals (synthetic)**

- **Claim:** On white noise, neither search calls a light curve "detected" more often than its 1% cutoff allows. A 0.4 day cycle of 1% amplitude in 1% noise, and repeating dips of a few percent, are found.
- **Settings it supports:**
  - code constant `_FALSE_ALARM_FOR_DETECTION` = 0.01 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._FALSE_ALARM_FOR_DETECTION --> in `astrometricslib/pipelines/photometry/processing/periodicity_search.py` (changing it means editing the code). Relation: confirmed by this entry.
  - code constant `_FALSE_ALARM_FOR_POSSIBLE` = 0.05 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._FALSE_ALARM_FOR_POSSIBLE --> in `astrometricslib/pipelines/photometry/processing/periodicity_search.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `_SHUFFLE_COUNT` = 300 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._SHUFFLE_COUNT --> in `astrometricslib/pipelines/photometry/processing/periodicity_search.py` (changing it means editing the code). Relation: sanity check only.
  - code constant `_CYCLES_FOR_DETECTION` = 3.0 <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search._CYCLES_FOR_DETECTION --> in `astrometricslib/pipelines/photometry/processing/periodicity_search.py` (changing it means editing the code). Relation: sanity check only.
- **Status:** Partly measured. Synthetic white noise only.
- **Data you need:** None from the sky. On real data, a star with a known period and amplitude observed on three or more nights (an eclipsing binary or a short-period pulsator).
- **Procedure:**
  1. Run the script's period checks (it also runs the spectral checks; `--skip-standards` leaves out the real-star part). The noise check uses 200 points over 2.5 days with 1% Gaussian noise.

```shell
python -m astrometricslib.scripts.validate_spectral_and_period_analysis --trials 100 --skip-standards
```

  1. The period part alone, from Python:

```python
from astrometricslib.scripts.validate_spectral_and_period_analysis import validate_period_searches

passed = validate_period_searches(25)
```
<!-- impl: astrometricslib.scripts.validate_spectral_and_period_analysis.validate_period_searches --> <!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.box_search -->
  1. On a real star, use the public `Astrometrics().stars.analyze_periodicity(star_id)` and compare the saved period and verdict with the known ones. <!-- impl: astrometricslib.api.stars.StellarCatalog.analyze_periodicity -->

- **Acceptance criterion:** The share of pure-noise curves called "detected" stays near 1%. The script fails above 3%.
- **What to look at:** The share of pure-noise curves called "detected" (should stay near 1%; the script fails above 3%). The share of injected signals detected at each amplitude or depth.
- **Our result:** Run for this register on 2026-10-15, `validate_period_searches(25)` (25 light curves per case, 200 points over 2.5 days, 1% Gaussian noise, 300 shuffles per search; about 6 minutes).

  | Case | Share called "detected" |
  | --- | --- |
  | Cycle search, pure noise | 0 of 25 |
  | Dip search, pure noise | 0 of 25 |
  | 0.4 day sinusoid, semi-amplitude 1, 2 and 4% | 100% each |
  | 0.04 day dips every 0.5 day, depth 2, 4 and 8% | 100% each |

  The script's noise checks passed. Before this run, no result of the period part had been recorded.
- **Verdict:** Pass. 0 of 25 pure-noise curves were called detected in each search, and the script's noise checks passed.
- **Limits:** White noise and a regular cadence only; real light curves have red noise ([PHO-11](#pho-11)) and gaps between nights. With 25 trials per case a rate of 0 means fewer than about 4% at 68% confidence. The hold-out and alias checks and the family-wise correction are not part of this script.
- **To tune for your setup:** Change the cadence, span and noise in `validate_period_searches` to match your own light curves and re-run. If noise is called detected too often, raise the shuffle count or tighten `_FALSE_ALARM_FOR_DETECTION`.
- **Regenerate with:** `astrometricslib/scripts/validate_spectral_and_period_analysis.py` (`python -m astrometricslib.scripts.validate_spectral_and_period_analysis`; options `--trials`, `--skip-standards`).
- **Guarded by tests:** Fixed cases in `astrometricslib/pipelines/photometry/test/processing/test_periodicity_search.py`.
- **Raw record:** None kept.

### PHO-13

**Scatter of constant stars with an earlier median ensemble (NGC 2244)**

- **Claim:** With an earlier ensemble (the median of 200 stars ranked 100th to 300th by brightness), constant stars on a 3-hour NGC 2244 sequence scattered by less than 0.012 mag. The current comparison set has not been measured on this field.
- **Settings it supports:** None of the current values; it is kept as the only real-field scatter figure for a differential ensemble on this camera. The current selection is `select_comparison_set`. <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.select_comparison_set -->
- **Status:** Partly measured. Earlier method; not repeated with the current comparison set.
- **Data you need:** One session of 25 or more frames over 2 or more hours of an open cluster with many stars of similar brightness.
- **Procedure:**
  1. Run the photometry stage on the target with the current code.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
result = astrometrics.processing.process_target(
    "NGC 2244", stages=["photometry"], photometry={"filter_type": "Luminance"}
)
print(result.quality.photometry.gate("comparison_ensemble").detail)
```

  1. Read the comparison scatter in magnitudes from the gate detail, and the scatter of the field's constant stars from the run's `noiseModelCurve` (`result.quality.photometry.photometry_metrics.noise_model_curve`). <!-- impl: astrometricslib.models.quality.quality_summary.PhotometryQualitySummary -->

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The scatter of non-variable stars, in magnitudes, at each brightness.
- **Our result:** NGC 2244 (DS-NGC2244), 29 frames, 10,800 s total exposure: non-variable stars scattered by less than 0.012 mag (about 1.1%) with the earlier median ensemble. Date of measurement not recorded.
- **Verdict:** Not judged.
- **Limits:** The method that produced it no longer exists. The figure is evidence about the data and the shape of the method, not about current performance.
- **To tune for your setup:** Use [PHO-02](#pho-02) for the current set.
- **Regenerate with:** `astrometricslib/scripts/run_empirical_validation.py` runs the photometry stage on NGC 2244, M 81 and NGC 2903 (`python -m astrometricslib.scripts.run_empirical_validation --camera "ZWO ASI 533MM Pro"`); it does not print the scatter, which was read by hand.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### PHO-14

**Cross-session star matching on an 8-session target (M 81)**

- **Claim:** Tracking each session on its own and then matching stars across sessions by sky position gives correct per-session measurements and one light curve per star. A repeat run gives the same result.
- **Settings it supports:**
  - The `tolerance_arcsec` argument of `_match_and_merge_across_sessions` (default 5.0 arcsec, in `astrometricslib/pipelines/photometry/batch.py`; changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.photometry.batch._match_and_merge_across_sessions --> <!-- impl: astrometricslib.pipelines.photometry.batch._merge_light_curves -->
- **Status:** Partly measured. One target; the match radius was not varied.
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

  1. Count the distinct stars before and after matching, and check that a second run adds, removes or changes no rows.
  2. Check the `session_plate_solve` gate: a session that cannot be solved is left out of matching.

- **Acceptance criterion:** The share of frames with a zero flux for a star is near 0.
- **What to look at:** The number of merges; whether a repeat run is identical; the share of frames with a zero flux for a star (should be near 0); whether false alarms fall on the faintest stars, as noise predicts.
- **Our result:** M 81 (DS-M81), 258 frames in 8 sessions (2023-05 to 2026-05). 11,392 session-scoped stars matched down to 8,422 (2,970 merges); a repeat run added, removed or changed 0 rows. One session was left out because one of its frames referenced another target's stack. Before per-session tracking, 85 to 94% of frames read zero flux for a star, and 75% of the brightest fifth of stars were flagged variable by the earlier CV flag. After per-session tracking, 223 of 258 frames were processed. The brightest fifth's flag rate fell to 14%, and the faintest fifth's was 46%, close to the single-session NGC 2903 (DS-NGC2903) baseline of 47%. Distinct stars rose from 1,353 to 11,392 before matching.
- **Verdict:** Not judged.
- **Limits:** One target. The flag rates use the earlier CV-only flag, not the current candidate rule. Dates of the runs were not recorded.
- **To tune for your setup:** If your plate solutions are poorer than about 1 arcsec, or your fields are crowded, change `tolerance_arcsec` and repeat; too large a radius merges neighbors, too small leaves the same star split.
- **Regenerate with:** `astrometricslib/scripts/run_empirical_validation.py` (photometry part); the repeat-run comparison was done by hand.
- **Guarded by tests:** Matching is pinned by `astrometricslib/pipelines/photometry/test/test_merge_errors_and_times.py`.
- **Raw record:** None kept.

### PHO-15

**Differential catalog zero point of one frame (M 57, one night)**

- **Claim:** The zero point of a frame, found from one fixed set of catalog stars, is steady from frame to frame to within about 0.0013 mag from the star counts alone, and it falls by the same amount for every star when cloud passes. It is exploratory: the observatory has no night labeled photometric, so no truth exists to check its absolute level. The level carries a star-sample error of 0.04 mag and systematic effects that are not removed (color, the L filter against the catalog band, aperture loss as the stars widen, an unflattened frame, mixed SIMBAD sources).
- **Settings it supports:**
  - code constant `CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION` = 0.5 in `astrometricslib/pipelines/photometry/processing/catalog_zero_point.py`. Relation: sanity check only. The region keeps about 39 percent of a square frame's area. The corner region was never measured. <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION -->
  - code constant `MINIMUM_STARS_FOR_ZERO_POINT` = 10, which also decides the band: Gaia G is used when its fixed set has at least this many stars. Relation: sanity check only. The M 57 night had 59 Gaia G and 89 SIMBAD V stars in the central region; Gaia G wins. <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.MINIMUM_STARS_FOR_ZERO_POINT -->
  - code constant `SESSION_STAR_FRAME_SHARE` = 0.8. Relation: sanity check only. All 59 Gaia G stars passed the cuts in all 61 frames, cloud frames included, so the share did not bind on this night. <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.SESSION_STAR_FRAME_SHARE -->
  - code constant `MINIMUM_SIGNAL_TO_NOISE` = 20. Relation: sanity check only. The flux errors come from the CCD equation with the camera profile's gain (0.783 e-/ADU for this camera), so they are not the unit-gain guess; a session whose gain is unknown stores its zero points as assumed. <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.MINIMUM_SIGNAL_TO_NOISE -->
  - the camera profile's photometric linearity limit (60,000 ADU for the ASI 533MM Pro) as the cut on a star's peak pixel, not the 65,000 ADU saturation threshold. Relation: sanity check only. With no limit in the profile nothing is measured.
  - the choice not to apply a color term to the stored value. Relation: derived from this entry (the first version of it measured the SIMBAD V stars of the same night): a color slope of -0.18 mag per mag of B-V did not shrink the star-to-star scatter (0.189 mag before, 0.191 after) and did not steady the frames. The slope is stored only for SIMBAD V sessions. The Gaia G session of this entry stores none, because Gaia G stars carry no B-V color in this library.
- **Status:** Partly measured. One target, one camera, one night of 61 frames, 59 Gaia G stars. Repeatability was measured in-sample, and the absolute level has no truth to check against.
- **Data you need:** One night of 30 or more light frames of a field with at least 30 catalog stars of one band in the central region, with a plate solution or a catalog identification for the reference frame, from one camera and filter, and a camera profile with a photometric linearity limit.
- **Procedure:**
  1. Run the photometry stage on one night of frames. The record step stores a zero point for every measured frame (before any frame is dropped as an outlier) and the star-sample error of its level, and for a SIMBAD V session the color slope.

```python
from datetime import datetime

from astrometricslib import Astrometrics
from astrometricslib.drivers.quality_store import QualityStore

astrometrics = Astrometrics()
target = astrometrics.targets.get("M 57")
night = "2026-10-03"
frames = [
    frame
    for frame in target.frames
    if frame.role == "LIGHT"
    and "533" in frame.camera
    and datetime.fromtimestamp(frame.timestamp - 43200).strftime("%Y-%m-%d") == night
]
astrometrics.processing.process_target(
    "M 57", stages=["photometry"], photometry={"frames": frames}, register_job=False
)
rows = QualityStore.from_configuration(astrometrics.config).query(
    metric="photometry.catalog_zero_point_gaia_g_mag", target_id="M 57"
)
print([(row.value, row.uncertainty, row.sample_count) for row in rows])
```
<!-- impl: astrometricslib.Astrometrics --> <!-- impl: astrometricslib.drivers.quality_store.QualityStore -->

  1. For repeatability, split the fixed star set into two random halves, find each half's zero point in every frame with `differential_frame_estimates` <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.differential_frame_estimates -->, and take the robust spread of the differences. Repeat for 50 random splits. For the error, compare `standard_error_of_median` <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.standard_error_of_median --> with `bootstrap_median_error` <!-- impl: astrometricslib.pipelines.shared.quality.frame_statistics.bootstrap_median_error --> on the same differences. For the dependence on seeing, regress the steady frames' zero points on `frame.star_width_moffat_px`. The scripts are in the session's scratch folder (`science/s2/`): `run_s2.py` and `evidence_s2b.py`.

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The frame zero points in magnitudes (higher is more sensitive; a drop is cloud, haze or dew, or a sharp loss of focus). Their standard error (the frame-to-frame precision, small because the same stars are used each time), against the star-sample error of the level (large, because the stars differ from each other). The slope against star width.
- **Our result:** 2026-10-23, M 57, night of 2026-10-03, 61 luminance frames (all 60 s, read from EXPTIME; the first version of this entry said 30 s) of the ASI 533MM Pro on the Apertura 75Q, run on a scratch copy of the library with the frames read only. SIMBAD is matched before Gaia, so the ambiguity filter removes only SIMBAD stars; of the 355 stars with a catalog magnitude, 59 Gaia G and 89 SIMBAD V stars lay in the central region and the session used Gaia G. All 59 passed the cuts in every frame. Code version: the working tree of this change.
  - Level and cloud. The 53 steady frames have a median zero point of 19.37 mag in Gaia G. Eight frames fell as low as 18.52 mag, 0.85 mag below the steady median, and all 61 frames still have a value (59 stars each).
  - Frame-to-frame precision. Two independent halves of the stars differ by a robust sigma of 0.0025 mag, which makes each half good to 0.0018 mag and the full set to about 0.0012 mag. The stored analytic error has a median of 0.0013 mag (0.0009 to 0.015 over the frames; the largest are cloud frames). Against a bootstrap on three steady frames the analytic error was 0.0023, 0.0043 and 0.0013 mag against 0.0026, 0.0031 and 0.0008 mag, so it is the right size but not exact. The steady frames still wander by a robust sigma of 0.013 mag, ten times the noise, so something other than counting noise moves them.
  - Absolute level. The star-sample error of the level is 0.039 mag (analytic); a bootstrap gives 0.057 mag, so the analytic value is about 30 percent low where the stars have heavy tails.
  - Seeing. Over the 53 steady frames the star width (Moffat FWHM) ran from 1.6 to 2.9 px. The zero point changes by -0.010 +/- 0.009 mag per pixel of width (r = -0.15), so no dependence is resolved over that range. The expected loss of a tenth to two tenths of a magnitude at 3.3 px is therefore not seen here; no aperture correction is added.
  - Color slope. None: a Gaia G session stores no slope.
  - Cost. The copy's NGC 2244 night of 2026-01-17 tracks only 492 stars (the stage caps the tracked stars), and the zero point step took 0.001 to 0.002 s there for 17 and 1 frames (two sessions); the whole stage took 37 s. At the full scale of about 11,700 stars, 60 frames and one band (synthetic stars through `session_zero_points`, nothing else), the step took 0.23 s. The extra memory is four arrays of stars times frames (11,700 by 60, about 5.6 MB each). The cost is negligible next to the aperture photometry.
  - Uncertainty and sample size: 61 frames, one night. Data split: in-sample.
- **Verdict:** Not judged.
- **Limits:** One night, one camera. The night was not labeled photometric. Photometry measures raw frames with no flat field, so the level carries whatever vignetting is left in the central region. The L filter and the sensor are not a catalog band, so the level depends on star color. The aperture is fixed at 4 px with no aperture correction. The flux errors assume a gain taken from the profile. The stored standard error is the frame-to-frame precision, not the error of the absolute level. Very cloudy frames of other nights may leave fewer than 10 stars and then have no value. The SIMBAD V band was not exercised on this run (the first version of this entry measured it on the same frames).
- **To tune for your setup:** Change `CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION` after measuring the offset of the inner and outer stars on frames flat-fielded with your own master flat. Change `MINIMUM_SIGNAL_TO_NOISE` if your faint stars bias the median. Then repeat the procedure above.
- **Regenerate with:** The procedure above; no script in `astrometricslib/scripts/`.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/processing/test_catalog_zero_point.py` (an injected zero point and an injected cloud are recovered with star offsets in place; the band rule, fixed set, region and cuts; the linearity limit; the color slope; paths and ordering) and `astrometricslib/pipelines/photometry/test/post_processing/test_record_zero_point.py` (including `validate_output` with two sessions).
- **Raw record:** None kept.

### PHO-16

**Spread of a night's frame zero points, as evidence for the night-summary helper**

- **Claim:** One number (the robust spread of a session's frame zero points) can summarize how much the transparency wandered on a night: 0.0139 mag over the 61 frames of one night of M 57, and 0.0129 mag over its 53 steady frames. The helper `zero_point_scatter` computes it; the pipeline does not store it. A night summary is made when it is read, on the observatory side (use case UC15). It is exploratory: no night here is labeled photometric, so no spread separates a steady night from a cloudy one.
- **Settings it supports:**
  - code constant `MINIMUM_FRAMES_FOR_SCATTER` = 10 in `astrometricslib/pipelines/photometry/processing/catalog_zero_point.py`. Relation: sanity check only. With 61 frames the bootstrap error of the spread is 0.0030 mag (22 percent); with 53 it is 0.0018 mag (14 percent). <!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.MINIMUM_FRAMES_FOR_SCATTER -->
- **Status:** Partly measured. One night of 61 frames; the spread has no truth to check against.
- **Data you need:** One night of 10 or more frames with frame zero points ([PHO-15](#pho-15)).
- **Procedure:**
  1. Read the stored zero points of a run and pass them to the helper.

```python
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import zero_point_scatter

rows = QualityStore.from_configuration(astrometrics.config).query(
    metric="photometry.catalog_zero_point_gaia_g_mag", target_id="M 57"
)
print(zero_point_scatter([row.value for row in rows if row.value is not None]))
```
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.zero_point_scatter --> <!-- impl: astrometricslib.drivers.quality_store.QualityStore -->

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The spread in magnitudes, with its frame count and bootstrap error. Compare it with the frame-to-frame precision of [PHO-15](#pho-15) (0.0013 mag): the difference is real wander.
- **Our result:** 2026-10-23, the same 61 frames of M 57 as [PHO-15](#pho-15), differential Gaia G zero points. Spread of all 61 frame zero points: 0.0139 +/- 0.0030 mag; of the 53 steady frames: 0.0129 +/- 0.0018 mag. The eight cloud frames (down to 0.85 mag below the steady median) raise the spread by only 8 percent because the spread is robust. The precision from the star counts alone is 0.0013 mag, so the steady frames wander by about 0.013 mag beyond measurement noise. Airmass is no longer stored by this stage, so no extinction slope is attempted; the airmass range of the night (1.08 to 1.19) was too small to see one in the first version of this entry. Sample size: 61 frames, one night. Data split: in-sample.
- **Verdict:** Not judged.
- **Limits:** The spread leaves out frames with no zero point (heavy cloud), so it flatters a night with solid cloud. It treats the frames as independent, which neighboring frames in slow cloud are not, so its error is too small. One night. The metric that would use it has a review date of 2027-04-30.
- **To tune for your setup:** Raise `MINIMUM_FRAMES_FOR_SCATTER` if you want a tighter error on the spread; its error falls about as one over the square root of the frame count.
- **Regenerate with:** The procedure above; no script in `astrometricslib/scripts/`.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/processing/test_catalog_zero_point.py` (the spread is the robust sigma and ignores a cloudy outlier; too few frames give no spread).
- **Raw record:** None kept.

### PHO-17

**Noise model by source: photon, sky, read and scintillation, for stars of stated magnitudes**

- **Claim:** The noise of one measurement can be written as four separate parts for a star of any catalog magnitude. Photon, sky and read noise come from the saved flux errors: the photometry made each error with the CCD equation, so the squared error in electrons minus the star's own electrons is exactly the background term (a unit test recovers it to 1e-9), and the read part is the camera's read noise squared times the geometry of the circle and the sky ring. Scintillation is Young's formula and is model-derived. On the M 57 night the model reproduces the scatter of the stars of magnitude 11 to 13 to within 8 percent and falls below the scatter of the brightest stars (9 to 11) by a third or more. The brightest stars' excess is mostly saturation: of the 67 stars of magnitude 9 to 11, the 9 whose peak pixel reaches the clip (about 65,500 ADU) scatter a median 31 mmag, while the 58 below 55,000 ADU scatter 4.7 to 5.1 mmag, still 1.3 to 1.4 times the model with scintillation. This check does not say what the rest of that gap is.
- **Settings it supports:**
  - code constants `YOUNG_COEFFICIENT` = 0.09, `YOUNG_AIRMASS_POWER` = 1.75 and `SCINTILLATION_SCALE_HEIGHT_M` = 8000 in `astrometricslib/pipelines/photometry/processing/noise_budget.py`. Relation: sanity check only. They are literature values; the M 57 night is consistent with them within the limits of the check below ([PHO-D27](#design-estimates)). <!-- impl: astrometricslib.pipelines.photometry.processing.noise_budget.YOUNG_COEFFICIENT -->
  - code constant `NOISE_MODEL_MAGNITUDES` = (10, 12, 14) in `astrometricslib/pipelines/photometry/metrics.py`. Relation: sanity check only. They span the stars of the M 57 field that have 40 or more points. <!-- impl: astrometricslib.pipelines.photometry.metrics.NOISE_MODEL_MAGNITUDES -->
- **Status:** Partly measured. One night of one target, 366 stars; the photon, sky and read parts are checked against the scatter of those stars, scintillation is a model whose site height is unknown.
- **Data you need:** One photometry run with a camera gain and read noise known for its setting, a catalog zero point, an optic with a focal length and ratio in the configuration and frames whose headers give an airmass.
- **Procedure:**
  1. Run the photometry stage on one night. The record step stores the rows in a step of their own.
  2. Compare the model's total noise at a stated magnitude with the robust scatter (1.4826 times the median absolute deviation, times 1.0857) of the normalized light curves of the stars of that magnitude.

```python
from datetime import datetime

from astrometricslib import Astrometrics

astrometrics = Astrometrics()
target = astrometrics.targets.get("M 57")
frames = [
    frame
    for frame in target.frames
    if frame.role == "LIGHT"
    and "533" in frame.camera
    and datetime.fromtimestamp(frame.timestamp - 43200).strftime("%Y-%m-%d") == "2026-10-03"
]
astrometrics.processing.process_target(
    "M 57", stages=["photometry"], photometry={"frames": frames}, register_job=False
)
for part in ("photon", "sky", "read"):
    for row in astrometrics.quality.query(
        metric=f"photometry.model_noise_{part}_at_mag12_mag", target_id="M 57"
    ):
        print(part, row.status, row.value)
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** At each stated magnitude, the three electron parts and scintillation, in magnitudes, against the measured scatter of stars of that magnitude. The sum in quadrature is the model's floor: the measured scatter should lie at or above it.
- **Our result:** 2026-10-23, M 57, night of 2026-10-03, 61 frames of 60 s (ASI 533MM Pro, Apertura 75Q, gain 0.783 e-/ADU, read noise 3.8 e-), run on a scratch copy; Gaia G zero point; the frame headers gave airmasses 1.07 to 1.08. Model rows (mmag), photon / sky / read at G 10: 2.10, 0.78, 0.12; at G 12: 5.27, 4.94, 0.75; at G 14: 13.2, 31.2, 4.7. Scintillation (75 mm, airmass about 1.07, 60 s, sea level assumed): 2.83 mmag, against 2.24 mmag from the electrons at G 10. The measured scatter of the normalized light curves (`science/s5/check_noise_model.py`): G 9 to 11, 62 stars, median 5.1 mmag (16 to 84 percent: 4.2 to 31.5), model 2.2 mmag from electrons alone and 3.6 with scintillation; G 11 to 13, 271 stars, median 7.2 mmag (5.6 to 9.6), model 7.3 and 7.8; G 13 to 15, one star with 40 points. Split of the G 9 to 11 bin by each star's median peak pixel in the 61 frames (`science/s5/bright_split.py`, which captures the peaks the photometry measures in memory): peak under 40,000 ADU, 46 stars, median scatter 5.05 mmag (16 to 84 percent: 4.19 to 6.13), median G 10.75; peak 40,000 to 55,000 ADU, 12 stars, 4.71 mmag (4.04 to 5.13), median G 9.94; peak above 55,000 ADU, 9 stars, 30.98 mmag (7.24 to 39.17), all at or near the 65,532 ADU clip. The 266 stars of G 11 to 13 that have peaks scatter 7.27 mmag with peaks no higher than 24,160 ADU. Sample: 24,291 light-curve points behind the background term. Data split: in-sample (no value is fitted).
- **Verdict:** Not judged.
- **Limits:** One night, one camera and one target. The measured scatter holds the comparison ensemble's noise, any real variability and cloud, and the model has no term for them. The brightest stars scatter more than the model. The split by peak shows the large excess belongs to stars at the clip (saturation or the nonlinearity above about 60,000 ADU), and it shows no rise between the stars under 40,000 ADU and those at 40,000 to 55,000 ADU. For the unsaturated stars the scatter stays 1.3 to 1.4 times the model with scintillation (about 3.5 mmag in quadrature). Candidates the data are consistent with, and this check cannot separate: a scintillation coefficient 1.5 times Young's (the factor Osborn et al. 2015, MNRAS 452, 1707, measured on average) would make scintillation about 4.3 mmag and the total about 4.8 mmag; no flat field (the frames are not flat-fielded) combined with the pointing wander of the stars across the sensor; and noise from the comparison ensemble. The faint bin has one star, because stars need 40 points. Scintillation is ASSUMED: it uses a sea-level site and the literature coefficient 0.09, and no bound is claimed. The site's real height would lower it (by about 12 percent for each kilometer, from the exp(-h/8000) factor), while measured coefficients run about 1.5 times Young's (Osborn et al. 2015), so the true value may lie on either side. The star-to-star correlation of scintillation that the normalization removes is not modeled. The dark current is assumed to be zero, as the photometry assumes. The noise is that of one star in one frame, not of a differential light curve.
- **To tune for your setup:** Set the site height in the configuration (`elevation` in `[Observatory.Location]`) so the height is no longer assumed (the coefficient stays a literature value); list the optic with its focal ratio. Then repeat the procedure.
- **Regenerate with:** The procedure above and `science/s5/check_noise_model.py` in the session's scratch folder; no script in `astrometricslib/scripts/`.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/processing/test_noise_budget.py` (Young's formula by hand, the exact recovery of the background term from the CCD equation, the sum of the sources) and `astrometricslib/pipelines/photometry/test/post_processing/test_record_science_outcomes.py` with `test_runner_science_outcomes.py` (assumed gain, read noise and site height, missing airmass, the runner storing the rows).
- **Raw record:** None kept.

### PHO-18

**Window function of the observing times: the strongest alias peaks of a run**

- **Claim:** The window function of a run's frame times is stored as the frequency and power of its three strongest peaks. Two periods whose frequencies differ by one of these frequencies cannot be told apart by the run. A run of one night has no daily echo and stores nothing. The function is the same Lomb-Scargle call the alias check uses.
- **Settings it supports:**
  - code constant `ALIAS_PEAK_COUNT` = 3 in `astrometricslib/pipelines/photometry/processing/observing_window.py`. Relation: sanity check only. On the real runs checked here the first three peaks all have power near 1, so three is not shown to be enough. <!-- impl: astrometricslib.pipelines.photometry.processing.observing_window.ALIAS_PEAK_COUNT -->
  - code constant `MAXIMUM_ALIAS_FREQUENCY_PER_DAY` = 2 in the same file. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.photometry.processing.observing_window.MAXIMUM_ALIAS_FREQUENCY_PER_DAY -->
- **Status:** Partly measured. The peaks follow from the times alone, so they are exact for the times used; what has not been checked is that they predict which periods a real light curve confuses.
- **Data you need:** Any run of two or more nights.
- **Procedure:**
  1. Collect the distinct frame times of the run and call `strongest_window_peaks` <!-- impl: astrometricslib.pipelines.photometry.processing.observing_window.strongest_window_peaks -->, or read the six `photometry.window_alias_*` rows of a run.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
for rank in (1, 2, 3):
    for quantity in ("frequency_per_day", "power_fraction"):
        for row in astrometrics.quality.query(
            metric=f"photometry.window_alias_{rank}_{quantity}", target_id="Vega"
        ):
            print(rank, quantity, row.value, row.reason)
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The frequencies of the peaks (cycles per day) and their power from 0 to 1. A power near 1 means the schedule cannot separate periods that differ by that frequency.
- **Our result:** 2026-10-23, the stored light curves of the scratch copy, frame times taken from the five stars nearest each target (`science/s5/real_outcomes.py`). Nightly schedules give the daily echo: M 106, 2 nights, 1.019 per day (power 1.00); M 81, 3 nights, 0.950 and 1.901 per day; NGC 2244, 3 nights, 0.983 per day (power 1.00) and 1.310 (0.90). Albireo, 2 nights 3 days apart, 0.986, 1.314 and 1.643 per day, all of power 1.00 (a spacing of 0.329 per day, one over the 3.04 days between the nights). Vega, 2 nights months apart, 0.024, 0.032 and 0.040 per day (a spacing of one over 42 days). With two clusters of identical nights every peak of the series has power near 1, so their order is decided by small differences and the set of frequencies, not the rank, is what to read. The 26 targets with one night store nothing, and the M 57 run on 2026-10-03 stored six not-measured rows with the reason. The last run of all 38 targets with light curves used the final code and gave the same peaks. Sample: the number of distinct frame times, 9 to 299. Data split: not applicable.
- **Verdict:** Not judged.
- **Limits:** Frame times are taken from the stars' light curves, which leave out frames dropped as outliers. A window made of a few clusters of nights has many equal peaks, so the first three are not the only ones that matter. The window says nothing about the star's own brightness changes. The peaks are listed only up to 2 cycles per day; a period shorter than 12 hours is not searched for aliases.
- **To tune for your setup:** Change `ALIAS_PEAK_COUNT` to store more peaks; a new rank also needs two metric declarations in `photometry/metrics.py`.
- **Regenerate with:** The procedure above; no script in `astrometricslib/scripts/`.
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/processing/test_observing_window.py` (the window is 1 at zero frequency and equals the alias check's call, nightly sampling puts the peak at one per day, a single night stores nothing) and `astrometricslib/pipelines/photometry/test/post_processing/test_record_science_outcomes.py`.
- **Raw record:** None kept.

### PHO-19

**Target amplitude, from its own light curve or from the VSX catalog, each with its source**

- **Claim:** The amplitude of the target star is stored twice and the two are never mixed. From the light curve it is the difference between the faintest and brightest of ten phase-bin medians, once at least eight bins hold three points each; the period is the star's own periodogram result when it is detected or possible, else the VSX period. From the catalog it is the VSX magnitude range. On an injected sine of 0.40 mag with 600 points over 12 cycles the fold gives 0.40 within 8 percent (the ten bins smooth the extremes). Only a target that is a single star by its name gets an amplitude, and its star is the one carrying the target's catalog name, not the nearest star. On the 38 targets with light curves in the scratch copy none gives a light-curve amplitude: 27 are clusters, galaxies, nebulae or solar system bodies by name, 7 have no catalog-identified star carrying their name, 3 have a named star 726 to 1,005 arcsec from the target position (the cross-check refuses them), and 1 (Mirach) has its star but neither a period from its periodogram nor a VSX period.
- **Settings it supports:**
  - code constants `PHASE_BIN_COUNT` = 10, `MINIMUM_POINTS_PER_BIN` = 3 and `MINIMUM_FILLED_BIN_FRACTION` = 0.8 in `astrometricslib/pipelines/photometry/processing/target_amplitude.py`. Relation: sanity check only ([PHO-D28](#design-estimates)). <!-- impl: astrometricslib.pipelines.photometry.processing.target_amplitude.PHASE_BIN_COUNT -->
  - code constant `MAXIMUM_TARGET_DISTANCE_ARCSEC` = 30 in the same file. Relation: sanity check only. The distance is a cross-check only: the star is found by its name, and one farther than this from the target's position is refused, not used. <!-- impl: astrometricslib.pipelines.photometry.processing.target_amplitude.MAXIMUM_TARGET_DISTANCE_ARCSEC -->
- **Status:** Design estimate. The fold is tested on injected light curves only; no stored run has a target star with a period and a covered cycle.
- **Data you need:** A run whose target is a periodic variable with a light curve covering most of a cycle, and VSX answers saved on the star (`backfill_variable_star_flags.py --refresh-vsx-ranges`).
- **Procedure:**
  1. Run the photometry stage, then read the three `photometry.target_*` rows.
  2. For the catalog value, fetch it with `vsx_entries_from_matches` <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.vsx_entries_from_matches --> (the backfill script saves it on the star).

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
for metric in (
    "photometry.target_light_curve_amplitude_mag",
    "photometry.target_phase_coverage_fraction",
    "photometry.target_catalog_amplitude_mag",
):
    for row in astrometrics.quality.query(metric=metric, target_id="Mirach"):
        print(metric, row.status, row.value, row.reason)
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The light-curve amplitude with its error and its phase coverage, beside the catalog amplitude and its band. Where both exist, a light curve far below the catalog value is a smoothed or partly covered cycle, or a different band.
- **Our result:** 2026-10-23. Injection: a 0.40 mag sine, 600 points over 12 cycles, gave an amplitude within 8 percent of 0.40; a fold on a wrong period (1.37 times) gave under 0.2; 3 days of a 10-day period filled too few bins and gave none. Stored runs, rerun on 2026-10-24 with the star found by its catalog name (`science/s5/real_outcomes.py`, 38 targets): 27 are not stars by name, 7 have no star carrying their name, 3 have a named star 726 to 1,005 arcsec from the target position, and Mirach (`* bet And`, 2 arcsec from the target position, 79 points) has neither a periodogram period (not detected) nor a saved VSX period, so no run had a light-curve amplitude. The earlier nearest-star rule had found a star within 30 arcsec for 7 targets, including clusters and a galaxy (M 13, M 31, M 42, M 81, NGC 2244, M 45), which are not single stars; that rule is gone. VSX asked live for Mirach: type LB, 2.01 to 2.10 V, amplitude 0.09 mag, no period. The ranges VSX listed for IC 1396 and NGC 1893 stars had a minimum brighter than the maximum (an amplitude written without the flag) and are dropped; a minimum in a different band than the maximum (`n_min` differs from `n_max`) also gives no range. Stars saved before VSX ranges were kept carry `vsx_range_checked` false and the catalog row says "saved before VSX ranges were kept; run backfill with --refresh-vsx-ranges". Sample: 38 targets. Data split: not applicable.
- **Verdict:** Not judged.
- **Limits:** The folded amplitude is smoothed by the bins; a narrow eclipse reads low. The fold assumes a strictly periodic star. The target star is found by its catalog name, so a target whose name SIMBAD does not give to any star in the run (a common name SIMBAD stores under another id) gets nothing; that is deliberate (not measured over a guess). The VSX range is for the band VSX names (stored beside it), often from visual or old data, and is not converted to this telescope's band. The saved VSX entries are not refreshed by a run; they come from the backfill script.
- **To tune for your setup:** Change `MAXIMUM_TARGET_DISTANCE_ARCSEC` if your pointing catalog is less accurate; change the bin settings in the same file for sharper minima, and the comparison is then not with the earlier rows.
- **Regenerate with:** The procedure above; no script for the stored comparison in `astrometricslib/scripts/` (`backfill_variable_star_flags.py` saves the VSX values).
- **Guarded by tests:** `astrometricslib/pipelines/photometry/test/processing/test_target_amplitude.py`, `astrometricslib/pipelines/photometry/test/post_processing/test_record_science_outcomes.py`, `astrometricslib/pipelines/shared/test/test_variable_star_catalogs.py` and `astrometricslib/scripts/test/test_backfill_variable_star_flags.py`.
- **Raw record:** None kept.

## Design estimates

These tuned values have not been measured. They are reasoned choices.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| PHO-D01 | Aperture radius; sky ring radii | 4 px; 7 to 12 px | arguments of `measure_aperture_photometry` in `pipelines/photometry/pre_processing/frame_photometry.py` | Extra scatter from a poorly sized aperture. | Run [PHO-02](#pho-02) at several radii and keep the one with the smallest ensemble scatter for your star width. |
| PHO-D02 | Re-centering box; passes; largest move | 11 px; 3; 1.5 px | `CENTROID_BOX_HALF_WIDTH_PX`, `CENTROID_REFINEMENT_PASSES`, `MAX_CENTROID_SHIFT_PX` in `frame_photometry.py` | Apertures off the star, or more centroid fallbacks. | Read the fallback share of the `registration_drift` gate ([PHO-04](#pho-04)) at several values on your own frames. |
| PHO-D03 | Anchor limits | 20 times the sky noise; skip brightest 5%; 50 anchors; at least 10 | `ALIGNMENT_ANCHOR_*` constants in `frame_photometry.py` | Frame shifts measured from noise peaks. | Repeat [PHO-05](#pho-05) on real frames with a known offset. |
| PHO-D04 | Anchor spread for a rotation fit; fewest anchors for a shift | 7% of the corner distance; 5 | `MINIMUM_ANCHOR_SPREAD_FRACTION`, `MINIMUM_ANCHORS_FOR_SHIFT` in `frame_photometry.py` | A rotation fit from too few or too close anchors. | Compare the corner displacement of [PHO-04](#pho-04) with and without the fit on a cluster-core field. |
| PHO-D05 | Comparison band; vetted candidates | brightest 2% skipped, band to 30%; 100 | `BRIGHTEST_SKIPPED_FRACTION`, `FAINTEST_BAND_FRACTION`, `MAXIMUM_VETTING_CANDIDATES` in `comparison_ensemble.py` | A noisier comparison ensemble. | Run [PHO-02](#pho-02) with several band edges and keep the smallest ensemble scatter over predicted. |
| PHO-D06 | Minimum and cap of comparison stars | 5 and 20 | `MINIMUM_COMPARISON_STARS`, `DEFAULT_MAXIMUM_COMPARISON_STARS` in `comparison_ensemble.py` | A noisy ensemble, or no gain from extra stars. | Re-run [PHO-02](#pho-02) with `VariabilityAnalyzer(maximum_comparison_stars=...)` and watch the ensemble error stop falling. |
| PHO-D07 | Vetting consistency limit; usable-frame share | 3σ; one half | `EXCESS_SCATTER_SIGMA`, `MINIMUM_USABLE_FRAME_FRACTION` in `comparison_ensemble.py` | Variable stars kept as comparison stars, or good ones dropped. | Plant a known variable among the comparison candidates of a real session, as [PHO-01](#pho-01) does on synthetic ones. |
| PHO-D08 | Frame outlier clip; per-star outlier clip | 3σ (at least 5 frames); 5σ | `VariabilityAnalyzer._reject_outlier_frames`, `_reject_outlier_measurements_for_star` | Good points clipped, or bad points kept. | Count clipped points on constant stars against the share a normal distribution predicts. |
| PHO-D09 | Share of frames rejected before the run is flagged | 25% (and at least 5 frames) | `MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG`, `MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG` in `record_quality.py` | Bad nights pass, or good nights are flagged. | Run `report_gate_limits.py` over the library and set the limit above the rejection share of known-good nights. |
| PHO-D10 | Noise model sizes | 20 stars; 10 per bin; 10 bins | `MINIMUM_STARS_FOR_NOISE_MODEL`, `MINIMUM_STARS_PER_NOISE_BIN`, `MAXIMUM_NOISE_BINS` in `variability_indices.py` | A poor noise model on small fields. | Vary them in [PHO-06](#pho-06) with a field size like yours and watch the AUC. |
| PHO-D11 | Thresholds | excess scatter 1.5; chi-square and J at median + 2.326 robust sigma | `EXCESS_SCATTER_THRESHOLD`, `CALIBRATION_SIGMAS` in `variability_indices.py` | Missed variables or false candidates. | Checked only on synthetic fields ([PHO-06](#pho-06)); run [PHO-09](#pho-09) on your field at several values. |
| PHO-D12 | False discovery level | 0.01 | `FALSE_DISCOVERY_LEVEL` in `variability_indices.py` | More false candidates, or fewer true ones. | Count untouched stars flagged in [PHO-09](#pho-09) at several levels. |
| PHO-D13 | Fewest usable stars for a scatter population | 10 | `MINIMUM_STARS_FOR_SCATTER_POPULATION` in `record_quality.py` | The gate judged on too few stars. | Run [PHO-06](#pho-06) with very small fields and see where the AUC collapses. |
| PHO-D14 | Discrimination AUC floor | 0.7 | `MINIMUM_DISCRIMINATION_AUC` in `variability_skill.py` | Poor fields pass the gate, or good fields fail it. | Collect the gate's AUC over many fields ([PHO-08](#pho-08)) and compare runs you trust with runs you do not. |
| PHO-D15 | Largest acceptable detectable amplitude | 0.3 mag peak to peak | `MAXIMUM_DETECTABLE_AMPLITUDE_MAG` in `record_quality.py` | Fields that cannot see small variables pass. | Compare the gate's estimate with the 50% depth of `validate_photometry_injection.py` ([PHO-09](#pho-09)) on the same field. |
| PHO-D16 | Corner displacement; centroid fallback share; tracking drift | 1 px; 20%; 20 px | `MAXIMUM_CORNER_DISPLACEMENT_PX`, `MAXIMUM_CENTROID_FALLBACK_FRACTION` in `record_quality.py`; `UNSTABLE_TRACKING_DRIFT_PX` in `pre_processing/assess_input_quality.py` | Bad alignment passes, or good nights are flagged. | Collect the `registration_drift` values over many nights ([PHO-04](#pho-04)) and set each limit above the nights you trust. |
| PHO-D17 | Low coverage; low confidence margin | 0.5; 1 MAD | `LOW_COVERAGE_THRESHOLD` in `assess_input_quality.py`; `LOW_CONFIDENCE_MARGIN` in `post_processing/assess_output_quality.py` | Verdicts that flip between nights. | Re-measure stars near the limit on a second night and count how often their verdict flips. |
| PHO-D18 | Between-session flag | significance above 3; amplitude at least 0.02 mag | `DEFAULT_LONG_TERM_SIGNIFICANCE_THRESHOLD`, `MINIMUM_LONG_TERM_AMPLITUDE_MAG` in `processing/variability_analyzer.py` | False between-session flags, or missed ones. | Count flags on stars a catalog calls constant over a multi-session target ([PHO-14](#pho-14)). |
| PHO-D19 | Period checks: alias rival share; agreeing nights | 90% of the best peak; more than half | `_RIVAL_ALIAS_POWER_SHARE`, `_AGREEING_SHARE` in `processing/period_checks.py` | Alias periods reported as real. | Run `holdout_cycle` and `alias_check` on a star of known period observed on several nights. |
| PHO-D20 | Stars searched for a period | target plus 10 brightest | `MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH` in `batch.py` | Run time grows with the number of stars searched. | A cost choice; time `search_periods_and_save` on your largest field. |
| PHO-D21 | Shuffles when a search is slow | 150 above 400,000 point-periods | `_REDUCED_SHUFFLE_COUNT`, `_SLOW_SEARCH_WORK_LIMIT` in `periodicity_search.py` | A less precise false-alarm probability. | Repeat [PHO-12](#pho-12) at 150 shuffles. |
| PHO-D22 | Frequency grid cap; refined peaks | 20,000 points; 20 peaks, 3 coarse steps | `_MAXIMUM_SEARCH_GRID_POINTS`, `_CANDIDATE_PEAK_COUNT`, `_REFINE_HALF_WIDTH_COARSE_STEPS` in `periodicity_search.py` | A real period missed on long baselines. | Inject a known period into a long, multi-month light curve and check it is recovered. |
| PHO-D23 | Hold-out period search: window and step | 25% of the frequency either side; step 1/(5 T) for training span T | `_FREQUENCY_WINDOW`, `_SAMPLES_PER_PEAK` in `processing/period_checks.py` | A period that the other nights would recover is missed, or a wrong peak is accepted as the same period. | Run `holdout_cycle` on a star of known period observed on several nights with the window at 10%, 25% and 50% and the step at 1/(3 T), 1/(5 T) and 1/(10 T), and see where the result stops changing. |
| PHO-D24 | Catalog zero point: central region; stars needed (also the band rule); signal-to-noise cut; share of frames for the fixed star set | 0.5 of the half-diagonal; 10 stars; 20; 80 percent | `CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION`, `MINIMUM_STARS_FOR_ZERO_POINT`, `MINIMUM_SIGNAL_TO_NOISE`, `SESSION_STAR_FRAME_SHARE` in `processing/catalog_zero_point.py` | Vignetting leaks into the zero point, too few stars are left in a thin frame, or a faint star that vanishes in cloud moves the level. The band rule: Gaia G whenever its fixed set has at least 10 stars, else SIMBAD V; a wrong rule mixes a worse band into the zero point. | Flat-field the frames with the master flat and compare the inner and outer stars ([PHO-15](#pho-15)); repeat with the share at 60 and 95 percent on a night with more cloud. |
| PHO-D25 | Catalog zero point: color slope needs stars; color spread | 20 stars; 0.4 mag | `MINIMUM_STARS_FOR_COLOR_SLOPE`, `MINIMUM_COLOR_RANGE_MAG` in `processing/catalog_zero_point.py` | A slope fitted to noise, or a real slope not reported. | Inject a known slope into frames and find the smallest color spread that recovers it. |
| PHO-D26 | Night-summary helper: frames needed | 10 | `MINIMUM_FRAMES_FOR_SCATTER` in `processing/catalog_zero_point.py` | A spread with an error too large to read. | Bootstrap the spread of a steady night at 10, 20 and 40 frames ([PHO-16](#pho-16)). |
| PHO-D27 | Scintillation formula: coefficient; airmass power; scale height; no site factor | 0.09; 1.75; 8000 m; 1 | `YOUNG_COEFFICIENT`, `YOUNG_AIRMASS_POWER`, `SCINTILLATION_SCALE_HEIGHT_M` in `processing/noise_budget.py` <!-- impl: astrometricslib.pipelines.photometry.processing.noise_budget.YOUNG_COEFFICIENT --> | Scintillation off by tens of percent; sites scatter around the formula. | Compare the model with the scatter of bright, unsaturated stars on several nights at known airmass and site height ([PHO-17](#pho-17)). |
| PHO-D28 | Target amplitude: phase bins; points per bin; filled share; target distance | 10; 3; 80 percent; 30 arcsec | `PHASE_BIN_COUNT`, `MINIMUM_POINTS_PER_BIN`, `MINIMUM_FILLED_BIN_FRACTION`, `MAXIMUM_TARGET_DISTANCE_ARCSEC` in `processing/target_amplitude.py` <!-- impl: astrometricslib.pipelines.photometry.processing.target_amplitude.PHASE_BIN_COUNT --> | A sharp minimum smoothed away, a part-covered cycle taken as covered, or the wrong star taken as the target. | Inject eclipses of several widths and depths into a real light curve and see where the folded amplitude stops recovering them ([PHO-19](#pho-19)). |
| PHO-D29 | Noise model magnitudes; alias peaks stored | G 10, 12, 14; 3 | `NOISE_MODEL_MAGNITUDES` in `metrics.py`; `ALIAS_PEAK_COUNT` in `processing/observing_window.py` <!-- impl: astrometricslib.pipelines.photometry.metrics.NOISE_MODEL_MAGNITUDES --> | A reader needing another magnitude or more peaks has none stored. | Compare the stored values with the stars and schedules the observatory really has ([PHO-17](#pho-17), [PHO-18](#pho-18)). |
