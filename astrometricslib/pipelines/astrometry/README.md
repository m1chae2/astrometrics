# Astrometry Pipeline

This pipeline answers two questions about a single image: where in the sky is it pointing, and which stars does it contain? It runs in three stages, in order: pre-processing, processing, and post-processing. Each stage has its own folder and its own README with more detail.

## What the pipeline does, in order

1. **Load the image.** The pipeline accepts either a file path or an already-loaded image.
2. **Pre-process the image.** Find every star-like point of light in the image, and, when needed elsewhere, measure how sharp those points look.
3. **Process the detections.** Match the detected points against the image itself to work out the exact map of the sky (plate solving), then look up each star's real identity in astronomical catalogs.
4. **Post-process each match.** Record how confident the pipeline is in each star's identity, based on how the match was made.
5. **Save the results.** The pipeline hands the stars, their catalog identities, and the solved sky coordinates back to the caller for storage.

## Where each piece lives

- `pipeline.py` runs steps 1 through 4 for a single image. It is the coordinator: it does not detect stars or query catalogs itself, but it calls the pieces that do, in the right order.
- `runner.py` sits one level above `pipeline.py`. It runs the pipeline across a whole observing session, writes the solved sky position back onto the target's record, and produces a summary of how well the run went. It also saves the solved sky map (the WCS, which converts pixel positions to sky positions) into the image's FITS header, so the next program to open the file does not solve it again. The save keeps the SIP distortion terms (`A_*`, `B_*`, `AP_*`, `BP_*`), which correct lens distortion near the image edges, and keeps `-SIP` in `CTYPE1` and `CTYPE2`. It first deletes the sky-map keywords from any earlier solve, so a re-solve leaves no contradictory cards. The shared function `write_wcs_to_fits_header` in `shared/session_identification.py` does this work. `test/test_runner_wcs_write_back.py` checks that a saved map gives the same corner positions as the original to 0.01 arcsecond.
- `pre_processing/`, `processing/`, and `post_processing/` hold the three stages described above.
- `utilities/` holds tools that support astrometry but are not part of running the pipeline on an image — for example, downloading a region of the star catalog ahead of time so a batch of images does not overwhelm the remote database.

## Searching the cached Gaia catalog near Right Ascension 0

The star identifier reads cached Gaia stars from a box around the field, and Right Ascension (RA, the east-west sky coordinate) wraps from 360 deg back to 0 deg. When the box crosses that line, `_catalog_ra_ranges` in `processing/star_identifier.py` splits it into two RA ranges, one on each side, and the identifier runs one query per range. A field at RA = 0.01 deg therefore selects stars at 359.6 deg as well as at 0.4 deg. The shared helper `wrapped_ra_difference_deg` in `pipelines/shared/angles.py` handles the same wrap for code that compares two RA values.

## Two residual numbers: the plate-solve fit and the catalog match separation

The summary records two different measures of how well the solved sky map fits the stars. They are separate fields because they measure different things.

- **Plate-solve fit residual** (`plate_solve_fit_residual_rms_arcsec`, arcseconds). The plate solver (solve-field) matches stars in the image to stars in its own reference catalog. For each matched star it reports where the fitted sky map puts the star and where the reference star sits. The residual is the root mean square (RMS: square each distance, average, take the root) of those distances. The driver reads it from solve-field's `.corr` table (see `drivers/README.md`). A small value means the fit is good. `plate_solve_matched_star_count` is the number of stars in that table. Both are `None` when the solver gave no table, as with the online service.
- **Catalog match separation** (`catalog_match_separation_rms_arcsec`, arcseconds). The identifier measures the distance from each detected star to the nearest SIMBAD or Gaia star and takes the RMS. It counts only stars within the 10 arcsecond match radius, so the number cannot exceed that radius. Wrong matches and SIMBAD's uneven position precision make it larger. It is a rough check, not a measure of the fit. Summaries saved before 2026-10-10 hold it as `astrometricResidualRmsArcsec`, a name that read like the fit residual; reading such a summary moves the value to `catalog_match_separation_rms_arcsec`.

The `astrometric_residual` gate judges the RMS against Gaia from the refit of the solution when there is one (see the next section). Otherwise it judges the plate-solve fit residual when the solver reported one, and falls back to the catalog match separation when the solver reported none. The gate's `detail` names the number it used. The limit (half a star's width) was set on catalog match separations, and a fit residual is normally smaller, so the limit is lenient for fit residuals. Measured on 2026-10-10: solve-field reported fit residuals of 1.35 arcsec (M 13, 95 stars) and 0.82 arcsec (NGC 2403, 24 stars), 0.29 and 0.11 of a star's width. An independent check of the same solutions (2-D Gaussian centroids against Gaia DR3 moved to the image date) found a median scatter of 0.23 to 0.24 arcsec, so the fit residual mostly reflects the solver's reference catalog, not the solution. That check also found a constant 0.60 arcsec shift in declination on NGC 2403, which no gate measures.

## Refitting the solution to Gaia

The plate solver fits its solution to the few dozen stars of its own index files, so it can sit a pixel or more from the truth while agreeing with itself. After the solve, `_refine_wcs_against_gaia` (in `processing/star_identifier.py`, the fit itself in `processing/wcs_refinement.py`) checks and corrects the solution against the Gaia DR3 stars of the field, moved to the date of the image:

1. Each detected star (all of them, not only the brightest ones that are named; in M 13 the brightest 500 are the unresolved cluster core, where Gaia has no star to match) is matched to the nearest Gaia star within 4 pixels of the solver's solution.
2. A tangent-plane solution with second-order SIP distortion is fitted to the matches (`astropy.wcs.utils.fit_wcs_from_points`). Stars more than 3 robust standard deviations from the fit (blends, wrong matches) are dropped, and the match is repeated within 1.5 pixels of the new solution, up to five times, until the set of stars stops changing.
3. The new solution replaces the solver's only when it fits the same stars better and moves no point of the image by more than 30 arcseconds. It is refused with fewer than 40 matched stars. The reason is kept in `wcs_refinement`, and the summary's `astrometry_flags` gets `wcs_refined_against_gaia` or `wcs_refinement_skipped`.

The summary records `gaia_fit_rms_arcsec` (the RMS distance of the stars used to their Gaia positions under the solution that was kept), `gaia_fit_rms_before_arcsec` (the same stars under the solver's solution), `gaia_fit_star_count` and `wcs_refinement_shift_arcsec` (the largest move of the solution over a 5 by 5 grid). The `astrometric_residual` gate judges `gaia_fit_rms_arcsec` first, because it does not depend on the solver. The refined solution is the one written back into the stack's header.

Measured on 2026-10-11 on the ASI 533MM stacks (1.92 arcsec per pixel). Before, a separate measurement (centroids from a 2-D Gaussian fit instead of the pipeline's, against Gaia stars brighter than G = 16 moved to the image date) found the solver's solutions off from Gaia by a constant 0.10 arcsec (M 13), 0.59 arcsec (NGC 2403) and 2.75 arcsec, or 1.4 pixels (Alnath, a 2-frame stack). After the refit the same measurement gives 0.00, 0.06 and 0.01 arcsec, with no trend across the field and a scatter of 0.15, 0.14 and 0.20 arcsec (about a tenth of a pixel). The refit used 3,171, 431 and 474 stars. The solver's own fit residual was 1.35, 0.73 and 0.79 arcsec, so it measured the index files and not the solution. Two limits: the second measurement uses the same Gaia positions as the fit, so a systematic error in Gaia would not show; and the refit assumes detected centroids are unbiased, which saturated and blended stars are not (the clipping removes most of them). `tests/processing/test_wcs_refinement.py` recovers a known solution from a synthetic field with 15 percent wrong detections to 0.03 arcsec.

## Gaia positions, proper motion and search depth

Gaia DR3 gives each star's position for the year 2016.0, plus its proper motion (how far it moves across the sky each year, in milliarcseconds per year). A star that moves 1 arcsecond per year sits 10 arcseconds from its Gaia position ten years later, which is the full match radius. The identifier therefore moves each Gaia position to the image's `DATE-OBS` before matching, using `SkyCoord.apply_space_motion`. The local Gaia cache stores `pmra` and `pmdec` for this.

The identifier leaves positions at epoch 2016.0 and records a flag in the summary's `astrometry_flags` when it cannot move them:

- `gaia_proper_motion_unknown`: the Gaia rows carry no proper motion. A cache file written before proper motions were stored gets the columns added, and its old rows read as unknown until a new download replaces them.
- `gaia_epoch_unknown`: the image has no readable `DATE-OBS`.
- `gaia_depth_reduced`: every Gaia service failed at the depth asked for and a shallower answer was used.
- `wcs_refined_against_gaia`, `wcs_refinement_skipped`: whether the solution was refitted to Gaia (see the section above).

The Gaia search asks only for stars brighter than a G magnitude limit and returns the brightest first. The limit is the faintest detected star's magnitude plus one, kept between 14 and 20. The identifier estimates that magnitude from the detected stars that SIMBAD identified: each gives a zero point (catalog V magnitude plus 2.5 times log10 of the measured flux), and the median zero point converts the faintest detection's flux to a magnitude. With fewer than five such stars, the limit is G < 18. The search returns at most 50,000 rows. It asks VizieR's copy of Gaia DR3 first and the ESA Gaia archive second; if both fail it asks VizieR again one and then two magnitudes brighter (`_fetch_gaia_cone`). On 2026-10-10 the ESA archive timed out on M 13 at G < 18 while VizieR answered in 10 s. A shallower answer records the flag `gaia_depth_reduced`, and the cache records the depth actually fetched. When the result hits that limit, the identifier records the flag `gaia_row_limit_reached`, because faint stars in the field may be missing. The same magnitude limit also filters cached rows. The cache is used only when a recorded download covers the whole circle, is at least as deep and was not cut short (see `catalog_store.py` in the drivers README).

## Pixel scale hint

The identifier gives the plate solver a pixel scale range so the solver does not search every scale. The scale is `206.265 * pixel size / focal length` in arcseconds per pixel, with the pixel size in micrometers and the focal length in millimeters. The pixel size is the header's `XPIXSZ`, which already includes binning: INDI/Ekos (libindi), NINA and Siril write the camera's pixel size times the binning factor there. The identifier therefore does not multiply `XPIXSZ` by `XBINNING`. When the header has no `XPIXSZ` but has `PIXSIZE1` (the camera's own, unbinned pixel size), the identifier uses `PIXSIZE1` times `XBINNING` (1 if absent). When the header has neither but has `PIXSCAL` (a scale in arcseconds per pixel), the identifier uses that value as the scale and applies no binning to it. When the X and Y cards differ (`XPIXSZ` and `YPIXSZ`, or `XBINNING` and `YBINNING` for the `PIXSIZE1` rule), the identifier uses their mean and records the flag `scale_hint_binning_mismatch`.

## A note on scope

This pipeline identifies stars and solves the sky position of an image. It does not measure star brightness (that is photometry) or star spectra (that is spectroscopy) — those pipelines depend on astrometry's output but live elsewhere.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.

## The gate record

The summary keeps one record per check in `gates`. Each gate is `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. `plate_solve` is built in `runner.py`; `source_detection` and `catalog_lookup` are built in `post_processing/run_gates.py`. The tests in `test/test_plate_solve_gate.py` and `test/post_processing/test_run_gates.py` give each gate input that must fail it.

| Gate | Fails when | Not checked when |
|---|---|---|
| `plate_solve` | The image was not solved to sky coordinates | Never |
| `source_detection` | No star was detected | Never |
| `catalog_matches` | Fewer than 20 stars were matched to a catalog, too few for the fit to be reliable | The image was not solved |
| `astrometric_residual` | The plate solution misses its stars by more than half a star's width (residual in arcseconds against the plate scale times the star width). The residual is the RMS against Gaia from the refit of the solution, else the solver's fit residual, else the catalog match separation; the detail says which | Neither residual, the plate scale or the star width was measured |
| `catalog_lookup` | Lookups were made and the catalog circuit breaker tripped, or half or more of them failed | No lookup was attempted, including when the breaker was already open from earlier failures in the same process |

`source_detection` and `catalog_lookup` are new flags. The 50% failed-lookup limit is a design estimate. The two limits are design estimates, tied to the equipment: the residual is judged against the star width in pixels times the plate scale, both recorded with each run. On the library (2026-10-09) the 8 saved solves with a plate scale had catalog match separations of 0.14 to 0.44 of a star's width, so the half-width limit catches a failed solve and does not rank good ones; and the four solves with fewer than 20 matched stars (4, 6, 11 and 19) are among the five with the largest residuals (5.8 to 9.0 arcsec).

## SIMBAD object types

When a detected star is matched to SIMBAD, the star keeps every object type SIMBAD lists for it (`simbad_object_types`, such as `*|**|EB*|SB*|V*`), and `StellarObject.known_variability` reads them (see `models/known_variability.py`). The full list matters: the single main type of Algol, a textbook eclipsing binary, is only `SB*`, so a check on the main type alone would call it "not listed as variable". If a result carries only the main type, it is kept only when that type is itself a variable type; otherwise the star is recorded as unknown. Stars named from Gaia alone have no SIMBAD types and are unknown. Stars saved before this was recorded are also unknown until a backfill runs.
