# Astrometry Validation and Tuning

*Version 1.0 · 2026-10-15 · Status: current*

This file records how the tuned values of the astrometry pipeline were checked. The astrometry pipeline finds the stars in an image, works out where the image points on the sky (plate solving) and names the stars from the SIMBAD and Gaia DR3 catalogs. Each entry gives the library calls behind a number, so you can repeat the check on your own frames and keep or change the value. Terms are defined in the [glossary](../../Glossary.md), and the status labels in [index.md](index.md).

Two quantities appear in many entries. A *residual* is the distance left between where a plate solution puts a star and where a catalog puts it. An *RMS* (root mean square) of distances is the square root of the mean of their squares. Both are in arcseconds (1/3600 of a degree). Our frames come from a ZWO ASI 533MM Pro camera at 1.92 arcsec/pixel.

## Measured and partly measured values

### AST-01

**The solver's fit residual against an independent check with Gaia**

- **Claim:** The plate solver's own fit residual mostly measures position errors in its index files (its pre-built reference stars), not errors in the solution. Measured against Gaia DR3, the solutions were several times better than the fit residual says. One solution still carried a constant shift that no gate measured.
- **Settings it supports:**
  - The order in which the `astrometric_residual` gate picks its residual: the RMS against Gaia from the refit first, then the solver's fit residual, then the catalog match separation. This order is code, not a setting (`_residual_and_basis` in `astrometricslib/pipelines/astrometry/post_processing/run_gates.py`; changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates._residual_and_basis -->
  - The Gaia refit itself (see [AST-02](#ast-02)). Relation: derived from this entry.
- **Status:** Partly measured. Two stacks from one camera, measured by hand with no script.
- **Data you need:** A stacked image solved by the local solver (solve-field), so that the solver's `.corr` match table and its fit residual exist. The image needs a `DATE-OBS` card and at least a few hundred unsaturated stars brighter than G = 16 that Gaia lists.
- **Procedure:**
  1. Solve the stack through the public API and read the solver's fit residual from the quality summary.
  2. Detect the stars and refine each center with a 2-D Gaussian fit, which is independent of the pipeline's own centroids. The detector call is public; `centroid_2dg` comes from photutils.
  3. Download the Gaia DR3 stars of the field brighter than G = 16 and move them to the image date (internal functions, not part of the public API; may change). <!-- impl: astrometricslib.drivers.gaia_cone_driver.query_gaia_cone_vizier --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.propagate_gaia_positions --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.read_observation_time -->
  4. Match each refined center to the nearest Gaia star within 2 arcsec and compute the median offset in RA and in Dec (the constant shift) and the scatter about it.

```python
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from photutils.centroids import centroid_2dg

from astrometricslib import Astrometrics
from astrometricslib.drivers.gaia_cone_driver import query_gaia_cone_vizier
from astrometricslib.pipelines.astrometry.processing.star_identifier import (
    propagate_gaia_positions,
    read_observation_time,
)

astrometrics = Astrometrics()
target = astrometrics.targets.get("NGC 2403")
answer = astrometrics.processing.process_target(target, stages=["astrometry"])
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

sources = astrometrics.stars.detect_point_sources(data)
centres = []
for source in sources[:3000]:
    x = int(round(float(source.get("x_centroid", source.get("xcentroid")))))
    y = int(round(float(source.get("y_centroid", source.get("ycentroid")))))
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
  2. Read the refit's results from the quality summary: `gaia_fit_rms_before_arcsec`, `gaia_fit_rms_arcsec`, `gaia_fit_star_count` and `wcs_refinement_shift_arcsec`, and the flags. <!-- impl: astrometricslib.AstrometryPipelineQualityMetrics -->
  3. For an independent view, repeat the Gaia check of [AST-01](#ast-01) (2-D Gaussian centers, Gaia stars brighter than G = 16 moved to the image date) with the solver's solution and with the refined one, and compare the constant shift and the scatter.
  4. To look at the refit alone, call it directly on your own centroids and Gaia stars (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia -->

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
for name in ["M 13", "NGC 2403", "Alnath"]:
    answer = astrometrics.processing.process_target(name, stages=["astrometry"])
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
- **Verdict:** Not judged.
- **Limits:** Three stacks from one camera. The Gaia check uses the same Gaia positions as the fit, so a systematic error in Gaia would not show. The refit assumes the detected centroids are unbiased; saturated and blended stars are not, and the clipping removes most of them. The RMS is measured on the stars the fit used, so it is slightly smaller than the error at a position with no star.
- **To tune for your setup:** If your solver is sometimes off by more than about 3 pixels, raise `INITIAL_MATCH_RADIUS_PIXELS` above that error; if your field is crowded at your pixel scale, lower it so neighbors are not matched. If the refit is refused with "moved the field", look at the solution before raising `MAXIMUM_SHIFT_ARCSEC`: a large move usually means wrong matches. Re-run steps 1 to 3.
- **Regenerate with:** No script. The real-stack check has no script; it was measured by hand on 2026-10-11.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_wcs_refinement.py` checks the refit on a synthetic field (see [AST-03](#ast-03)).
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
  - code constant `MAXIMUM_RESIDUAL_FRACTION_OF_FWHM` = 0.5 in `astrometricslib/pipelines/astrometry/post_processing/run_gates.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MAXIMUM_RESIDUAL_FRACTION_OF_FWHM -->
- **Status:** Partly measured. Set on catalog match separations, not on the solver's fit residuals or the Gaia refit RMS that the gate now prefers; no failed solve was in the sample.
- **Data you need:** A library of solved stacks with quality summaries that carry a plate scale and a star width. Include at least one known bad solve if you have one.
- **Procedure:**
  1. Run astrometry on each stack (public API).
  2. For each, divide the residual by the star width in arcseconds: residual / (`plate_scale_arcsec_per_pixel` × `star_fwhm_px`). Do this for each of the three residuals the gate can use. <!-- impl: astrometricslib.AstrometryPipelineQualityMetrics -->
  3. Read the gate itself with `gate("astrometric_residual")` on the quality summary. <!-- impl: astrometricslib.AstrometryQualitySummary.gate -->

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
for target in astrometrics.targets.list():
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
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/post_processing/test_run_gates.py` checks the gate logic.
- **Raw record:** None kept.

### AST-05

**The minimum of 20 catalog-matched stars**

- **Claim:** Solves with fewer than 20 catalog-matched stars are among those with the largest residuals, so a residual over so few stars cannot be trusted.
- **Settings it supports:**
  - code constant `MINIMUM_MATCHED_STARS` = 20 in `astrometricslib/pipelines/astrometry/post_processing/run_gates.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MINIMUM_MATCHED_STARS -->
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
for target in astrometrics.targets.list():
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
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/post_processing/test_run_gates.py` checks the gate logic.
- **Raw record:** None kept.

### AST-06

**Sizing the detection kernel from the blob width**

- **Claim:** Sizing the star finder's kernel from the blob width (the spread of all the light in a box around each bright source) gives about twice as many catalog matches, and a residual five times smaller, as sizing it from the fitted star width (FWHM).
- **Settings it supports:**
  - The choice of `measure_blob_width_from_data` over `measure_fwhm_from_data` to size the kernel in `StarIdentifier.detect_stars` (code, in `astrometricslib/pipelines/astrometry/processing/star_identifier.py`; changing it means editing the code). Relation: derived from this entry.
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
from astrometricslib.pipelines.astrometry.processing import star_identifier
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
with mock.patch.object(star_identifier, "measure_blob_width_from_data", fwhm.measure_fwhm_from_data):
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

- **Claim:** VizieR's copy of Gaia DR3 answered a dense field quickly when the ESA Gaia archive did not, and the ESA archive answered a query one magnitude shallower. Asking VizieR first, then ESA, then VizieR at shallower depths gets an answer in the cases seen.
- **Settings it supports:**
  - code constants `GAIA_VIZIER_TIMEOUT_SECONDS` = 45, `GAIA_ESA_TIMEOUT_SECONDS` = 60, `GAIA_DEPTH_STEP_MAGNITUDES` = 1.0 and `GAIA_DEPTH_STEPS_DOWN` = 2 in `astrometricslib/pipelines/astrometry/processing/star_identifier.py` (changing them means editing the code). Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_VIZIER_TIMEOUT_SECONDS --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_ESA_TIMEOUT_SECONDS --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEPTH_STEP_MAGNITUDES --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEPTH_STEPS_DOWN -->
- **Status:** Partly measured. One day and one field; the limits are not validated over many nights.
- **Data you need:** A dense field (a globular cluster or the Milky Way) and network access.
- **Procedure:**
  1. Time each service on the same cone (internal driver functions, not part of the public API; may change). <!-- impl: astrometricslib.drivers.gaia_cone_driver.query_gaia_cone_vizier --> <!-- impl: astrometricslib.drivers.gaia_cone_driver.query_gaia_cone_esa -->
  2. Repeat at G < 18 and G < 17, and on several days.

```python
import time

from astrometricslib.drivers.gaia_cone_driver import query_gaia_cone_esa, query_gaia_cone_vizier

ra, dec, radius = 250.42, 36.46, 1.13  # M 13
for query in (query_gaia_cone_vizier, query_gaia_cone_esa):
    for depth in (18.0, 17.0):
        start = time.monotonic()
        try:
            table = query(ra, dec, radius, depth, 50_000, 120)
            print(query.__name__, depth, len(table), "rows in", round(time.monotonic() - start, 1), "s")
        except Exception as error:
            print(query.__name__, depth, "failed after", round(time.monotonic() - start, 1), "s:", error)
```

- **Acceptance criterion:** A time limit sits well above the normal answer time and below the time a failing service takes to give up.
- **What to look at:** The time to answer, in seconds, and whether the query failed. A time limit should sit well above the normal answer time and below the time a failing service takes to give up.
- **Our result:** 2026-10-10, M 13 (1.13° radius): VizieR returned the 14,275 stars brighter than G = 18 in 10 s. The ESA archive failed with HTTP 408 after 64 s at G < 18 and answered G < 17 in 6 s. On the same day every remote lookup of 22 of 23 targets had failed this way on the ESA archive.
- **Verdict:** Not judged.
- **Limits:** One field on one day. The ESA archive said at the time that it may be unstable while it prepares for Gaia DR4, so its behavior may change.
- **To tune for your setup:** If your connection is slow, raise the time limits until a normal dense-field answer fits with room to spare. Re-run the procedure on a few days.
- **Regenerate with:** No script; measured by hand on 2026-10-10.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_gaia_service_fallback.py` checks the order of services, not the time limits.
- **Raw record:** None kept.

### AST-09

**Naming the center star of an unsolved spectral frame**

- **Claim:** The mount's reported position was within 2.5 arcmin of the target on every spectral stack seen. The target's zero order is the brightest compact source near the center, while noise detections there are far fainter. The three limits sit between the cases seen.
- **Settings it supports:**
  - code constant `POINTING_ERROR_SEARCH_RADIUS_ARCSEC` = 300.0 (5 arcmin) in `astrometricslib/pipelines/astrometry/processing/star_identifier.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.POINTING_ERROR_SEARCH_RADIUS_ARCSEC -->
  - code constant `CENTRE_STAR_SEARCH_RADIUS_FRACTION` = 0.10 of the frame's shorter side, same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.CENTRE_STAR_SEARCH_RADIUS_FRACTION -->
  - code constant `CENTRE_STAR_MINIMUM_PEAK_FRACTION` = 0.10 of the frame's brightest pixel, same file. Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.CENTRE_STAR_MINIMUM_PEAK_FRACTION -->
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
- **Limits:** One night of spectral stacks and one mount. A mount with worse pointing needs a wider radius, and the 12 arcmin SIMBAD region searched on this path caps how far it can go.
- **To tune for your setup:** If your mount's pointing error exceeds about half of 300 arcsec, raise `POINTING_ERROR_SEARCH_RADIUS_ARCSEC`, and raise `CENTRE_STAR_SEARCH_RADIUS_FRACTION` so it covers the same angle in pixels. Re-run the procedure.
- **Regenerate with:** No script; measured by hand on 2026-10-04.
- **Guarded by tests:** `astrometricslib/pipelines/astrometry/test/processing/test_center_star_naming.py` checks the selection rules, not the values.
- **Raw record:** None kept.

### AST-10

**Unresolved catalog companions within 3 arcsec**

- **Claim:** Two catalog entries closer than 3 arcsec appear as one star in our frames, and the brighter one dominates the light. Naming the nearer entry can give the wrong star.
- **Settings it supports:**
  - code constant `UNRESOLVED_COMPANION_RADIUS_ARCSEC` = 3.0 in `astrometricslib/pipelines/astrometry/processing/star_identifier.py` (changing it means editing the code). Relation: sanity check only. <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.UNRESOLVED_COMPANION_RADIUS_ARCSEC --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.brightest_unresolved_entry_index -->
- **Status:** Partly measured. One pair (Albireo), a judgment call.
- **Data you need:** An image of a close double star whose two parts have different spectral types, closer than about two pixels at your scale.
- **Procedure:**
  1. Run astrometry (or spectroscopy) on the target through the public API.
  2. Check which catalog entry the star was named after, and whether its catalog spectral type matches the measured spectrum or color.

```python
from astrometricslib import Astrometrics

astrometrics = Astrometrics()
answer = astrometrics.processing.process_target("Albireo", stages=["astrometry"])
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

- **Claim:** Pairs of named catalog rows (not position-only rows) within 1, 2 and 5 arcsec number 678, 754 and 793. The count nearly stops growing after 2 arcsec, so pairs inside it are one star under two names.
- **Settings it supports:**
  - code constant `SAME_STAR_POSITION_TOLERANCE_ARCSEC` = 2.0 in `astrometricslib/pipelines/shared/catalog_star_identity.py` (changing it means editing the code). Relation: derived from this entry. <!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.SAME_STAR_POSITION_TOLERANCE_ARCSEC -->
- **Status:** Measured. On our star catalog.
- **Data you need:** A star catalog built by several runs that named stars from different catalogs (SIMBAD names and Gaia DR3 ids).
- **Procedure:**
  1. Read every star record (public API) and its stored position (internal function, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.shared.star_kinds.stored_position_degrees -->
  2. Leave out position-only rows (ids starting `FIELD_J`).
  3. Count the pairs closer than 1, 2, 3, 5 and 10 arcsec.

```python
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord

from astrometricslib import Astrometrics
from astrometricslib.pipelines.shared.star_kinds import stored_position_degrees

astrometrics = Astrometrics()
stars = astrometrics.stars.query(detail="objects").objects
positions = [stored_position_degrees(s) for s in stars if not s.id.startswith("FIELD_J")]
positions = np.array([p for p in positions if p is not None])
coords = SkyCoord(positions[:, 0] * u.deg, positions[:, 1] * u.deg)
for radius in (1, 2, 3, 5, 10):
    first, second, _, _ = coords.search_around_sky(coords, radius * u.arcsec)
    print(radius, "arcsec:", int(np.sum(first < second)), "pairs")
```

- **Acceptance criterion:** The radius is where the pair count levels off.
- **What to look at:** The pair count against the radius. Pairs of one star under two names pile up at small radii; real neighbors spread out evenly. Choose the radius where the count levels off.
- **Our result:** The library catalog (date not recorded): 678 pairs within 1 arcsec, 754 within 2 and 793 within 5.
- **Verdict:** Not judged.
- **Limits:** One catalog from one camera. The date of the count was not recorded. In a much more crowded field, real neighbors can sit inside 2 arcsec.
- **To tune for your setup:** Run the procedure on your catalog and set the tolerance where the count levels off. Then preview the merge with the script before applying it.
- **Regenerate with:** `astrometricslib/scripts/merge_duplicate_catalog_stars.py` lists the clusters it would merge at this tolerance (`python -m astrometricslib.scripts.merge_duplicate_catalog_stars`; add `--apply` to write). No script records the pair counts.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### AST-12

**The variable-star catalog match radius of 3 arcsec**

- **Claim:** At a 10 arcsec radius, the VSX and Gaia DR3 cross-matches paired bright stars with faint neighbors. At 3 arcsec, the cases seen matched correctly.
- **Settings it supports:**
  - code constants `MATCH_RADIUS_ARCSEC` = 3.0 and `MAXIMUM_MAGNITUDE_DIFFERENCE` = 2.0 in `astrometricslib/pipelines/shared/variable_star_catalogs.py` (changing them means editing the code). Relation: confirmed by this entry. <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.MATCH_RADIUS_ARCSEC --> <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.MAXIMUM_MAGNITUDE_DIFFERENCE -->
- **Status:** Partly measured. Individual cases from the live services, not a count.
- **Data you need:** A star catalog that includes bright stars with faint neighbors and some close pairs.
- **Procedure:**
  1. Preview the variable-star lookup for a set of stars with the script below (it makes no changes without `--apply`).
  2. Build a table of star positions and cross-match it against Gaia DR3 at the library's radius and at a wider one (internal functions, not part of the public API; may change). <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.build_position_table --> <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.run_crossmatch --> <!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.gaia_flags_from_matches -->
  3. Compare which stars change their answer, and look at the distance (`angDist`, arcseconds) and G magnitude (`Gmag`) of what they matched.

```python
from astropy import units as u
from astroquery.xmatch import XMatch

from astrometricslib.pipelines.shared.variable_star_catalogs import (
    GAIA_CATALOG,
    build_position_table,
    gaia_flags_from_matches,
    run_crossmatch,
)

stars = [("Sirius", 101.2872, -16.7161), ("Mira", 34.8366, -2.9776)]  # (id, RA deg, Dec deg)
positions = build_position_table(stars)
at_library_radius = run_crossmatch(XMatch.query, positions, GAIA_CATALOG)
wider = XMatch.query(
    cat1=positions, cat2=GAIA_CATALOG, max_distance=10 * u.arcsec, colRA1="ra", colDec1="dec"
)
ids = [star_id for star_id, _, _ in stars]
print(gaia_flags_from_matches(ids, at_library_radius, {"Sirius": -1.46, "Mira": 6.5}))
print(wider["id", "angDist", "Gmag"])
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** For each star whose answer changes with the radius, the distance and magnitude of the matched entry. A match several magnitudes fainter than the star is a neighbor, not the star.
- **Our result:** At a wider radius, Sirius matched a magnitude-8 entry 4 arcsec away, and the blended pair Mira and VZ Cet matched twice. Dates not recorded.
- **Verdict:** Not judged.
- **Limits:** Individual cases, not a measured rate of wrong matches.
- **To tune for your setup:** At a coarse pixel scale, a real match can sit more than 3 arcsec from the stored position; raise the radius and rely on `MAXIMUM_MAGNITUDE_DIFFERENCE` to reject neighbors.
- **Regenerate with:** `astrometricslib/scripts/backfill_variable_star_flags.py` (`python -m astrometricslib.scripts.backfill_variable_star_flags --limit 2000`; add `--apply` to write).
- **Guarded by tests:** None.
- **Raw record:** None kept.

### AST-13

**The fewest detections for a local solve**

- **Claim:** With fewer than 10 detected stars, the local solver does not solve and spends its whole time limit trying.
- **Settings it supports:**
  - code constant `MINIMUM_SOURCES_FOR_LOCAL_SOLVE` = 10 in `astrometricslib/drivers/astrometry_net_driver.py` (changing it means editing the code). Relation: confirmed by this entry. <!-- impl: astrometricslib.drivers.astrometry_net_driver.MINIMUM_SOURCES_FOR_LOCAL_SOLVE -->
- **Status:** Partly measured. One underexposed frame.
- **Data you need:** A few frames with few detectable stars (short or cloudy exposures), and a local solver with index files.
- **Procedure:**
  1. Count the detections in each frame (public API).
  2. Solve each frame (public API) and time it.

```python
import time

import numpy as np
from astropy.io import fits

from astrometricslib import Astrometrics, PlateSolveFailedError

astrometrics = Astrometrics()
for path in ["/path/to/frame1.fits", "/path/to/frame2.fits"]:
    with fits.open(path, memmap=False) as hdul:
        data = np.asarray(hdul[0].data, dtype=float)
    count = len(astrometrics.stars.detect_point_sources(data))
    start = time.monotonic()
    try:
        solved = astrometrics.stars.plate_solve(path) is not None
    except PlateSolveFailedError:
        solved = False
    print(path, count, "stars,", "solved" if solved else "not solved", round(time.monotonic() - start), "s")
```

- **Acceptance criterion:** Not recorded (set after the result).
- **What to look at:** The detection count against whether the local solve succeeded and how long it took, in seconds.
- **Our result:** A real underexposed M 13 reference frame (date not recorded): a hinted and a blind local solve each ran for their full 300 s time limit with too few detected sources, and neither solved.
- **Verdict:** Not judged.
- **Limits:** One frame. The value 10 was not varied.
- **To tune for your setup:** With deep index files or a wide field, your local solver may succeed with fewer stars; lower the value if solves with 6 to 9 stars succeed on your data.
- **Regenerate with:** No script; seen once by hand.
- **Guarded by tests:** None.
- **Raw record:** None kept.

### AST-14

**Library-wide plate-solve run**

- **Claim:** The plate solve succeeded on the stacks tried and named stars from SIMBAD.
- **Settings it supports:** None directly; it is a check that the whole stage runs. The entry point is `ProcessingPipelines.process_target`. <!-- impl: astrometricslib.ProcessingPipelines.process_target -->
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
        answer = astrometrics.processing.process_target(target, stages=["astrometry"])
        result = answer.results["astrometry"]
        print(name, result.get("wcs") is not None, len(result.get("stellar_objects", [])))
        astrometrics.targets.save()
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

## Design estimates

These values have not been measured. Each is a code constant unless the table says otherwise; changing a code constant means editing the code. Paths are relative to `astrometricslib/`.

| ID | Setting | Value | Where | Risk if wrong | How you could measure it on your data |
| --- | --- | --- | --- | --- | --- |
| AST-D01 | Detection threshold; sharpness range; roundness range | 5σ; 0.2 to 1.0; −0.6 to 0.6 | `SourceDetector` defaults and `DAOStarFinder` arguments in `pipelines/astrometry/pre_processing/source_detection.py` <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector.detect --> | Missed stars or false detections. | Run `astrometrics.stars.detect_point_sources(data, threshold_sigma=k)` for several `k` on solved frames and count the detections with and without a catalog match. |
| AST-D02 | Merge distance for duplicate detections | 15 px | `SourceDetector.deduplicate` default <!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector.deduplicate --> | One star counted twice, or two stars merged. | Count close pairs among detections of a solved frame and check against Gaia whether they are one star or two. |
| AST-D03 | Color-frame block averaging before detection | 2 by 2 | `_COLOR_DETECTION_BIN_FACTOR` in `pipelines/astrometry/processing/star_identifier.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier._COLOR_DETECTION_BIN_FACTOR --> | Fewer catalog matches on color frames. | Checked on a synthetic demosaiced background only (`test_color_frame_detection.py`); on a real color stack, compare catalog matches with factors 1, 2 and 3. |
| AST-D04 | Fewest detections to try a solve | 4 | inline in `StarIdentifier.process_image` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier.process_image --> | Solves tried that cannot work, or solvable frames skipped. | Solve frames with 4 to 10 detections with `astrometrics.stars.plate_solve` and record which solve. |
| AST-D05 | Brightest stars sent to the online solver | 100 | `MAXIMUM_PLATE_SOLVE_SOURCES` in `pipelines/astrometry/processing/star_identifier.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.MAXIMUM_PLATE_SOLVE_SOURCES --> | Slow or failed online solves. | Time online solves of a few frames with 50, 100 and 200 stars sent. |
| AST-D06 | Pixel-scale window; driver widening; blind-retry cut-off | ±5%; 0.8 and 1.2; 90% of the time limit | `StarIdentifier._calculate_scale_hints`; `AstrometryNetPlateSolveDriver` and `HINTED_SOLVE_NEAR_TIMEOUT_FRACTION` in `drivers/astrometry_net_driver.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.StarIdentifier._calculate_scale_hints --> <!-- impl: astrometricslib.drivers.astrometry_net_driver.HINTED_SOLVE_NEAR_TIMEOUT_FRACTION --> | Solver search too wide (slow) or too narrow (no solve). | Compare the solved plate scale with the hint across your library (`report_gate_limits.py` lists stored values) to see how much window you need. |
| AST-D07 | Catalog match radius | 10 arcsec | `CATALOG_MATCH_RADIUS_ARCSEC` in `pipelines/shared/catalog_star_identity.py` <!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.CATALOG_MATCH_RADIUS_ARCSEC --> | True matches missed or wrong stars matched. | After a Gaia refit, histogram the detected-to-catalog distances from `StarIdentifier.catalog_match_separations_arcsec` and set the radius where true matches end. |
| AST-D08 | Most stars identified per image | 500 | config key `[Processing.Astrometry] maximum_identified_stars` (default 500; 0 means no limit) <!-- impl: astrometricslib.AppConfiguration.get_maximum_identified_stars --> | Long run time and high memory use. | Time a run and watch memory with 500, 1000 and 0 on your densest field. |
| AST-D09 | Failed catalog lookups allowed | under 50% | `MAXIMUM_FAILED_LOOKUP_FRACTION` in `pipelines/astrometry/post_processing/run_gates.py` <!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MAXIMUM_FAILED_LOOKUP_FRACTION --> | Network outages pass the gate, or normal runs fail it. | `report_gate_limits.py` prints the failed / attempted share of every stored run; compare it with runs you know had a network outage. |
| AST-D10 | Consecutive Gaia failures before the circuit breaker trips | 3 | `GAIA_CONSECUTIVE_FAILURE_LIMIT` in `pipelines/astrometry/processing/star_identifier.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_CONSECUTIVE_FAILURE_LIMIT --> | Retries last too long, or the service is abandoned too early. | Read `get_gaia_query_statistics()` after batch runs and count how often a run recovered after 1, 2 or 3 failures. |
| AST-D11 | Gaia search depth: margin; clamps; default; fewest stars for the estimate | 1.0 mag; G 14 to 20; G 18; 5 | `GAIA_MAGNITUDE_LIMIT_MARGIN`, `GAIA_MINIMUM_MAGNITUDE_LIMIT`, `GAIA_MAXIMUM_MAGNITUDE_LIMIT`, `GAIA_DEFAULT_MAGNITUDE_LIMIT`, `MINIMUM_STARS_FOR_DEPTH_ESTIMATE` in `pipelines/astrometry/processing/star_identifier.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MAGNITUDE_LIMIT_MARGIN --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MINIMUM_MAGNITUDE_LIMIT --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MAXIMUM_MAGNITUDE_LIMIT --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEFAULT_MAGNITUDE_LIMIT --> <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.MINIMUM_STARS_FOR_DEPTH_ESTIMATE --> | Too few Gaia stars to match, or too many to download. | Compare `estimate_gaia_magnitude_limit` with the G magnitude of the faintest detected star that Gaia matched, over several frames. |
| AST-D12 | Gaia rows per cone search | 50,000 | `GAIA_ROW_LIMIT` in `pipelines/astrometry/processing/star_identifier.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_ROW_LIMIT --> | Truncated Gaia lists in dense fields. | Count how often runs record `gaia_row_limit_reached` in `astrometry_flags`. |
| AST-D13 | Gaia refit: fewest matched stars | 40 | `MINIMUM_MATCHED_STARS` in `pipelines/astrometry/processing/wcs_refinement.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MINIMUM_MATCHED_STARS --> | An unreliable refit, or a good refit refused. | Refit a rich field with random subsets of 20 to 100 stars (`refine_wcs_with_gaia`) and see when the error on the left-out stars grows. |
| AST-D14 | Warning distance between the center star and the position hint | 30 arcsec | `HINT_MATCH_WARNING_ARCSEC` in `pipelines/astrometry/processing/star_identifier.py` <!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.HINT_MATCH_WARNING_ARCSEC --> | Wrong center-star matches pass without a warning. | Lies between one correct match (Vega, 25 arcsec from the solved center) and one wrong one (145 arcsec); log the distance on more spectral stacks with the procedure of [AST-09](#ast-09). |
