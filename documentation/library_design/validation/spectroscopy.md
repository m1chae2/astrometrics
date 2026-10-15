# Spectroscopy Validation and Tuning

*Version 1.0 · 2026-10-15 · Status: current*

This file holds the evidence behind the tuned values of the slitless spectroscopy pipeline: what was checked, on what kind of data, with which library calls, and what came out. It is written for a reader who wants to repeat a check on their own grating spectra and tune the pipeline to their own camera, grating and sky. The method itself is described in the pipeline READMEs under `astrometricslib/pipelines/spectroscopy/`. Terms are defined in the [glossary](../Astrometrics_Library_Architecture.md#appendix-a-glossary), and the status labels (Measured, Partly measured, Design estimate) are explained in [index.md](index.md).

Wavelengths are in ångströms (Å; 1 Å is 0.1 nm). Most procedures below call internal functions of the spectroscopy pipeline. The public API (`from astrometricslib import Astrometrics`) runs whole pipelines, but it does not expose the single steps that a check needs. Every call marked "internal" may change between versions; the impl markers in this file let a test confirm that each named function still exists.

Two calls recur in many procedures. They are written out once here.

```python
import numpy as np

from astrometricslib.drivers.image import AstrometricsImage  # internal
from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline  # internal
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline  # internal
from astrometricslib.pipelines.spectroscopy.pre_processing.standard_star_selection import (  # internal
    select_standard_star,
)

# (a) The raw extraction of one star: brightness at each step, before any
#     colour correction. Wavelengths come back in nm.
raw = SpectroscopyPipeline().process_image(AstrometricsImage("stack.fits"), target_stars=[(x0, y0)])[0]
wavelength = np.asarray(raw["wavelengths"]) * 10.0  # Å
counts = np.asarray(raw["intensities"])

# (b) The fully calibrated result for one star (response corrected, classified,
#     with its quality checkpoints).
context = AstrometryPipeline().process("stack.fits", attempt_plate_solving=False)
context.stellar_objects = [
    select_standard_star(context.stellar_objects, context.image.data.shape, (x0, y0))
]
star = SpectroscopyPipeline().process(context, limit=1)[0]
spectrum = star.spectroscopy  # a SpectroscopyResult
```

<!-- impl: astrometricslib.drivers.image.AstrometricsImage -->
<!-- impl: astrometricslib.pipelines.astrometry.pipeline.AstrometryPipeline -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pipeline.SpectroscopyPipeline -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pipeline.SpectroscopyPipeline.process_image -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pipeline.SpectroscopyPipeline.process -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.standard_star_selection.select_standard_star -->
<!-- impl: astrometricslib.SpectroscopyResult -->

Call (a) is called "the raw extraction" below and call (b) "the calibrated result". `(x0, y0)` is the star's zero-order pixel position (its undispersed point image).

## Measured and partly measured values

### SPE-01

**Placement of the sky strips beside the extraction box**

- **Claim:** Moving the two sky strips from 2 px to 6 px away from the extraction box cuts the star light wrongly removed as sky from 2.05% to 0.57% of the total. Beyond 6 px the share falls only slowly, and the line depths do not change.
- **Settings it supports:**
  - code constant `SKY_BAND_GAP_PX` = 6 in `astrometricslib/pipelines/spectroscopy/pre_processing/spectrum_extractor.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_GAP_PX -->
  - code constant `SKY_BAND_WIDTH_PX` = 10 in the same file. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_WIDTH_PX -->
- **Status:** Partly measured. Swept on one frame of a sparse field, and then used unchanged on a crowded field where no sweep was run.
- **Data you need:** A stacked spectral image of one bright, isolated star with an unsaturated trail, on a sparse field, and the star's zero-order position. A second, crowded field is useful to check that the strips do not reach neighboring trails.
- **Procedure:**
  1. Extract the star once with sky subtraction switched off. This gives the total light in the box.
  2. For each gap and width, set the two module constants (internal, not part of the public API; may change) and extract again with subtraction on.
  3. The share removed is one minus the ratio of the two totals.
  4. Measure the depths of Na D, H-beta and H-gamma at each setting with `measure_local_dip_depth` (internal) and check that they do not move.

  ```python
  from astrometricslib.pipelines.spectroscopy.pre_processing import spectrum_extractor  # internal

  image = AstrometricsImage("isolated_star_stack.fits")
  base = SpectroscopyPipeline().config
  no_sky = base.model_copy(update={"subtract_sky_background": False})
  total = np.nansum(SpectroscopyPipeline(no_sky).process_image(image, target_stars=[(x0, y0)])[0]["intensities"])
  for gap, width in [(2, 4), (3, 6), (5, 6), (6, 10), (8, 10), (12, 10), (20, 10), (30, 10)]:
      spectrum_extractor.SKY_BAND_GAP_PX = gap
      spectrum_extractor.SKY_BAND_WIDTH_PX = width
      kept = np.nansum(SpectroscopyPipeline(base).process_image(image, target_stars=[(x0, y0)])[0]["intensities"])
      print(gap, width, 100.0 * (1.0 - kept / total))
  ```
  <!-- impl: astrometricslib.utilities.spectroscopy_models.SpectroscopyConfig -->
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector.measure_local_dip_depth -->
- **Acceptance criterion:** The gap is at the knee of the curve, and the line depths do not change with the gap.
- **What to look at:** The removed share, in percent of the total light, against the gap. It falls fast while the strips still sit on the star's faint wings and then flattens. The gap to choose is at the knee: past it, more distance gains little and moves the strips toward neighboring trails. Line depths that change with the gap mean the strips are eating into the trail.
- **Our result:** Vega master stack (ZWO ASI 533MM Pro on a 405 mm telescope, vertical dispersion, traced extraction, extraction radius 10 px, trail tilt 2.03°), 2026-09-19. The stack had already been background-flattened upstream (image median exactly 0). Trail sigma: median 1.67 px, 10th percentile 1.32 px, 90th percentile 3.96 px.

  | Gap (px) | Width (px) | Removed (% of total) |
  | --- | --- | --- |
  | 2 | 4 | 2.05 |
  | 3 | 6 | 1.26 |
  | 3 | 10 | 0.87 |
  | 5 | 6 | 0.87 |
  | 6 | 10 | 0.57 |
  | 8 | 10 | 0.46 |
  | 10 | 10 | 0.39 |
  | 12 | 10 | 0.34 |
  | 16 | 10 | 0.27 |
  | 20 | 10 | 0.23 |
  | 30 | 10 | 0.17 |

  Raw line depths were Na D 0.050, H-beta 0.149 and H-gamma 0.271; with subtraction at gap 6, width 10 they were 0.050, 0.149 and 0.272. Across all widths tried, the depths moved by less than 0.002.
- **Verdict:** Not judged.
- **Limits:** One frame of one bright star on a sparse, background-flattened field. A 10 px strip gives 20 sky pixels per step; the width choice rests on that count and on the insensitive line depths, not on a separate sweep of crowded data. The same gap was later used on the crowded M 13 stack (SPE-02), but not swept there.
- **To tune for your setup:** If your trail is wider (worse seeing, softer focus, smaller pixels), the wings reach further and the knee moves out: raise the gap. In a crowded field where `contaminated_sky_fraction` is high on isolated stars, the strips may be reaching neighbors: lower the gap or the width. Re-run the sweep after each change.
- **Regenerate with:** No script; measured by hand on 2026-09-19.
- **Guarded by tests:** `test_sky_band_settings_are_sensible` in `astrometricslib/pipelines/spectroscopy/test/test_sky_background_subtraction.py` (a sanity check of the values, not the sweep).
- **Raw record:** [`documentation/evidence/sky_band_sweep_vega_20260919.json`](../../evidence/sky_band_sweep_vega_20260919.json).

### SPE-02

**Sky subtraction on a crowded field**

- **Claim:** Without sky subtraction, the spectra of faint field stars in a crowded cluster frame are mostly background light, which is brightest in the red. That red excess made K stars classify as M stars. With the sky subtracted, five of the six stars that extract come within 0 to 5 subtype steps of their catalog type.
- **Settings it supports:**
  - config key `[Observatory.Camera.<camera name>] subtract_sky_background` (default true) <!-- impl: astrometricslib.utilities.spectroscopy_models.SpectroscopyConfig --> and the sky measurement `measure_sky` in `astrometricslib/pipelines/spectroscopy/pre_processing/spectrum_extractor.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.measure_sky -->
- **Status:** Partly measured. One frame, seven stars, and the sky estimator compared there is no longer the one the pipeline uses (see Limits).
- **Data you need:** A spectral stack of a crowded field (a globular cluster works) with a solved normal (non-spectral) stack of the same field, and at least five field stars of magnitude 7 to 9 with catalog spectral types, ideally K stars.
- **Procedure:**
  1. Back up the catalog (`python -m astrometricslib.scripts.backup_library_databases`), because a pipeline run saves its results.
  2. Set `subtract_sky_background = "false"` in the camera's section of the config file and run the spectroscopy stage through the public API. Read each star's type.
  3. Set it back to `"true"`, run again, and read the types again.
  4. For each star, the error in subtype steps is the difference of the two ladder positions (`ladder_position`, internal).
  5. To see how much of each spectrum was sky, compare the raw extraction of each star with and without subtraction in four wavelength bands.

  ```python
  from astrometricslib import Astrometrics
  from astrometricslib.models.stellar_source import ladder_position  # internal

  astrometrics = Astrometrics()
  target = astrometrics.targets.get("M 13")
  astrometrics.processing.process_target(target, stages=["spectroscopy"])
  ids = [s.id for s in astrometrics.catalog_access.list_star_summaries(target_id=target.id) if s.has_spectra]
  for star in astrometrics.catalog_access.get_by_ids("stellar_catalog", ids):
      own = star.spectroscopy.self_determined_spectral_type
      if star.spectral_type and own not in ("", "Unknown"):
          print(star.id, star.spectral_type, own, abs(ladder_position(own) - ladder_position(star.spectral_type)))
  ```
  <!-- impl: astrometricslib.Astrometrics -->
  <!-- impl: astrometricslib.ProcessingPipelines.process_target -->
  <!-- impl: astrometricslib.models.stellar_source.ladder_position -->
- **Acceptance criterion:** A good sky stage removes the red excess and brings K stars back to K.
- **What to look at:** The sky share of each star's un-subtracted spectrum, by band (0 to 1; above about 0.5 the "spectrum" is mostly sky), and the mean error in subtype steps against the catalog type, with and without subtraction. A good sky stage removes the red excess and brings K stars back to K.
- **Our result:** M 13 spectral stack (ASI 533MM Pro, 405 mm), 2026-09-19. Field stars of V 6.8 to 9.2 with catalog types.

  Share of the un-subtracted spectrum that was background, by band 4200 to 5400 / 5400 to 6800 / 6800 to 7400 / 7400 to 8000 Å:

  | Star (V) | Shares |
  | --- | --- |
  | BD+36 2764 (9.16) | 0.67 / 0.68 / 0.82 / 0.91 |
  | BD+36 2775 (8.79) | 0.62 / 0.49 / 0.89 / 0.92 |
  | HD 150293 (8.74) | 1.02 / 1.01 / 1.00 / 1.00 |
  | HD 150679 (7.32) | 0.22 / 0.32 / 0.59 / 0.80 |
  | HD 150998 (6.83) | 0.25 / 0.17 / 0.29 / 0.44 |
  | HD 151023 (8.47) | 0.55 / 0.54 / no value / no value |
  | HD 151086 (8.68) | 0.55 / 0.49 / 0.92 / 0.92 |

  For comparison, the sky stage removed 0.6% of Vega's total flux (SPE-01).

  Types (catalog type: no subtraction, then pooled-median sky, then lower-of-two-strips sky; steps from the catalog type in brackets):

  | Star | Catalog | No sky | Pooled median | Lower of two |
  | --- | --- | --- | --- | --- |
  | BD+36 2764 | K0 | M2V (12) | K0V, RMS 0.084 (0) | K0V, RMS 0.07 (0) |
  | BD+36 2775 | K5 | M2V (7) | K3V, 0.19 (2) | K4V, 0.17 (1) |
  | HD 151086 | K2 | M1V (9) | G8V, 0.12 (4) | K0V, 0.11 (2) |
  | HD 150998 | K2 | M0V (8) | K5V, 0.13 (3) | K5V, 0.13 (3) |
  | HD 151023 | K0 | K2V, 0.105 (2) | K2V, 0.053 (2) | K2V, 0.05 (2) |
  | HD 150679 | A2 | G0V (18) | A7V, 0.081 (5) | A7V, 0.08 (5) |
  | HD 150293 | K0 | M1V (11) | Unknown | M5V, 0.76 (15) |

  Mean steps from the catalog type over the six stars that extract: pooled median 2.67, lower of two strips 2.17, pooled 25th percentile 2.17. HD 150293 fails with either estimator: all of its spectrum is removed as background. It sits at x = 85 px, near the left edge, and the trail is probably not being followed; this is not a sky-estimator problem and was not resolved.

  In the same frame, the residual of each corrected spectrum divided by its catalog-type reference was within about 6% of 1.00 at every wavelength for the bright A standards (Vega, the second star of the Alcor pair, g UMa). For six K stars it was 0.6 to 0.8 at 4200 to 6000 Å, about 1.0 at 6000 to 6800 Å, 1.4 to 1.7 at 6960 to 7700 Å and about 2.0 at 7720 to 8000 Å (spread 0.14 to 0.38), and 3.3 at 7720 to 8000 Å for HD 150679.
- **Verdict:** Not judged.
- **Limits:** One frame and seven stars; treat as provisional. The record compares a pooled median and the lower of the two strip medians. The pipeline now takes the mean of both strips and drops a strip only when it is brighter than noise allows (SPE-03); that rule was not run on this frame. Spectra stored before the sky stage existed must be re-extracted from the images, not only recomputed.
- **To tune for your setup:** Leave subtraction on. If faint stars still come out too red, check `contaminated_sky_fraction` and the strip placement (SPE-01), and re-run steps 2 to 4.
- **Regenerate with:** No script; measured by hand on 2026-09-19.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_sky_background_subtraction.py`.
- **Raw record:** [`documentation/evidence/k_star_sky_investigation_20260919.json`](../../evidence/k_star_sky_investigation_20260919.json).

### SPE-03

**The sky estimator: mean of both strips, with a contamination test**

- **Claim:** On simulated 10-pixel strips of normal noise, the mean-of-both rule reads clean sky 0.30 ADU low at 13 ADU of noise per pixel, against 2.6 ADU low (about 0.2 of one pixel's noise) for always taking the lower strip median. With the pooled-noise floor, about 4% of clean steps are wrongly called contaminated (about 11% without it), while a strip half filled by a neighbor's trail is still flagged in most cases.
- **Settings it supports:**
  - code constant `SKY_CONTAMINATION_SIGMA` = 3.0 in `astrometricslib/pipelines/spectroscopy/pre_processing/spectrum_extractor.py`. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_CONTAMINATION_SIGMA -->
  - code constant `SKY_POOLED_NOISE_FLOOR` = 0.75 in the same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_POOLED_NOISE_FLOOR -->
- **Status:** Partly measured. By simulation only, not on real frames.
- **Data you need:** None for the simulation. For a real-data check, spectral stacks with isolated stars (clean sky on both sides) and stars with a neighbor's trail beside them.
- **Procedure:**
  1. Simulate many cross-sections of pure sky noise and run `measure_sky` (internal) on each.
  2. The bias is the mean of the returned levels minus the true sky. The false-positive rate is the share of clean readings whose mode ends in `_contaminated`.
  3. Add a bright stripe to half of one strip and count how often that strip is dropped.
  4. On real data, read `extraction_diagnostics.contaminated_sky_fraction` of the calibrated result for isolated stars.

  ```python
  from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import measure_sky  # internal

  rng = np.random.default_rng(1)
  levels, modes = [], []
  for _ in range(5000):
      cross_section = rng.normal(100.0, 13.0, 61)  # sky 100 ADU, 13 ADU noise per pixel
      reading = measure_sky(cross_section, aperture_center=30.0, aperture_half_width=4.0)
      levels.append(reading.level_per_pixel)
      modes.append(reading.mode)
  print(np.mean(levels) - 100.0, np.mean([m.endswith("_contaminated") for m in modes]))
  ```

- **Acceptance criterion:** A bias near zero, a false-positive rate of a few percent, and `contaminated_sky_fraction` at a few percent on real isolated stars.
- **What to look at:** The bias in ADU (near zero is good; a negative bias leaves sky in a faint star's total) and the false-positive rate (a few percent is expected; much more means the noise floor is too low). On real isolated stars, `contaminated_sky_fraction` should stay at a few percent.
- **Our result:** Simulated 2026-10-10: 10-pixel strips of normal noise, 13 ADU per pixel. Mean-of-both rule: bias -0.30 ADU. Lower of two medians: -2.6 ADU. Clean steps flagged as contaminated: about 11% with the lower of the two strip scatters as the noise, about 4% with the 0.75 pooled floor.
- **Verdict:** Not judged.
- **Limits:** Simulation only, with normal (Gaussian) noise. Real sky has hot pixels, gradients and faint unresolved stars.
- **To tune for your setup:** If isolated stars show a high `contaminated_sky_fraction`, raise `SKY_CONTAMINATION_SIGMA` or the noise floor. If known neighbors are not caught, lower them. Re-run the simulation and the real-data check.
- **Regenerate with:** No script. The 2026-10-10 numbers were simulated by hand.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_sky_estimate_bias.py` (`test_the_recovered_sky_is_within_half_an_adu_of_the_truth`, `test_a_faint_trail_is_not_read_bright_by_an_under_subtracted_sky`, `test_a_bright_neighbour_in_the_upper_band_leaves_the_sky_at_the_truth`, `test_a_clean_frame_uses_both_bands_for_most_readings`).
- **Raw record:** None kept.

### SPE-04

**Smoothing the trail width before it sets the box**

- **Claim:** A box that follows each step's fitted width directly changes size by a whole pixel and makes the extracted brightness jump by about 2%. A running median over 61 steps removes those jumps and still follows the real change of width along the trail, which on Vega is from 1.2 px at 4200 Å to 1.9 px at 5000 Å over about 400 steps of a 563-step trail.
- **Settings it supports:**
  - code constant `APERTURE_SIGMA_SMOOTHING_STEPS` = 61 in `astrometricslib/pipelines/spectroscopy/pre_processing/spectrum_extractor.py`. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.APERTURE_SIGMA_SMOOTHING_STEPS -->
  - code constant `APERTURE_SIGMA_MULTIPLIER` = 2.5 in the same file. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.APERTURE_SIGMA_MULTIPLIER -->
- **Status:** Partly measured. The jumps are shown on synthetic frames and the width change on one real trail; 61 itself was chosen by judgment, not tuned.
- **Data you need:** A spectral stack of a bright star with a well-traced trail.
- **Procedure:**
  1. Take the raw extraction and its `trail_width_px` (the fitted sigma at each step).
  2. Look at how the fitted width changes along the trail and how noisy it is from step to step.
  3. Set `spectrum_extractor.APERTURE_SIGMA_SMOOTHING_STEPS` (internal) to 1 and to 61, extract again, and compare the step-to-step scatter of the brightness about a smooth curve.

  ```python
  from scipy.ndimage import median_filter

  from astrometricslib.pipelines.spectroscopy.pre_processing import spectrum_extractor  # internal

  for steps in (1, 61):
      spectrum_extractor.APERTURE_SIGMA_SMOOTHING_STEPS = steps
      raw = SpectroscopyPipeline().process_image(AstrometricsImage("stack.fits"), target_stars=[(x0, y0)])[0]
      counts = np.asarray(raw["intensities"])
      print(steps, np.nanstd(counts / median_filter(counts, 15) - 1.0))
  ```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The relative scatter of the brightness about its smooth curve (a fraction; lower is steadier). Unsmoothed widths give sharp steps; smoothed ones should not. The fitted width itself (pixels) should change slowly over hundreds of steps.
- **Our result:** Vega's trail: 563 steps; fitted sigma 1.2 px at 4200 Å rising to 1.9 px at 5000 Å over about 400 steps. Synthetic frames (bright trail, sigma 1.78 px, about 0.6% noise per column): an unsmoothed box gives steps of about 2%; the smoothed box gives a flat continuum.
- **Verdict:** Not judged.
- **Limits:** The window length was not swept. A trail whose width changes faster than over about 60 steps (a very short trail) would be followed less well.
- **To tune for your setup:** For a much shorter trail, lower the window toward a tenth of the trail's length. Re-run the comparison above.
- **Regenerate with:** No script. The Vega width numbers were read by hand from the stored trail width.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_extraction_aperture_stability.py`.
- **Raw record:** None kept.

### SPE-05

**Resolution element from the trail width, and the 45 Å fallback**

- **Claim:** On Vega's stack the trail's cross-section gives a blur of 35 to 43 Å across the middle of the spectrum (about 40 Å at 5000 Å), and the estimator gives 43.3 Å for the whole trail. The fixed fallback of 45 Å sits inside both this and the zero-order image width (48 to 49 Å). Vega's classification score barely changes between 30, 43.3 and 45 Å.
- **Settings it supports:**
  - code constant `FALLBACK_RESOLUTION_ELEMENT_ANGSTROM` = 45.0 in `astrometricslib/pipelines/spectroscopy/pre_processing/spectral_resolution.py`. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution.FALLBACK_RESOLUTION_ELEMENT_ANGSTROM -->
  - code constant `MINIMUM_FITTED_TRAIL_WIDTH_FRACTION` = 0.5 in the same file. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution.MINIMUM_FITTED_TRAIL_WIDTH_FRACTION -->
- **Status:** Partly measured. One star on one stack.
- **Data you need:** A spectral stack of a bright A-type star (strong Balmer lines) with a traced extraction, and a stored instrument response for the classification part.
- **Procedure:**
  1. Take the raw extraction and pass its wavelengths and `trail_width_px` to `estimate_resolution_element_angstrom` (internal) and `estimate_resolution_profile` (internal).
  2. To test robustness, set a random share of the widths to zero (failed fits) and estimate again.
  3. Classify the calibrated result's response-corrected spectrum with `classify_spectral_type` (internal) at several blur widths.

  ```python
  from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (  # internal
      estimate_resolution_element_angstrom,
      estimate_resolution_profile,
  )
  from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import classify_spectral_type  # internal

  print(estimate_resolution_element_angstrom(wavelength, raw["trail_width_px"]))
  print(estimate_resolution_profile(wavelength, raw["trail_width_px"]))
  for width in (30.0, 45.0):
      result = classify_spectral_type(
          np.asarray(spectrum.wavelengths_angstrom), np.asarray(spectrum.response_corrected_intensities),
          resolution_element_angstrom=width,
      )
      print(width, result["spectral_type"], result["classification_rms"])
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution.estimate_resolution_element_angstrom -->
  <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution.estimate_resolution_profile -->
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_classifier.classify_spectral_type -->
- **Acceptance criterion:** The resolution element agrees with the zero-order width converted to Å and moves little when a third to a half of the fits fail.
- **What to look at:** The resolution element, a FWHM in Å. It should agree with the zero-order width converted to Å and should not move much when a third to a half of the fits fail. The classification RMS (relative RMS, lower is closer) should change little across nearby widths.
- **Our result:** Vega master stack (140 frames, vertical dispersion, traced extraction, sky subtraction on), 2026-09-19. Instrument: 3.76 µm pixels, 200 lines/mm grating, grating distance fitted then at 16.54 mm. Dispersion: 11.37 Å/px (small-angle formula), 10.96 to 11.24 Å/px (instrument model), 11.04 Å/px (median sample spacing).

  | Measure | Value |
  | --- | --- |
  | Zero-order FWHM | 4.39 px (x), 4.45 px (y), about 48 to 49 Å; the peak is the frame maximum and may be saturated, which would overestimate |
  | Trail FWHM by band (px) | 3800 to 4200: 6.6; 4200 to 4600: 2.57; 4600 to 5000: 3.81; 5000 to 5500: 3.86; 5500 to 6000: 3.41; 6000 to 6600: 3.15; 6600 to 7200: 3.32; 7200 to 8000: 4.61 |
  | Trail blur, middle bands | 35 to 43 Å |
  | Estimator on the whole trail | 563 samples, all fitted; median sigma 1.666 px, FWHM 3.92 px, 43.3 Å |
  | Estimator on parts | blue half below 6000 Å 41.1 Å; red half 51.7 Å; 4600 to 7200 Å 37.8 Å |
  | Random fit failures | 30% failed: 43.8 Å; 50%: 43.5 Å; 60% and 80%: no estimate |
  | Template blur that matches Vega's line depths | H-alpha 60 Å, H-beta 60 Å, H-gamma 45 to 50 Å, H-delta 30 Å |
  | Vega's observed depths | H-alpha 5.1%, H-beta 8.7%, H-gamma 12.8%, H-delta 23.3% |
  | A0V template depths at 30 Å blur | 14.3, 19.4, 20.0, 21.7% |
  | A0V template depths at 45 Å blur | 10.4, 14.3, 13.4, 14.2% |
  | Template pairs with a dip of at least 1% (of 272) | 210 at 30 Å (median depth 7.6%); 189 at 45 Å (5.7%) |
  | Vega classification RMS (A0V) | 0.0843 at 30 Å; 0.0774 at 45 Å; 0.0781 at 43.3 Å |
  | Change of the re-derived response's shape against the stored one | 0.37% at 30 Å, 0.39% at 45 Å, 0.38% at 43.3 Å |

  The value used before 45 Å was 30 Å. It was never measured: it came from assuming a resolving power of about 150, and it made the expected line depths too deep (H-beta 19% instead of 14%).
- **Verdict:** Not judged.
- **Limits:** One star on one stack. The estimate assumes a round star image, so the width across the trail equals the blur along it. The pixel size and grating fix only the Å per pixel; the blur depends on seeing and focus.
- **To tune for your setup:** Measure your own trail width this way and set the fallback near it. Re-run the classification check at the new value.
- **Regenerate with:** No script; measured by hand on 2026-09-19.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_spectral_resolution.py`.
- **Raw record:** [`documentation/evidence/spectral_resolution_estimate_vega_20260919.json`](../../evidence/spectral_resolution_estimate_vega_20260919.json).

### SPE-06

**The stored line-spread profile**

- **Claim:** The Gaussian blur that makes each catalog-type reference spectrum match the observed line depths of three bright stars grows from about 45 Å near 4400 Å to 148 Å at H-alpha. Between those, it follows a straight line of 119 Å plus 27 Å per 1000 Å about 5500 Å. Above about 4400 Å it is wider than the trail width.
- **Settings it supports:**
  - data file `astrometricslib/pipelines/spectroscopy/data/line_spread_zwo_asi_533mm_pro.json` (42, 45, 102, 110, 130, 148 and 148 Å at 4200, 4400, 4861, 5175, 5893, 6563 and 9000 Å), read by `load_line_spread_profile`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution.load_line_spread_profile -->
- **Status:** Partly measured. Three stars, one setup; the values below 4400 Å come from trail widths and the value beyond 6563 Å is held constant because nothing was measured there.
- **Data you need:** Spectral stacks of three or more bright stars of well-known type from one night and one focus setting, covering lines across the spectrum (Balmer lines, Mg b, Na D). Cool stars give Mg b and Na D; hot stars give the Balmer lines. Hold out a few more stars to check the result.
- **Procedure:**
  1. Take each star's calibrated result.
  2. For each line and each trial blur width, blur the star's catalog-type reference with `blurred_reference_spectrum` (internal) and measure the depth of the line in the reference and in the observed spectrum with `measure_local_dip_depth` (internal).
  3. Keep the width at which the two depths agree. Fit a smooth curve through the widths of all stars and lines.
  4. On the held-out stars, read `measured_vs_stored_line_spread_ratio_halpha` from checkpoint 1.

  ```python
  from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import blurred_reference_spectrum  # internal
  from astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector import measure_local_dip_depth  # internal

  observed_wl = np.asarray(spectrum.wavelengths_angstrom)
  observed = np.asarray(spectrum.response_corrected_intensities)
  for line in (4861.0, 5175.0, 5893.0, 6563.0):
      for width in np.arange(30.0, 200.0, 5.0):
          ref_wl, ref = blurred_reference_spectrum(catalog_type, resolution_element_angstrom=width)  # e.g. "K0III"
          ref_depth = measure_local_dip_depth(ref_wl, ref, line, 0.5 * width, width)
          obs_depth = measure_local_dip_depth(observed_wl, observed, line, 0.5 * width, width)
          print(line, width, ref_depth, obs_depth)
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_classifier.blurred_reference_spectrum -->
- **Acceptance criterion:** On held-out stars, the H-alpha ratio of measured to stored blur is near 1.
- **What to look at:** The blur width (FWHM, Å) at which reference and observed depths match, for each line. A smooth rise toward the red is expected for a grating in a converging beam. On held-out stars, the H-alpha ratio of measured to stored blur should be near 1.
- **Our result:** Fitted 2026-09-26 to the Balmer, Mg b and Na D line depths of Schedar, Mirach and Mirfak (frames of 2026-09-25, Star Analyzer 200 on the Apertura 75Q). Vega, Albireo B, Deneb and Albireo A were held out. Vega's H-gamma at 4340 Å needed 41 Å, where the trail says 36 to 45 Å. Focuser temperature 18 to 22 °C across the stars.
- **Verdict:** Not judged.
- **Limits:** A fit to line depths may include scattered light that the trail width does not see. A star whose true line strength differs from its catalog-type template (luminosity class, metal content) biases the width. One setup only.
- **To tune for your setup:** Refit after any change of focus, optics or grating. Until then, the option `SpectroscopyPipeline(config, use_measured_line_spread=True)` blurs with each spectrum's own trail-width profile instead (design estimate; see the table below).
- **Regenerate with:** No script; fitted by hand on 2026-09-26.
- **Guarded by tests:** `test_the_stored_line_spread_grows_toward_the_red_and_is_found_by_camera` in `astrometricslib/pipelines/spectroscopy/test/test_spectral_resolution.py` (checks the file, not the fit).
- **Raw record:** None kept.

### SPE-07

**The per-sample error model**

- **Claim:** On one small synthetic frame, the reported 1σ error of each sample matches the scatter over many noise realizations. The median ratio of reported error to measured scatter is 1.00 for the flare-mask extraction with sky subtraction, 0.97 for the dispersion-line extraction and 0.96 for the traced dispersion-line extraction. The traced flare-mask extraction scatters more than its errors (median ratio 0.73, and 0.2 at the 5th percentile of the samples). The `+ 1.4` and `+ 0.4` terms of the small-sample median variance match a simulation to about 1%.
- **Settings it supports:**
  - code constants `MEDIAN_VARIANCE_EVEN_OFFSET` = 1.4 and `MEDIAN_VARIANCE_ODD_OFFSET` = 0.4 in `astrometricslib/pipelines/spectroscopy/pre_processing/intensity_variance.py`. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance.MEDIAN_VARIANCE_EVEN_OFFSET --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance.MEDIAN_VARIANCE_ODD_OFFSET -->
  - the noise model `PixelNoiseModel` in the same file. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance.PixelNoiseModel -->
- **Status:** Partly measured. Synthetic frames only.
- **Data you need:** Ten or more single (unstacked) frames of one bright star, same exposure, taken close together, with the camera's gain and read noise in its profile or the `EGAIN` and `RDNOISE` header cards.
- **Procedure:**
  1. Run the calibrated result on each frame separately.
  2. Put every frame's spectrum on one wavelength grid.
  3. Compare the scatter between frames at each wavelength with the median reported error.

  ```python
  grid = np.arange(4200.0, 8000.0, 11.0)
  values, errors = [], []
  for path in frame_paths:
      context = AstrometryPipeline().process(path, attempt_plate_solving=False)
      context.stellar_objects = [select_standard_star(context.stellar_objects, context.image.data.shape)]
      result = SpectroscopyPipeline().process(context, limit=1)[0].spectroscopy
      wl = np.asarray(result.wavelengths_angstrom)
      values.append(np.interp(grid, wl, result.intensities))
      errors.append(np.interp(grid, wl, result.intensity_errors))
  ratio = np.median(errors, axis=0) / np.std(values, axis=0, ddof=1)
  print(np.median(ratio))
  ```

- **Acceptance criterion:** The ratio of reported error to measured scatter is near 1.
- **What to look at:** The ratio of reported error to measured scatter, unitless. Near 1 means the errors are right. Below 1 means the errors are too small (the data scatter more than they say); above 1, too large. Seeing and pointing change between real frames, so a real ratio somewhat below 1 is expected.
- **Our result:** One small synthetic frame. Flare-mask with sky subtraction: 1.00 (200 realizations). Dispersion line: 0.97. Traced dispersion line: 0.96 (60 realizations). Traced flare mask: 0.73 median and 0.2 at the 5th percentile (200 realizations). Its box switched between the fitted width and the fixed fallback width from one realization to the next, because the width fit fails in about 40% of the steps on that small frame.
- **Verdict:** Not judged.
- **Limits:** Synthetic frames only. The model leaves out the covariance between neighboring samples, the errors of the QE curve and the instrument response, the error of the neighbor-wing subtraction, and the scatter from the traced box moving between exposures.
- **To tune for your setup:** Nothing to tune directly. If the real ratio is far below 1, check the gain and read noise in the camera profile (the checkpoint 1 flags `gain_assumed` and `read_noise_assumed`).
- **Regenerate with:** No script. The traced flare-mask ratio of 0.73 is not asserted by a test; it was measured by hand.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/pre_processing/test_intensity_variance.py`: `test_flare_mask_errors_match_the_scatter_over_200_noise_realizations`, `test_dispersion_line_errors_match_the_scatter_between_columns`, `test_traced_dispersion_line_errors_match_the_scatter`, `test_median_variance_factor_matches_a_simulation`.
- **Raw record:** None kept.

### SPE-08

**Variance where the stacker rejected frames**

- **Claim:** On real stacks the rejection map raises the variance of the brightest pixels (the star and its trail) by a factor of 1.02 to 1.28 for most targets, and by up to 1.48. Without the map, the reported variance of those stacks is too small by 21 to 32% (the errors by 11 to 18%). On simulated stacks that lost frames, the model with the map recovers the known variance to 1%, and the model without it reports under 70% of it.
- **Settings it supports:**
  - the use of the stack's rejection map (`<stem>_RejMap.fits`) through `find_rejection_map` and `rejection_variance_scale` in `astrometricslib/pipelines/spectroscopy/pre_processing/intensity_variance.py` (no tuned value). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance.find_rejection_map --> <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance.rejection_variance_scale -->
- **Status:** Measured. 19 real spectral stacks and a simulation.
- **Data you need:** Spectral stacks made by Siril with their rejection maps beside them, and the `STACKCNT` header card.
- **Procedure:**
  1. Find the rejection map with `find_rejection_map` (internal).
  2. Turn its rejected fractions into variance factors with `rejection_variance_scale` (internal).
  3. Average the factor over the brightest pixels of the stack.

  ```python
  from astropy.io import fits

  from astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance import (  # internal
      find_rejection_map,
      rejection_variance_scale,
  )

  stack = fits.getdata("stack.fits", memmap=False)
  frames = int(fits.getheader("stack.fits")["STACKCNT"])
  rejected = fits.getdata(find_rejection_map("stack.fits"), memmap=False)
  scale = rejection_variance_scale(rejected, frames)
  bright = stack > np.nanpercentile(stack, 99.9)
  print(np.mean(scale[bright]))
  ```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The mean variance factor over the bright pixels (unitless, at least 1). Near 1, the map changes little. Well above 1, the errors without the map would be too small by the square root of that factor.
- **Our result:** 19 real spectral stacks, 2026-10-11. Most targets 1.02 to 1.28. Alnath 1.27, Vega 1.28, Arcturus 1.48; for these the variance without the map was too small by 21 to 32% (errors by 11 to 18%). Simulation: a 9-pixel cross-section of a 20-frame stack where even pixels kept 8 frames; the model with the map matches the known variance to 1%, the model without it reports under 70%.
- **Verdict:** Not judged.
- **Limits:** The sky term is not scaled, because it uses the scatter measured in the sky strips.
- **To tune for your setup:** Nothing to tune. Keep the rejection map next to the stack.
- **Regenerate with:** No script. The 19-stack survey: no script; measured by hand on 2026-10-11.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/pre_processing/test_intensity_variance.py`: `test_the_box_variance_follows_the_frames_each_pixel_kept`, `test_the_extractor_reports_more_variance_where_frames_were_rejected`, `test_a_rejection_map_is_found_beside_the_stack_and_its_processed_picture`.
- **Raw record:** None kept.

### SPE-09

**The two signal-to-noise estimates**

- **Claim:** On four synthetic frames run through the whole pipeline with the true gain and read noise, the ratio of the variance-based signal-to-noise to the post-hoc one (`snr_estimate_ratio`) was 1.0 to 1.8. The post-hoc estimate alone ranged from 29 to 69, and from 40 to 69 between two noise seeds of one frame with the traced extraction.
- **Settings it supports:**
  - the decision to report `median_snr_per_resolution_element`, `fraction_samples_snr_below_5` and `snr_estimate_ratio` without a limit (`summarize_spectrum_noise` in `astrometricslib/pipelines/spectroscopy/pre_processing/intensity_variance.py`). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance.summarize_spectrum_noise -->
- **Status:** Partly measured. Four synthetic frames.
- **Data you need:** Spectral stacks of stars over a range of brightness, with known gain and read noise.
- **Procedure:**
  1. Take the calibrated result.
  2. Read the checkpoint 1 metrics.

  ```python
  checkpoint = next(c for c in spectrum.stage_quality if c.stage == "pre_processing")
  values = {m.name: m.value for m in checkpoint.metrics}
  print(values["signal_to_noise"], values["median_snr_per_resolution_element"], values["snr_estimate_ratio"])
  ```

- **Acceptance criterion:** `snr_estimate_ratio` is near 1.
- **What to look at:** `snr_estimate_ratio`, unitless. Near 1 means the two estimates agree. Far from 1 means either the gain or read noise in the model is wrong or the spectrum scatters for a reason the model does not include.
- **Our result:** Four synthetic frames: ratio 1.0 to 1.8; post-hoc estimate 29 to 69.
- **Verdict:** Not judged.
- **Limits:** The post-hoc estimate is itself noisy, so a ratio up to about 2 can come from it alone.
- **To tune for your setup:** Collect the ratio over many real spectra (the run summary keeps its median per run) before choosing a limit.
- **Regenerate with:** No script. The four-frame numbers: no script; measured by hand (date not recorded).
- **Guarded by tests:** `test_the_variance_estimate_agrees_with_the_post_hoc_estimate_on_noise` in `astrometricslib/pipelines/spectroscopy/test/pre_processing/test_intensity_variance.py` (a flat synthetic spectrum, agreement to 20%).
- **Raw record:** None kept.

### SPE-10

**The minimum signal-to-noise for analyzing a spectrum**

- **Claim:** On 170 stored spectra, the two stars that are pure noise score 0.4 and 0.3, and the faintest real spectra score 0.9, 1.4 and 2.1, then 2.3 and 2.7 (median 3.8). A cut at 1.5 stops the same four faintest stars that an earlier cut of 2.0 stopped with an earlier estimator.
- **Settings it supports:**
  - code constant `MINIMUM_SPECTRUM_SIGNAL_TO_NOISE` = 1.5 in `astrometricslib/pipelines/spectroscopy/processing/spectrum_signal.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectrum_signal.MINIMUM_SPECTRUM_SIGNAL_TO_NOISE -->
- **Status:** Partly measured. Only two pure-noise spectra exist in that set.
- **Data you need:** All stored spectra of a library, including some known to be pure noise (stars fainter than the frames can reach).
- **Procedure:** For every stored spectrum, call `estimate_spectrum_signal_to_noise` (internal) and sort the scores.

  ```python
  from astrometricslib import Astrometrics
  from astrometricslib.pipelines.spectroscopy.processing.spectrum_signal import estimate_spectrum_signal_to_noise  # internal

  astrometrics = Astrometrics()
  ids = [s.id for s in astrometrics.catalog_access.list_star_summaries() if s.has_spectra]
  for star in astrometrics.catalog_access.get_by_ids("stellar_catalog", ids):
      s = star.spectroscopy
      print(star.id, estimate_spectrum_signal_to_noise(
          np.asarray(s.wavelengths_angstrom), np.asarray(s.intensities), s.resolution_element_angstrom or 45.0))
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectrum_signal.estimate_spectrum_signal_to_noise -->
- **Acceptance criterion:** Pure-noise spectra fall below the cut and real ones above it, with a gap between them.
- **What to look at:** The signal-to-noise per resolution element (unitless). Pure-noise spectra should fall below the cut and real ones above it, with a gap between them.
- **Our result:** 170 spectra stored on 2026-09-25 (137 on 2026-09-21). Pure noise (Gaia DR3 2097952543255224448 and 2098050434148460544, magnitude 13.6 to 13.7, 79 to 81% of samples at or below zero): 0.4 and 0.3. Faintest real spectra: 0.9, 1.4, 2.1, 2.3, 2.7. Median 3.8. The earlier estimator scored the four faintest 0.5, 0.7, 1.2 and 1.9 and the next ones 2.6, 3.1 and 3.7; the current one reads about 0.66 of it on noisy spectra.
- **Verdict:** Not judged.
- **Limits:** Two pure-noise spectra. Not tested on a broad mix of faint stars.
- **To tune for your setup:** If your faint stars pass the cut but give meaningless types, raise it. Re-run the survey.
- **Regenerate with:** No script. The survey: no script; measured by hand on 2026-09-25.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_spectrum_signal_gate.py`.
- **Raw record:** None kept.

### SPE-11

**Line significance for the wavelength zero point**

- **Claim:** On synthetic noise-only spectra, no fake line reached 6 noise widths: 4 of 9000 trials reached 5.0 and the highest was 5.85. Real lines in a clean spectrum reach 20 or more.
- **Settings it supports:**
  - code constant `MINIMUM_LINE_SIGNIFICANCE` = 6.0 in `astrometricslib/pipelines/spectroscopy/pre_processing/wavelength_zero_point.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.MINIMUM_LINE_SIGNIFICANCE -->
- **Status:** Partly measured. Synthetic noise only; not checked on real faint spectra, whose noise may be correlated.
- **Data you need:** None for the synthetic part. For the real part, calibrated results of stars over a range of brightness.
- **Procedure:**
  1. Set the module constant to 0 (internal) so that every best dip is returned, and run `measure_line` (internal) for each line in `ZERO_POINT_LINES` on many noise-only spectra.
  2. Record the largest significance. On real spectra, read `wavelength_zero_point.lines[i].significance` of the calibrated result.

  ```python
  from astrometricslib.pipelines.spectroscopy.pre_processing import wavelength_zero_point as zp  # internal

  zp.MINIMUM_LINE_SIGNIFICANCE = 0.0
  rng = np.random.default_rng(0)
  wl = np.arange(4000.0, 8000.0, 11.3)
  best = []
  for _ in range(1000):
      flux = 1000.0 + rng.normal(0.0, 20.0, wl.size)
      for line in zp.ZERO_POINT_LINES:
          found = zp.measure_line(wl, flux, line, 40.0)
          if found is not None:
              best.append(found.significance)
  print(np.max(best), np.mean(np.array(best) >= 6.0))
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.measure_line -->
  <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.ZERO_POINT_LINES -->
- **Acceptance criterion:** The cut sits clearly above the largest significance that noise alone reaches.
- **What to look at:** The largest significance (in noise widths) that noise alone reaches. The cut should sit clearly above it.
- **Our result:** 1500 flat trails with Poisson and read noise, line spread 40 Å (9000 trials): 4 reached 5.0, 1 reached 5.5 (5.85), none reached 6.0. 1000 sloped, curved continua with 2% white noise at 11.3 Å per sample with the stored line spread of 100 to 150 Å (6000 trials): 1 reached 5.0, none reached 6.0.
- **Verdict:** Not judged.
- **Limits:** Synthetic white or Poisson noise. Real spectra have correlated noise and response errors.
- **To tune for your setup:** If real noise-only stretches reach 6, raise the cut.
- **Regenerate with:** No script. The trial counts: no script; simulated by hand (date not recorded).
- **Guarded by tests:** `test_a_noise_only_spectrum_finds_no_lines` and `test_a_25_angstrom_zero_point_error_with_three_lines_is_corrected` in `astrometricslib/pipelines/spectroscopy/test/pre_processing/test_wavelength_zero_point.py`.
- **Raw record:** None kept.

### SPE-12

**Fitting the grating distance (wavelength calibration)**

- **Claim:** On synthetic frames with Balmer lines at known positions, the calibration tuner recovers each line to 0.5 px and the grating-to-sensor distance to 1%, whether or not leading samples were dropped. A noisy frame gives at most 12 candidate dips, including the three true lines, and finishes in under 10 s. Refined dip centers bring the fitted distance within 0.02% of the truth. Each flare-mask sample distance lands within 0.01 px of the true distance. On the real Vega stack, the fit left an RMS error of 0.42 nm over H-beta, H-gamma and H-delta.
- **Settings it supports:**
  - the fit's rejection tolerance of 10 nm RMS (a literal in `SpectroscopyCalibrationTuner._fit_grating_distance`, `astrometricslib/pipelines/spectroscopy/utilities/calibration_tuner.py`). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner.SpectroscopyCalibrationTuner._fit_grating_distance -->
  - code constant `_MAX_DIP_CANDIDATES` = 12 in the same file. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner._MAX_DIP_CANDIDATES -->
  - the result is written to config key `[Observatory.Camera.<camera name>] grating_distance_mm` (fallback 0, which the pipeline reports as a missing hardware setting; 16.49 in the example config for the ASI 533MM Pro). Relation: derived from this entry.
- **Status:** Partly measured. The accuracy claims are synthetic; the one real fit was made with an earlier version of the extraction (see the first validation campaign, [index.md](index.md#6-validation-sessions) Section 6).
- **Data you need:** A spectral stack of a bright A-type star with strong Balmer lines (Vega is ideal), its trail unsaturated, and the zero-order position if the star is not the one nearest the frame center.
- **Procedure:**
  1. Back up the config file: the tuner writes the fitted distance into it.
  2. Run the tuner through the public API and read the RMS error and the per-line deviations.

  ```python
  from astrometricslib import Astrometrics

  summary = Astrometrics().stars.tune_spectroscopy_calibration("vega_stack.fits", camera_id="ZWO ASI 533MM Pro")
  print(summary["rms_error_nm"])
  ```
  <!-- impl: astrometricslib.StellarCatalog.tune_spectroscopy_calibration -->
- **Acceptance criterion:** An RMS well below one pixel of dispersion (about 1.1 nm). A fit above 10 nm is refused.
- **What to look at:** `rms_error_nm`, the RMS difference between the fitted and the true wavelengths of the three lines, in nm. One pixel of dispersion is about 1.1 nm; an RMS well below that is good. Above 10 nm the fit is refused.
- **Our result:** Synthetic frames: measured value not recorded (the tests assert the bounds listed under Guarded by tests). On a noisy frame (3000 ADU continuum with Poisson noise), the unfiltered search had found about 60 dips and run for minutes. Real data: Vega session (DS-VEGA), RMS 0.42 nm (4.2 Å) over H-beta, H-gamma and H-delta.
- **Verdict:** Not judged.
- **Limits:** The Vega figure predates the current extraction and has not been repeated. Three lines fit one free parameter, so the RMS has only two degrees of freedom.
- **To tune for your setup:** Run the tuner once per camera and grating setup, and again after any mechanical change to the grating spacing.
- **Regenerate with:** `python -m astrometricslib.scripts.run_empirical_validation` (runs the tuner on Vega and prints the RMS).
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/utilities/test/test_calibration_tuner_sample_distances.py` (`test_tuner_recovers_lines_and_grating_distance`, `test_noisy_adu_frame_gives_few_candidates_and_finishes_quickly`, `test_refined_dip_centres_improve_the_fitted_distance`, `test_refined_dip_positions_hold_for_a_zero_order_between_pixels`, `test_flare_mask_sample_distances_match_the_true_zero_order`) and `test_calibration_tuner_dip_detection.py`. The tests assert these bounds on synthetic frames. Line positions are within 0.5 px and the grating distance within 1%, with and without dropped samples. A noisy frame gives at most 12 candidate dips, including the three true lines, in under 10 s. With refined dip centers, the fitted distance is within 0.02% of the truth on both the dispersion-line and flare-mask extractions, with zero orders between pixels. Flare-mask sample distances are within 0.01 px of the true distance. A smooth valley's center is within 0.1 sample.
- **Raw record:** None kept.

### SPE-13

**The instrument response**

- **Claim:** Without the instrument response, every reference spectrum correlates at 0.95 or higher with every observed star, and the classifier matched Vega to F6V instead of A0V. Blurring the reference to one width when fitting the response leaves the response up to 0.5% wrong (a 0.3% red-to-blue tilt) at 45 Å and up to 2.6% wrong at 119 Å, while the wavelength-dependent line spread recovers it exactly. With the response, the second star of the Alcor pair (A5V) matches A5V or A7V and HD 151023 (K0) matches K2V.
- **Settings it supports:**
  - data file `astrometricslib/pipelines/spectroscopy/data/instrument_response_zwo_asi_533mm_pro.json`, read by `load_instrument_response`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response.load_instrument_response -->
  - code constant `DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM` = (4200, 8000) in `astrometricslib/pipelines/spectroscopy/pre_processing/instrument_response.py`. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response.DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM -->
- **Status:** Partly measured. The template-blur error is synthetic, and the check on stars uses two stars of independently known type.
- **Data you need:** A master spectral stack of a bright standard star of known type (an A0V star such as Vega), and a few stars of known type observed with the same setup. A second standard star from the same night checks that the response is a property of the instrument.
- **Procedure:**
  1. Derive the response with the script (it writes into the data folder; copy the old file first), or call `derive_instrument_response` (internal) on a calibrated result to keep it in memory.
  2. Derive a second response from another standard star and compare the two with `compare_instrument_responses.py`.
  3. Classify the check stars and compare with their catalog types (SPE-16).

  ```python
  from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (  # internal
      apply_instrument_response,
      derive_instrument_response,
  )
  from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import load_line_spread_profile  # internal

  response = derive_instrument_response(
      np.asarray(spectrum.wavelengths_angstrom),
      np.asarray(spectrum.quantum_efficiency_corrected_intensities),
      "A0V", "ZWO ASI 533MM Pro", source="check",
      line_spread_profile=load_line_spread_profile("ZWO ASI 533MM Pro"),
  )
  other = check_star.spectroscopy  # the calibrated result of a check star, taken as in call (b)
  corrected = apply_instrument_response(np.asarray(other.wavelengths_angstrom),
                                        np.asarray(other.quantum_efficiency_corrected_intensities), response)
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response.derive_instrument_response -->
  <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response.apply_instrument_response -->
- **Acceptance criterion:** A fractional difference below 0.02 passes the comparison script.
- **What to look at:** From `compare_instrument_responses.py`, the largest and RMS fractional difference between two responses and the difference in red-to-blue tilt (fractions; below 0.02 passes the script). For the check stars, the subtype steps from the catalog type.
- **Our result:** The stored response came from Vega (40 frames of 0.25 s, 2026-09-25, airmass 1.15). Without any response, every reference correlated at 0.95 or more with every star, and Vega matched F6V. Synthetic Vega observed through the stored line spread: response error up to 0.5% (0.3% tilt) with one 45 Å blur, up to 2.6% with one 119 Å blur, none with the wavelength-dependent blur. Check stars: Alcor pair star 2 (A5V) matched A7V, 2 steps, RMS 0.049 in the score calibration of 2026-09-19, and A5V in the atmospheric-band test (SPE-15); HD 151023 (K0) matched K2V. Most other stored spectra did not match any reference closely at that time.
- **Verdict:** Not judged.
- **Limits:** Vega is not independent of a response fitted on Vega. The stored coefficients came from a fit that blurred the reference to one width and skipped a fixed 60 Å band around each line, so they carry some of Vega's line wings. Two responses made at different airmasses differ by the air's extinction, which the comparison script reports as a mismatch.
- **To tune for your setup:** Re-derive after any change to camera, grating or telescope. Use a standard star high in the sky and record its airmass.
- **Regenerate with:** `python -m astrometricslib.scripts.derive_instrument_response [--target Vega] [--reference-type A0V] [--maximum-wavelength 8000] [--reference-airmass X]`; `python -m astrometricslib.scripts.compare_instrument_responses first.json second.json --tolerance 0.02`; `python -m astrometricslib.scripts.validate_spectral_and_period_analysis` (real standards and stored typed stars). The "0.95 correlation, Vega as F6V" observation: no script or record found.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_response_template_blur.py`.
- **Raw record:** [`documentation/evidence/spectral_score_calibration_20260919.json`](../../evidence/spectral_score_calibration_20260919.json).

### SPE-14

**Upper wavelength limit of the comparison (8000 Å)**

- **Claim:** Extending the comparison and the response fit to 10000 Å leaves the match residual of Vega, HD 151023 and the second star of the Alcor pair about the same (0.08, 0.11 and 0.08) but raises it sharply for other stars. HD 150679 (A2) goes from 0.19 to 0.28 to 0.5 to 1.8. Weighting each wavelength by the sensor's sensitivity does not fix it: HD 150679 then matches F6V.
- **Settings it supports:**
  - code constant `DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM` = (4200, 8000) in `astrometricslib/pipelines/spectroscopy/pre_processing/instrument_response.py`, and the derivation script's `--maximum-wavelength` option. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response.DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM -->
- **Status:** Partly measured. Four stars, no recorded date or script.
- **Data you need:** A standard-star stack and a handful of stars of known type, including some bright in the blue (A and B stars), whose red end is most exposed to second-order light.
- **Procedure:**
  1. Derive a response in memory for each upper limit with `derive_instrument_response(..., wavelength_range_angstrom=(4200.0, upper))` (internal; see SPE-13).
  2. Apply it to each check star and classify with `classify_spectral_type` (internal).
  3. Compare the best RMS and the type for each upper limit.
- **Acceptance criterion:** A limit is too high when stars bright in the blue get much worse.
- **What to look at:** The best match's relative RMS (lower is closer) and the subtype steps from the catalog type, per star and per upper limit. A limit is too high when stars bright in the blue get much worse.
- **Our result:** Vega (A0V), HD 151023 (K0), Alcor pair star 2 (A5V): residual about 0.08, 0.11 and 0.08 at either limit. HD 150679 (A2): 0.19 to 0.28 at 8000 Å, 0.5 to 1.8 at 10000 Å; F6V with sensitivity weighting.
- **Verdict:** Not judged.
- **Limits:** Few stars. A setup with a filter that blocks second-order light may gain from the full range.
- **To tune for your setup:** With a second-order blocking filter, re-derive with `--maximum-wavelength 10000` and re-run the comparison.
- **Regenerate with:** No script; measured by hand (date not recorded).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### SPE-15

**The atmospheric bands left out of comparisons**

- **Claim:** Between 5000 and 8400 Å, the dips deeper than 3% that Earth's air puts into this setup's spectra lie in three bands: oxygen B (6860 to 6940 Å), oxygen A (7490 to 7720 Å) and water (8100 to 8320 Å). Leaving them out of the classification does not make any of 11 test stars worse at upper limits of 7300 Å and above. Leaving them out of the feature detector would change 12.5% of its verdicts, so the detector keeps them.
- **Settings it supports:**
  - code constant `ATMOSPHERIC_BANDS_ANGSTROM` in `astrometricslib/pipelines/spectroscopy/pre_processing/atmospheric_mask.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_mask.ATMOSPHERIC_BANDS_ANGSTROM -->
- **Status:** Partly measured. Two stars for the band edges, 11 stars for the classification test.
- **Data you need:** Stacks of two or more bright A-type stars of known type (few lines of their own in the red), with an instrument response.
- **Procedure:**
  1. Divide each star's response-corrected spectrum by its catalog-type reference blurred to about 43 Å, then by a 600 Å running median.
  2. List the stretches more than 3% below 1 between 5000 and 8400 Å.
  3. Classify stars of known type with `classify_spectral_type(..., exclude_atmospheric_bands=True)` and `False` (internal), for several upper limits, and compare the steps from the catalog type.

  ```python
  from scipy.ndimage import median_filter

  wl = np.asarray(spectrum.wavelengths_angstrom)
  flux = np.asarray(spectrum.response_corrected_intensities)
  ref_wl, ref = blurred_reference_spectrum("A0V", resolution_element_angstrom=43.0)
  ratio = flux / np.interp(wl, ref_wl, ref)
  window = int(round(600.0 / np.median(np.diff(wl))))
  ratio = ratio / median_filter(ratio, size=window)
  dips = (wl > 5000.0) & (wl < 8400.0) & (ratio < 0.97)
  ```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The start, end and deepest percentage of each dip; the mean steps from the catalog type and the count of stars with the right letter, mask off against mask on.
- **Our result:** Vega master stack (A0V, 2026-05-24) and Alcor pair star 2 (A5V, 2026-05-02), same camera and telescope, analyzed 2026-09-19.

  | Band | Vega | Alcor pair star 2 |
  | --- | --- | --- |
  | Oxygen B | 6882 to 6915 Å, 4.6% | under 3% |
  | Oxygen A | 7543 to 7686 Å, 16% (also 7510 to 7521 Å, 3.0%) | 7521 to 7697 Å, 14.4% |
  | Water | 8123 to 8298 Å, 11.5% | 8134 to 8167 Å 4.7%, 8200 to 8211 Å 4.3%, 8254 to 8265 Å 3.4% |

  Also seen in both stars at 3 to 5%, outside any named band and left out of the mask: 7740 to 7773 Å and 7894 to 7916 Å. Alcor star 2 also showed 5449 to 5461 Å at 3.2%. Not seen above 3%: the oxygen band near 6280 Å and the water band near 7160 to 7340 Å. Each chosen band is the widest measured extent plus about 20 to 30 Å.

  Classification test (11 stored spectra with a catalog type, response re-derived from Vega for each upper limit), mask off then on:

  | Upper limit (Å) | Mean steps off | True type in top 3 | Letter right | Good match |
  | --- | --- | --- | --- | --- |
  | 7000 | 7.4, 8.5 | 6, 5 | 6, 5 | 5, 6 |
  | 7300 | 9.3, 9.3 | 3, 3 | 5, 5 | 4, 4 |
  | 7600 | 11.0, 10.4 | 2, 2 | 4, 4 | 3, 3 |
  | 7800 | 11.2, 10.9 | not recorded | not recorded | not recorded |
  | 8000 | 11.3, 11.0 | 2, 2 | 4, 4 | 3, 3 |

  No star got worse at or above 7300 Å. At 7000 Å, g UMa went from 0 to 2 steps, 2MASS J05182233+4554089 from 1 to 8 and 2MASS J05192973+4616552 from 7 to 10. With the mask at 8000 Å, BD+36 2764, HD 150293 and HD 151086 were still M stars (13, 11 and 10 steps off). Standards: Vega A0V/A0V at 7000 Å, B9V to A0V at 7600 Å, A0V to B9V at 8000 Å; Alcor star 2 A3V/A3V at 7000 Å and A5V/A5V at 7600 and 8000 Å; Alcor star 1 (A2V) F2V at every setting.

  Feature detector test (mask applied before `detect_named_features`, 131 stored spectra): 1016 feature tests, 127 verdicts changed (12.5%); median p-value shift -0.0015 (5th to 95th percentile -0.187 to 0.161).
- **Verdict:** Not judged.
- **Limits:** Two stars, with unknown altitude, so bands that grow at high airmass may be missing. The water band overlaps hydrogen Paschen lines in A stars. The K stars in this test were extracted before the sky stage existed (SPE-02), which explains why a 7000 Å limit looked better for them.
- **To tune for your setup:** Repeat the dip search on your own standards, especially low in the sky, and widen or add bands where dips appear.
- **Regenerate with:** No script; measured by hand on 2026-09-19.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_atmospheric_mask.py`.
- **Raw record:** [`documentation/evidence/atmospheric_mask_validation_20260919.json`](../../evidence/atmospheric_mask_validation_20260919.json).

### SPE-16

**Poor-match limit (0.15 relative RMS)**

- **Claim:** On 14 stars with a known type, the stars matched within three subtype steps of their catalog type scored 0.046 to 0.105, and the stars matched to a wrong type scored 0.181 to 0.283. 0.15 sits between the two groups. A low score means the reference is close to the spectrum, not that the type is right: two known-wrong matches scored below the limit.
- **Settings it supports:**
  - code constant `NO_GOOD_MATCH_RMS` = 0.15 in `astrometricslib/models/stellar_source.py`. Relation: derived from this entry. <!-- impl: astrometricslib.models.stellar_source.NO_GOOD_MATCH_RMS -->
- **Status:** Partly measured. 14 stars, and the score was calibrated with the instrument response fitted on one of them (Vega).
- **Data you need:** Calibrated spectra of 10 or more stars with reliable catalog types, across several spectral classes, with a response from a standard star.
- **Procedure:**
  1. Run the spectroscopy stage on the targets (public API, as in SPE-02).
  2. For each star with a catalog type, list the best type, its `self_determined_spectral_type_rms` and the steps from the catalog type (`ladder_position`, internal).
  3. Sort by RMS and look for the value that separates right from wrong matches.
- **Acceptance criterion:** Right matches fall below the limit and wrong matches above it.
- **What to look at:** The relative RMS of the best match (unitless; 0.05 means a typical sample is 5% off) against the steps from the catalog type. A good limit has right matches below it and wrong matches above it.
- **Our result:** Score calibration of 2026-09-19 (response applied, atmospheric bands excluded, upper limit 8000 Å):

  | Star | Catalog | Best | RMS | Steps off | Gap to second | Quality |
  | --- | --- | --- | --- | --- | --- | --- |
  | Vega | A0V | A2V | 0.053 | 2 | 0.0030 | good |
  | Alcor pair star 2 | A5V | A7V | 0.049 | 2 | 0.0019 | good |
  | Alcor pair star 1 | A2V | F2V | 0.048 | 10 | 0.0150 | good |
  | g UMa | A5V | A7V | 0.046 | 2 | 0.0112 | good |
  | 2MASS J05182233+4554089 | M1V | M4V | 0.607 | 3 | 0.0035 | poor |
  | 2MASS J05192973+4616552 | M0V | M3V | 0.638 | 3 | 0.0030 | poor |
  | BD+36 2764 | K0V | M2V | 0.283 | 12 | 0.0068 | poor |
  | BD+36 2775 | K5V | M2V | 0.202 | 7 | 0.0167 | poor |
  | HD 150293 | K0V | M1V | 0.205 | 11 | 0.0070 | poor |
  | HD 150679 | A2V | G0V | 0.181 | 18 | 0.0052 | poor |
  | HD 150998 | K2V | M0V | 0.086 | 8 | 0.0183 | good |
  | HD 151023 | K0V | K2V | 0.105 | 2 | 0.0138 | good |
  | HD 151086 | K2V | M1V | 0.218 | 9 | 0.0027 | poor |
  | TYC 3358-979-1 | B8V | M4V | 0.632 | 46 | 0.0053 | poor |

  The two known-wrong matches below the limit are HD 150998 (K2 as M0, 0.086) and Alcor pair star 1 (A2 as F2, 0.048). Median gap between best and second-best: 0.011; median gap among the top six neighbors: 0.0284 (both with the score of that time).
- **Verdict:** Not judged.
- **Limits:** The two M dwarfs (2MASS J05182233+4554089 and J05192973+4616552) are within three steps but scored 0.61 and 0.64, so the "0.046 to 0.105" range of the claim covers the right matches other than these two. Several K stars in this set were extracted before the sky stage (SPE-02). Vega is not independent of the response.
- **To tune for your setup:** Repeat the table on your own stars. If right matches score above 0.15 with your response, raise the limit; if wrong ones score below it, a limit alone cannot separate them.
- **Regenerate with:** `python -m astrometricslib.scripts.validate_spectral_and_period_analysis` (reports good matches at RMS ≤ 0.15 and whether the class letter agrees, for stored stars with a catalog type).
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_threshold_single_definition.py` (checks the single definition, not the value).
- **Raw record:** [`documentation/evidence/spectral_score_calibration_20260919.json`](../../evidence/spectral_score_calibration_20260919.json).

### SPE-17

**No-type limit (0.45 relative RMS)**

- **Claim:** Of 55 stars classified on 2026-09-24, 19 scored above 0.45, and 18 of those had no catalog type: they had been matched to the wrong part of the sky. Above this limit the closest reference would only invent a type.
- **Settings it supports:**
  - code constant `UNRELIABLE_MATCH_RMS` = 0.45 in `astrometricslib/models/stellar_source.py`. Relation: derived from this entry. <!-- impl: astrometricslib.models.stellar_source.UNRELIABLE_MATCH_RMS -->
- **Status:** Partly measured. One night's run.
- **Data you need:** A run with many classified stars, some with catalog types.
- **Procedure:** As SPE-16, but list every classified star with its best RMS and whether it has a catalog type. Look at the stars above the candidate limit.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The relative RMS distribution. Stars far above the poor-match limit that also lack a catalog type point to misidentified stars, not to the classifier.
- **Our result:** 55 stars on 2026-09-24: 19 above 0.45; 18 of the 19 had no catalog type.
- **Verdict:** Not judged.
- **Limits:** One run. The limit mainly catches identification failures.
- **To tune for your setup:** Raise it if real, correctly identified stars land above it.
- **Regenerate with:** No script; measured by hand on 2026-09-24.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### SPE-18

**Ambiguity gap (0.02 relative RMS)**

- **Claim:** The smallest relative RMS distance between two neighboring reference spectra on the main-sequence ladder is 0.019 (A0V and A2V). Neighboring rungs sit 0.02 to 0.19 apart (median 0.043, 10th percentile 0.024) at resolutions from 25 to 70 Å. A typical interstellar reddening, E(B-V) of 0.065, shifts a score by about 0.060, as much as one rung.
- **Settings it supports:**
  - code constant `AMBIGUOUS_RMS_GAP` = 0.02 in `astrometricslib/models/stellar_source.py`. Relation: derived from this entry. <!-- impl: astrometricslib.models.stellar_source.AMBIGUOUS_RMS_GAP -->
- **Status:** Measured. On the bundled reference spectra.
- **Data you need:** None; the reference library is bundled. Repeat at your own resolution.
- **Procedure:**
  1. Blur each main-sequence reference to your resolution with `blurred_reference_spectrum` (internal).
  2. For each pair of neighbors, scale one to the other by least squares over 4200 to 8000 Å and take the RMS difference over the mean.

  ```python
  from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import REFERENCE_SPECTRAL_TYPES  # internal

  gaps = []
  for first, second in zip(REFERENCE_SPECTRAL_TYPES, REFERENCE_SPECTRAL_TYPES[1:]):
      wl, a = blurred_reference_spectrum(first, resolution_element_angstrom=45.0)
      _, b = blurred_reference_spectrum(second, resolution_element_angstrom=45.0)
      keep = (wl >= 4200.0) & (wl <= 8000.0)
      scale = np.dot(a[keep], b[keep]) / np.dot(b[keep], b[keep])
      gaps.append(np.sqrt(np.mean((a[keep] - scale * b[keep]) ** 2)) / np.mean(a[keep]))
  print(min(gaps), np.median(gaps), np.percentile(gaps, 10))
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_classifier.REFERENCE_SPECTRAL_TYPES -->
- **Acceptance criterion:** The ambiguity gap sits near the smallest neighbor distance.
- **What to look at:** The smallest, median and 10th-percentile neighbor distance (relative RMS). The ambiguity gap should sit near the smallest.
- **Our result:** A0V to A2V 0.019; median 0.043; 10th percentile 0.024; range 0.02 to 0.19, at resolutions from 25 to 70 Å. Median neighbor gap 0.043 to 0.087 depending on resolution, against a shift of about 0.060 for E(B-V) 0.065.
- **Verdict:** Not judged.
- **Limits:** Templates, not real stars. The classifier masks atmospheric bands and excluded windows, which the snippet above does not.
- **To tune for your setup:** At a very different resolution, recompute and set the gap near the smallest neighbor distance.
- **Regenerate with:** No script; computed by hand (date not recorded).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### SPE-19

**Catalog disagreement (20 subtype steps)**

- **Claim:** Of 29 stars with a catalog type classified on 2026-09-24, 27 were within 12 subtype steps of it (F8 matched as K0 was the worst). The two beyond were wrong for reasons other than the templates (34.5 and 45 steps). On the templates, the line-index leave-one-out miss never passes 20 (worst 19).
- **Settings it supports:**
  - code constant `DIFFERS_FROM_CATALOG_SUBTYPES` = 20.0 in `astrometricslib/models/stellar_source.py`. Relation: derived from this entry. <!-- impl: astrometricslib.models.stellar_source.DIFFERS_FROM_CATALOG_SUBTYPES -->
- **Status:** Partly measured. One night's run.
- **Data you need:** As SPE-16.
- **Procedure:** As SPE-16; take the distribution of steps from the catalog type.
- **Acceptance criterion:** The limit sits above the ordinary misses and below the gross failures.
- **What to look at:** Steps on the O-to-M ladder (ten to a letter class). The limit should sit above the ordinary misses and below the gross failures.
- **Our result:** 29 stars, 2026-09-24: 27 within 12 steps; two at 34.5 and 45 steps. A limit of 8 steps has no measurement behind it and would call the F8-as-K0 match a disagreement.
- **Verdict:** Not judged.
- **Limits:** One run, 29 stars.
- **To tune for your setup:** Lower the limit only if your ordinary misses are much smaller.
- **Regenerate with:** No script; measured by hand on 2026-09-24.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### SPE-20

**Giant and supergiant references**

- **Claim:** With only dwarf references, the classifier matched catalog type K3II (HD 183753) and K1.5III (Arcturus) both to a K7V template. Deneb (A2Ia) matches the A2I reference best, at RMS 0.036 against 0.063 for its stored A5V. Ranking all luminosity classes together mislabeled ordinary stars as giants, so the giant references are used only for catalog giants.
- **Settings it supports:**
  - code constant `GIANT_REFERENCE_SPECTRAL_TYPES` in `astrometricslib/pipelines/spectroscopy/processing/spectral_classifier.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_classifier.GIANT_REFERENCE_SPECTRAL_TYPES -->
- **Status:** Partly measured. Three stars.
- **Data you need:** Calibrated spectra of a few catalog giants and supergiants.
- **Procedure:** Classify each with `classify_spectral_type(..., reference_types=REFERENCE_SPECTRAL_TYPES)` and with `reference_types=GIANT_REFERENCE_SPECTRAL_TYPES` (internal), and compare the types and scores.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether the giant reference fits clearly better (lower relative RMS) than the closest dwarf.
- **Our result:** HD 183753 (K3II) and Arcturus (K1.5III) both matched K7V among dwarfs. Deneb: A2I at 0.036, against 0.063 for A5V.
- **Verdict:** Not judged.
- **Limits:** Class II has only eight references, so a bright giant's match can be several subclasses off.
- **To tune for your setup:** Nothing to tune; extend the giant library if your targets need it.
- **Regenerate with:** No script; measured by hand (date not recorded).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### SPE-21

**Dereddening on the templates**

- **Claim:** A G0V template reddened with E(B-V) = 0.3 and blurred to the stored line spread classifies as K2V (12 subtypes too late) without dereddening, and as G0V when the known E(B-V) is removed. With E(B-V) 0.25 for a star that has 0.30, the type still moves closer to G0V.
- **Settings it supports:**
  - the dereddening step (`deredden_spectrum`, Cardelli, Clayton and Mathis law with `STANDARD_R_V` = 3.1 in `astrometricslib/pipelines/spectroscopy/processing/interstellar_extinction.py`). Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction.deredden_spectrum --> <!-- impl: astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction.STANDARD_R_V -->
- **Status:** Partly measured. Templates only; a real star improves by less, because the catalog E(B-V) is itself an estimate.
- **Data you need:** None for the template test. For real data, stars with a Gaia DR3 id and a known spectral type.
- **Procedure:**

  ```python
  from astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction import (  # internal
      deredden_spectrum,
      redden_spectrum,
  )

  profile = load_line_spread_profile("ZWO ASI 533MM Pro")
  wl, flux = blurred_reference_spectrum("G0V", resolution_profile=profile)
  reddened = redden_spectrum(wl, flux, 0.3)
  for ebv in (0.0, 0.25, 0.3):
      result = classify_spectral_type(wl, deredden_spectrum(wl, reddened, ebv), resolution_profile=profile)
      print(ebv, result["spectral_type"])
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.interstellar_extinction.redden_spectrum -->

  On real data, read `reddening.type_shift_steps` of the calibrated result.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The type found and its steps from the true type, for each E(B-V) removed.
- **Our result:** G0V with E(B-V) 0.3: K2V without dereddening, G0V with 0.3 removed; with 0.25 removed, closer to G0V.
- **Verdict:** Not judged.
- **Limits:** Templates only.
- **To tune for your setup:** Nothing to tune; check `ebv_used` and `dereddening_type_shift_steps` at checkpoint 2.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/processing/test_dereddened_classification.py` (`test_a_reddened_g0v_is_classified_several_subtypes_too_late_without_dereddening`, `test_dereddening_with_the_known_colour_excess_recovers_g0v`, `test_a_modest_error_in_the_colour_excess_still_helps`).
- **Raw record:** None kept.

### SPE-22

**Line-index cross-check**

- **Claim:** On the 34 bundled main-sequence templates with the ZWO ASI 533MM Pro line spread, the six line indices name each template's own type exactly (34 of 34). With each template left out of the references, 50% land within two subtypes and 71% within five, and the worst miss is 19 subtypes (B3 read as F2). With 0.5% noise on every 10 Å sample, 99% stay within two subtypes; with 1%, 85%; with 2%, 72%. Adding E(B-V) = 0.3 to a G0V template moves every index by under 0.01.
- **Settings it supports:**
  - the index set `LINE_INDICES` and `CORE_RESOLUTION_FRACTION` = 0.5 in `astrometricslib/pipelines/spectroscopy/processing/spectral_line_indices.py`. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_line_indices.LINE_INDICES --> <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_line_indices.CORE_RESOLUTION_FRACTION -->
  - the checkpoint 2 limit `DIFFERS_FROM_CATALOG_SUBTYPES` (SPE-19). Relation: confirmed by this entry.
- **Status:** Partly measured. Templates only.
- **Data you need:** None for the template test. For real data, calibrated spectra of stars of known type.
- **Procedure:** For each template, blur it to the line spread, add noise if wanted, and call `classify_by_line_indices` (internal), optionally with `reference_types` that leave the template out.

  ```python
  from astrometricslib.pipelines.spectroscopy.processing.spectral_line_indices import classify_by_line_indices  # internal

  # profile = load_line_spread_profile(camera name), as in SPE-21
  for true_type in REFERENCE_SPECTRAL_TYPES:
      wl, flux = blurred_reference_spectrum(true_type, resolution_profile=profile)
      others = [t for t in REFERENCE_SPECTRAL_TYPES if t != true_type]
      found = classify_by_line_indices(wl, flux, resolution_profile=profile, reference_types=others)
      print(true_type, found.best_type, abs(ladder_position(found.best_type) - ladder_position(true_type)))
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_line_indices.classify_by_line_indices -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The share of templates within two and within five subtypes, and the worst miss, in subtype steps.
- **Our result:** As in the claim. The ladder has gaps of three to five subtypes in places, so even a perfect method could not reach two subtypes everywhere.
- **Verdict:** Not judged.
- **Limits:** Templates only. The 1% and 2% noise figures are not asserted by a test.
- **To tune for your setup:** Nothing to tune; on real stars, compare `index_vs_template_type_steps` at checkpoint 2 with the catalog.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/processing/test_spectral_line_indices.py` (`test_each_template_is_recovered_within_two_subtypes`, `test_a_template_left_out_of_the_references_lands_near_its_neighbours`, `test_light_noise_does_not_move_the_type_far`, `test_the_indices_barely_change_when_the_spectrum_is_reddened`).
- **Raw record:** None kept.

### SPE-23

**Equivalent widths and their errors**

- **Claim:** On a synthetic Gaussian line (equivalent width 9.6 Å, blur 45 Å, 200 noise realizations), the mean equivalent width is within one standard error of the truth. The mean reported error is 0.95 times the scatter of the results (0.99 with noise only in the window, 1.01 with noise only in the continuum bands). With the center chosen by the detector's best-dip search, a faint line reads about 3% high.
- **Settings it supports:**
  - code constant `EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS` = 1.5 in `astrometricslib/pipelines/spectroscopy/processing/equivalent_width.py`. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.equivalent_width.EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS -->
- **Status:** Partly measured. Synthetic only.
- **Data you need:** None for the synthetic test. For real data, several frames of one star (as SPE-07), to compare the scatter of the measured widths with their errors.
- **Procedure:** Build a noisy Gaussian dip many times and call `measure_equivalent_width` (internal) on each; compare the mean with the truth and the mean error with the scatter.

  ```python
  from astrometricslib.pipelines.spectroscopy.processing.equivalent_width import measure_equivalent_width  # internal

  wl = np.arange(4000.0, 6000.0, 11.0)
  sigma = 45.0 / 2.355
  rng = np.random.default_rng(3)
  widths, errors = [], []
  for _ in range(200):
      flux = 1000.0 * (1.0 - 0.2 * np.exp(-0.5 * ((wl - 4861.0) / sigma) ** 2))
      noisy = flux + rng.normal(0.0, 5.0, wl.size)
      m = measure_equivalent_width(wl, noisy, np.full(wl.size, 5.0), 4861.0, 45.0, 1.5 * 45.0, 135.0)
      widths.append(m.equivalent_width_angstrom)
      errors.append(m.equivalent_width_error_angstrom)
  print(np.mean(widths), np.mean(errors) / np.std(widths, ddof=1))
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.equivalent_width.measure_equivalent_width -->
- **Acceptance criterion:** The ratio of mean reported error to measured scatter is near 1.
- **What to look at:** The mean equivalent width against the truth (Å) and the ratio of mean reported error to measured scatter (near 1 is right).
- **Our result:** As in the claim.
- **Verdict:** Not judged.
- **Limits:** Synthetic white noise. Neighboring samples on real spectra are correlated (SPE-07), which the error does not include.
- **To tune for your setup:** Nothing to tune directly.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/processing/test_equivalent_width.py` (`test_the_equivalent_width_of_a_gaussian_dip_is_recovered`, `test_the_reported_error_matches_the_scatter_over_200_realizations`, `test_the_window_noise_alone_is_the_error_when_the_bands_are_exact`, `test_the_continuum_covariance_alone_is_the_error_when_only_the_bands_are_noisy`).
- **Raw record:** None kept.

### SPE-24

**False verdicts of the absorption-feature test**

- **Claim:** On synthetic featureless spectra, the share of features called "inconclusive" is 0.4% at 2% noise, 15% at 5%, 19% at 10% and 20% at 20%. On 131 stored spectra, 17% of 1,025 tested features were inconclusive, and 95 stars had at least one.
- **Settings it supports:**
  - code constants `DETECTED_P_VALUE` = 0.01, `POSSIBLE_P_VALUE` = 0.05, `INCONCLUSIVE_P_VALUE` = 0.25 and `INCONCLUSIVE_MINIMUM_DEPTH` = 0.065 in `astrometricslib/pipelines/spectroscopy/processing/spectral_feature_detector.py`. Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector.DETECTED_P_VALUE --> <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector.INCONCLUSIVE_P_VALUE --> <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector.INCONCLUSIVE_MINIMUM_DEPTH -->
  - code constant `MINIMUM_CONTROL_POSITIONS` = 20 in the same file. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector.MINIMUM_CONTROL_POSITIONS -->
- **Status:** Partly measured. The stored-spectra figures were taken before the resolution changed from 30 Å to 45 Å and have not been re-run.
- **Data you need:** None for the synthetic part; the stored spectra of a library for the real part.
- **Procedure:** Run the script below. It builds featureless spectra with a curved continuum and correlated noise, runs `detect_named_features` (internal) on each, and prints the share called detected, the share called inconclusive at each noise level, and the share of injected H-alpha dips and bumps found.
- **Acceptance criterion:** The share of pure-noise features called "detected" stays near 1%. The script fails above 3%.
- **What to look at:** The share of pure-noise features called "detected" (should stay near 1%; the script fails above 3%), and the share called "inconclusive" (expected to approach the 25% p-value cutoff in loud noise).
- **Our result:** Inconclusive in pure noise: 0.4% (2% noise), 15% (5%), 19% (10%), 20% (20%). 131 stored spectra: 17% of 1,025 features inconclusive; 95 stars with at least one.
- **Verdict:** Not judged.
- **Limits:** Synthetic noise is Gaussian smoothed over 1.2 samples. The real figures are from 30 Å resolution.
- **To tune for your setup:** Run the script with `--trials 1000` for tighter rates.
- **Regenerate with:** `python -m astrometricslib.scripts.validate_spectral_and_period_analysis [--trials 100] [--skip-standards]`. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector.detect_named_features -->
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/processing/test_spectral_feature_detector.py`.
- **Raw record:** None kept.

### SPE-25

**What the emission-line detector can see**

- **Claim:** On 228 stored spectra of stars not known to emit, the detector reports a false "detected" line in 1 (0.4%) and calls none an emission-line source. A box-shaped line added to those spectra is detected only about half the time (44% to 47%) when its height is 1.6 times the spectrum's median brightness, and 2% to 6% of the time at 0.4 to 0.8 times.
- **Settings it supports:**
  - code constants `DETECTED_SIGNIFICANCE` = 5.0 and `UNCLEAR_SIGNIFICANCE` = 3.0 in `astrometricslib/pipelines/spectroscopy/processing/emission_line_detector.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.emission_line_detector.DETECTED_SIGNIFICANCE --> <!-- impl: astrometricslib.pipelines.spectroscopy.processing.emission_line_detector.UNCLEAR_SIGNIFICANCE -->
- **Status:** Partly measured. The false-positive rate and completeness are measured on stored spectra, but the thresholds were set on one nebula (M 57) only.
- **Data you need:** A catalog with many stored spectra of ordinary stars, and a spectral stack of a bright emission nebula.
- **Procedure:** Run the script. It reads every stored spectrum except glowing-gas targets and star types that usually emit, runs `detect_emission_lines` (internal) as the pipeline does. It then adds box-shaped H-alpha + [N II] and [O III] lines of heights 0.05 to 1.6 times the median brightness and counts how many are detected.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The share of ordinary-star spectra with a false detected line (an upper bound, since some stars may truly emit), and the share of injected lines detected at each height.
- **Our result:** 2026-10-09: 228 spectra; 1 false detected line (0.4%); 0 emission-line sources. Injected lines: 44% to 47% detected at height 1.6; 2% to 6% at 0.4 to 0.8. Thresholds: set on M 57; a half-width scan there (2026-09-23) gave the strongest fit at 200 Å (blend 21.9 error bars, H-alpha 6.0), weaker at 100 and 300 Å.
- **Verdict:** Not judged.
- **Limits:** Built for a bright nebula; a faint emitter passes unseen. Thresholds tuned on one nebula.
- **To tune for your setup:** Lower `DETECTED_SIGNIFICANCE` to catch fainter lines and re-run the script to see the false-positive rate it costs.
- **Regenerate with:** `python -m astrometricslib.scripts.validate_emission_line_detector` (read-only). <!-- impl: astrometricslib.pipelines.spectroscopy.processing.emission_line_detector.detect_emission_lines -->
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/processing/test_emission_line_detector.py`.
- **Raw record:** None kept.

### SPE-26

**Second-order light**

- **Claim:** Fitting the stored spectra of catalog-typed stars in two stacks gave second-order fractions between -0.7% and +2.0% of the first-order brightness at half the wavelength, and almost every uncertainty range included zero. 2% is the largest value seen, not a measured amount.
- **Settings it supports:**
  - code constant `SECOND_ORDER_MAXIMUM_PLAUSIBLE_FRACTION` = 0.02 in `astrometricslib/pipelines/spectroscopy/processing/second_order_risk.py`. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.spectroscopy.processing.second_order_risk.SECOND_ORDER_MAXIMUM_PLAUSIBLE_FRACTION -->
- **Status:** Partly measured. Two stacks, estimates indistinguishable from zero.
- **Data you need:** Stacks of hot stars of known type whose red end reaches beyond 7600 Å.
- **Procedure:** Model each observed spectrum as the reference of its type plus a fraction of the reference at half the wavelength, and fit the fraction. `compute_second_order_blue_to_red_ratio` (internal) gives the ratio of the brightness at half the wavelength to that at the wavelength.
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.second_order_risk.compute_second_order_blue_to_red_ratio -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The fitted fraction and its uncertainty, per star.
- **Our result:** 2026-09-23, Albireo and Vega stacks: -0.7% to +2.0%. A pooled fit over five stacks was less certain (4% to 8% half-widths, driven by giants matched to dwarf templates).
- **Verdict:** Not judged.
- **Limits:** Two stacks.
- **To tune for your setup:** With a blocking filter, the fraction may be near zero; without one, measure it on a hot star.
- **Regenerate with:** No script; measured by hand on 2026-09-23.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/test_second_order_risk.py`.
- **Raw record:** None kept.

### SPE-27

**Color agreement with the catalog (0.4 mag)**

- **Claim:** On 2026-09-24, 30 of 36 stars had a spectrum B-V within 0.30 mag of the catalog color, and the problem spectra were further out. 0.4 mag sits above every ordinary star seen and well above the 0.09 mag median excess for F, G and early K stars.
- **Settings it supports:**
  - code constant `COLOUR_DISAGREEMENT_MAGNITUDES` = 0.4 in `astrometricslib/pipelines/spectroscopy/post_processing/compare_to_catalog.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog.COLOUR_DISAGREEMENT_MAGNITUDES -->
- **Status:** Partly measured. One data set; the gap is thin.
- **Data you need:** Calibrated spectra of 20 or more stars with catalog B-V.
- **Procedure:** For each star, compute `synthetic_b_minus_v` (internal) on the observed response-corrected spectrum and subtract the catalog `b_minus_v`.

  ```python
  from astrometricslib.pipelines.spectroscopy.processing.synthetic_colour import synthetic_b_minus_v  # internal

  colour = synthetic_b_minus_v(np.asarray(spectrum.wavelengths_angstrom), np.asarray(spectrum.response_corrected_intensities))
  print(colour - float(star.b_minus_v))
  ```
  <!-- impl: astrometricslib.pipelines.spectroscopy.processing.synthetic_colour.synthetic_b_minus_v -->
- **Acceptance criterion:** Ordinary stars cluster near zero, and the limit sits above them.
- **What to look at:** The color difference in magnitudes. Ordinary stars should cluster near zero; the limit should sit above them.
- **Our result:** 36 stars, 2026-09-24: 30 within 0.30 mag (worst ordinary star HD 150293, 0.30 too red). Out: TYC 3105-899-1 -0.58, Elnath +1.04, HD 172449 +1.0 (also unclassified). Arcturus (+0.39 to +0.47 across reruns) sits at the limit and is not reliably flagged.
- **Verdict:** Not judged.
- **Limits:** One data set.
- **To tune for your setup:** Repeat on your stars and set the limit above the ordinary spread.
- **Regenerate with:** No script; measured by hand on 2026-09-24.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/post_processing/test_compare_to_catalog.py`.
- **Raw record:** None kept.

### SPE-28

**The Gaia XP comparison**

- **Claim:** Gaia XP's own blur, measured by fitting a blurred A0V template to the XP spectra of 12 A0V stars, is 95 Å at H-gamma, 130 Å at H-beta, 70 Å at H-alpha and 185 Å at the Paschen lines. These are medians, and the spread between stars is 20 to 40%. Synthetic tests recover a tilt of 5% per 1000 Å to within 0.2 and a 20 Å shift to within 1 Å. Between a real XP spectrum and a template of a different star of the same type, the shift ranged from -23 to +36 Å over the 12 stars (median +11 Å).
- **Settings it supports:**
  - code constant `XP_RESOLUTION_FWHM_NODES_ANGSTROM` in `astrometricslib/pipelines/spectroscopy/post_processing/compare_to_gaia_xp.py`. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.XP_RESOLUTION_FWHM_NODES_ANGSTROM -->
  - code constant `MINIMUM_SHIFT_CORRELATION` = 0.3 in the same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.MINIMUM_SHIFT_CORRELATION -->
- **Status:** Partly measured. The XP blur on 12 stars; the method on synthetic spectra; the limits are design estimates (see the table below).
- **Data you need:** A processed target whose stars have Gaia DR3 ids and response-corrected spectra; network access the first time (XP spectra are cached).
- **Procedure:**
  1. Run the script on a processed target. It calls `compare_to_gaia_xp` (internal) for each star with the public `GaiaXpDriver`, then `summarize_gaia_xp` and `gaia_xp_gate` (internal).
  2. For one star by hand:

  ```python
  from astrometricslib.drivers.driver_set import Drivers  # internal
  from astrometricslib.pipelines.shared.catalog_star_identity import gaia_dr3_source_id_of  # internal
  from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import compare_to_gaia_xp  # internal

  comparison = compare_to_gaia_xp(
      Drivers().gaia_xp_or_default(), gaia_dr3_source_id_of(star),
      np.asarray(spectrum.wavelengths_angstrom), np.asarray(spectrum.response_corrected_intensities),
      line_spread=load_line_spread_profile("ZWO ASI 533MM Pro"),
  )
  print(comparison.slope_percent_per_1000_angstrom, comparison.residual_rms_fraction, comparison.wavelength_shift_angstrom)
  ```
  <!-- impl: astrometricslib.GaiaXpDriver -->
  <!-- impl: astrometricslib.drivers.driver_set.Drivers.gaia_xp_or_default -->
  <!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.gaia_dr3_source_id_of -->
  <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.compare_to_gaia_xp -->
  <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.summarize_gaia_xp -->
  <!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.gaia_xp_gate -->
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The tilt of observed / XP (percent per 1000 Å; a tilt points at the response or the airmass correction). The RMS residual (a fraction). The wavelength shift (Å; it points at the wavelength scale). At run level, the median ratio per band (the residual instrument response).
- **Our result:** XP blur, 2026-10-10, 12 Gaia A0V stars (G 6.0 to 6.6): 95 Å (4340 Å), 130 Å (4861 Å), 70 Å (6563 Å), 185 Å (8800 Å); spread 20 to 40%; a star-template mismatch makes these slightly too large. Synthetic: 5% per 1000 Å tilt recovered within 0.2; 20 Å shift within 1 Å. In synthetic tests, two spectra of the same star correlate at 0.9 or more, and chance correlation for about 700 independent points is about 0.04, which places the 0.3 correlation floor. Real XP against a different A0V star's template: shift -23 to +36 Å, median +11 Å; this spread measures how real stars differ from the template, not the instrument.
- **Verdict:** Not judged.
- **Limits:** The shift's noise floor for a real spectrum of the same star is not known. No real target has yet been run through the comparison for this file.
- **To tune for your setup:** Run the script on a target with many Gaia stars. Divide your response by the run's median band ratio if it is consistent across nights.
- **Regenerate with:** `python -m astrometricslib.scripts.compare_spectra_with_gaia_xp "<target>" [--camera "<camera name>"]` (read-only). The XP blur fit: no script; measured by hand on 2026-10-10.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/test/post_processing/test_compare_to_gaia_xp.py` (`test_a_tilt_of_five_percent_per_1000_angstrom_is_recovered`, `test_a_wavelength_shift_of_twenty_angstroms_is_recovered`).
- **Raw record:** None kept.

### SPE-29

**Registration of spectral frames (zero-order tracking)**

- **Claim:** On the M 13 grating session, all 86 frames were extracted with no zero-order tracking failure, and the zero-order position was stable to ±0.2 px.
- **Settings it supports:**
  - the registration-quality thresholds (design estimate; see the table below), reached through `QualityDiagnostics.spectral_registration_thresholds`. Relation: sanity check only. <!-- impl: astrometricslib.QualityDiagnostics.spectral_registration_thresholds -->
- **Status:** Partly measured. One session, measured with an earlier version of the extraction (see the first validation campaign, [index.md](index.md#6-validation-sessions) Section 6).
- **Data you need:** A session of 30 or more grating frames of one field, and Siril installed.
- **Procedure:** Run the script on the target and date. It registers the frames with Siril's shift-only transform, parses the per-frame star lists and flags frames with too few matched pairs, a high fit RMSE, or a zero-order star that jumps or fades. `--sweep-filters` also stacks with several sharpness filters and measures the stacked zero-order FWHM.
- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The count of flagged frames and the zero-order position scatter (pixels).
- **Our result:** M 13 session (DS-M13; 86 frames, 2520 s total): 86 extracted, 0 tracking failures, position stable to ±0.2 px.
- **Verdict:** Not judged.
- **Limits:** Predates the current extraction and registration checks; not repeated.
- **To tune for your setup:** If good frames are flagged, raise the sigma thresholds; if bad frames pass, lower them.
- **Regenerate with:** `python -m astrometricslib.scripts.spectral_registration_quality_analysis "M 13" [--date 2026-05-23] [--camera "ZWO ASI 533MM Pro"] [--sweep-filters]`; `python -m astrometricslib.scripts.run_empirical_validation`.
- **Guarded by tests:** `astrometricslib/pipelines/spectroscopy/utilities/test/test_spectral_registration_quality.py`.
- **Raw record:** None kept.

## Design estimates

These tuned values have not been measured. Each is a reasoned choice.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| SPE-D01 | Extraction box half-width | 2.5 × trail sigma (holds about 99% of a Gaussian's light) | `APERTURE_SIGMA_MULTIPLIER` in `pre_processing/spectrum_extractor.py` | Lost star light (too narrow) or extra noise (too wide). | Extract an isolated star at 2.0, 2.5 and 3.0σ (set the constant) and compare total flux and per-sample errors (SPE-07). |
| SPE-D02 | Width smoothing window | 61 steps | `APERTURE_SIGMA_SMOOTHING_STEPS` in `pre_processing/spectrum_extractor.py` | Jumps in extracted brightness, or a box that lags the real width. | Repeat the comparison of SPE-04 for several windows. |
| SPE-D03 | Sky clipping | 3σ, at most 5 passes | `SKY_CLIP_SIGMA`, `SKY_CLIP_MAX_PASSES` in `pre_processing/spectrum_extractor.py` | Cosmic rays bias the sky level. | Run `measure_sky` on real strips containing cosmic rays (SPE-03) and compare the level with a hand-cleaned median. |
| SPE-D04 | Fewest pixels in a sky strip | 4 | `SKY_BAND_MINIMUM_SAMPLE_COUNT` in `pre_processing/spectrum_extractor.py` | A noisy sky level near the image edge. | Extract stars near the image edge and check the scatter of their sky levels. |
| SPE-D05 | Refraction limit | 20 Å along the trail (half the trail-width resolution element at 5000 Å) | `DAR_ALONG_DISPERSION_LIMIT_ANGSTROM` in `pre_processing/assess_raw_frame_quality.py` | The wavelength zero point shifts at high airmass. | Observe one star at airmass 1.1 and 2.0 with the correction off and on, and compare the wavelength zero points (SPE-11). |
| SPE-D06 | Lowest altitude for the refraction model | 20° | `MINIMUM_ALTITUDE_DEGREES` in `pre_processing/atmospheric_refraction.py` | A refraction correction used where it is unreliable. | Compare the zero point of a star observed below 25° with and without the correction. |
| SPE-D07 | Wavelength zero-point limit | 20 Å | `ZERO_POINT_OFFSET_LIMIT_ANGSTROM` in `pre_processing/wavelength_zero_point.py` | Bad zero points accepted, or good ones refused. | Collect `wavelength_zero_point_offset_angstrom` over many runs (`python -m astrometricslib.scripts.report_gate_limits`) and compare with the line spread. |
| SPE-D08 | Lines needed, agreement p-value | 2 lines; p ≥ 0.01 | `MINIMUM_LINES_TO_APPLY`, `AGREEMENT_MINIMUM_P_VALUE` in `pre_processing/wavelength_zero_point.py` | Wrong zero-point corrections applied. | Inject known offsets into real spectra and count wrong corrections. |
| SPE-D09 | Chromatic defocus limit | red/blue width ratio 1.3 (seeing alone, roughly wavelength to the power -0.2, gives about 0.92 from 4600 to 6600 Å) | `CHROMATIC_DEFOCUS_RATIO_LIMIT` in `pre_processing/measured_line_spread.py` | Defocused spectra pass unflagged. | Take a focus sweep and run `python -m astrometricslib.scripts.spectral_focus_sweep frame_1.fits frame_2.fits ...`; read the ratio at the best mid-spectrum focus. |
| SPE-D10 | Blur with the measured line spread | off | `use_measured_line_spread` argument of `SpectroscopyPipeline` | Template blur that does not match the observed star. | Classify stars of known type with the option off and on and compare the steps from the catalog type. |
| SPE-D11 | Zero-order saturation limit | 0.1% of pixels | `DEFAULT_SATURATION_FLAG_THRESHOLD` in `pipelines/shared/quality/saturation.py` | Saturated zero orders pass unflagged. | Plot `wavelength_scale_summary.offset_saturation_correlation` against the saturated share over many runs. |
| SPE-D12 | Wavelength-scale gate | 11 Å RMS (one pixel of dispersion) | `WAVELENGTH_SCALE_LIMIT_ANGSTROM` in `post_processing/wavelength_scale.py` | A poor wavelength scale passes, or a good one fails. | Collect `rms_after_correction_angstrom` over many runs with `report_gate_limits.py`. |
| SPE-D13 | Processing-quality gate | 50% of classified spectra | `PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION` in `post_processing/run_gates.py` | A run with many failed spectra passes. | Collect the failed share from `stage_quality_summary` over many runs with `report_gate_limits.py`. |
| SPE-D14 | Gaia XP residual limit | 0.05 RMS (above XP's own 1 to 2% calibration error) | `GAIA_XP_RESIDUAL_RMS_LIMIT` in `post_processing/compare_to_gaia_xp.py` | Calibration errors pass, or good runs fail. | Run `compare_spectra_with_gaia_xp.py` on targets of known good calibration and take the spread. |
| SPE-D15 | Gaia XP tilt limit | 3% per 1000 Å (11% across the range; about the extinction error at 0.3 airmass) | `GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM` in `post_processing/compare_to_gaia_xp.py` | Response tilt errors pass, or good runs fail. | As above, over several nights. |
| SPE-D16 | Gaia XP shift limit | 11 Å (one pixel) | `GAIA_XP_WAVELENGTH_SHIFT_LIMIT_ANGSTROM` in `post_processing/compare_to_gaia_xp.py` | Wavelength shifts pass, or good runs fail. | As above; first measure the shift's noise floor on one star observed many times. |
| SPE-D17 | Gaia XP gate, fewest stars | 3 | `MINIMUM_STARS_FOR_GATE` in `post_processing/compare_to_gaia_xp.py` | The gate decided on too few stars. | Subsample a large target and see how often three stars give the same verdict as all. |
| SPE-D18 | Second-order risk share | 10% of the brightness | `SECOND_ORDER_RISKY_SHARE_OF_BRIGHTNESS` in `processing/second_order_risk.py` | Second-order light contaminates the red end unnoticed. | Compare a hot star's red end with and without a second-order blocking filter. |
| SPE-D19 | Reddest catalog color compared | B-V 1.7 (the references stop at M4V, B-V 1.64) | `MAXIMUM_CALIBRATED_B_MINUS_V` in `post_processing/compare_to_catalog.py` | Cool stars compared outside the calibrated references. | Compare `synthetic_b_minus_v` with catalog B-V for M stars. |
| SPE-D20 | Response agreement tolerance | 0.02 | `DEFAULT_TOLERANCE` in `scripts/compare_instrument_responses.py` | Different responses accepted as the same. | Derive responses from pairs of standard stars on the same night and take the spread. |
| SPE-D21 | Calibration fit rejection | 10 nm RMS | literal in `SpectroscopyCalibrationTuner._fit_grating_distance` | A bad grating-distance fit accepted. | Run the tuner on several standards (SPE-12); good fits land far below. |
| SPE-D22 | Calibration dip search | 12 candidates; depth 1% (down to 0.1%); 5 × local noise; 101-sample continuum | `_MAX_DIP_CANDIDATES`, `_FIRST_FRACTIONAL_PROMINENCE`, `_LAST_FRACTIONAL_PROMINENCE`, `_NOISE_PROMINENCE_MULTIPLE`, `_CONTINUUM_WINDOW_SAMPLES` in `utilities/calibration_tuner.py` | True lines missed among the dip candidates. | Run the tuner on faint standards and count how often the true lines are among the candidates. |
| SPE-D23 | Registration checks | 10 matched pairs; 2.5σ RMSE; 3σ position jump; 2.5σ amplitude | `MIN_MATCHED_STAR_PAIRS`, `RMSE_OUTLIER_SIGMA`, `ZERO_ORDER_POSITION_JUMP_OUTLIER_SIGMA`, `ZERO_ORDER_AMPLITUDE_OUTLIER_SIGMA` in `utilities/registration_quality.py` | Bad frames missed, or good frames flagged. | Run `spectral_registration_quality_analysis.py` on sessions with known bad frames (SPE-29). |
| SPE-D24 | Extinction curve | Kitt Peak mean curve | `data/atmospheric_extinction_kpno.txt` | A wrong airmass correction across wavelength. | Observe one standard at two airmasses and fit k(λ) from the ratio. |

<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_CLIP_SIGMA -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_CLIP_MAX_PASSES -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_MINIMUM_SAMPLE_COUNT -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.assess_raw_frame_quality.DAR_ALONG_DISPERSION_LIMIT_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_refraction.MINIMUM_ALTITUDE_DEGREES -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.ZERO_POINT_OFFSET_LIMIT_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.MINIMUM_LINES_TO_APPLY -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.AGREEMENT_MINIMUM_P_VALUE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.measured_line_spread.CHROMATIC_DEFOCUS_RATIO_LIMIT -->
<!-- impl: astrometricslib.pipelines.shared.quality.saturation.DEFAULT_SATURATION_FLAG_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.wavelength_scale.WAVELENGTH_SCALE_LIMIT_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.run_gates.PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.GAIA_XP_RESIDUAL_RMS_LIMIT -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.GAIA_XP_WAVELENGTH_SHIFT_LIMIT_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.MINIMUM_STARS_FOR_GATE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.processing.second_order_risk.SECOND_ORDER_RISKY_SHARE_OF_BRIGHTNESS -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog.MAXIMUM_CALIBRATED_B_MINUS_V -->
<!-- impl: astrometricslib.scripts.compare_instrument_responses.DEFAULT_TOLERANCE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner._FIRST_FRACTIONAL_PROMINENCE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner._LAST_FRACTIONAL_PROMINENCE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner._NOISE_PROMINENCE_MULTIPLE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner._CONTINUUM_WINDOW_SAMPLES -->
<!-- impl: astrometricslib.pipelines.spectroscopy.utilities.registration_quality.MIN_MATCHED_STAR_PAIRS -->
<!-- impl: astrometricslib.pipelines.spectroscopy.utilities.registration_quality.RMSE_OUTLIER_SIGMA -->
<!-- impl: astrometricslib.pipelines.spectroscopy.utilities.registration_quality.ZERO_ORDER_POSITION_JUMP_OUTLIER_SIGMA -->
<!-- impl: astrometricslib.pipelines.spectroscopy.utilities.registration_quality.ZERO_ORDER_AMPLITUDE_OUTLIER_SIGMA -->
