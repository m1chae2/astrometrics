# Astrometry Validation and Tuning

*Version 1.1 · 2026-11-27 · Status: current*

This file records how the tuned values of the astrometry pipeline were checked. The astrometry pipeline finds the stars in an image, works out where the image points on the sky (plate solving) and names the stars from the SIMBAD and Gaia DR3 catalogs. Each entry gives the library calls behind a number, so you can repeat the check on your own frames and keep or change the value. Terms are defined in the [glossary](../../Glossary.md), and the status labels in [index.md](index.md).

Two quantities appear in many entries. A *residual* is the distance left between where a plate solution puts a star and where a catalog puts it. An *RMS* (root mean square) of distances is the square root of the mean of their squares. Both are in arcseconds (1/3600 of a degree). Our frames come from a ZWO ASI 533MM Pro camera at 1.92 arcsec/pixel.

## Measured and partly measured values

### AST-01

**The solver's fit residual against an independent check with Gaia**

- **Claim:** The plate solver's own fit residual mostly measures position errors in its index files (its pre-built reference stars), not errors in the solution. Measured against Gaia DR3, the solutions were several times better than the fit residual says. One solution still carried a constant shift that no gate measured.
- **Settings it supports:**
  - The order in which the `astrometric_residual` gate picks its residual: the RMS against Gaia from the refit first, then the solver's fit residual, then the catalog match separation. This order is code, not a setting (`_residual_and_basis` in `astrometricslib/pipelines/astrometry/post_processing/record_quality.py`; changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality._residual_and_basis -->
  - The Gaia refit itself (see [AST-02](#ast-02)). Relation: derived from this entry.
- **Status:** Partly measured. Two stacks from one camera, measured by hand with no script.
- **Data you need:** A stacked image solved by Siril, so that Siril's fit numbers and its fit residual exist. (The measurement was made on solve-field solutions, which the app no longer uses.) The image needs a `DATE-OBS` card and at least a few hundred unsaturated stars brighter than G = 16 that Gaia lists.
- **Procedure:**
  1. Solve the stack through the public API and read the solver's fit residual from the quality summary.
  2. Detect the stars and refine each center with a 2-D Gaussian fit, which is independent of the pipeline's own centroids. The detector call is public; `centroid_2dg` comes from photutils.
  3. Download the Gaia DR3 stars of the field brighter than G = 16 and move them to the image date (internal functions, not part of the public API; may change). <!-- impl: astrometricslib._internal.drivers.gaia_cone_driver.query_gaia_cone_vizier --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_field_matching.propagate_gaia_positions --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_field_matching.read_observation_time -->
  4. Match each refined center to the nearest Gaia star within 2 arcsec and compute the median offset in RA and in Dec (the constant shift) and the scatter about it.

```python
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from photutils.centroids import centroid_2dg

from astrometricslib import Astrometrics
from astrometricslib._internal.drivers.gaia_cone_driver import query_gaia_cone_vizier
from astrometricslib.pipelines.astrometry.processing.gaia_field_matching import (
    propagate_gaia_positions,
    read_observation_time,
)

astrometrics = Astrometrics()
target = astrometrics.targets.get("NGC 2403")
answer = astrometrics.processing.astrometry.run(target)
metrics = answer.quality.astrometry.astrometry_metrics
print(
    "solver fit residual",
    metrics.plate_solve_fit_residual_rms_arcsec,
    "arcsec over",
    metrics.plate_solve_matched_star_count,
    "stars",
)

wcs = answer.results["astrometry"]["wcs"]
path = target.stacking.stacked_image
with fits.open(path, memmap=False) as hdul:
    data = np.asarray(hdul[0].data, dtype=float)
    header = hdul[0].header

stars = detect_point_sources(astrometrics.processing, data).stars
centres = []
for star in stars[:3000]:
    x = int(round(star.x - 1.0))  # 1-based to 0-based
    y = int(round(star.y - 1.0))
    box = data[y - 7 : y + 8, x - 7 : x + 8]
    if box.shape != (15, 15):
        continue
    dx, dy = centroid_2dg(box - np.median(box))
    centres.append((x - 7 + dx, y - 7 + dy))
centres = np.array(centres)

centre = wcs.pixel_to_world(data.shape[1] / 2, data.shape[0] / 2)
gaia = query_gaia_cone_vizier(centre.ra.deg, centre.dec.deg, 1.0, 16.0, 50_000, 60)
ra, dec = propagate_gaia_positions(
    gaia["ra"], gaia["dec"], gaia["pmra"], gaia["pmdec"], read_observation_time(header)
)
gaia_coords = SkyCoord(ra * u.deg, dec * u.deg)

sky = wcs.pixel_to_world(centres[:, 0], centres[:, 1])
index, separation, _ = sky.match_to_catalog_sky(gaia_coords)
close = separation.arcsec < 2.0
matched = gaia_coords[index[close]]
d_ra = (sky[close].ra - matched.ra).wrap_at(180 * u.deg).arcsec * np.cos(matched.dec.radian)
d_dec = (sky[close].dec - matched.dec).arcsec
print("constant shift RA, Dec:", np.median(d_ra), np.median(d_dec), "arcsec")
print("scatter:", np.median(np.hypot(d_ra - np.median(d_ra), d_dec - np.median(d_dec))), "arcsec")
```

  Run step 4 once with the solver's solution and once with the refined one. Both are in the run: the identifier keeps the solver's fit residual and the refit's before and after RMS (see [AST-02](#ast-02)).

- **Acceptance criterion:** A constant shift above about a tenth of a pixel means the solution is offset as a whole.
- **What to look at:** The solver's fit residual against the scatter and the constant shift from the Gaia check, all in arcseconds. A scatter well under the fit residual means the fit residual is dominated by the index files. A constant shift above about a tenth of a pixel means the solution is offset as a whole.
- **Our result:** 2026-10-10, ASI 533MM stacks.

  | Stack | Solver fit residual | Stars in the solver's fit | Fit residual / star width |
  | --- | --- | --- | --- |
  | M 13 | 1.35 arcsec | 95 | 0.29 |
  | NGC 2403 | 0.82 arcsec | 24 | 0.11 |

  The independent Gaia check of the same solutions found a median scatter of 0.23 to 0.24 arcsec, and a constant shift of 0.60 arcsec in declination on NGC 2403. The Gaia refit later removed that shift (0.59 to 0.06 arcsec; see [AST-02](#ast-02)).
- **Verdict:** Not judged.
- **Limits:** Two stacks from one camera and one night of measurement. The check was done by hand and no record was saved. The Gaia check uses Gaia positions, so a systematic error in Gaia would not show.
- **To tune for your setup:** Nothing to tune directly. If your solver's fit residual is close to the Gaia scatter, your index files are better than ours and the fit residual is a fair measure. If the Gaia check shows a constant shift, check that the refit ran (`wcs_refined_against_gaia` in the run's `astrometry_flags`).
- **Regenerate with:** No script; measured by hand on 2026-10-10. `astrometricslib/scripts/report_gate_limits.py` reports the spread of the stored fit residuals across the library (`python -m astrometricslib.scripts.report_gate_limits`).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### AST-02

**Refitting the solution to Gaia on real stacks**

- **Claim:** The refit to Gaia DR3 removes the constant offsets the solver leaves (up to 1.4 pixels) and leaves a scatter of about a tenth of a pixel, with no trend across the field.
- **Settings it supports:**
  - code constant `INITIAL_MATCH_RADIUS_PIXELS` = 4.0 in `astrometricslib/pipelines/astrometry/processing/wcs_refinement.py` (changing it means editing the code); it must exceed the largest solver error seen. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.INITIAL_MATCH_RADIUS_PIXELS -->
  - code constant `MAXIMUM_SHIFT_ARCSEC` = 30.0 in the same file; more than ten times the largest solver error seen. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MAXIMUM_SHIFT_ARCSEC -->
  - code constant `SIP_DEGREE` = 2 in the same file; the solver's own solutions use 2 and the M 13 stack showed up to 1.8 arcsec of distortion at a corner. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.SIP_DEGREE -->
  - code constants `REFINED_MATCH_RADIUS_PIXELS` = 1.5, `CLIP_SIGMAS` = 3.0 and `MAXIMUM_PASSES` = 5 in the same file; these were used in the runs below but not varied. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.REFINED_MATCH_RADIUS_PIXELS --> <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.CLIP_SIGMAS --> <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MAXIMUM_PASSES -->
- **Status:** Partly measured. Three stacks from one camera, checked by hand with the same Gaia positions the fit used.
- **Data you need:** Solved stacks with `DATE-OBS`, a few hundred or more detected stars that Gaia lists, and if possible one stack with few frames (a short stack is where the solver tends to be furthest off).
- **Procedure:**
  1. Run astrometry on each stack through the public API. The refit runs inside it.
  2. Read the refit's results from the quality summary: `gaia_fit_rms_before_arcsec`, `gaia_fit_rms_arcsec`, `gaia_fit_star_count` and `wcs_refinement_shift_arcsec`, and the flags. <!-- impl: astrometricslib.reports.AstrometryPipelineQualityMetrics -->
  3. For an independent view, repeat the Gaia check of [AST-01](#ast-01) (2-D Gaussian centers, Gaia stars brighter than G = 16 moved to the image date) with the solver's solution and with the refined one, and compare the constant shift and the scatter.
  4. To look at the refit alone, call it directly on your own centroids and Gaia stars (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia -->

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
for name in ["M 13", "NGC 2403", "Alnath"]:
    answer = astrometrics.processing.astrometry.run(astrometrics.targets.get(name))
    metrics = answer.quality.astrometry.astrometry_metrics
    print(name, metrics.astrometry_flags)
    print("  Gaia RMS before and after:", metrics.gaia_fit_rms_before_arcsec, metrics.gaia_fit_rms_arcsec)
    print("  stars used:", metrics.gaia_fit_star_count, " largest move:", metrics.wcs_refinement_shift_arcsec)
    print("  solver fit residual:", metrics.plate_solve_fit_residual_rms_arcsec)
```

The direct call takes the solver's astropy WCS (`wcs`), an N by 2 array of zero-based (x, y) centroids (`centroids`), and the Gaia stars moved to the image date as a `SkyCoord` (`gaia_coords`, built as in [AST-01](#ast-01)).

```python
from astrometricslib.pipelines.astrometry.processing.wcs_refinement import refine_wcs_with_gaia

refinement = refine_wcs_with_gaia(wcs, centroids, gaia_coords, data.shape)
print(refinement.applied, refinement.reason, refinement.star_count)
print(refinement.rms_before_arcsec, refinement.rms_after_arcsec, refinement.shift_arcsec)
```

- **Acceptance criterion:** A constant shift near zero, a scatter of about a tenth of a pixel and no trend across the field.
- **What to look at:** The constant shift before and after the refit (arcseconds; near zero is good). The scatter after it (arcseconds; compare with the pixel scale, where a tenth of a pixel is good). Any trend of the offsets across the field (none is good). `wcs_refinement_shift_arcsec` against `MAXIMUM_SHIFT_ARCSEC`.
- **Our result:** 2026-10-11, ASI 533MM stacks at 1.92 arcsec/pixel. Gaia check with stars brighter than G = 16.

  | Stack | Constant shift before refit | After refit | Scatter after refit | Stars in the refit | Solver fit residual |
  | --- | --- | --- | --- | --- | --- |
  | M 13 | 0.10 arcsec | 0.00 arcsec | 0.15 arcsec | 3,171 | 1.35 arcsec |
  | NGC 2403 | 0.59 arcsec | 0.06 arcsec | 0.14 arcsec | 431 | 0.73 arcsec |
  | Alnath (2-frame stack) | 2.75 arcsec (1.4 px) | 0.01 arcsec | 0.20 arcsec | 474 | 0.79 arcsec |

  No trend across the field remained after the refit. The largest solver error seen was 2.7 arcsec (Alnath); the solver errors seen reached up to 2 pixels. In M 13 the brightest 500 detections are the unresolved cluster core, where Gaia has no star to match, which is why the refit uses every detection and not only the identified ones.

  2026-11-23, two changes to the code and a re-run on the saved stacks, read only, with the cached Gaia tiles complete to G 18 (Gaia stars of the cache, detections of the current star finder). First, the fit now projects about the middle of the matched stars (`proj_point="center"`) and not about the solver's reference point. M 13's reference pixel is at (1632, 2077) of 3,004 by 3,004, and a fit started there stopped in a wrong answer 8,056 arcsec from the solver's solution, so the refit was refused. With the change it was applied: 2,642 stars, RMS 0.62 to 0.25 arcsec, largest move 2.76 arcsec. On a synthetic field with the same off-center reference pixel the old fit also succeeded, so only the real stack shows the failure, and `test_wcs_refinement.py` checks the projection choice itself. Second, a color stack's single-star detections were scaled back to full size twice (see [AST-D03](#design-estimates)), which refused the refit on the IC 1805 stack (3,957 by 5,936 pixels, one-shot color): only 21 stars matched Gaia. With the positions fixed it was applied: 4,340 stars, RMS 0.64 to 0.42 arcsec, largest move 5.76 arcsec. Both runs were a single stack each; nothing was varied.
- **Verdict:** Not judged.
- **Limits:** Three stacks from one camera. The Gaia check uses the same Gaia positions as the fit, so a systematic error in Gaia would not show. The refit assumes the detected centroids are unbiased; saturated and blended stars are not, and the clipping removes most of them. The RMS is measured on the stars the fit used, so it is slightly smaller than the error at a position with no star.
- **To tune for your setup:** If your solver is sometimes off by more than about 3 pixels, raise `INITIAL_MATCH_RADIUS_PIXELS` above that error; if your field is crowded at your pixel scale, lower it so neighbors are not matched. If the refit is refused with "moved the field", look at the solution before raising `MAXIMUM_SHIFT_ARCSEC`: a large move usually means wrong matches. Re-run steps 1 to 3.
- **Regenerate with:** No script. The real-stack check has no script; it was measured by hand on 2026-10-11.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_wcs_refinement.py` checks the refit on a synthetic field (see [AST-03](#ast-03)) and that it projects about the middle of the stars. `astrometricslib/pipelines/stacking/test/test_stack_plate_solution_save.py` checks that an accepted refit found by the analysis step is saved with the stack, and that a stack already refit is left alone.
- **Raw record:** None kept.

### AST-03

**Refitting the solution to Gaia on a synthetic field**

- **Claim:** On a synthetic field with a known solution, the refit brings a solution that starts 2.7 arcsec off back to the truth to a few hundredths of an arcsecond, although 15% of the detections are wrong by about 3 pixels.
- **Settings it supports:**
  - The same code constants as [AST-02](#ast-02). Relation: confirmed by this entry.
  - code constant `MINIMUM_MATCHED_STARS` = 40 in `astrometricslib/pipelines/astrometry/processing/wcs_refinement.py` (the test checks the refusal below it, not the value). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MINIMUM_MATCHED_STARS -->
- **Status:** Partly measured. Synthetic data only.
- **Data you need:** None; the test builds the field. To repeat it on your own setup, change the pixel scale and image size in the test to yours.
- **Procedure:**
  1. Build 600 stars at random pixels of a 3008 by 3008 image under a known tangent-plane solution with small SIP terms, at 1.92 arcsec/pixel.
  2. Add 0.1 pixel of noise to every centroid and move 15% of them by about 3 pixels.
  3. Give the refit a worse solution: 2.7 arcsec off, 0.03% off in scale, slightly rotated and with no SIP terms (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia -->
  4. Measure the distance between the refined and the true solution at 2,000 random pixels.

```python
import numpy as np

from astrometricslib.pipelines.astrometry.processing.wcs_refinement import refine_wcs_with_gaia
from astrometricslib.pipelines.astrometry.test.processing import test_wcs_refinement as field

measured, gaia, truth = field.make_field()
start = field.alnath_like_solution()
refinement = refine_wcs_with_gaia(start, measured, gaia, field.IMAGE_SHAPE)
errors = field.error_against_truth_arcsec(refinement.wcs, truth)
print(refinement.applied, refinement.star_count, np.median(errors), errors.max())
```

- **Acceptance criterion:** A median error under 0.05 arcsec and a largest error under 0.15 arcsec (the test's limits).
- **What to look at:** The median and largest error of the refined solution against the truth, in arcseconds. The test requires a median under 0.05 and a largest error under 0.15 arcsec.
- **Our result:** 2026-10-15 (re-run for this register): the refit used 494 stars; the median error was 0.027 arcsec (0.03) and the largest 0.096 arcsec; the RMS against the synthetic Gaia stars fell from 2.76 to 0.26 arcsec, and the field moved by 4.2 arcsec. The solution it started from was 2.6 arcsec (median) from the truth.
- **Verdict:** Pass. The median error was 0.027 arcsec against a limit of 0.05, and the largest was 0.096 arcsec against a limit of 0.15.
- **Limits:** Synthetic data: the wrong detections are random, while real blends and saturated stars are biased in a consistent direction. The distortion is smaller than a real optic's.
- **To tune for your setup:** Change `PIXEL_SCALE_ARCSEC`, `IMAGE_SHAPE` and the wrong fraction in the test to your camera, then run the test.
- **Regenerate with:** `.venv/bin/pytest astrometricslib/pipelines/astrometry/test/processing/test_wcs_refinement.py` runs the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_wcs_refinement.py`.
- **Raw record:** None kept.

### AST-04

**The residual limit of half a star width**

- **Claim:** On the saved solves, the residual was 0.14 to 0.44 of a star's width, so a limit of 0.5 does not fire on any of them. It is there to catch a failed solve, not to rank good ones.
- **Settings it supports:**
  - code constant `MAXIMUM_RESIDUAL_FRACTION_OF_FWHM` = 0.5 in `astrometricslib/pipelines/astrometry/post_processing/record_quality.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MAXIMUM_RESIDUAL_FRACTION_OF_FWHM -->
- **Status:** Partly measured. Set on catalog match separations, not on the solver's fit residuals or the Gaia refit RMS that the gate now prefers; no failed solve was in the sample.
- **Data you need:** A library of solved stacks with quality summaries that carry a plate scale and a star width. Include at least one known bad solve if you have one.
- **Procedure:**
  1. Run astrometry on each stack (public API).
  2. For each, divide the residual by the star width in arcseconds: residual / (`plate_scale_arcsec_per_pixel` × `star_fwhm_px`). Do this for each of the three residuals the gate can use. <!-- impl: astrometricslib.reports.AstrometryPipelineQualityMetrics -->
  3. Read the gate itself with `gate("astrometric_residual")` on the quality summary. <!-- impl: astrometricslib.reports.AstrometryQualitySummary.gate -->

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
for target in astrometrics.targets.query(include_empty=True).items:
    summary = target.quality.astrometry
    if summary is None:
        continue
    m = summary.astrometry_metrics
    if not m.plate_scale_arcsec_per_pixel or not m.star_fwhm_px:
        continue
    width_arcsec = m.plate_scale_arcsec_per_pixel * m.star_fwhm_px
    for label, value in [
        ("Gaia refit RMS", m.gaia_fit_rms_arcsec),
        ("solver fit residual", m.plate_solve_fit_residual_rms_arcsec),
        ("catalogue match separation", m.catalog_match_separation_rms_arcsec),
    ]:
        if value is not None:
            print(target.id, label, round(value / width_arcsec, 3))
    gate = summary.gate("astrometric_residual")
    print("  gate:", gate.status if gate else None, gate.detail if gate else "")
```

- **Acceptance criterion:** Good solves sit well below the limit. A known bad solve sits above it.
- **What to look at:** The fraction of a star width, with no unit. Good solves should sit well below the limit; a known bad solve should sit above it.
- **Our result:** 2026-10-09, the library: the 8 saved solves whose stacked image carries a solved plate scale had catalog match separations of 0.14 to 0.44 of a star width (median 0.25). None fired.
- **Verdict:** Not judged.
- **Limits:** Eight solves, all from one camera, and all good. The numbers are catalog match separations; a fit residual is usually smaller, so the limit is more lenient for it. The limit has not been re-measured on fit residuals or on the Gaia refit RMS.
- **To tune for your setup:** Run the procedure with the Gaia refit RMS on your library. If your good solves sit near 0.5, the limit is too tight for your optics; if a known bad solve sits below it, lower the limit. Re-run the procedure afterwards.
- **Regenerate with:** `astrometricslib/scripts/report_gate_limits.py` reports the spread of the stored fit residuals and catalog match separations in arcseconds, but not the fraction (`python -m astrometricslib.scripts.report_gate_limits`).
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/post_processing/test_record_quality.py` checks the gate logic.
- **Raw record:** None kept.

### AST-05

**The minimum of 20 catalog-matched stars**

- **Claim:** Solves with fewer than 20 catalog-matched stars are among those with the largest residuals, so a residual over so few stars cannot be trusted.
- **Settings it supports:**
  - code constant `MINIMUM_MATCHED_STARS` = 20 in `astrometricslib/pipelines/astrometry/post_processing/record_quality.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MINIMUM_MATCHED_STARS -->
- **Status:** Partly measured. One look at the saved solves; four solves below the limit, and no tuning of the value.
- **Data you need:** A library of solved stacks with quality summaries.
- **Procedure:**
  1. Run astrometry on each stack (public API).
  2. For each solve, read `catalog_matched_star_count` and the residual, and sort the solves by residual.
  3. See where the solves with few matches fall in that order.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
rows = []
for target in astrometrics.targets.query(include_empty=True).items:
    summary = target.quality.astrometry
    if summary is None:
        continue
    m = summary.astrometry_metrics
    residual = m.catalog_match_separation_rms_arcsec
    if residual is not None:
        rows.append((residual, m.catalog_matched_star_count, target.id))
for residual, matched, name in sorted(rows, reverse=True):
    print(f"{name:20s} {matched:6d} stars  {residual:.2f} arcsec")
```

- **Acceptance criterion:** If the solves below the limit cluster at the top of the residual list, the limit separates them.
- **What to look at:** The number of matched stars against the residual in arcseconds. If the solves below the limit cluster at the top of the residual list, the limit separates them.
- **Our result:** 2026-10-09, the library: of the 42 saved solves, 4 had fewer than 20 matched stars (4, 6, 11 and 19). They are among the five with the largest residuals: 5.8, 5.9, 9.0 and 7.1 arcsec.
- **Verdict:** Not judged.
- **Limits:** The residuals are catalog match separations. Four solves below the limit is a small sample, and the value 20 itself was not varied.
- **To tune for your setup:** Run the procedure. If your solves with, say, 15 matches have small residuals, lower the limit; if solves with 30 matches still scatter widely, raise it.
- **Regenerate with:** `astrometricslib/scripts/report_gate_limits.py` reports the spread of catalog-matched star counts and how many fall below 20 (`python -m astrometricslib.scripts.report_gate_limits`).
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/post_processing/test_record_quality.py` checks the gate logic.
- **Raw record:** None kept.

### AST-06

**Sizing the detection kernel from the blob width**

- **Claim:** Sizing the star finder's kernel from the blob width (the spread of all the light in a box around each bright source) gives about twice as many catalog matches, and a residual five times smaller, as sizing it from the fitted star width (FWHM).
- **Settings it supports:**
  - The choice of `measure_blob_width_from_data` over `measure_fwhm_from_data` to size the kernel in `find_unique_stars`, which `StarIdentifier.detect_stars` calls (code, in `astrometricslib/pipelines/astrometry/processing/star_detection.py`; changing it means editing the code). Relation: derived from this entry.
  - code constants `BLOB_WIDTH_BOX_RADIUS_PX` = 15 and `FWHM_MEASUREMENT_STAR_COUNT` = 15 in `astrometricslib/pipelines/astrometry/pre_processing/fwhm.py`. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_blob_width_from_data --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.detect_stars --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.BLOB_WIDTH_BOX_RADIUS_PX --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_MEASUREMENT_STAR_COUNT -->
- **Status:** Partly measured. One stack.
- **Data you need:** A monochrome stack that solves, with a few hundred stars SIMBAD or Gaia lists.
- **Procedure:**
  1. Measure the blob width and the fitted FWHM of the stack (internal functions, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_fwhm_from_data -->
  2. Run the star identifier twice on the stack: once as it is, and once with the blob-width measurement replaced by the FWHM (internal class, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.process_image -->
  3. Compare the number of catalog-matched stars and the catalog match separation. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.get_catalog_match_separation_rms_arcsec -->

```python
from unittest import mock

import numpy as np
from astropy.io import fits

from astrometricslib.pipelines.astrometry.pre_processing import fwhm
from astrometricslib.pipelines.astrometry.processing import star_detection
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

path = "/path/to/your_stack.fits"
with fits.open(path, memmap=False) as hdul:
    data = np.asarray(hdul[0].data, dtype=float)
print("blob width", fwhm.measure_blob_width_from_data(data), "px")
print("fitted FWHM", fwhm.measure_fwhm_from_data(data), "px")


def run(label):
    identifier = StarIdentifier()
    stars, wcs = identifier.process_image(path)
    matched = [star for star in stars if star.catalog_match_quality is not None]
    print(label, len(matched), "matched,", identifier.get_catalog_match_separation_rms_arcsec(), "arcsec")


run("blob width:")
with mock.patch.object(star_detection, "measure_blob_width_from_data", fwhm.measure_fwhm_from_data):
    run("fitted FWHM:")
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The number of catalog-matched stars (more is better) and the catalog match separation in arcseconds (smaller is better).
- **Our result:** The Bubble Nebula stack (2026-10-02, ASI 533MM): blob width 4.9 px against a fitted FWHM of 2.5 px. Kernel from the blob width: 335 catalog matches and a 1.04 arcsec residual. Kernel from the fitted FWHM: 181 matches and 5.0 arcsec.
- **Verdict:** Not judged.
- **Limits:** One stack. The residual was the catalog match separation. Color frames do not use this path (they are averaged in 2 by 2 blocks instead).
- **To tune for your setup:** Run the procedure on two or three of your stacks. If the FWHM-sized kernel matches as many stars on your data, either choice works; the blob width stays the default.
- **Regenerate with:** No script; measured by hand.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_color_frame_detection.py` checks that monochrome frames size the kernel from the measurement, not the value.
- **Raw record:** None kept.

### AST-07

**Agreement of the star-width (FWHM) estimator with other estimators**

- **Claim:** The pipeline's FWHM (median of 2-D Gaussian fits to bright unsaturated stars) agrees with an independent Gaussian fit to within 0.1 px. Siril's PSF fit reads about 20% wider, by a roughly fixed factor, because it fits a profile with extended wings. Values from different estimators must not be compared.
- **Settings it supports:**
  - code constant `FWHM_SATURATION_FRACTION` = 0.95 in `astrometricslib/pipelines/astrometry/pre_processing/fwhm.py` (stars reaching it are skipped). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_SATURATION_FRACTION -->
  - code constant `FWHM_EXCLUDED_CORE_RADIUS_PX` = 3 in the same file (stars with a masked pixel this close to the center are skipped). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_EXCLUDED_CORE_RADIUS_PX -->
  - code constants `FWHM_FIT_BOX_RADIUS_PX` = 10 and `FWHM_MINIMUM_FIT_R_SQUARED` = 0.8 in the same file. Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_FIT_BOX_RADIUS_PX --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_MINIMUM_FIT_R_SQUARED -->
- **Status:** Partly measured. A few stacks, mostly the Bubble Nebula stack.
- **Data you need:** A monochrome stack with many unsaturated stars and some saturated ones, and, if you use Siril, its registration output for the same frames.
- **Procedure:**
  1. Measure the stack with the pipeline's estimator (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm -->
  2. Measure it again with an independent method. The script below takes the half-maximum width of each star's averaged radial profile, which assumes no shape.
  3. Split the stars by peak value (above 0.95 of the saturation level, and below 0.75 of it) and fit each group, to see how much clipped stars widen the fit.

```python
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_image_fwhm

print(measure_image_fwhm("/path/to/your_stack.fits"), "px")
```

- **Acceptance criterion:** The pipeline's value and an independent Gaussian fit agree to about 0.1 px. A fixed ratio to another estimator is expected.
- **What to look at:** The FWHM in pixels from each estimator. The pipeline's value and an independent Gaussian fit should agree to about 0.1 px. A fixed ratio to another estimator is expected; a ratio that changes from stack to stack is not.
- **Our result:**

  | Stack and date | Pipeline FWHM | Independent Gaussian fit | Siril PSF fit |
  | --- | --- | --- | --- |
  | Bubble Nebula, 2026-10-02 | 2.5 px | 2.4 px | 2.8 px (about 20% wider) |

  On the Bubble Nebula stack (saturation level 1.0), stars that reach the saturation level fit 2.67 px wide and stars below 0.75 of it fit 2.45 px; a raw frame showed the same split (2.45 and 2.38 px). On the M 57 stack of 2026-10-03, all 15 stars measured without the core mask sat on saturated cores and read a median of 3.40 px, while 5,000 stars off those cores read 2.38 px.
- **Verdict:** Not judged.
- **Limits:** One stack for the estimator comparison. The radial-profile method of `recompute_headline_numbers.py` is not a Gaussian fit and reads smaller (the stored values are 1.2 to 1.5 times its values on this library), so it is compared as a scale factor.
- **To tune for your setup:** If your camera clips below its nominal saturation level, lower `FWHM_SATURATION_FRACTION`. If your stars are wider than about 7 px, check that the larger refit box is used (`FWHM_REFIT_SIGMA_PX`). Re-run steps 1 and 3.
- **Regenerate with:** `astrometricslib/scripts/recompute_headline_numbers.py` re-measures each stored stack's FWHM with the pipeline's method and with the radial-profile method and lists disagreements (`python -m astrometricslib.scripts.recompute_headline_numbers`).
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/pre_processing/test_fwhm.py` checks the estimator on synthetic stars.
- **Raw record:** None kept.

### AST-08

**Gaia service order and time limits**

- **Claim:** VizieR's copy of Gaia DR3 answered a dense field quickly when the ESA Gaia archive did not, and the ESA archive answered a query one magnitude shallower. Asking VizieR first, then ESA, then VizieR at shallower depths gets an answer in the cases seen. Each Gaia sky tile of the cache is asked for in this order.
- **Settings it supports:**
  - code constants `GAIA_VIZIER_TIMEOUT_SECONDS` = 45, `GAIA_ESA_TIMEOUT_SECONDS` = 60, `GAIA_DEPTH_STEP_MAGNITUDES` = 1.0 and `GAIA_DEPTH_STEPS_DOWN` = 2 in `astrometricslib/pipelines/astrometry/processing/gaia_tile_queries.py` (changing them means editing the code). Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_VIZIER_TIMEOUT_SECONDS --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_ESA_TIMEOUT_SECONDS --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_DEPTH_STEP_MAGNITUDES --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_DEPTH_STEPS_DOWN -->
- **Status:** Partly measured. The order and the time limits come from one day and one field and are not validated over many nights. The rule that a service which cannot be reached is skipped for the rest of the run, and that the shallower VizieR retries run only after a slow answer: no run has compared it with trying every step (design estimate). If every service fails for a tile, the tile is recorded as failed and its detections keep position-only names; a run with missing tiles records `gaia_tiles_missing` and a notice row. A shallower answer leaves the tile complete only to the shallower depth, so it counts as missing at the depth asked for. The order and limits were measured on cone searches, before the cache was cut into tiles; tile queries are smaller and have not been timed.
- **Data you need:** A dense field (a globular cluster or the Milky Way) and network access.
- **Procedure:**
  1. Time each service on the same sky tiles (internal driver functions, not part of the public API; may change). <!-- impl: astrometricslib._internal.drivers.gaia_cone_driver.query_gaia_tile_vizier --> <!-- impl: astrometricslib._internal.drivers.gaia_cone_driver.query_gaia_tile_esa -->
  2. Repeat at G < 18 and G < 17, and on several days.

```python
import time

from astrometricslib._internal.drivers.gaia_cone_driver import query_gaia_tile_esa, query_gaia_tile_vizier
from astrometricslib._internal.drivers.sky_tiles import tiles_for_cone

for tile in tiles_for_cone(250.42, 36.46, 0.3, 7):  # M 13
    for query in (query_gaia_tile_vizier, query_gaia_tile_esa):
        for depth in (18.0, 17.0):
            start = time.monotonic()
            arguments = (7, tile, 0.0, depth, 50_000) + ((120,) if query is query_gaia_tile_vizier else ())
            try:
                table = query(*arguments)
                print(
                    query.__name__,
                    tile,
                    depth,
                    len(table),
                    "rows in",
                    round(time.monotonic() - start, 1),
                    "s",
                )
            except Exception as error:
                print(
                    query.__name__,
                    tile,
                    depth,
                    "failed after",
                    round(time.monotonic() - start, 1),
                    "s:",
                    error,
                )
```

- **Acceptance criterion:** A time limit sits well above the normal answer time and below the time a failing service takes to give up.
- **What to look at:** The time to answer, in seconds, and whether the query failed. A time limit should sit well above the normal answer time and below the time a failing service takes to give up.
- **Our result:** 2026-10-10, M 13 (1.13° radius): VizieR returned the 14,275 stars brighter than G = 18 in 10 s. The ESA archive failed with HTTP 408 after 64 s at G < 18 and answered G < 17 in 6 s. On the same day every remote lookup of 22 of 23 targets had failed this way on the ESA archive. 2026-11-22, one run of the same tile query against each service: the ESA archive answered synchronously with 8,129 rows in 2.8 s, but its asynchronous query ended in HTTP 500 after 65 s. VizieR's Harvard mirror returned 24,551 rows in 39.7 s, and the Strasbourg (CDS) mirror timed out. The ESA archive has announced that it is down from 2026-11-25 until the Gaia DR4 release on 2026-12-02, so VizieR serves every tile in that period.
- **Verdict:** Not judged.
- **Limits:** One field on one day. The ESA archive said at the time that it may be unstable while it prepares for Gaia DR4, so its behavior may change.
- **To tune for your setup:** If your connection is slow, raise the time limits until a normal dense-field answer fits with room to spare. Re-run the procedure on a few days.
- **Regenerate with:** No script; measured by hand on 2026-10-10.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_gaia_service_fallback.py` checks the order of services and the run's note of a gateway pause, not the time limits.
- **Raw record:** None kept.

### AST-09

**Naming the center star of an unsolved spectral frame**

- **Claim:** The mount's reported position was within 2.5 arcmin of the target on every spectral stack seen. The target's zero order is the brightest compact source near the center, while noise detections there are far fainter. The three limits sit between the cases seen.
- **Settings it supports:**
  - code constant `POINTING_ERROR_SEARCH_RADIUS_ARCSEC` = 300.0 (5 arcmin) in `astrometricslib/pipelines/astrometry/processing/simbad_matching.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.POINTING_ERROR_SEARCH_RADIUS_ARCSEC -->
  - code constant `CENTRE_STAR_SEARCH_RADIUS_FRACTION` = 0.10 of the frame's shorter side, same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.CENTRE_STAR_SEARCH_RADIUS_FRACTION -->
  - code constant `CENTRE_STAR_MINIMUM_PEAK_FRACTION` = 0.10 of the frame's brightest pixel, same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.CENTRE_STAR_MINIMUM_PEAK_FRACTION -->
- **Status:** Partly measured. Four spectral stacks of one night and one earlier mislabel.
- **Data you need:** Spectral (grating) stacks of bright named stars with the mount's RA and Dec in the header, ideally including one where the zero order was not detected.
- **Procedure:**
  1. For each stack, run the identifier without a solve, with the mount's position and the target name (internal class, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.process_image -->
  2. Measure the distance between the mount's position and the named star's catalog position (the pointing error), and the pixel distance of the named detection from the frame center.
  3. Divide the named detection's peak by the frame's brightest pixel, and do the same for the detections near the center that are not the target.

```python
from astropy import units as u
from astropy.coordinates import SkyCoord

from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
from astrometricslib.pipelines.shared.star_kinds import stored_position_degrees

identifier = StarIdentifier()
mount_ra, mount_dec = 101.287, -16.716  # the header's RA and DEC
stars, _ = identifier.process_image(
    "/path/to/spectral_stack.fits",
    attempt_plate_solving=False,
    center_ra=mount_ra,
    center_dec=mount_dec,
    target_name="Sirius",
)
hint = SkyCoord(mount_ra * u.deg, mount_dec * u.deg)
for star in stars:
    position = stored_position_degrees(star)
    if star.name and position:
        print(star.name, hint.separation(SkyCoord(*position, unit="deg")).arcsec, "arcsec from the hint")
```

  The function `stored_position_degrees` is internal (not part of the public API; may change). <!-- impl: astrometricslib.pipelines.shared.star_kinds.stored_position_degrees -->

- **Acceptance criterion:** The search radius covers the largest pointing error with margin, the center radius covers the zero order, and the target's peak fraction is well above the limit and the noise well below.
- **What to look at:** The pointing error in arcseconds (the search radius should cover the largest with margin). The distance of the zero order from the frame center in pixels (the center radius should cover it). The peak fractions (the target's should be well above the limit and the noise well below).
- **Our result:** 2026-10-04 spectral stacks, ASI 533MM at about 1.9 arcsec/pixel, 3008 pixel frames:

  | Quantity | Values seen | Limit |
  | --- | --- | --- |
  | Pointing error | Aldebaran 24, Sirius 24, Mars 22, Alhena 138 arcsec; a wrong Vega match in an earlier session was 145 arcsec from the mount's position | 300 arcsec |
  | Zero order from the frame center | Sirius 151 px | 10% of the shorter side, about 300 px |
  | Peak / brightest pixel | zero orders 0.64 to 1.0; noise detections near the center of the Sirius stack (zero order not detected) 0.00003 | 0.10 |

- **Verdict:** Not judged.
- **Limits:** One night of spectral stacks and one mount. A mount with worse pointing needs a wider radius, and the 12 arcmin SIMBAD region searched on this path caps how far it can go. The rule is for stars. An extended target is a different case: on the M 57 (Ring Nebula) spectral stack of 2026-11-22, the SIMBAD entry for the nebula passed the star filter, which keeps any row that has a spectral type. It was chosen by target name, no detection near the center reached the peak limit, and the run logged that the entry was not applied to any star (4 runs). The star filter now drops every galaxy, nebula and cluster row (by `target_object_category`) before any matching, so such an entry is never put on a star. The nebula's position and size come from the extended-source hint instead.
- **To tune for your setup:** If your mount's pointing error exceeds about half of 300 arcsec, raise `POINTING_ERROR_SEARCH_RADIUS_ARCSEC`, and raise `CENTRE_STAR_SEARCH_RADIUS_FRACTION` so it covers the same angle in pixels. Re-run the procedure. Tuner `astrometry.pointing_error` sets the search radius and center fraction from the stored pointing errors of spectral stacks; `build/config/tune_parameters.py` runs it.
- **Regenerate with:** No script; measured by hand on 2026-10-04.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_center_star_naming.py` checks the selection rules, not the values. `test_extended_catalog_entry.py` and `test_extended_source_hint.py` in the same folder check the extended-object test and the hint (including the record that stands in when SIMBAD cannot be reached).
- **Raw record:** None kept.

### AST-10

**Unresolved catalog companions within 3 arcsec**

- **Claim:** Two catalog entries closer than 3 arcsec appear as one star in our frames, and the brighter one dominates the light. Naming the nearer entry can give the wrong star.
- **Settings it supports:**
  - code constant `UNRESOLVED_COMPANION_RADIUS_ARCSEC` = 3.0 in `astrometricslib/pipelines/astrometry/processing/simbad_matching.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.UNRESOLVED_COMPANION_RADIUS_ARCSEC --> <!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.brightest_unresolved_entry_index -->
- **Status:** Partly measured. One pair (Albireo), a judgment call.
- **Data you need:** An image of a close double star whose two parts have different spectral types, closer than about two pixels at your scale.
- **Procedure:**
  1. Run astrometry (or spectroscopy) on the target through the public API.
  2. Check which catalog entry the star was named after, and whether its catalog spectral type matches the measured spectrum or color.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
answer = astrometrics.processing.astrometry.run(astrometrics.targets.get("Albireo"))
for star in answer.results["astrometry"]["stellar_objects"][:5]:
    print(star.id, star.name, star.spectral_type)
```

- **Acceptance criterion:** The radius is about one and a half pixels. Below it, the pair cannot be split.
- **What to look at:** Whether the named entry is the brighter of the pair. The radius should be about one and a half pixels: below it, the pair cannot be split.
- **Our result:** Albireo (beta1 Cyg A, a K3II star, and its B9.5V companion, 0.4 arcsec apart): the spectrum was K-type, but the star had been named after the companion because the companion happened to be nearer by a fraction of a pixel. With the rule, the brighter star is chosen. The scale was 1.9 arcsec/pixel.
- **Verdict:** Not judged.
- **Limits:** One pair. The value was not varied.
- **To tune for your setup:** Set the radius to about 1.5 times your pixel scale, or to your typical star FWHM in arcseconds.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_unresolved_companions.py` checks the selection rule, not the radius.
- **Raw record:** None kept.

### AST-11

**The same-star position tolerance of 2 arcsec**

- **Claim:** Pairs of named catalog rows (not position-only rows) within 1, 2 and 5 arcsec number 678, 754 and 793. The count nearly stops growing after 2 arcsec, so pairs inside it are one star under two names. A pair of one Gaia-keyed and one name-keyed row (a SIMBAD position, a few arcsec from Gaia's for a bright star) levels off later, near 3 arcsec.
- **Settings it supports:**
  - code constant `SAME_STAR_POSITION_TOLERANCE_ARCSEC` = 2.0 in `astrometricslib/pipelines/shared/catalog_star_identity.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.SAME_STAR_POSITION_TOLERANCE_ARCSEC -->
  - code constant `SAME_STAR_TOLERANCE_WITHOUT_GAIA_NUMBER_ARCSEC` = 3.0 in the same file, used when one row has no Gaia DR3 number. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.SAME_STAR_TOLERANCE_WITHOUT_GAIA_NUMBER_ARCSEC -->
- **Status:** Measured. On our star catalog.
- **Data you need:** A star catalog built by several runs that named stars from different catalogs (SIMBAD names and Gaia DR3 ids).
- **Procedure:**
  1. Read every star record (public API) and its stored position (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.shared.star_kinds.stored_position_degrees -->
  2. Leave out position-only rows (ids starting `FIELD_J`).
  3. Count the pairs closer than 1, 2, 3, 5 and 10 arcsec. For the second setting, count only pairs where exactly one id starts with `Gaia DR3`.

```python
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord

from astrometricslib import Astrometrics
from astrometricslib.pipelines.shared.star_kinds import stored_position_degrees

astrometrics = Astrometrics()
stars = astrometrics.stars.query()
positions = [stored_position_degrees(s) for s in stars if not s.id.startswith("FIELD_J")]
positions = np.array([p for p in positions if p is not None])
coords = SkyCoord(positions[:, 0] * u.deg, positions[:, 1] * u.deg)
for radius in (1, 2, 3, 5, 10):
    first, second, _, _ = coords.search_around_sky(coords, radius * u.arcsec)
    print(radius, "arcsec:", int(np.sum(first < second)), "pairs")
```

- **Acceptance criterion:** The radius is where the pair count levels off.
- **What to look at:** The pair count against the radius. Pairs of one star under two names pile up at small radii; real neighbors spread out evenly. Choose the radius where the count levels off.
- **Our result:** The library catalog (date not recorded): 678 pairs within 1 arcsec, 754 within 2 and 793 within 5. The library backup of 2026-11-24, pairs of one Gaia-keyed and one name-keyed row: 564 within 1 arcsec, 645 within 2, 709 within 2.5, 750 within 3, 772 within 3.5, 788 within 4 and 819 within 5; past 3.5 arcsec the count grows by about 10 to 15 pairs per half arcsec, the rate of real neighbors. Albireo's `* bet01 Cyg` and its Gaia row sit 2.9 arcsec apart.
- **Verdict:** Not judged.
- **Limits:** One catalog from one camera. The date of the count was not recorded. In a much more crowded field, real neighbors can sit inside 2 arcsec.
- **To tune for your setup:** Run the procedure on your catalog and set the tolerance where the count levels off. Then preview the merge with the script before applying it.
- **Regenerate with:** `astrometricslib/scripts/merge_duplicate_catalog_stars.py` lists the clusters it would merge at this tolerance (`python -m astrometricslib.scripts.merge_duplicate_catalog_stars`; add `--apply` to write). No script records the pair counts.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### AST-12

**The variable-star catalog match radius of 3 arcsec**

- **Claim:** At a 10 arcsec radius, the VSX and Gaia DR3 cross-matches (run through the CDS XMatch service in 2026-10; the library now asks VSX through VizieR and matches on this computer, and asks Gaia DR3's variability classification by source number) paired bright stars with faint neighbors. At 3 arcsec, the cases seen matched correctly.
- **Settings it supports:**
  - code constants `FIELD_RADIUS_DEGREES` = 1.5 (stars within this distance of a field's first star share one VizieR request), `FIELD_ROW_LIMIT` = 100,000 (an answer that reaches it is refused, never used as complete) and `FIELD_QUERY_TIMEOUT_SECONDS` = 120; method choices sized from the M 52 count above, not tuned. Relation: sanity check only (one field, M 52).
  - code constants `MATCH_RADIUS_ARCSEC` = 3.0 in `astrometricslib/pipelines/shared/variable_star_catalogs.py` (changing it means editing the code; it applies to VSX and to the stored SIMBAD answers in `stored_variability.py`, not to Gaia, which is matched by source number). Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.MATCH_RADIUS_ARCSEC -->
- **Status:** Partly measured. Individual cases from the live services, not a count. The change to VizieR keeps the radius and the matching rules; it was checked on 2026-11-22 for the M 52 field (2,543 VSX entries within 1.1 degrees, one request, under 2 seconds; the Gaia classification table gave 2,020 variables in the same circle).
- **Data you need:** A star catalog that includes bright stars with faint neighbors and some close pairs.
- **Procedure:**
  1. Preview the variable-star lookup for a set of stars with the script below (it makes no changes without `--apply`).
  2. Ask VizieR for the VSX entries in a circle around the stars, then match the stars to the entries at the library's radius and at a wider one (internal functions, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.build_position_table --> <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.query_vizier_field --> <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.match_to_catalog_rows --> <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.vsx_types_from_matches -->
  3. Compare which stars change their answer, and look at the distance (`angDist`, arcseconds) and type of what they matched.

```python
import astropy.units as u
from astropy.coordinates import SkyCoord

from astrometricslib.pipelines.shared.variable_star_catalogs import (
    VSX_CATALOG,
    VSX_COLUMNS,
    build_position_table,
    match_to_catalog_rows,
    query_vizier_field,
    vsx_types_from_matches,
)

stars = [("Sirius", 101.2872, -16.7161), ("Mira", 34.8366, -2.9776)]  # (id, RA deg, Dec deg)
positions = build_position_table(stars)
# One circle around Mira holds the VSX entries near Mira; ask once per field of sky.
entries = query_vizier_field(VSX_CATALOG, VSX_COLUMNS, 34.8366, -2.9776, 0.05)
at_library_radius = match_to_catalog_rows(positions[1:], entries, "RAJ2000", "DEJ2000")
print(vsx_types_from_matches(["Mira"], at_library_radius))
star_sky = SkyCoord(34.8366, -2.9776, unit="deg")
entry_sky = SkyCoord(entries["RAJ2000"], entries["DEJ2000"], unit="deg")
wider = entries[star_sky.separation(entry_sky) < 10 * u.arcsec]
print(wider["Name", "Type"])
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** For each star whose answer changes with the radius, the distance and magnitude of the matched entry. A match several magnitudes fainter than the star is a neighbor, not the star.
- **Our result:** At a wider radius, Sirius matched a magnitude-8 entry 4 arcsec away, and the blended pair Mira and VZ Cet matched twice. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** Individual cases, not a measured rate of wrong matches.
- **To tune for your setup:** At a coarse pixel scale, a real match can sit more than 3 arcsec from the stored position; raise the radius and check the type and name of what matched.
- **Regenerate with:** `astrometricslib/scripts/backfill_variable_star_flags.py` (`python -m astrometricslib.scripts.backfill_variable_star_flags --limit 2000`; add `--apply` to write).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### AST-14

**Library-wide plate-solve run**

- **Claim:** The plate solve succeeded on the stacks tried and named stars from SIMBAD.
- **Settings it supports:** None directly; it is a check that the whole stage runs. The entry point is `processing.astrometry.run`. <!-- impl: astrometricslib.pipelines.pipeline_runs.run_astrometry -->
- **Status:** Partly measured. It records only success and a SIMBAD match count, and it predates the current gates (`catalog_matches`, `astrometric_residual`) and the Gaia refit.
- **Data you need:** Stacked images of a few targets, including an open cluster and a bright cluster.
- **Procedure:**
  1. Run astrometry on each target's stack through the public API.
  2. Record whether a solution was found and how many stars were identified.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
for name in ["NGC 1893", "M 45", "NGC 2403", "M 81"]:
    target = astrometrics.targets.get(name)
    if target and target.stacking.stacked_image:
        answer = astrometrics.processing.astrometry.run(target)
        result = answer.results["astrometry"]
        print(name, result.get("wcs") is not None, len(result.get("stellar_objects", [])))
        astrometrics.targets.update(target)
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** Whether each stack solved, and the number of catalog-named stars. Today, also read the gates of the quality summary (see [AST-04](#ast-04) and [AST-05](#ast-05)).
- **Our result:** ASI 533MM stacks (date not recorded): NGC 1893 (DS-NGC1893) solved with 10 SIMBAD matches; M 45 (DS-M45) solved with 12 SIMBAD matches.
- **Verdict:** Not judged.
- **Limits:** Two results, measured with earlier code. Neither has been judged against the matched-star minimum or the residual gate. The M 45 frame count was not recorded.
- **To tune for your setup:** Run the script on your own targets with `--camera` set to your camera.
- **Regenerate with:** `astrometricslib/scripts/run_empirical_validation.py` (`python -m astrometricslib.scripts.run_empirical_validation --camera "ZWO ASI 533MM Pro"`); it runs all five pipelines, astrometry fourth. It prints success and the star count; it does not record SIMBAD matches separately.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### AST-15

**Writing the solution back into the FITS header**

- **Claim:** A solution written into a FITS header and read back gives the same corner positions as the original to 0.01 arcsec, SIP terms included.
- **Settings it supports:** None; it checks `write_wcs_to_fits_header` in `astrometricslib/pipelines/shared/session_identification.py`. <!-- impl: astrometricslib.pipelines.shared.session_identification.write_wcs_to_fits_header -->
- **Status:** Partly measured. Synthetic test only.
- **Data you need:** None; the test builds a header.
- **Procedure:**
  1. Write a solution with SIP terms into a copy of a FITS file (internal function, not part of the public API; may change).
  2. Read it back with astropy and compare the sky positions of the four corners.

```python
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.pipelines.shared.session_identification import write_wcs_to_fits_header

path = "/path/to/copy_of_a_stack.fits"  # the header of this file is rewritten
write_wcs_to_fits_header(path, wcs)  # wcs: a solved astropy WCS with SIP terms
with fits.open(path, memmap=False) as hdul:
    back = WCS(hdul[0].header, naxis=2)
corners = np.array([[0, 0], [3007, 0], [0, 3007], [3007, 3007]], dtype=float)
print(np.abs(back.all_pix2world(corners, 0) - wcs.all_pix2world(corners, 0)).max() * 3600, "arcsec")
```

- **Acceptance criterion:** The largest corner difference is far below a pixel.
- **What to look at:** The largest corner difference in arcseconds; it should be far below a pixel.
- **Our result:** Measured value not recorded.
- **Verdict:** Not judged.
- **Limits:** A synthetic solution only.
- **To tune for your setup:** Nothing to tune.
- **Regenerate with:** `.venv/bin/pytest astrometricslib/pipelines/astrometry/test/test_runner_wcs_write_back.py` runs the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/test_runner_wcs_write_back.py`. It passes with a tolerance of 0.01 arcsec.
- **Raw record:** None kept.

### AST-16

**A local background map for star detection**

- **Claim:** A sky background that varies across the frame keeps nebula glow from being detected as stars. When the frame is too small for that map, a random sample of 1/16 of the pixels gives the same background statistics as the whole frame.
- **Settings it supports:**
  - code constant `_BACKGROUND_2D_BOX_FRACTION` = 1/20 of the shorter side (clamped to 20 to 200 px) in `astrometricslib/pipelines/astrometry/pre_processing/source_detection.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection._BACKGROUND_2D_BOX_FRACTION -->
  - code constant `_BACKGROUND_STATS_SAMPLE_FRACTION` = 1/16 in the same file (changing it means editing the code). Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection._BACKGROUND_STATS_SAMPLE_FRACTION -->
  - code constants `_BACKGROUND_2D_BOX_MIN_PX` = 20, `_BACKGROUND_2D_BOX_MAX_PX` = 200 and `_BACKGROUND_2D_MIN_BOXES_PER_AXIS` = 3 in the same file (the limits of the box size above, and the fewest boxes per axis before one global value is used; changing them means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection._BACKGROUND_2D_BOX_MIN_PX --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection._BACKGROUND_2D_BOX_MAX_PX --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection._BACKGROUND_2D_MIN_BOXES_PER_AXIS -->
  - code constants `STAR_FINDER_BOX_FRACTION` = 1/20, `STAR_FINDER_BOX_MINIMUM_PX` = 20, `STAR_FINDER_BOX_MAXIMUM_PX` = 200 and `STAR_FINDER_MINIMUM_BOXES_PER_AXIS` = 3 in `astrometricslib/pipelines/shared/quality/star_profile_fitting.py` (the frame measurement's star finder copies the numbers of the source detector above; changing them means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_BOX_FRACTION --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_BOX_MINIMUM_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_BOX_MAXIMUM_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_MINIMUM_BOXES_PER_AXIS -->
- **Status:** Partly measured. One nebula frame and one set of galaxy subframes; dates not recorded.
- **Data you need:** A frame with bright nebulosity and a plate solution, and a large frame (several thousand pixels on a side) of an ordinary field.
- **Procedure:**
  1. Detect the stars in the nebula frame (public API) and solve it.
  2. Count the detections with no SIMBAD or Gaia match within the match radius.
  3. For the sample check, compare `sigma_clipped_stats` of the whole frame and of a random 1/16 sample (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.background_sample_indices -->

```python
import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib.pipelines.astrometry.pre_processing.source_detection import background_sample_indices

with fits.open("/path/to/large_frame.fits", memmap=False) as hdul:
    data = np.asarray(hdul[0].data, dtype=float)
full = sigma_clipped_stats(data, sigma=3.0)
sample = sigma_clipped_stats(data.ravel()[background_sample_indices(data.size)], sigma=3.0)
print([abs(a - b) / abs(a) for a, b in zip(full, sample)])
```

- **Acceptance criterion:** The share of detections with no catalog match is near zero, and the relative difference of the sample statistics is well under 0.1%.
- **What to look at:** For the nebula frame, the share of detections with no catalog match (near zero is good). For the sample, the relative difference of mean, median and standard deviation (well under 0.1% is good).
- **Our result:** NGC 6992 (Veil Nebula) frame: with a single global background, 194 of 196 detections had no SIMBAD or Gaia match. M 81 subframes of 4000 by 6000 pixels: a random 1/16 sample matched the whole-frame mean, median and standard deviation within 0.05% at about 20 times lower cost; a strided sample (every fourth pixel in each direction) was 77% off in standard deviation.
- **Verdict:** Not judged.
- **Limits:** One nebula frame; the count with the local map was not recorded. The sample check used one target.
- **To tune for your setup:** If small nebulae or galaxy cores are still detected as stars, lower `_BACKGROUND_2D_BOX_FRACTION`; if faint extended stars are lost, raise it. Re-run steps 1 and 2.
- **Regenerate with:** No script.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/pre_processing/test_source_detection_background_sample.py` and `astrometricslib/pipelines/astrometry/test/pre_processing/test_fast_background_map.py` check the mechanics, not the values.
- **Raw record:** None kept.

### AST-17

**Frame star width, elongation and angle from many stars (Moffat fit)**

- **Claim:** The frame measurement recovers the width of known synthetic stars to within 0.5 percent, and their elongation to within 4 percent (low for strongly stretched stars), using a Moffat fit to up to about 196 isolated stars spread over the frame. A frame of trailed stars reads wide and elongated, not sharp. The stack image gets the same elongation, angle and angle coherence (`stacking.star_elongation`, `stacking.star_elongation_angle_deg`, `stacking.star_elongation_coherence`) from the same code, so one definition holds for a frame and a stack. The viewer's star card measures one star with the same fit and moments (`measure_single_star_shape`); it is checked on synthetic stars of known stretch and angle only, not on real stars. The synthetic check ran on frame pixels only; no real stack has been compared yet.
- **Settings it supports:**
  - code constant `FRAME_MEASUREMENT_TIME_BUDGET_SECONDS` = 60 in `pipelines/shared/quality/frame_measurement.py` (changing it means editing the code). Relation: derived from this entry and from [STK-D22](stacking.md). The budget counts the CPU time of the measuring thread. One golden M 13 frame used 13.1 to 15.1 s of CPU time alone and 20 to 27.5 s with four running at once; 60 s is 4 times the first and 2.2 times the worst of the second. (A wall-clock budget of 60 s cut 46 of 61 real frames short on a busy machine.) <!-- impl: astrometricslib.pipelines.shared.quality.time_budget.FRAME_MEASUREMENT_TIME_BUDGET_SECONDS -->
  - code constants `MOFFAT_GRID_CELL_COUNT` = 196, `MOFFAT_CANDIDATES_PER_CELL` = 3 and `MOFFAT_MINIMUM_STAR_COUNT` = 5 in the same file. Relation: sanity check only. The synthetic runs kept about 150 stars of 300. <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_GRID_CELL_COUNT --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_CANDIDATES_PER_CELL --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MINIMUM_STAR_COUNT -->
  - code constants `MOFFAT_FIT_BOX_HALF_WIDTH_PX` = 10 and `MOFFAT_SUBPIXEL_FACTOR` = 3 in the same file. Relation: sanity check only. Widths from 2 to 6 px came back within 0.5 percent. <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_FIT_BOX_HALF_WIDTH_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_SUBPIXEL_FACTOR -->
  - code constants `MOFFAT_ISOLATION_RADIUS_PX` = 14, `MOFFAT_SATURATION_MARGIN_FRACTION` = 0.9, `MOFFAT_MINIMUM_R_SQUARED` = 0.8 `MOFFAT_MAXIMUM_CENTER_SHIFT_PX` = 2.5, `MOFFAT_MINIMUM_FWHM_PX` = 0.8 and `MOFFAT_MAXIMUM_FWHM_PX` = 25.0 in the same file. Relation: sanity check only. They are not tested apart from the synthetic runs. <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_ISOLATION_RADIUS_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_SATURATION_MARGIN_FRACTION --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MINIMUM_R_SQUARED --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MAXIMUM_CENTER_SHIFT_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MINIMUM_FWHM_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MAXIMUM_FWHM_PX -->
  - code constants `MOFFAT_BETA_MINIMUM` = 1.2 and `MOFFAT_BETA_MAXIMUM` = 8.0 in the same file (the limits of the shared Moffat power index; changing them means editing the code). Relation: sanity check only. The shared beta came back within 2 percent of true values from 2.0 to 5.0; the limits themselves are a design choice. <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_BETA_MINIMUM --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_BETA_MAXIMUM -->
  - code constants `MOMENT_APERTURE_FWHM_MULTIPLE` = 1.5 and `MOMENT_APERTURE_MINIMUM_RADIUS_PX` = 3 in the same file. Relation: sanity check only. The window biases strongly stretched stars low (4 percent at an elongation of 2.5). <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOMENT_APERTURE_FWHM_MULTIPLE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOMENT_APERTURE_MINIMUM_RADIUS_PX -->
  - code constants `STAR_PROFILE_RADIUS_PX` = 14, `STAR_CUT_HALF_LENGTH_PX` = 19, `STAR_CENTROID_BOX_HALF_WIDTH_PX` = 3.5, `STAR_SKY_ANNULUS_WIDTH_PX` = 7, `STAR_CLIPPED_PEAK_FRACTION` = 0.95 and `STAR_CUT_STEP_PX` = 0.5, `STAR_SNAP_RADIUS_LIMIT_PX` = 50, `STAR_SNAP_SMOOTHING_SIGMA_PX` = 1, `STAR_MINIMUM_SIGNAL_TO_NOISE` = 5, `STAR_MINIMUM_FWHM_PX` = 1 and `STAR_MAXIMUM_FWHM_PX` = 18.6 and `STAR_MAXIMUM_EXTENT_PX` = 100 in `pipelines/shared/quality/star_profile.py` (the single star profile that the viewer asks for). Relation: sanity check only. A synthetic Gaussian star of known width was recovered to within 3 percent by both fits, and a pair 5 px apart showed two peaks in a cut along its line; the sizes were not tuned on real stars. <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_PROFILE_RADIUS_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_CUT_HALF_LENGTH_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_CENTROID_BOX_HALF_WIDTH_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_SKY_ANNULUS_WIDTH_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_CLIPPED_PEAK_FRACTION --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_CUT_STEP_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_SNAP_RADIUS_LIMIT_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_SNAP_SMOOTHING_SIGMA_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_MINIMUM_SIGNAL_TO_NOISE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_MINIMUM_FWHM_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_MAXIMUM_FWHM_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_MAXIMUM_EXTENT_PX -->
  - code constants `STAR_RING_MAXIMUM_RADIUS_PX` = 60, `STAR_RING_INNER_PEAK_FRACTION` = 0.01, `STAR_RING_OUTER_NOISE_MULTIPLE` = 2, `STAR_RING_OUTER_MARGIN_FACTOR` = 1.5, `STAR_RING_SKY_INNER_FRACTION` = 0.5, `STAR_RING_MINIMUM_WIDTH_PX` = 4, `STAR_RING_MINIMUM_INNER_PX` = 2, `STAR_RING_TARGET_MATCH_RADIUS_PX` = 3, `STAR_RING_NEIGHBOR_MASK_NOISE_MULTIPLE` = 1, `STAR_RING_NEIGHBOR_SEARCH_MARGIN_PX` = 40, `STAR_RING_NORMALIZER_FLOOR_NOISE` = 2, `STAR_RING_HARMONIC_COUNT` = 8, `STAR_RING_SIGNIFICANCE_THRESHOLD` = 3, `STAR_RING_BIN_DEGREES` = 5, `STAR_RING_NEIGHBOR_MINIMUM_SIGNAL_TO_NOISE` = 5, `STAR_RING_MINIMUM_PIXELS_PER_FITTED_NUMBER` = 3, `STAR_RING_ANNULUS_MINIMUM_SHARE` = 0.5 and `STAR_RING_NEIGHBOR_PEAK_SEARCH_PX` = 1 in `pipelines/shared/quality/star_ring.py` (the ring analysis of the single star profile: ring radii, own sky, neighbor mask and Fourier fit). Relation: sanity check only. Synthetic stars gave no significant term for a round star, a significant order 1 at the injected angle for a one-sided ray, a significant order 2 for an elongated star, and no order 1 once a neighbor was masked; the values were not tuned on real stars. <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_MAXIMUM_RADIUS_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_INNER_PEAK_FRACTION --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_OUTER_NOISE_MULTIPLE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_OUTER_MARGIN_FACTOR --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_SKY_INNER_FRACTION --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_MINIMUM_WIDTH_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_MINIMUM_INNER_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_TARGET_MATCH_RADIUS_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NEIGHBOR_MASK_NOISE_MULTIPLE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NEIGHBOR_SEARCH_MARGIN_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NORMALIZER_FLOOR_NOISE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_HARMONIC_COUNT --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_SIGNIFICANCE_THRESHOLD --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_BIN_DEGREES --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NEIGHBOR_MINIMUM_SIGNAL_TO_NOISE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_MINIMUM_PIXELS_PER_FITTED_NUMBER --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_ANNULUS_MINIMUM_SHARE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NEIGHBOR_PEAK_SEARCH_PX -->
- **Status:** Design estimate. The fit recovers known widths on synthetic frames. It has not yet been compared with real data. The stack rows are not measured on real stacks yet.
- **Data you need:** Light frames of one camera setting with a known gain and read noise in the camera profile, ideally a session with round stars and a session with trailed stars, and bias frames at the same setting in the calibration library (for the sky metrics).
- **Procedure:**
  1. Measure the frames with the frame measurement job. It stores the rows and skips a frame it has already measured.
  2. Read the rows for the Moffat width, the Gaussian width of the brightest stars and the elongation, and compare the two widths frame by frame.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  target = astro.targets.get("M 13", required=True)
  frames = list(astro.targets.frames.query(target, order_by="newest"))[:20]
  summary = astro.quality.frames.record(target, frames=frames)
  print(summary.frames_measured, summary.rows_written, summary.frames_failed)
  for metric in (
      "frame.star_width_moffat_px",
      "frame.star_width_gaussian_brightest_px",
      "frame.star_elongation",
  ):
      for row in astro.quality.records.query(metric=metric, target=astro.targets.get("M 13")):
          print(metric, row.subject.subject_id, row.value, row.status.value, row.reason)
  ```

  `QualityFrames.record` <!-- impl: astrometricslib.api._quality_frames.QualityFrames.record --> calls `measure_frame` <!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.measure_frame --> for each light frame.
- **Acceptance criterion:** Not recorded (set after the real-data check).
- **What to look at:** The Moffat width and the Gaussian width of the brightest stars on round frames (the Moffat width is expected to be 0.83 to 0.91 of the Gaussian width: see the synthetic ratios below), and on trailed frames the Gaussian width reading sharp (it keeps the narrow axis of a star stretched by more than 1.5) while the Moffat width and the elongation rise.
- **Our result:** Synthetic runs on 2026-10-22: Moffat stars of known width, stretch and angle, drawn with 5 by 5 sub-sampling on a 1500 by 1500 px frame (300 stars, sky 500 ADU, gain 0.5 e-/ADU, read noise 2 e-, fixed seeds), through `measure_star_profiles`. About 150 stars survived the isolation and fit cuts in each case.

  | True width (px) | beta | True elongation, angle | Width found (px) | Elongation found | Angle found (deg) |
  | --- | --- | --- | --- | --- | --- |
  | 3.0 | 3 | 1.0 | 3.013 | 1.017 | not meaningful (coherence 0.04) |
  | 2.0 | 3 | 1.0 | 2.012 | 1.024 | not meaningful |
  | 5.0 | 3 | 1.0 | 5.005 | 1.016 | not meaningful |
  | 6.0 | 3 | 1.0 | 6.012 | 1.020 | not meaningful |
  | 3.0 | 2 | 1.0 | 3.006 | 1.025 | not meaningful |
  | 3.0 | 5 | 1.0 | 3.005 | 1.015 | not meaningful |
  | 3.0 | 3 (faint stars, 94 stars) | 1.0 | 2.998 | 1.054 | not meaningful |
  | 3.0 | 3 | 1.3 | 3.01 | 1.29 | |
  | 3.0 | 3 | 1.5, 28.65 | 3.010 | 1.487 | 28.57 |
  | 3.0 | 3 | 1.6 | 3.01 | 1.58 | |
  | 3.0 | 3 | 2.0 | 3.01 | 1.96 | |
  | 3.0 | 3 | 2.2 | 3.01 | 2.15 | |
  | 3.0 | 3 | 2.5, 57.3 | 3.005 | 2.40 | 57.2 |
  | 4.0 | 3 | 1.2, 114.6 | 4.00 | 1.196 | 114.6 |

  The shared beta came back within 2 percent of the true value (2.0, 3.05, 5.03). The width is within 0.5 percent of the truth in every case. The elongation of round stars reads 1.02 to 1.05 (noise pushes it above 1), and that of stretched stars reads 1 to 4 percent low because the window cuts the faint ends. The detector of the frame quarantine and the source detector cut stars by roundness, so they lose stretched stars (at an elongation of 1.6 the source detector returned 4 sources where 294 exist); `detect_stars_without_shape_cut` <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.detect_stars_without_shape_cut --> keeps the sharpness window and drops the roundness window, and finds 150 or more stars at every elongation up to 2.5.

  Run time: the full `measure_frame` took 11.6 s (`M_13_Light_Luminance_019`) and 13.5 s (`M_13_Light_Luminance_020`) on 3008 by 3008 px ASI533MM frames, serial, on a 12-core machine, with 179 and 175 stars. The Moffat width read 1.92 and 2.14 px, the Gaussian width of the brightest stars 2.33 and 2.48 px, and the elongation 1.05 and 1.19 (angle 149 and 0.1 degrees, coherence 0.59 and 0.97). These are run-through numbers, not a validation: the gain and read noise used were test values.
  Ratio of the two widths on the same synthetic frames (`measure_star_profiles` against `measure_fwhm_from_data`, 700 by 700 px, 90 stars, run 2026-10-22): the Moffat width over the Gaussian width is 0.874 for 3 px stars of beta 3, 0.838 for 2 px, 0.907 for 5 px, 0.825 for beta 2 and 0.914 for beta 5. The Gaussian fit reads wider because it has no wings to absorb, so a ratio near 0.85 to 0.9 is expected, not 1. On a frame stretched to 2 the Gaussian function returns no width.

  Real data (ZWO ASI 533MM Pro, gain 0, offset 10, 60 s, M 57, night of 2026-10-03, 61 frames, run 2026-10-22 on copies of the databases): 61 of 61 frames were measured (793 rows) and a second run measured nothing. The median Moffat width over the 47 round frames (elongation below 1.15) was 1.90 px against a Gaussian 2.26 px (ratio 0.834, inside the synthetic range). For the 8 frames at elongation 1.15 to 1.30 it was 2.37 against 2.62 px (0.902), and for the one frame at 1.30 or more 2.54 against 2.79 (0.911). The median was 196 stars per frame and the elongation ranged from 1.03 to 1.30. The ratio rises with the elongation because the Gaussian estimator falls back to the narrow axis of a stretched star. The quarantine step had already set the worst frames aside, so badly trailed frames are not in this sample. A width of 1.92 px is just under Nyquist sampling (2 px per FWHM), so the fits there rest on pixel-integrated models and are less certain than on wider stars. To repeat: `astro.quality.frames.record(astro.targets.get("M 57"))`, then query `frame.star_width_moffat_px` and `frame.star_width_gaussian_brightest_px` and the ratio per frame.

  Ring analysis (the "Around the star" view), real data, run 2026-11-15 read-only on the ASI 533MM Pro M 31 stack (`M_31_L_Stacked_ZWO_ASI_533MM_Pro_405mm.fits`, peak 1 and sky noise about 5e-5 in stack units), through `QualityDetections.measure_profile`. Each measurement took 0.07 to 0.09 s (the star finder runs on a box of about 130 by 130 px only). Positions are 1-based; "sig" is amplitude over its 1σ uncertainty; direction is from +y toward +x, with 180 degrees toward lower y.

  | Star (x, y) | Clipped | Ring (px) | Neighbors left out (masked share) | Significant terms (amplitude, sig, direction) |
  | --- | --- | --- | --- | --- |
  | BD+41 112 (1659, 2808), FWHM 2.25 px | yes | 5 to 22 | 1 (2%) | order 1: 26% (11.4σ, 166°); 2: 12% (5.4, 179°); 3: 8% (3.7, 10°); 5: 11% (4.6, 10°); 8: 15% (6.7, 5°) |
  | (1858.7, 175.6), FWHM 2.14 px | no | 5 to 22 | 1 (0%) | order 2: 21% (7.9, 16°); 3: 9% (3.7, 27°) |
  | (1111.7, 1804.0), FWHM 2.37 px | no | 4 to 21 | 2 (4%) | order 1: 29% (10.7, 89°); 2: 22% (8.1, 16°); 5: 9% (3.3, 15°); 8: 12% (4.6, 4°) |
  | (2114.6, 2528.4), FWHM 2.16 px | no | 5 to 26 | 3 (6%) | order 1: 29% (10.4, 160°); 2: 22% (8.3, 163°); 3: 20% (7.7, 112°); 8: 9% (3.2, 44°) |

  The ring of BD+41 112 reaches 22 px; the fitted Moffat of the clipped core would have given 4 to 10 px. Order 1 on BD+41 112 points toward 166°, where a faint ray below the star is visible in the stack. The same two or three terms recur at similar directions on all four stars (order 2 near 16°, order 8 near 5°), so they are not flares of one star: they come from the stack (the pixel grid, the point spread function or a tilted background) and any one-sided ray has to be told apart from that common pattern. The sky of the profile (taken at 14 to 21 px, inside the glow) reads 6.4e-5 (1.2σ) above the sky far from the star. With that sky the ring of BD+41 112 ended at 20 px and order 1 read 44%; with a flat sky from 30 to 60 px it read 30%; the tilted plane gives the 26% above.

  Synthetic checks (`test_star_ring.py`, noise 5e-5, Moffat stars of FWHM 2.25 px and beta 2.5): three seeds of a round star gave no significant term; a 3e-4 ray at 150° gave order 1 within 15° of 150° at more than 5σ; a star squeezed by 1.25 along x gave order 2 along y; a neighbor 3% as bright at 15 px gave a significant order 1 that fell below 3σ and under a fifth of its size once masked; a clipped star of peak 3 times the clip level got a ring outer radius more than 1.5 times the radius from its fitted Moffat. The noise-only false alarm rate follows from the Rayleigh law: a 3σ threshold flags about 1.1% of orders, about 9% of round stars show at least one of eight.
- **Verdict:** Not judged.
- **Limits:** Synthetic stars only, plus two real frames that were run but not compared, and for the ring analysis four stars of one stack with no known flare to recover. The ring amplitude is biased high at low signal (the debiased value corrects the mean, not each value), its uncertainty assumes independent pixels, and a small star elongation or center error shows strongly at the inner edge, where the profile is steep. The fit box (21 by 21 px) is validated for widths up to about 7 px; wider stars read a little narrow because the box cuts their wings. The moment window biases elongation low for strongly stretched stars. The pixel scale comes from the frame record and is treated as exact.
- **To tune for your setup:** If your stars are wider than 7 px, raise `MOFFAT_FIT_BOX_HALF_WIDTH_PX`. If few frames reach 5 stars, lower `MOFFAT_MINIMUM_STAR_COUNT` or raise `MOFFAT_CANDIDATES_PER_CELL`. Re-run the procedure on a round session and a trailed one after any change. One star-width scale (issue #175): the per-frame Gaussian width of the brightest stars, 2.33 px on the reference setup (median of 1,008 frames). Other estimators are put on that scale before a run records its star width: × 1.192 for the frame Moffat width, × 0.997 for the frame-quality Gaussian, × 0.938 for a stack's Gaussian width (medians over 14 to 15 targets, 2026-11-16). Tuner `foundation.typical_star_width` derives each setup's typical width from its stored frames.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/shared/quality/test/test_star_ring.py` checks the ring analysis on synthetic stars (round, ray, elongated, neighbor, clipped, edge). `astrometricslib/pipelines/shared/quality/test/test_frame_measurement.py` checks the recovered widths, elongations and angles on synthetic frames, and `astrometricslib/pipelines/stacking/test/test_frame_measurement_run.py` checks the job around it.
- **Raw record:** None kept.

### AST-18

**Effective star width, and star width in the center and the corners of the field**

- **Claim:** The effective star width adds the blur of one square pixel to the fitted Moffat width, with a fixed theoretical term (`FWHM_eff^2 = FWHM^2 + c p^2`, `c` = 2 ln 2 / 3 = 0.4621). The width of the stars in the center and in each corner of the field comes from the same fits as the frame's width, with no second fit. The same five widths are stored for the stack image (`stacking.star_width_center_px` and four corner rows). Neither has been checked against a known tilt or against a known seeing. The 61 frames of the night are not 61 independent results: they are undithered, so each region holds the same 16 stars in every frame.
- **Settings it supports:**
  - code constant `PIXEL_BLUR_FWHM_SQUARED_FACTOR` (the exact value 2 ln 2 / 3, about 0.462) in `pipelines/shared/quality/frame_measurement.py`. Relation: sanity check only. It is theory (the user's decision of 2026-10-23: no fitted value), not a tuned number. The check below shows what the Gaussian-core assumption leaves out. <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.PIXEL_BLUR_FWHM_SQUARED_FACTOR -->
  - code constants `FIELD_GRID_CELLS_PER_SIDE` = 14 and `FIELD_REGION_CELLS_PER_SIDE` = 4 in the same file. Relation: sanity check only. A block of 4 by 4 of the 14 by 14 cells is 8 percent of the frame and holds 16 stars when every cell has one, which is more than the 5 a median needs. <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.FIELD_GRID_CELLS_PER_SIDE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.FIELD_REGION_CELLS_PER_SIDE -->
- **Status:** Design estimate. The blur term is theory, checked on a computed star only. The region widths are measured on one night and have no known tilt to compare with.
- **Data you need:** Light frames of one camera setting, and for the tilt question a session where the camera plane is known to be tilted or square (no such session is held).
- **Procedure:**
  1. Measure the frames with the frame measurement job.
  2. Read the effective width rows and the five region rows, and compare each corner with the center, frame by frame, using the bootstrap errors.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for metric in (
      "frame.star_width_moffat_px",
      "frame.star_width_effective_px",
      "frame.star_width_center_px",
      "frame.star_width_corner_xhigh_ylow_px",
  ):
      for row in astro.quality.records.query(metric=metric, target=astro.targets.get("M 57")):
          print(metric, row.subject.subject_id, row.value, row.uncertainty, row.sample_count)
  ```

  `measure_frame` <!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.measure_frame --> stores the rows. A frame measured before the rows existed is measured again by the job, because the job stores a fingerprint of the frame metrics (`frame_metric_set_fingerprint`) on every frame it measures and compares it with the current one; `measure_star_profiles` <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.measure_star_profiles --> and `measure_field_region_widths` <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.measure_field_region_widths --> give the same numbers on one plane.
- **Acceptance criterion:** Not recorded. A tilt claim needs a session with a known tilt, or a session before and after the camera plane was changed, which the observatory does not hold.
- **What to look at:** The size of each corner minus the center against the bootstrap errors, the same sign in the same corner in most frames of a night, and the same pattern on another night. A pattern that follows the seeing and not the corner is not a tilt.
- **Our result:** 2026-10-23, M 57, night of 2026-10-03, 61 frames of 60 s (ASI533MM Pro, 1.915 arcsec/px), read only.

  The pixel blur term. A Moffat star was sampled finely, smeared over a 1 px square, and its FWHM read. The formula reads low when the star has wings (smeared width, formula, difference):

  | Moffat beta | FWHM 1.6 px | 2.0 px | 3.0 px | 4.5 px |
  | --- | --- | --- | --- | --- |
  | 2 | 1.853, 1.738, -6.2% | 2.207, 2.112, -4.3% | 3.141, 3.076, -2.1% | 4.595, 4.551, -1.0% |
  | 3 | 1.818, 1.738, -4.4% | 2.177, 2.112, -3.0% | 3.119, 3.076, -1.4% | 4.580, 4.551, -0.6% |
  | 5 | 1.791, 1.738, -2.9% | 2.153, 2.112, -1.9% | 3.103, 3.076, -0.9% | 4.569, 4.551, -0.4% |
  | 20 | 1.760, 1.738, -1.2% | 2.127, 2.112, -0.7% | 3.084, 3.076, -0.3% | 4.556, 4.551, -0.1% |

  For the real frames the fitted width had a median of 1.953 px (range 1.62 to 2.87, bootstrap error 0.004 px), and the effective width a median of 2.068 px (3.96 arcsec); the effective width is 1.059 times the fitted one at the median. At this width the formula is about 3 percent below a computed beta 3 star, so the effective width is a little low, by 0.06 px.

  The center and the corners, over the 61 frames (every region held 16 stars in every frame; the median bootstrap error of a region is 0.008 to 0.014 px; the corner is named by the low or high end of x, the columns, and of y, the rows):

  | Region | Median width (px) | Corner minus center (median) | Frames where the corner is wider | Median of corner minus center over its error |
  | --- | --- | --- | --- | --- |
  | center | 1.946 | | | |
  | x low, y low | 1.960 | +0.007 px (+0.3%) | 39 of 61 | 1.1 |
  | x high, y low | 1.995 | +0.061 px (+3.3%) | 61 of 61 | 4.4 |
  | x low, y high | 1.963 | +0.028 px (+1.4%) | 50 of 61 | 2.0 |
  | x high, y high | 2.003 | +0.041 px (+2.0%) | 59 of 61 | 2.4 |

  The 16 stars of a region are the same stars in every frame. The pointing was dithered every 10 frames by about 1.7 px or more and otherwise drifted about 13 px an hour ([STK-44](stacking.md#stk-44)). The grid takes the brightest star in each cell, so a region's 16 stars are 16 grid cells' brightest stars each frame. Siril's transform for each frame was used to put every frame's stars on the reference frame's grid and match them to the reference frame's 196 stars (within 3 px). A reference star was found in a median of 59 of the 61 frames; each of the five regions holds 16 stars in the reference frame. So a corner's median rests on 16 stars measured 61 times, not on 61 independent samples, and the bootstrap errors in the table above (which resample stars within a frame) do not cover the fact that the frames repeat.

  Leaving out one star. With the matched stars, the median corner minus center over the frames is +0.006 px (x low, y low), +0.054 px (x high, y low), +0.025 px (x low, y high) and +0.043 px (x high, y high). Dropping each one of the 16 stars of a corner in turn, and then each of the 16 center stars in turn, the corner minus center moved by at most 0.005 px in every corner. The sign did not change for any single star. The x low, y low corner is the weakest: +0.004 to +0.008 px, 38 percent of its value for the worst single star. The x high, y low corner is the firmest: +0.047 to +0.056 px, 5 percent. So no one star makes the pattern, but 16 stars per region is a small set and the same stars stand behind every frame.

  The causes of the pattern the data cannot separate: a tilt, field curvature, coma, the coarse sampling, the choice of stars (one brightest star in each of the 4 by 4 cells of a region, the same ones every frame), and the brightness of the stars (the corners of a vignetted field hold fainter stars, and a fit to a fainter star is noisier and can be wider). A second field or a second night is needed before any tilt reading.

  The largest corner over the center had a median of 1.035 (range 1.005 to 1.082). The difference of the x high, y high corner from the center does not follow the frame's width (correlation -0.08). The pattern is steady through the night, but a width 3 percent larger in one corner could be a small tilt, field curvature, coma or the coarse sampling (the stars are 1.95 px wide, near Nyquist); the data cannot tell them apart.

  To repeat: `python science/s3/evidence_frames.py` then `science/s3/analyse_regions.py`, `science/s3/check_pixel_blur.py` and `science/s3/leave_one_star_out.py` in the session's scratch folder, or the call in the procedure.
- **Verdict:** Not judged.
- **Limits:** The blur term is theory with a Gaussian core and reads about 3 percent low for beta 3 at 2 px (the effective width's uncertainty method states this bias: about -3 percent at 2 px for beta 3, up to -6 percent at 1.6 px for beta 2). The regions are measured on one night and one camera, and on one field: in these frames (dithered every 10 frames by a few pixels, otherwise drifting about 13 px an hour) each region's stars are the same 16 in every frame, because the dither and the drift move the stars by only a few pixels, so the number of frames is not the number of independent results. A corner is named by x (the column) and y (the array row, row 0 first); a viewer that draws row 0 at the bottom shows the same corner on the other side. A raw color mosaic is fitted on 2 by 2 super-pixels and its blur term still uses the original pixel. The stars must lie 10 px from the edge, so the corners are not the extreme corners. A frame measured before the rows existed has none until the job measures it again, which it does on its next run.
- **To tune for your setup:** None for the blur term (it is theory; a fitted value was not chosen). A smaller or larger region block means changing `FIELD_REGION_CELLS_PER_SIDE`.
- **Regenerate with:** The call in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/shared/quality/test/test_frame_measurement.py` checks the constant, the formula and its error, a wider corner on a drawn lattice of stars, the minimum count and the mosaic positions. `astrometricslib/pipelines/stacking/test/test_frame_measurement_run.py` checks that a frame measured before the metric set changed is measured again.
- **Raw record:** `science/s3/frames.pkl` in the session's scratch folder holds each frame's widths, regions and star positions; not kept in the repository.

### AST-19

**Share of the target's own pixels at the clip ceiling or the linearity limit, frame by frame**

- **Claim:** The target is found on each raw frame through this run's plate solution of the stack and Siril's registration transforms, and the share of the pixels of a 30 arcsecond disc around it that reach the clip level or the linearity limit is stored. The mount's commanded pointing is not a usable solution for this. A source must stand in the disc on the stack, which finds a target record placed on blank sky but not a wrong star in a crowded field.
- **Settings it supports:**
  - code constant `TARGET_REGION_RADIUS_ARCSEC` = 30 in `pipelines/astrometry/post_processing/target_clipping.py`. Relation: sanity check only. The commanded pointing of the M 57 night was 157 arcsec from the plate solution, so the disc would have to be many times larger to cover that error. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.TARGET_REGION_RADIUS_ARCSEC -->
  - code constants `MAXIMUM_FRAMES_READ` = 200 and `MAXIMUM_READ_SECONDS` = 120 in the same file. Relation: sanity check only. One real run read a frame in 0.014 s, so 200 frames take about 3 s on a local disk; the time limit allows 40 times that for a network mount. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.MAXIMUM_FRAMES_READ --> <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.MAXIMUM_READ_SECONDS -->
  - code constant `MINIMUM_PEAK_SIGMA` = 5 in the same file. Relation: sanity check only. On the M 57 stack the blank discs sat at 1.5 to 4.5σ, so 5 lies above blank sky. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.MINIMUM_PEAK_SIGMA -->
- **Status:** Partly measured. The chain was checked end to end on one clipped star of the M 57 frames; the Ring Nebula itself never clips on this night. The source check was measured on one crowded field.
- **Data you need:** A stack whose registration record the stacking run wrote (a stack made before the record existed has none and gets no rows), the stack's plate solution from the astrometry run, its raw frames and the target's catalog position.
- **Procedure:**
  1. Stack the target. The stacking run writes `<stack name>_registration_frames.json` beside the stack (Siril's transform for each frame, the frame list in Siril's order and the trimmed columns and rows).
  2. Run the astrometry pipeline on the stack. After a successful solve it stores `astrometry.frame_target_clipped_fraction` and `astrometry.frame_target_nonlinear_fraction` for each frame read. The row's sample count is the number of disc pixels inside the frame, so a count is the share times it.

  ```python
  from astrometricslib import Astrometrics

  astro = Astrometrics()
  for metric in (
      "astrometry.frame_target_clipped_fraction",
      "astrometry.frame_target_nonlinear_fraction",
  ):
      for row in astro.quality.records.query(metric=metric, target=astro.targets.get("M 57")):
          print(metric, row.subject.subject_id, row.value, row.sample_count, row.status.value, row.reason[:80])
  ```

  `target_clipping_measurements` <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.target_clipping_measurements --> builds the rows and `record_target_clipping_quality` <!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.record_target_clipping_quality --> stores them.
- **Acceptance criterion:** Not recorded.
- **What to look at:** A frame where the share is not zero, and the reason on a not-measured row (no plate solution of this run, no source at the position, a frame not sampled or not read in time).
- **Our result:** 2026-10-23, M 57, night of 2026-10-03 to 04, 61 frames of 60 s (ASI533MM Pro, clip ceiling 65532 ADU), read only. The scratch stack has no coordinate solution of its own and no registration record, so the local solver (then `solve-field`) solved it (1.919 arcsec/px) and the record was written from the stack's `_inputs.json` and `_Registration.seq` (the trimmed columns and rows are 0).

  Pointing: the reference frame's header holds the commanded pointing (18 53 29.33, +33 05 00.43). Through it, the SIMBAD position of M 57 (18 53 35.08, +33 01 45) fell at stack pixel (row 1608, column 1496); through the plate solution it fell at (row 1534, column 1528). The difference is 81.6 px, 156.5 arcseconds. The brightest smoothed spot of the reference frame lay 9 px from the plate-solved position (the ring is brighter than its center), so the plate solution places the nebula to about 10 px. A disc of 30 arcseconds around the commanded position would have missed the nebula, so the step uses only this run's own solution (rows are not measured without one).

  Counts: with the plate solution and the SIMBAD position, 0 clipped and 0 nonlinear pixels in the 769 px disc in all 61 frames (the camera profile's linearity limit applies). The nebula's brightest smoothed value is about 1,800 ADU against a sky of 800 ADU, far from the ceiling.

  The source check. At the SIMBAD position the disc's brightest pixel stood 421.8σ above the local sky on the stack. At 2,000 random positions of the stack, 0.4 percent of the discs stood under 3σ, 1.2 percent under 4, 2.2 percent under 5, 5.2 percent under 8 and 20 percent under 20; the lowest was 1.47σ and the 1st percentile 3.7. So blank discs sit at about 1.5 to 4.5σ and the bar of 5 lies above them. But 97.8 percent of all discs passed, because M 57 lies in the Milky Way and nearly every disc holds a star. The target record in the copy database holds 18 55 54.17, +32 37 45.85 for M 57, which is 38 arcminutes from the Ring Nebula; at that position the disc holds a star (324.7σ) and the step measured it (0 clipped pixels). The check does not catch that wrong record; it catches a record that points at blank sky. Fixing the record is the remedy.

  Check of the chain on real pixels: the step was pointed at the clipped star in the reference frame (65532 ADU, at row 9, column 2772; 11,055σ), placed through the plate solution and moved onto each frame by the inverse of Siril's transform. All 61 frames showed clipped pixels (share 0.0030 to 0.0159, median 0.0107, of 543 disc pixels inside the frame) and nonlinear pixels (up to 0.0175). The transform convention was checked separately: with the inverse transform and y = height - 1 - row (the frames are stored top-down), all 60 reference stars landed on detected stars in every frame tested; the other three choices landed 0 or 1 of 60 in the four frames tested whose shifts exceeded 2 px in both axes (they agree with it when the shift is small).

  Time: 61 frames took 0.86 s in all (0.014 s a frame, the stack read and 9 progress messages included) on the local disk of the scratch copy. 200 frames would take about 3 s.

  To repeat: `science/s3/real_target_clipping.py` and `science/s3/peak_sigma_tail.py` in the session's scratch folder (run through `realrun.sh`).
- **Verdict:** Not judged.
- **Limits:** The disc is the larger of 30 arcseconds and the target's semi-major axis; no size is stored today, so a larger target is examined in its core only. Every pixel of the disc counts as a target pixel, so in a crowded field the clipped pixels of field stars in the disc are counted too. The source check does not tell the target from a field star in a crowded field. The step needs the stack's registration record (a stack made before it existed gets no rows until it is stacked again) and a plate solution found by this run (not the header's own). The target record's position is trusted. The step reads at most 200 frames, spread evenly, and for at most 120 seconds; the others get not-measured rows that say so. A color frame clips when any plane clips.
- **To tune for your setup:** If your targets are larger than 30 arcseconds, change `TARGET_REGION_RADIUS_ARCSEC`; with a stored target size the step uses it. Slow storage may need a longer `MAXIMUM_READ_SECONDS`.
- **Regenerate with:** The scripts in the procedure.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/post_processing/test_target_clipping.py` checks the transform, the counts in the disc, the source check on noise and on a star, the crop offset, the assumed and not-measured cases, the cap, the time limit and the shared frame subject; `test_runner_target_clipping.py` checks that the runner hands over the target's position from before the run and this run's own solution; `astrometricslib/pipelines/shared/test/test_registration_record.py` checks the record.
- **Raw record:** None kept.

### AST-20

**A target's stored position against its catalog position**

- **Claim:** A target's RA/Dec should come from the catalog position of its name, not from the center of a solved frame. Copying the first solved frame's center left many targets tens of arcminutes from the object.
- **Settings it supports:**
  - code constant `POSITION_TOLERANCE_FLOOR_ARCMIN` = 15 arcmin in `astrometricslib/pipelines/shared/target_position.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.shared.target_position.POSITION_TOLERANCE_FLOOR_ARCMIN -->
  - code constant `EXTENT_TOLERANCE_FACTOR` = 1.0 in the same file (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.shared.target_position.EXTENT_TOLERANCE_FACTOR -->
- **Status:** Partly measured. One library (56 targets), one day, and SIMBAD gave a size for few of the objects, so the extent factor was not exercised.
- **Data you need:** A library database of targets with stored RA/Dec, and a network connection to SIMBAD.
- **Procedure:**
  1. Run the audit on a copy of the database. It opens the database read-only and creates no file.
  2. For each listed target, decide by eye whether the framing or the stored position is wrong.

```bash
.venv/bin/python -m astrometricslib.scripts.audit_target_positions /path/to/astrometrics.db
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The separation in arcminutes between the stored and the SIMBAD position. The camera's field is 96 arcmin (1.60 degrees) across, so the floor of 15 arcmin is about one sixth of the field.
- **Our result:** 2026-10-23, the user's library (56 targets), audited read-only. 21 targets were more than 15 arcmin from their SIMBAD position: M 57 (37.8 arcmin), corrected first, and NGC 2024 (103.1), NGC 6823 (89.1), NGC 6888 (70.8), NGC 1499 (60.7), NGC 7023 (57.4), M 27 (53.2), M 106 (51.0), IC 1805 (49.1), IC 1396 (47.5), NGC 2403 (42.3), M 51 (41.9), M 31 (41.3), M 101 (34.6), NGC 7000 (31.2), Altair (29.4), M 1 (26.9), NGC 2903 (21.4), M 16 (18.1), Albireo (16.8) and Algol (16.7). All 21 were set to their SIMBAD positions by the user's decision the same day, after a backup. The audit ran before the newer checks: on a scratch copy that still holds the old values, the current audit lists 18 of them and not IC 1396, M 31 or M 16; why it leaves those three out was not checked.
- **Verdict:** Not judged.
- **Limits:** One library. Albireo is a double star, and SIMBAD gives its primary. The extended objects NGC 7000 and IC 1805 are framed away from their center on purpose, so some listed targets may be correct.
- **To tune for your setup:** Set the floor to about one sixth of your camera's field across. Run the audit again.
- **Regenerate with:** `astrometricslib/scripts/audit_target_positions.py`.
- **Guarded by tests:** `astrometricslib/scripts/test/test_audit_target_positions.py` and `astrometricslib/pipelines/shared/test/test_target_position.py` check the mechanics, not the values.
- **Raw record:** None kept. The audit prints its list; the 2026-10-23 list is in "Our result" above.

### AST-21

**Expected position residual: centroid noise, pixel phase and the Gaia epoch gap**

- **Claim:** After the Gaia refit, part of the distance between a star and its Gaia position is noise the star finder cannot avoid. The centroid part is the Cramér-Rao bound for a Gaussian star, `sqrt(2/pi) n / A` pixels along each axis for a peak `A` over a sky noise `n`, which equals `sqrt(2) FWHM / (2.355 SNR)`. On noisy injected stars the project's star finder scatters 1.04 to 1.27 times that bound at peak over noise 8 to 60 (widths 2, 3 and 4 px), so the bound is a floor about 20 percent low. The pixel-phase part is a table found by injection: 0.092, 0.050, 0.033, 0.027, 0.026, 0.022, 0.014 and 0.008 px per axis at widths 1.5, 1.75, 2, 2.25, 2.5, 3, 4 and 5 px. The Gaia epoch part is never measured, because the cached Gaia stars hold no error columns. On the M 57 stack the Gaia refit RMS is 0.200 arcsec over 8,115 stars, and the two parts add in quadrature to 0.115 arcsec (centroid noise 0.090, pixel phase 0.071), so noise alone explains about a third of the squared residual; the rest, 0.163 arcsec in quadrature, is not noise the model knows. The centroid part is a lower bound (the Cramér-Rao limit), and the stack's noise is correlated, which lowers the per-pixel sky noise the bound uses, so the "not noise" remainder is provisional.
- **Settings it supports:**
  - code constant `CRAMER_RAO_PEAK_FACTOR` = sqrt(2/pi) in `astrometricslib/pipelines/astrometry/post_processing/expected_residual.py`. Relation: confirmed by this entry (a theory value; the injection puts the finder at 1.04 to 1.27 times it). <!-- impl: astrometricslib.pipelines.astrometry.post_processing.expected_residual.CRAMER_RAO_PEAK_FACTOR -->
  - code table `PIXEL_PHASE_RMS_PX_BY_FWHM_PX` in the same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.expected_residual.PIXEL_PHASE_RMS_PX_BY_FWHM_PX -->
- **Status:** Partly measured. The bound and the table were checked on injected stars only (a Gaussian star, white noise, a star finder kernel matched to the star). On real data there is one stack (M 57), so the comparison with the measured residual has no replicate.
- **Data you need:** For the injection, nothing. For the comparison with the measured residual, a solved stack with at least 40 Gaia-matched stars.
- **Procedure:**
  1. Inject pixel-integrated Gaussian stars at random positions, with and without noise, and find them with `find_sources_above_background`. <!-- impl: astrometricslib.pipelines.shared.star_finding.find_sources_above_background -->
  2. Compare the scatter of the found centroids with `centroid_error_per_axis_px` <!-- impl: astrometricslib.pipelines.astrometry.post_processing.expected_residual.centroid_error_per_axis_px --> after removing the pixel-phase part in quadrature.
  3. Run the astrometry stage on a stack and read the four `astrometry.expected_*` rows beside `astrometry.gaia_fit_rms_arcsec`.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
astrometrics.processing.astrometry.run(astrometrics.targets.get("M 57"))
for metric in (
    "astrometry.gaia_fit_rms_arcsec",
    "astrometry.expected_centroid_noise_rms_arcsec",
    "astrometry.expected_pixel_phase_rms_arcsec",
    "astrometry.expected_gaia_epoch_error_rms_arcsec",
):
    for row in astrometrics.quality.records.query(metric=metric, target=astrometrics.targets.get("M 57")):
        print(metric, row.status, row.value, row.reason)
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The measured Gaia RMS against the quadrature sum of the two parts. A measured value several times larger is distortion, tilt or a bad match, not noise. The Gaia epoch part is missing, so the sum is a lower bound.
- **Our result:** 2026-10-23, injection (`science/s5/pixel_phase.py` and `science/s5/centroid_noise.py` in the session's scratch folder). Pixel phase, noise-free, 800 stars per width, rms per axis (x, y): 1.5 px 0.090, 0.094; 1.75 px 0.049, 0.051; 2 px 0.033, 0.034; 2.25 px 0.027, 0.028; 2.5 px 0.026, 0.026; 3 px 0.022, 0.021; 4 px 0.014, 0.015; 5 px 0.009, 0.008; the means were within 0.006 px of zero. Noisy stars, rms centroid error with the pixel part removed, over the bound (ratio): width 2 px, peak over noise 8, 15, 30, 60: 1.14, 1.09, 1.04, 1.08; width 3 px: 1.20, 1.17, 1.10, 1.09; width 4 px: 1.27, 1.26, 1.26, 1.19. At peak over noise 120 the pixel part dominates and the ratio is not meaningful. Real stack: the M 57 library stack of the scratch copy (star width 2.44 px, 1.9185 arcsec per pixel), rerun on 2026-10-24 with the table that includes 1.75 and 2.25 px (`science/s5/run_astrometry_stage.py`): Gaia refit RMS 0.1998 arcsec before 0.2530, 8,115 stars; expected centroid noise 0.0905 arcsec over the same stars; expected pixel phase 0.0713 arcsec; the Gaia epoch part not measured. Quadrature sum of the two parts 0.115 arcsec, 0.58 of the measured RMS (0.33 of its square). Sample: 8,115 stars; one stack.
- **Verdict:** Not judged.
- **Limits:** A Gaussian star and white noise; real stars have wings, a field varies in width, and the stack's noise is correlated (see STK-38), which lowers the per-pixel sky sigma and makes the noise term too small, so the part of the residual called "not noise" is provisional. The centroid part is a lower bound by construction (the Cramér-Rao limit). The noise is the sky's only, so a star bright enough for its own photon noise to matter is placed worse than the bound. Faint stars use the star finder's peak, which is biased up by noise. The pixel-phase table was found with a finder kernel equal to the star width. One-shot-color frames are searched on a binned copy and get no value. The Gaia epoch part is never measured.
- **To tune for your setup:** Rebuild the pixel-phase table with `science/s5/pixel_phase.py` if the star finder or its kernel changes. Nothing else needs tuning.
- **Regenerate with:** The scripts named above, run through the session's real-run wrapper; no script in `astrometricslib/scripts/`.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/post_processing/test_expected_residual.py` (the bound by hand, the finder against the bound on noisy stars, the table against the finder on noise-free stars, missing inputs, the rows), `astrometricslib/pipelines/astrometry/test/processing/test_wcs_refinement.py` (the fit's stars and their peak over noise) and `astrometricslib/pipelines/astrometry/test/pre_processing/test_background_noise_report.py`.
- **Raw record:** None kept.

### AST-22

**The same-star radius of 3 arcsec for a star known only by position**

- **Claim:** A position-only row (a star saved as `FIELD_J...` because no catalog named it) within 3 arcsec of a Gaia DR3 star, or of another star, is the same star. Of 247,678 position-only rows, 99,103 lie within 3 arcsec of a cached Gaia star, against 4,218 when every position is moved 30 arcsec north (the chance count). Beyond 3 arcsec the chance count grows faster than the real one. Pairs of position-only rows level off at the same radius.
- **Settings it supports:**
  - code constant `POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC` = 3.0 in `astrometricslib/pipelines/shared/catalog_star_identity.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.POSITION_ONLY_SAME_STAR_RADIUS_ARCSEC -->
  - The 1 arcsec radius at which the cleanup script gives a named row without a stored Gaia number the number of the nearest cached Gaia star (`NAMED_STAR_GAIA_RADIUS_ARCSEC` in `astrometricslib/scripts/reconcile_position_only_star_catalog.py`). Relation: derived from this entry. <!-- impl: astrometricslib.scripts.reconcile_position_only_star_catalog.NAMED_STAR_GAIA_RADIUS_ARCSEC -->
  - The factor of 2 in flux beyond which two rows with light-curve points at the same times are two stars, so the cleanup script does not merge them (`SAME_FRAME_FLUX_RATIO_LIMIT` in the same script). Relation: derived from this entry. <!-- impl: astrometricslib.scripts.reconcile_position_only_star_catalog.SAME_FRAME_FLUX_RATIO_LIMIT -->
- **Status:** Measured. On a copy of our star catalog and our Gaia cache.
- **Data you need:** A star catalog with position-only rows, and a Gaia cache (downloaded tiles) covering the same fields.
- **Procedure:**
  1. Read the position of every position-only row from the star table, and every cached Gaia star with its proper motion (internal functions, not part of the public API; may change). <!-- impl: astrometricslib._internal.drivers.catalog_store.stars_in_tiles -->
  2. Move each Gaia position from epoch 2016.0 to 2026.0 by its proper motion.
  3. Count the rows whose nearest Gaia star is within 1, 2, 3, 4, 5 and 10 arcsec. Repeat with every row moved 30 arcsec north; that count is the chance matches.
  4. Count the pairs of position-only rows within 1, 2, 3 and 5 arcsec of each other.

```python
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord

from astrometricslib import Astrometrics
from astrometricslib._internal.drivers.catalog_store import stars_in_tiles
from astrometricslib._internal.drivers.sky_tiles import healpix_pixels_of_points

astrometrics = Astrometrics()
rows = [s for s in astrometrics.stars.query(text="FIELD_J") if s.id.startswith("FIELD_J")]
ra = np.array([s.right_ascension for s in rows])
dec = np.array([s.declination for s in rows])
tiles = sorted(set(healpix_pixels_of_points(7, ra, dec).tolist()))
gaia = stars_in_tiles(astrometrics.config, tiles, 99.0, include_proper_motion=True)
g_ra = np.array([g[1] for g in gaia]) + np.nan_to_num(
    np.array([g[5] or 0.0 for g in gaia])
) * 10 / 3.6e6 / np.cos(np.radians([g[2] for g in gaia]))
g_dec = np.array([g[2] for g in gaia]) + np.nan_to_num(np.array([g[6] or 0.0 for g in gaia])) * 10 / 3.6e6
catalog = SkyCoord(g_ra * u.deg, g_dec * u.deg)
for shift in (0.0, 30.0):
    _, separation, _ = SkyCoord(ra * u.deg, (dec + shift / 3600) * u.deg).match_to_catalog_sky(catalog)
    print(shift, [int(np.sum(separation.arcsec <= r)) for r in (1, 2, 3, 4, 5, 10)])
```

- **Acceptance criterion:** The radius keeps most real matches (the count minus the chance count) while the chance count stays a small share of the matches.
- **What to look at:** The real-minus-chance count against the radius, and the chance share.
- **Our result:** 2026-11-20, a copy of the library catalog (292,287 rows, 247,678 position-only) and the Gaia cache (1,212,451 stars). Within 1, 2, 3, 4, 5 and 10 arcsec of a cached Gaia star: 44,079, 80,899, 99,103, 107,959, 112,888 and 123,215 rows; chance counts 502, 1,949, 4,218, 7,155, 10,620 and 35,382. Real minus chance: 43,577, 78,950, 94,885, 100,804, 102,268 and 87,833. At 3 arcsec the chance share is 4.3 percent and 93 percent of the real matches at 5 arcsec are kept; at 5 arcsec the chance share is 9.4 percent. In 5,028 rows a second Gaia star is also within 3 arcsec; the cleanup takes the nearest only when the next is at least twice as far. Pairs of position-only rows within 1, 2, 3 and 5 arcsec: 44,927, 74,496, 83,762 and 88,185. For a named row, the chance count at 1 arcsec is 0.2 percent of rows. Of the rows the cleanup would merge, 3,371 groups hold two light curves with points at the same times (102,488 such points). The median flux ratio there had its middle half from 0.82 to 0.99 (two runs that measured one star) and its 5th percentile at 0.22 (a neighbor). Rows already named by a Gaia number lie a median 0.92 arcsec from their own Gaia star (90th percentile 6.2 arcsec), because their saved position is the one measured on an image. The star table is the only place that refers to star ids: the quality rows name frames, stacks and runs, and no target record holds a star id (searched on the same copy).
- **Verdict:** Pass.
- **Limits:** One catalog from one camera at about 1.9 arcsec per pixel. The chance count assumes the sky 30 arcsec away is as crowded. In a crowded field (a globular cluster core) a 3 arcsec radius can join two real stars. A position-only row more than 3 arcsec from its Gaia star (a poor plate solution) is not matched.
- **To tune for your setup:** Run the procedure and choose the radius where the chance count starts to grow faster than the real one, about 1.5 times your pixel scale.
- **Regenerate with:** The procedure above. `astrometricslib/scripts/reconcile_position_only_star_catalog.py` (dry run by default) lists the merges this radius makes.
- **Guarded by tests:** `astrometricslib/test/test_reconcile_position_only_star_catalog.py` and `astrometricslib/pipelines/shared/test/test_star_recording_identified_reconciliation.py` check the merges at this radius, not the radius.
- **Raw record:** None kept.

### AST-23

**Choosing the local plate solver: solve-field against Siril's own solver**

- **Claim:** On 28 frames and stacks of M 13 and M 31 from two cameras, Siril's own plate solver solved every frame that carried a position hint, also with the hint 2° or 5° off. Its solutions lie closer to Gaia DR3 than those of solve-field (the local solver of astrometry.net that the app runs), and it solves in about a fifth of the time. It cannot solve a frame with no position, which solve-field can. ASTAP was not tested. On these results the user chose Siril's own solver as the local solver.
- **Settings it supports:**
  - The removal of solve-field and of its settings (`index_path`, `autoindex` and `cpulimit` in `[Processing.Astrometry.Local Solver]`), of its blind retry and time-limit cut-off, of its widening of the scale window and of the minimum of 10 stars for a local solve (AST-13, a removed register entry). Relation: derived from this entry. A settings file that still holds the old keys loads; nothing reads them.
  - The search radius of 2° around the header position that the pipeline passes to the solver (a literal in `StarIdentifier`, `astrometricslib/pipelines/astrometry/processing/star_identifier.py`; changing it means editing the code). Relation: sanity check only. Hints 2° off still solved with both solvers; solve-field cannot solve a hint 5° off within it. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier -->
  - The retry focal lengths of the app's Siril solve (0.8, 1.25, 0.64 and 1.5625 of the header's, after the header's own, in that order) and its 60 s limit for one run, `FOCAL_LENGTH_RETRY_FACTORS` and `SOLVE_TIMEOUT_SECONDS` in `astrometricslib/_internal/drivers/siril/astrometry/plate_solve.py`. Relation: sanity check only. The extra Siril cases below (10% too long and 20% too short solved, 20% too long failed) and the app check after them agree with the steps of 0.8 and, in the other direction, 1.25 (the inverse of 0.8), but no step size was tuned. The longer factors 1.25 and 1.5625 were added on 2026-11-29, after the app check showed that a header focal length 26% too short failed; they have not been run on a real frame. The order puts each shorter factor before its longer twin, because a header that is too long (a zoom lens's longest value) is the case seen most. <!-- impl: astrometricslib._internal.drivers.siril.astrometry.plate_solve.FOCAL_LENGTH_RETRY_FACTORS --> <!-- impl: astrometricslib._internal.drivers.siril.astrometry.plate_solve.SOLVE_TIMEOUT_SECONDS -->
- **Status:** Partly measured. Two targets, 28 frames and stacks from two cameras, one Linux computer, one run per frame and mode; ASTAP and Windows were not tested, and Siril ran from the study scripts. The app's own Siril solve (`SirilPlateSolveDriver`) was run on one real frame and the retry on four header focal lengths; see the app check below.
- **Data you need:** Light frames whose headers carry a pointing hint (`RA`, `DEC`, `FOCALLEN`, `XPIXSZ`), from each camera and optic you use. Include the poor frames a night gives (trailed, short, through a grating, off target) and a stack or two. To rerun the solve-field half you need solve-field with its index files, and the commit before it was removed (62906cee). You also need Siril 1.4 with its local Gaia astrometry catalog, and a Gaia tile cache that covers the fields to G = 16 with proper motions.
- **Procedure:** The scripts are in `build/agent_tools/plate_solver_study/`; its README gives the commands. solve-field and the project's Gaia refit are no longer in the app, so the scripts that call them need the commit before their removal (62906cee). Each script writes only inside a study folder given on the command line and refuses a folder inside the library.
  1. Copy the frames out of the library and remove their plate solutions (the sky map and SIP distortion cards), keeping the hint cards (`prep_frames.py`).
  2. Run solve-field once per frame with the command line the app's driver builds: `--downsample 2 --uniformize 0`, the pipeline's scale window (5% each side of the header scale) widened by 0.8 and 1.2, `--radius 2` around the hint, `autoindex` and `cpulimit 300`. A blind run passes no position and no scale. The app's blind retry is not run, so each mode is one attempt (`run_solvers.py`). The driver's fit statistics come from an internal function.
  3. Run Siril 1.4 headless and offline, `siril-cli -o -i <private settings file> -s <script>`, with the script `load <frame>`, `platesolve <ra>,<dec> -focal=<FOCALLEN> -pixelsize=<XPIXSZ> -force -noflip` and `save`. The private settings file is a copy of the user's Siril settings with `update_default_scale=false`. Siril's other astrometry settings stay as the user had them: SIP order 3, a near-search radius of 10°, a scale range of 20% and a 30 s limit. A blind run gets a copy of the frame with no `RA`, `DEC`, `OBJCTRA` or `OBJCTDEC` card (`run_solvers.py`).
  4. Judge every solution with the project's own code (`judge.py`). Detect the stars with `SourceDetector`, refine each center with photutils' `centroid_2dg` and measure the star width with `measure_fwhm_from_data`. Read the Gaia DR3 stars brighter than G = 16 from a copy of the Gaia tile cache and move them to `DATE-OBS`. Then run the AST-01 check (stars matched within 2 arcsec, the constant shift and the scatter about it) and the Gaia refit of [AST-02](#ast-02), `refine_wcs_with_gaia`, which reports the solution's RMS against Gaia before and after the refit. Divide that RMS by the star width and compare it with the [AST-04](#ast-04) limit of half the star width. These are internal functions, not part of the public API, and may change. <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_fwhm_from_data --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_field_matching.propagate_gaia_positions --> <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia --> <!-- impl: astrometricslib._internal.drivers.catalog_store.stars_in_tiles -->
  5. Compare the two solutions of each frame: the center, the local scale, rotation and parity (taken from the full mapping, SIP terms included, by finite differences 50 pixels each side of the image center) and the largest sky difference over a 5 by 5 grid of pixels (`judge.py`).
  6. Time ten hinted solves in a row on ten M 31 frames, as mount centering runs them: Siril in one session, Siril in ten separate runs and solve-field in ten separate runs (`centering_timing.py`).
  7. Run the extra Siril cases on one ZWO frame and one Nikon frame (`extras.py`), and join everything into one table (`summarize.py`).

```bash
cd build/agent_tools/plate_solver_study
python prep_frames.py --library <library folder> --study <study folder>
python run_solvers.py --study <study folder> solve-field
python run_solvers.py --study <study folder> siril --siril-settings-source <the user's Siril settings file>
python judge.py --study <study folder> --gaia-cache <the app's catalog_cache.db>
python centering_timing.py --study <study folder>
python extras.py --study <study folder>
python summarize.py --study <study folder>
```

- **Acceptance criterion:** Not recorded (set after the result). The study compared the two solvers side by side and set no limit in advance.
- **What to look at:** The solve rate in each hint mode. The RMS of each solver's own solution against Gaia before any refit, in arcseconds, and that RMS as a fraction of the star width; lower is better. The constant shift in pixels; above about a tenth of a pixel the solution is offset as a whole (AST-01). The Siril minus solve-field differences of center, scale, rotation and parity. The wall-clock time of one solve and of ten solves in a row, and the disk space of each solver's star data.
- **Our result:** 2026-11-27. Code version: commit 1257e980 for the project's measuring code and the app's solve-field command line. solve-field 0.98 (the Ubuntu `astrometry.net` package) with the 4200-series and Tycho-2 index files; Siril 1.4.5 (Flatpak) in offline mode with its Gaia DR3 astrometry catalog `siril_cat_healpix8_astro.dat`. Sample size: 28 frames and stacks.

  - M 13, ZWO ASI 533MM Pro on the 405 mm Apertura 75Q, 10 frames of 30 s: 6 luminance frames of 2026-05-24 (one of them trailed and set aside), 2 grating frames of the same night and 2 frames of 2026-01-11.
  - M 31, the same camera and optic, 10 frames of 2026-11-05: one each of 20 s, 30 s and 120 s, six of 60 s, and one 60 s frame set aside as off target.
  - M 13, Nikon D5300 color camera (raw, with its RGGB color filter pattern), 5 frames of 30 s: 2 of 2023-04-28 and 3 of 2023-05-14. The lens is a Nikkor 75 to 300 mm zoom, which the headers record as 300 mm.
  - Three stacks: the ZWO luminance stacks of M 13 and M 31 and the Nikon color stack of M 13.

  Uncertainty: each number is a median over frames, with the smallest and largest value; no confidence interval was estimated. Data split: no value was tuned on these frames, so the in-sample or held-out question does not arise.

  **Table A.** Solve rate by hint mode, solved of tried.

  | Hint | solve-field | Siril |
  | --- | --- | --- |
  | The header position and scale | 28 of 28 | 28 of 28 |
  | None (blind) | 28 of 28 | 0 of 28 |
  | Position 2° north of the header's | 28 of 28 | 28 of 28 |
  | Position 5° north of the header's | 0 of 4 tried | 28 of 28 |

  Siril's solver needs a position: with none it stops with "no target coordinates passed". solve-field searches 2° around the hint, so a hint 5° off lies outside its search by construction. The four such runs took 60 to 270 s each before failing; only four were tried, because each takes minutes. In the app the blind retry follows such a failure; the blind runs here took a median of 6.7 s (4.5 to 14.2 s). Siril found every 5° error with its near search, which moved the solution 294 to 308 arcmin from the hint.

  **Table B.** Position accuracy of the hinted solutions against Gaia DR3, median (smallest to largest). The RMS is that of the solver's own solution, before any refit.

  | Frames | Solver | RMS against Gaia | Constant shift | Scatter about the shift | RMS / star width |
  | --- | --- | --- | --- | --- | --- |
  | ZWO frames (20) | solve-field | 0.67 arcsec (0.53 to 1.92) | 0.16 px (0.09 to 0.51) | 0.41 arcsec | 0.16 (0.11 to 0.48) |
  | ZWO frames (20) | Siril | 0.24 arcsec (0.12 to 0.31) | 0.026 px (0.019 to 0.034) | 0.15 arcsec | 0.053 (0.033 to 0.074) |
  | Nikon frames (5) | solve-field | 4.85 arcsec (4.71 to 6.85) | 0.11 px (0.03 to 0.20) | 1.33 arcsec | 0.43 (0.28 to 0.63) |
  | Nikon frames (5) | Siril | 1.44 arcsec (1.40 to 1.75) | 0.038 px (0.021 to 0.132) | 0.70 arcsec | 0.12 (0.09 to 0.13) |
  | ZWO stacks, M 13 and M 31 | solve-field | 0.54 and 0.71 arcsec | 0.10 and 0.26 px | 0.33 and 0.37 arcsec | 0.13 and 0.18 |
  | ZWO stacks, M 13 and M 31 | Siril | 0.096 and 0.077 arcsec | 0.026 and 0.022 px | 0.048 and 0.035 arcsec | 0.022 and 0.020 |
  | Nikon stack | solve-field | 5.82 arcsec | 0.15 px | 1.09 arcsec | 0.47 |
  | Nikon stack | Siril | 0.95 arcsec | 0.055 px | 0.33 arcsec | 0.076 |

  Siril fits a third-order SIP distortion to a median of 458 matched stars (102 to 1018) on the ZWO frames; solve-field fits a second-order one to a median of 20.5 (11 to 45). The Gaia refit brought solve-field's ZWO solutions to a median of 0.28 arcsec (0.17 to 0.35) and was applied to all 20. On Siril's ZWO solutions it was never applied, because its result (median 0.28 arcsec) did not fit Gaia better than Siril's own. On the Nikon frames and stack the refit was refused for both solvers, because it would have moved the field by more than its 30 arcsec limit. Two of the five Nikon solutions of solve-field sit above half the star width before the refit (0.63 and 0.51); Siril's largest is 0.13.

  The poor frames solved with both solvers with the header hint and the 2° hint, and with Siril also with the 5° hint: the trailed frame, both grating frames, the 20 s frame and the off-target frame. Siril's RMS against Gaia on them was 0.14 to 0.31 arcsec, solve-field's 0.53 to 0.73 arcsec.

  **Table C.** Siril minus solve-field, hinted solutions: median size of the difference (largest).

  | Frames | Center | Scale | Rotation | Parity | Largest sky difference on a 5 by 5 grid |
  | --- | --- | --- | --- | --- | --- |
  | ZWO frames (20) | 0.46 arcsec (0.94) | 0.021% (0.030%) | 0.008° (0.012°) | Same on all | 2.8 arcsec (5.3), 1.5 px (2.7) |
  | ZWO stacks, M 13 and M 31 | 0.29 and 0.73 arcsec | 0.021% and 0.007% | 0.010° and 0.008° | Same | 3.1 and 2.7 arcsec |
  | Nikon frames (5) | 4.5 arcsec (5.3) | 0.28% (0.40%) | 0.017° (0.026°) | Same on all | 238 arcsec (248), 87 px (91) |
  | Nikon stack | 4.2 arcsec | 0.39% | 0.003° | Same | 164 arcsec, 60 px |

  On the ZWO frames the two solutions agree to well under a pixel at the center and to under 3 px everywhere. On the Nikon field they part near the edges by up to 91 px. Table B shows which one fits there: under solve-field's solution 39 to 89 stars per Nikon frame lie within 2 arcsec of a Gaia star, under Siril's 367 to 709. The Nikon scale solved at 2.737 arcsec/pixel (solve-field) and 2.747 arcsec/pixel (Siril), against 2.675 from the header, which is a focal length of 292 to 293 mm. That is expected of the zoom lens, not an error.

  **Table D.** Wall-clock time, median (smallest to largest). Siril's times include starting its Flatpak; Siril's own reported run time on the ZWO frames was a median of 0.32 s.

  | Case | solve-field | Siril |
  | --- | --- | --- |
  | One hinted solve, ZWO frame | 2.8 s (2.0 to 4.3) | 0.56 s (0.52 to 0.84) |
  | One hinted solve, Nikon frame | 3.9 s (3.8 to 4.2) | 1.30 s (1.26 to 1.31) |
  | One solve, hint 2° off, all 28 | 3.9 s (2.0 to 5.4) | 1.56 s (1.34 to 1.79) |
  | One solve, hint 5° off, all 28 | Failed after 60 to 270 s (4 tried) | 1.59 s (1.50 to 2.57) |
  | Ten centering solves, separate runs | 39.7 s, 10 solved | 6.0 s, 10 solved |
  | Ten centering solves, one Siril session | Not applicable | 3.1 s, 10 solved |

  Disk space: the installed solve-field index files take 458 MB (the 4200 series 160 MB, the Tycho-2 series 298 MB), and the app points solve-field at the whole folder. These runs solved with Tycho-2 indexes 08 to 12 and index 4211. Siril's Gaia astrometry catalog takes 1,521 MB.

  The extra Siril cases, on one M 31 ZWO frame and one M 13 Nikon frame: a focal length 20% too long failed on both (the near search over 10° found nothing); 20% too short and 10% too long solved on both. With the near search turned off (`-radius=0`), positions 0.5° and 1° off solved on both and 2° off only on the wider Nikon field. A position about 20° off solved with `-radius=30` in 5.9 s (ZWO) and 2.7 s (Nikon); radii of 50° and more were rejected as invalid arguments (tried on the ZWO frame only). `-downscale` solved on both. The same command run twice gave identical solution cards on both frames.

  Before the study the user's own Siril settings held a focal length of 292.6 mm and a pixel size of 3.89 µm, the values of a Nikon solve. Siril writes each solve's focal length and pixel size into those settings when `update_default_scale` is true, which is why the study gave Siril a private copy.

  **App check, 2026-11-28.** The app's own Siril solve (`solve_image` in `astrometricslib/_internal/drivers/siril/astrometry/plate_solve.py`, run through a private settings file in a scratch folder, offline, with the local Gaia catalog) on one copy of the M 13 frame `M_13_Light_Luminance_019` (ZWO ASI 533MM Pro, 405 mm). With the header focal length it solved in 2.4 s in all: Siril detected 454 stars and matched 381, with spreads of 0.164 and 0.183 arcsec on the two axes. Their combination, 0.246 arcsec, is what the app reports as the fit residual; it is close to the study's 0.24 arcsec median against Gaia on the ZWO frames, but this is one frame and the two numbers were not compared star by star. With the header focal length set to 500 mm (23% too long) the first attempt failed and the second, at 400 mm, solved to the same solution (5 s). At 700 mm (73% too long) the first two attempts (700 and 560 mm) failed and the third (448 mm) solved (15 s), each failed attempt taking about 5 s. At 300 mm (26% too short) it failed after all three attempts (300, 240 and 192 mm), as the retry then only shortened the focal length. The longer retries (1.25 and 1.5625) were added after this check. The user's own Siril settings were not read as the scratch folder held a made-up settings file.

  **Decisions.** The user made these on 2026-11-27, on these results:

  1. Siril's own plate solver is the local solver for image astrometry and for mount centering.
  2. A frame with no usable position, which Siril cannot solve, goes to the online astrometry.net service.
  3. The app always runs Siril with a private settings file and `update_default_scale=false`, so the user's own Siril settings never change.
  4. The app always passes `-noflip`, so Siril does not flip the image to match the sky.
  5. ASTAP was not tested, because the user chose not to install it. This is a decision, not a measurement.
- **Verdict:** Not judged.
- **Limits:** Two targets, both in the northern sky near declination +36° and +41°, and one computer running Linux; Windows was not tested. Siril ran from the study scripts, so the app's own Siril driver has been checked on one frame only (the app check above), not on the M 31 frames, the Nikon frames or Windows, and not on a frame sent online. The longer retry factors were not run on a real frame, and a frame with a header position that Siril fails is reported as not solved: nothing else is tried. Each frame and mode ran once; only the extra cases repeated a solve, on two frames. solve-field's 5° case ran on 4 frames, and the app's blind retry and online fallback were not run. The Gaia check reads the cached stars to G = 16, so an error common to Gaia and both solvers would not show. The centering times cover the solve only, not the slew or the exposure. The study's star-width fraction after the refit uses the refit's RMS even where the refit was not applied, so Table B gives the fraction before the refit. The frame copies were deleted after the study, so a rerun starts at step 1.
- **To tune for your setup:** Run the study on your own frames, one camera and optic at a time. If Siril fails on a frame whose hint is right, check `FOCALLEN` and `XPIXSZ` in its header: Siril accepts a scale error up to its scale range (`percent_scale_range` in its astrometry settings, 20% here), and a focal length 20% too long failed here. If your pointing errors exceed Siril's near-search radius (`radius`, 10° here), raise it. For a wide field with strong distortion, compare the Nikon rows of Tables B and C.
- **Regenerate with:** The scripts in `build/agent_tools/plate_solver_study/`, in the order of the procedure; their README gives each command. `summarize.py` prints every number above from the study folder's `out/` files.
- **Guarded by tests:** `astrometricslib/_internal/drivers/siril/test/test_siril_plate_solve.py` (the command, the inputs read from header and hints, the fit numbers, the retry order, a blind frame never started in Siril, and one real solve of the M 13 sample frame, marked `golden`) and `test_siril_plate_solve_driver.py` (Siril tried first, the online path for what Siril cannot solve, the choice of driver).
- **Raw record:** Not in the repository. The study folder's `out/` folder on the observatory computer (`~/.cache/astrometrics/solver-study/out/`) holds `results.csv`, `results.json`, `runs.jsonl`, `judge.jsonl`, `compare.jsonl`, `centering_timing.json`, `extras.json`, `manifest.json`, the saved solutions and every run's log.

## Design estimates

These values have not been measured. Each is a code constant unless the table says otherwise; changing a code constant means editing the code. Paths are relative to `astrometricslib/`.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| AST-D01 | Detection threshold; sharpness range; roundness range; expected star width of the frame measurement's finder | 5σ; 0.2 to 1.0; −0.6 to 0.6; 4 px | `SourceDetector` defaults (the 4 px fallback kernel, 1.7 star widths, is `SOURCE_DETECTOR_KERNEL_FWHM_PX` <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SOURCE_DETECTOR_KERNEL_FWHM_PX -->) and `DAOStarFinder` arguments, and `SOURCE_DETECTOR_ROUNDNESS_RANGE`, in `pipelines/astrometry/pre_processing/source_detection.py`; `STAR_SHARPNESS_RANGE` in `pipelines/shared/star_finding.py`; `STAR_FINDER_EXPECTED_FWHM_PX` and `STAR_FINDER_THRESHOLD_SIGMA` in `pipelines/shared/quality/star_profile_fitting.py` <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector.detect --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SOURCE_DETECTOR_ROUNDNESS_RANGE --> <!-- impl: astrometricslib.pipelines.shared.star_finding.STAR_SHARPNESS_RANGE --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_EXPECTED_FWHM_PX --> <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_THRESHOLD_SIGMA --> | Missed stars or false detections. | Run `astrometrics.processing.detect_point_sources(data, threshold_sigma=k)` for several `k` on solved frames and count the detections with and without a catalog match. |
| AST-D02 | Merge distance for duplicate detections | 1 kernel width, at the brightest detection's position (was 15 px at the average position, which on the M 31 stack of 2026-11-15 merged 2,038 of 11,134 detections, all separate stars, and left 143 of the 500 named stars between two real ones; with the new merge none, and the plate solve still succeeds) | `SOURCE_DETECTOR_MERGE_KERNEL_WIDTHS` <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SOURCE_DETECTOR_MERGE_KERNEL_WIDTHS -->, used by `SourceDetector.deduplicate` <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector.deduplicate --> | One star counted twice, or two stars merged. | Count close pairs among detections of a solved frame and check against Gaia whether they are one star or two. |
| AST-D03 | Color-frame block averaging before detection | 2 by 2 | `COLOR_DETECTION_BIN_FACTOR` in `pipelines/astrometry/processing/star_detection.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_detection.COLOR_DETECTION_BIN_FACTOR --> | Fewer catalog matches on color frames. | The false-detection effect is checked on a synthetic demosaiced background only (`test_color_frame_detection.py`); on a real color stack, compare catalog matches with factors 1, 2 and 3. The scaling of the found positions back to full size was checked on the IC 1805 stack (3,957 by 5,936 pixels, 2026-11-23): 379 Gaia stars brighter than G 12 lie in the image, and 87.3% have a detection within 2 px (median distance 0.26 px). Before the fix scaled each single-star position twice, 7,913 of the 11,050 detections lay outside the image and none was within 2 px (median 37.7 px). |
| AST-D04 | Fewest detections to try a solve | 4 | inline in `StarIdentifier.process_image` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.process_image --> | Solves tried that cannot work, or solvable frames skipped. | Solve frames with 4 to 10 detections with `astrometrics.processing.plate_solve` and record which solve. |
| AST-D05 | Brightest stars sent to the online solver | 100 | `MAXIMUM_PLATE_SOLVE_SOURCES` in `pipelines/astrometry/processing/star_identifier.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.MAXIMUM_PLATE_SOLVE_SOURCES --> | Slow or failed online solves. | Time online solves of a few frames with 50, 100 and 200 stars sent. |
| AST-D06 | Pixel-scale window | ±5% | `StarIdentifier._calculate_scale_hints` <!-- impl: astrometricslib.pipelines.astrometry.processing.plate_solution_steps.PlateSolutionSteps._calculate_scale_hints --> | Solver search too wide (slow) or too narrow (no solve). | Compare the solved plate scale with the hint across your library (`report_gate_limits.py` lists stored values) to see how much window you need. No tuner: the time of each hinted solve is not stored. |
| AST-D07 | Catalog match radius | 10 arcsec | `CATALOG_MATCH_RADIUS_ARCSEC` in `pipelines/shared/catalog_star_identity.py` <!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.CATALOG_MATCH_RADIUS_ARCSEC --> | True matches missed or wrong stars matched. | After a Gaia refit, histogram the detected-to-catalog distances from `StarIdentifier.catalog_match_separations_arcsec` and set the radius where true matches end. |
| AST-D08 | Most stars identified per image | 500 | config key `[Processing.Astrometry] maximum_identified_stars` (default 500; 0 means no limit) <!-- impl: astrometricslib.AppConfiguration.get_maximum_identified_stars --> | Long run time and high memory use. | Time a run and watch memory with 500, 1000 and 0 on your densest field. |
| AST-D09 | Failed catalog lookups allowed | under 50% | `MAXIMUM_FAILED_LOOKUP_FRACTION` in `pipelines/astrometry/post_processing/record_quality.py` <!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MAXIMUM_FAILED_LOOKUP_FRACTION --> | Network outages pass the gate, or normal runs fail it. | `report_gate_limits.py` prints the failed / attempted share of every stored run; compare it with runs you know had a network outage. |
| AST-D10 | Failures in a row before the gateway pauses a service | 2 (refused connections or server errors; a timeout, 429, 503, 404 or 410 pauses at once) | `FAILURES_BEFORE_BACKOFF` in `_internal/drivers/outside_services/services.py` <!-- impl: astrometricslib._internal.drivers.outside_services.services.FAILURES_BEFORE_BACKOFF --> | Pauses start too late, so runs wait out many timeouts, or too early, so one network hiccup silences a service. Before 2026-11-22 the Gaia tile fetcher used 3 failures in a row per run; the gateway now keeps the count for every process (see AST-D27). | Read `get_gaia_query_statistics()` after batch runs and `CatalogUseLog.summary()` per job, and count how often a run recovered after 1 failure. |
| AST-D11 | Gaia depth when the header gives no prediction; fewest Gaia-matched stars for the depth check | G 18; 5 | `GAIA_DEFAULT_MAGNITUDE_LIMIT` in `pipelines/astrometry/processing/gaia_tile_queries.py`, `MINIMUM_STARS_FOR_DEPTH_ESTIMATE` in `pipelines/astrometry/processing/star_detection.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_DEFAULT_MAGNITUDE_LIMIT --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_detection.MINIMUM_STARS_FOR_DEPTH_ESTIMATE --> | Too few Gaia stars to match on a frame with no camera profile or exposure, or a depth check that rests on too few stars. The frame's own estimate of its faint end (`estimate_faintest_detected_magnitude`, the flux 99 percent of detections exceed, on a zero point from Gaia G magnitudes only, not SIMBAD's V) no longer sets the depth; it only checks the prediction and records `gaia_depth_exceeded`. | Count how often runs record `gaia_depth_exceeded`, and compare the estimate with the faintest Gaia-matched G over several frames. |
| AST-D12 | Gaia rows per tile download | 50,000 | `GAIA_ROW_LIMIT` in `pipelines/astrometry/processing/gaia_tile_queries.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_ROW_LIMIT --> | A tile at the limit is fetched again as four smaller tiles; a smaller tile at the limit stays incomplete and its stars go unmatched. | Count how often runs record `gaia_tiles_missing` in `astrometry_flags` on dense fields (see AST-D22 for the measured row counts). |
| AST-D13 | Gaia refit: fewest matched stars | 40 | `MINIMUM_MATCHED_STARS` in `pipelines/astrometry/processing/wcs_refinement.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MINIMUM_MATCHED_STARS --> | An unreliable refit, or a good refit refused. | Refit a rich field with random subsets of 20 to 100 stars (`refine_wcs_with_gaia`) and see when the error on the left-out stars grows. |
| AST-D14 | Warning distance between the center star and the position hint | 30 arcsec | `HINT_MATCH_WARNING_ARCSEC` in `pipelines/astrometry/processing/simbad_matching.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.HINT_MATCH_WARNING_ARCSEC --> | Wrong center-star matches pass without a warning. | Lies between one correct match (Vega, 25 arcsec from the solved center) and one wrong one (145 arcsec); log the distance on more spectral stacks with the procedure of [AST-09](#ast-09). Tuner `astrometry.pointing_error` sets the warning from the same stored pointing errors. |
| AST-D15 | Margin around the solved frame for a catalog position | 15 arcmin | `FOOTPRINT_MARGIN_ARCMIN` in `pipelines/shared/target_position.py` <!-- impl: astrometricslib.pipelines.shared.target_position.FOOTPRINT_MARGIN_ARCMIN --> | A catalog answer for the wrong object is accepted, or a right one (a mosaic panel, an offset spectral frame) is rejected. | Log the distance from each solved frame's center to its target's catalog position and look at the largest ones by eye. |
| AST-D16 | Target clipping check: disc inside the image, sky ring around the disc, fewest ring pixels | At least 0.5 of the disc inside the image; ring from 1.5 to 2.5 disc radii; at least 100 ring pixels | `MINIMUM_DISC_SHARE_INSIDE`, `SKY_RING_INNER_FACTOR`, `SKY_RING_OUTER_FACTOR` and `SKY_RING_MINIMUM_PIXELS` in `pipelines/astrometry/post_processing/target_clipping.py` <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.MINIMUM_DISC_SHARE_INSIDE --> <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.SKY_RING_INNER_FACTOR --> <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.SKY_RING_OUTER_FACTOR --> <!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.SKY_RING_MINIMUM_PIXELS --> | A frame with a cut disc is measured on too few pixels, or a good frame is left out. A ring that touches the target's wings or lies too far away gives a wrong sky level, and a ring with too few pixels gives a noisy sky bar. | Repeat the [AST-19](#ast-19) run with the ring edges and the pixel minimum changed, and see whether the share of frames with a measured disc, the sky level and the sky scatter change. |
| AST-D17 | Star-width estimator: candidates, fit refinements and what is skipped | 400 unsaturated candidates (a saturated star is passed over without counting, so a crowded frame whose brightest several hundred stars are saturated still reaches stars it can fit: NGC 6888, Nikon D5300, 180 s at ISO 800, 2026-11-24, first unsaturated star the 717th brightest; 57 of 60 frames had no width before); a fit that moves the center over 2.5 px is dropped; stars over 1.5 times as long as wide are dropped; candidates under 5% of the brightest flux are skipped; a refit in a 15 px box when the fitted sigma is over 3 px; 3 or more pixels at the top value mark saturation | `FWHM_MEASUREMENT_CANDIDATE_COUNT`, `FWHM_MAXIMUM_CENTRE_SHIFT_PX`, `FWHM_MEASUREMENT_MAX_ELONGATION`, `FWHM_MEASUREMENT_MIN_RELATIVE_FLUX`, `FWHM_REFIT_SIGMA_PX`, `FWHM_REFIT_BOX_RADIUS_PX` and `FWHM_SATURATED_PLATEAU_PX` in `pipelines/astrometry/pre_processing/fwhm.py` <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_MEASUREMENT_CANDIDATE_COUNT --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_MAXIMUM_CENTRE_SHIFT_PX --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_MEASUREMENT_MAX_ELONGATION --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_MEASUREMENT_MIN_RELATIVE_FLUX --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_REFIT_SIGMA_PX --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_REFIT_BOX_RADIUS_PX --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.fwhm.FWHM_SATURATED_PLATEAU_PX --> | The median star width reads too wide (trails, neighbors, noise peaks or clipped cores are kept) or too narrow, or too few stars are left. Soft stars are fitted in a box that cuts their wings. | Measure the width of a sparse field, a crowded field and a grating frame with each limit changed in turn, and see which limit changes the median by more than its spread (see [AST-07](#ast-07)). |
| AST-D18 | Star radius stored with each detection (for the star overlay) | Radius where the star falls to 10% of its peak; window of 30 px around the star | `_STAR_RADIUS_PEAK_FRACTION` and `_STAR_RADIUS_WINDOW_HALF_WIDTH_PX` in `pipelines/astrometry/pre_processing/source_detection.py` <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection._STAR_RADIUS_PEAK_FRACTION --> <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection._STAR_RADIUS_WINDOW_HALF_WIDTH_PX --> | Overlay circles look too big or too small, or a neighbor is counted as part of the star. No measurement uses the radius. | Compare the stored radius with the visible disc of bright and faint stars on a few frames. |
| AST-D19 | Check of a reused plate solution, and of a photometry session's solution before its stars are saved: share of stars that must match the catalog, fewest stars to judge, search circle, and share of stars with a catalog entry in that circle whose entry lies within the match radius | At least 10% of the identified stars match a catalog; at least 20 identified stars; a search circle of 2 match radii (20 arcsec on the ZWO ASI533MM with the 405 mm refractor, about 28 arcsec on the Nikon D5300 with a 300 mm lens); of the n stars with a catalog entry in the circle, the share with it within the match radius must reach the chance share s0 plus 3 binomial standard errors, sqrt(s0 (1 - s0) / n), and at least 0.35; judged only when n is at least 20 and that limit is at most 0.5. s0 = (1 - exp(-lam)) / (1 - exp(-lam (W/R)^2)), with lam the field's catalog density times pi R^2; with no density, s0 = (R/W)^2 | `MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS`, `MIN_STARS_TO_VERIFY_REUSED_WCS`, `SOLUTION_CHECK_RADIUS_FACTOR`, `MATCH_CONCENTRATION_SIGMAS` (3) and `MIN_MATCH_CONCENTRATION` (the 0.35 floor) in `pipelines/shared/session_identification.py`, with `chance_concentration` and `match_concentration_limit`; the distances come from `StarIdentifier.nearest_catalog_separations_arcsec`, the density (Gaia stars per square degree over the image footprint, unknown when a tile is missing) from `field_catalog_density` via `SessionIdentificationResult.catalog_density_per_square_degree` <!-- impl: astrometricslib.pipelines.shared.session_identification.MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS --> <!-- impl: astrometricslib.pipelines.shared.session_identification.MIN_STARS_TO_VERIFY_REUSED_WCS --> <!-- impl: astrometricslib.pipelines.shared.session_identification.SOLUTION_CHECK_RADIUS_FACTOR --> <!-- impl: astrometricslib.pipelines.shared.session_identification.MIN_MATCH_CONCENTRATION --> <!-- impl: astrometricslib.pipelines.shared.session_identification.MATCH_CONCENTRATION_SIGMAS --> <!-- impl: astrometricslib.pipelines.shared.session_identification.MAX_DECIDABLE_CONCENTRATION_LIMIT --> <!-- impl: astrometricslib.pipelines.shared.session_identification.chance_concentration --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_field_stars.field_catalog_density --> | A wrong header solution is trusted, or a wrong session saves false position-only stars (limits too loose), or a right solution is thrown away (limits too strict, or too few stars to judge by). | Status: Design estimate. History: until 2026-11-26 the check limited the median distance of matched stars from their catalog positions, first to a fixed 10 arcsec, which could not fire because no match lies beyond the match radius (`CATALOG_MATCH_RADIUS_ARCSEC`, 10 arcsec at 1.915 arcsec per pixel and larger at a coarser plate scale), then to 0.5 of the match radius. The median test was dropped on 2026-11-26 because right solutions have broad match distributions: on the library backup of 2026-11-24 the per-target median match distance was 6.5 arcsec for Algol (ZWO ASI533MM, limit 5 arcsec, so it would have been rejected) and 6.2 arcsec for M 67 (Nikon D5300, limit about 7 arcsec, a narrow pass); every other target with at least 20 matches was below 2.5 arcsec except the Moon field (3.8), and the 99th percentile of 9,965 match distances was 9.75 arcsec. A plain median over every nearest neighbor within a wider circle would not do either, because faint detections with no true catalog star take chance neighbors spread over the whole circle. Basis of the concentration test: under a wrong solution each star's nearest catalog entry is a chance neighbor. When chance neighbors are sparse it is equally likely anywhere in the circle, so the share inside the match radius is (R/W)^2 = 0.25 for W = 2R. In a dense field a star often has several entries within W and its nearest one is drawn inward: s0 is about 0.32 at 10 arcsec with 7,700 catalog stars per square degree and about 0.40 at 14 arcsec. Until 2026-11-26 the limit was a fixed 0.4, which a wrong solution in a dense Nikon field passed about half the time. The share of n stars under a wrong solution is binomial about s0, so 3 standard errors set a wrong solution aside except about once in 700; the limit is 0.64 at n = 20 and 0.46 at n = 100 on the ZWO in that field, 0.73 and 0.55 on the Nikon. The floor of 0.35 keeps a margin above the sparse value when n is large. A right solution puts true neighbors inside R; faint detections with no true star lower the share but, at 20% true matches in the 7,700 per square degree field, it stays near 0.55, which passes once about 40 (ZWO) or 100 (Nikon) stars have a catalog entry in the circle; with fewer the limit would rise above 0.5 (`MAX_DECIDABLE_CONCENTRATION_LIMIT`), where the test cannot tell a right solution from chance, so it is skipped and only the 10% test judges the run (user decision, 2026-11-26). The match-share test alone does not catch a wrong solution in a dense field: chance matches reach about 17% at 10 arcsec and about 30% at 14 arcsec, above the 10% floor; the code comment cites about 28% for good and under 6% for bad alignment, which has no register record yet. The M 31 session of 2026-11-04, named through the pointing map INDI writes, was 56 arcsec off. Tested only on synthetic distances (`test_session_identification.py::TestPlateSolutionProblem`, at 1.915 and 2.675 arcsec per pixel): a right solution with 80% of 500 detections unmatched in the dense field and one with an Algol-like 6.5 arcsec median pass; 400 chance neighbors in the dense field fail for ten seeds at both scales; neighbors spread evenly over the circle with no density fail for five seeds. The shifted-solution check on real frames is still to run. To check: run `build/agent_tools/validate_plate_solution_check.py` on a copy of the library. It shifts the reference sky position (`CRVAL1`, `CRVAL2`) of real plate-solved frames from both setups by 0, 0.5, 1, 2 and 5 match radii in memory, identifies the stars with `identify_session_stars` and `trust_image_wcs=True` inside the run's parameter context, and prints per frame and shift the camera, plate scale, catalog density, n, the share, s0, the limit and the verdict. Unshifted frames should pass and shifts of 2 match radii or more should fail, including a dense Milky Way field on the Nikon. |
| AST-D20 | Catalog seeding: field radius | 0.8 degree radius per field; each field is filled to the depth its frames' equipment predicts (AST-D21) | `DEFAULT_FIELD_RADIUS_DEGREES` in `pipelines/astrometry/utilities/catalog_seeding.py` <!-- impl: astrometricslib.pipelines.astrometry.utilities.catalog_seeding.DEFAULT_FIELD_RADIUS_DEGREES --> | A smaller radius leaves the corners of a wide field without cached tiles. Seeding uses the frame header's pointing, which can be off by minutes of arc; a solved image fills the tiles under its own corners anyway. | Compare the field diagonal of your widest camera with twice the radius. |
| AST-D21 | Gaia query depth predicted from the equipment | 3.2e6 photons/s/cm² at G = 0 (Gaia G band: flux density 2.54e-9 erg/s/cm²/Å, effective width 4053 Å, wavelength 6218 Å); band 400 to 850 nm; optics throughput 0.62; default star width 5 arcsec; fallback sky 18.7 mag/arcsec²; detection signal-to-noise 11.6 in a one-FWHM circle; margin 1.0 mag; cap G 21.0 | `GAIA_BAND_PHOTON_RATE_G0`, `GAIA_BAND_LOWER_NM`, `GAIA_BAND_UPPER_NM`, `OPTICS_THROUGHPUT`, `DEFAULT_FWHM_ARCSEC`, `FALLBACK_SKY_MAG_PER_ARCSEC2`, `DETECTION_SIGNAL_TO_NOISE`, `GAIA_DEPTH_MARGIN`, `GAIA_DEPTH_CAP` in `pipelines/astrometry/processing/gaia_depth.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.GAIA_BAND_PHOTON_RATE_G0 --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.GAIA_BAND_LOWER_NM --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.GAIA_BAND_UPPER_NM --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.OPTICS_THROUGHPUT --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.DEFAULT_FWHM_ARCSEC --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.FALLBACK_SKY_MAG_PER_ARCSEC2 --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.DETECTION_SIGNAL_TO_NOISE --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.GAIA_DEPTH_MARGIN --> <!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.GAIA_DEPTH_CAP --> | Too shallow leaves faint stars unmatched. Too deep slows the queries and grows the cache. | Status: Partly measured (one setup, three deep stacks). Measured 2026-11-20 on the library's plate-solved ASI 533MM stacks through the 75 mm refractor with the L filter, read-only: Gaia DR3 level-7 tiles to G 21 within 0.2 to 0.35 degrees of each center, matched to the star finder's detections within 3 arcsec at `DATE-OBS`, with edges and crowded stars (a neighbor within 20 arcsec) left out. The G where half of the isolated Gaia stars are detected: NGC 7331 19.14, M 31 18.93, M 101 17.70. The 99th percentile of matched G: 19.58, 20.65, 20.77 (the faintest single match is a chance match: 0.1 to 1.2 percent at 3 arcsec). The old constants (throughput 0.85, k = 5, star width 3 arcsec, sky 21.0) predicted 20.47, 20.11 and 18.98 with the measured sky and width (gap 1.18 to 1.32 mag). Causes: the zero point from matched G 13 to 16 stars is 5.5e7 electrons per second at G = 0 over 12 stacks (3.3e7 to 9.1e7), against 7.56e7, so throughput 0.62 (0.33 mag); the finder thresholds 5σ on a cut, zero-mean kernel, worth 0.66 times the circle's signal-to-noise (0.45 mag); the stack noise is 1.15 to 1.51 times the frame prediction (0.15 mag); the shape cuts take about 0.14 mag. The finder keeps no capped list. Fitted k: 10.8, 12.2 and 11.8, so 11.6. Star width: median 2.6 px (2.19 to 3.10) at 1.92 arcsec per pixel. Sky: median frame sky 6.5 electrons per second per pixel (G 18.7 per square arcsecond; 17.9 to 19.1). With the new constants and header inputs only (`predict_gaia_depth_from_header`) the predictions are 19.19, 18.80 and 18.27 (prediction minus measurement +0.05, -0.13, +0.57; M 101's sky is brighter than the fallback). The Nikon D5300 has no QE curve and is not predicted (isolated 50 percent points 15.3 to 18.1). Not held out; one camera and one telescope. Wiring check, 2026-11-20, NGC 7331 L stack (read-only, real cache): 25 level-7 tiles under the image all filled to G 20.19 (58,605 stars, none split, 75 s); 10,913 of 12,159 detections Gaia-named; the single faintest detection read G 20.2 and wrongly flagged the depth, so the check now uses the faint end of 99 percent. Repeat: `measure_detections_in_file`, `stars_in_tiles`, `propagate_gaia_positions` and `predict_gaia_depth_from_header` on a solved stack, then compare `depth_exceeded` over many runs. |
| AST-D22 | Gaia cache tile level | Level 7 (HEALPix nested): a tile covers about 0.21 square degrees, about 0.46° across; a tile whose download hits the 50,000-row `GAIA_ROW_LIMIT` is fetched as its four level-8 children | `GAIA_TILE_LEVEL` in `_internal/drivers/catalog_store.py` <!-- impl: astrometricslib._internal.drivers.catalog_store.GAIA_TILE_LEVEL -->; the split in `pipelines/astrometry/processing/gaia_field_stars.py` | Level 6 exceeds the row limit in most dense fields. A different level means rebuilding the cache. | Status: Partly measured. 2026-11-20, ESA Gaia archive, count-only queries (`build_pixel_query(..., count_only=True)`) of stars with G < 21 in the central tile of ten fields: level 6 held 12,445 (NGC 7331) to 168,274 (NGC 6888), six of ten over 50,000; level 7 held 3,143 to 44,150 (median 14,000), none over; level 8 held 788 to 12,218. All 23 level-7 tiles within 1° of NGC 6888: median 39,510, maximum 57,278, two over 50,000, hence the split. A 1° radius field needs 23 to 28 level-7 requests. Repeat: `pixel_source_id_range(7, tile)` and `build_pixel_query(low, high, 21, count_only=True)` for the tiles from `tiles_for_cone`, sent to the Gaia TAP sync endpoint. |
| AST-D23 | Closest two stars in the viewing and naming list | 1 kernel width (about one star width); plate solving keeps the finder's 2.5 kernel widths | `VIEWING_SEPARATION_KERNEL_WIDTHS` <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.VIEWING_SEPARATION_KERNEL_WIDTHS -->, used when `SourceDetector` is built with `keep_close_pairs` | Too large: the faint companion of a bright star is never detected or named. Too small: one star is split into two, or noise next to a bright star is marked as a star. | Status: Measured. 2026-11-20, NGC 7331 stack (L, 2958 by 2958 px, kernel 4.26 px): 12,159 detections with the separated list, 16,854 with close pairs kept. Star naming against Gaia: named 11,966 to 16,450, detections with no catalog star 193 to 404 (211 more; not yet checked one by one for false detections), catalog stars not detected 2,566 to 339. Repeat: `find_unique_stars(SourceDetector(keep_close_pairs=True), data)` against `SourceDetector()`, then `identify_stack_stars`. |
| AST-D24 | How long a stored catalog answer is reused | SIMBAD 180 days; VSX 90 days; Gaia DR3 variability 3,650 days (the release is frozen). Past its lifetime an answer is asked again; if the catalog cannot be reached the old answer is used with a warning | `SIMBAD_ANSWER_LIFETIME_DAYS` <!-- impl: astrometricslib._internal.drivers.catalog_cache.SIMBAD_ANSWER_LIFETIME_DAYS -->, `VSX_ANSWER_LIFETIME_DAYS` <!-- impl: astrometricslib._internal.drivers.catalog_cache.VSX_ANSWER_LIFETIME_DAYS -->, `GAIA_VARIABILITY_ANSWER_LIFETIME_DAYS` <!-- impl: astrometricslib._internal.drivers.catalog_cache.GAIA_VARIABILITY_ANSWER_LIFETIME_DAYS --> in `_internal/drivers/catalog_cache.py` | Too long: a variable star added to VSX or SIMBAD is not seen as a listed variable, so it may serve as a comparison star. Too short: more live questions, and more days when an outage hits. | Count, for the stars of a field, how many gained a VSX or SIMBAD variable listing between a stored answer and a fresh one (`catalog_use_scope(refresh=True)`), per month of age. |
| AST-D25 | Size of a stored catalog answer tile | SIMBAD: HEALPix level 6 (about 0.9° across); VSX and Gaia variability: level 5 (about 1.8° across) | `SIMBAD_ANSWER_TILE_LEVEL` <!-- impl: astrometricslib._internal.drivers.catalog_cache.SIMBAD_ANSWER_TILE_LEVEL -->, `VARIABLE_CATALOG_TILE_LEVEL` <!-- impl: astrometricslib._internal.drivers.catalog_cache.VARIABLE_CATALOG_TILE_LEVEL --> in `_internal/drivers/catalog_cache.py` | A tile at SIMBAD's row limit loses its faintest objects. Smaller tiles mean more first-time questions per field. A different level leaves the old stored answers unused. | Count the rows SIMBAD returns for a level 6 tile in the densest fields you image, and compare with the 5,000-row limit. |
| AST-D26 | Requests per second sent to one outside host | SIMBAD and VizieR 4 per host; the ESA Gaia archive, Gaia XP, SkyBoT, Astrometry.net, the server Siril asks for Gaia stars, and each download host 1 per host | `CDS_REQUESTS_PER_SECOND` <!-- impl: astrometricslib._internal.drivers.outside_services.services.CDS_REQUESTS_PER_SECOND -->, `DEFAULT_REQUESTS_PER_SECOND` <!-- impl: astrometricslib._internal.drivers.outside_services.services.DEFAULT_REQUESTS_PER_SECOND --> in `_internal/drivers/outside_services/services.py` | Too high: the service can blacklist this computer. Too low: lookups wait for no reason. | Status: Design estimate. SIMBAD's documentation says a client sending more than about 6 queries per second can be blacklisted; 4 leaves a margin for the extra request pyvo sends when it first reads a service's capabilities. VizieR is assumed to follow the same CDS policy; its own page was not read (the SIMBAD page could not be reached on 2026-11-22, so read both again). The ESA archive, SkyBoT and Astrometry.net publish no per-second figure; 1 per second is a cautious guess. Check: open `catalog_use_scope()` around a batch run and compare `requests_sent(service)` with the run time, and count 429 or 503 replies in the log. |
| AST-D27 | When a host is paused, and for how long | A pause starts after 2 failures in a row (`FAILURES_BEFORE_BACKOFF`) or at once after a timeout, a 429, a 404 or 410 from a query address, or a 503 that repeats after 2 repeats 2 s apart (`BUSY_RETRIES`, `BUSY_WAIT_SECONDS`). A pause lasts 60 s, doubling with each further failure up to 900 s (`BACKOFF_BASE_SECONDS`, `BACKOFF_CAP_SECONDS`), or the time in the host's `Retry-After` header, at most 3600 s (`RETRY_AFTER_CAP_SECONDS`). Request time limits: SIMBAD 30 s, VizieR 60 s, ESA Gaia archive 20 s, Gaia XP 60 s, SkyBoT 60 s, Astrometry.net 300 s, downloads 60 s | `BACKOFF_BASE_SECONDS` <!-- impl: astrometricslib._internal.drivers.outside_services.services.BACKOFF_BASE_SECONDS -->, `BACKOFF_CAP_SECONDS` <!-- impl: astrometricslib._internal.drivers.outside_services.services.BACKOFF_CAP_SECONDS -->, `RETRY_AFTER_CAP_SECONDS` <!-- impl: astrometricslib._internal.drivers.outside_services.services.RETRY_AFTER_CAP_SECONDS -->, `BUSY_RETRIES` <!-- impl: astrometricslib._internal.drivers.outside_services.services.BUSY_RETRIES -->, `BUSY_WAIT_SECONDS` <!-- impl: astrometricslib._internal.drivers.outside_services.services.BUSY_WAIT_SECONDS -->, `SIMBAD_TIMEOUT_SECONDS` <!-- impl: astrometricslib._internal.drivers.outside_services.services.SIMBAD_TIMEOUT_SECONDS -->, `VIZIER_TIMEOUT_SECONDS` <!-- impl: astrometricslib._internal.drivers.outside_services.services.VIZIER_TIMEOUT_SECONDS -->, `ESA_TIMEOUT_SECONDS` <!-- impl: astrometricslib._internal.drivers.outside_services.services.ESA_TIMEOUT_SECONDS --> in `_internal/drivers/outside_services/services.py` | Too short: a busy service is asked again before it recovers, and may blacklist this computer. Too long: a service that came back is left unused, and answers come from old stored results (with a warning). | Status: Design estimate. A host that sends `Retry-After` is obeyed. A timeout pauses at once because several jobs that start together each lost 35 to 130 s waiting on SIMBAD (analysis jobs, 2026-11-22 19:53). The 404 rule comes from 2026-11-23, when VizieR in Strasbourg answered 404 on its query address while the Harvard mirror answered it; a call for one object or file (Gaia XP, downloads, Astrometry.net) is exempt. The "too busy" repeat comes from 2026-11-22 on the real Harvard SIMBAD server, which shed load for a few seconds at a time. The other values are guesses of the same kind as the old 120 s SIMBAD pause. Check: list the pause lines in the log (`<service> host <host> is paused for`) after a night and compare each pause with how long the service stayed down. |
