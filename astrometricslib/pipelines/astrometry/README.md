# Astrometry Pipeline

Astrometry is the measurement of where things are on the sky. This pipeline answers two questions about a single image: where in the sky is it pointing, and which stars does it contain? Later pipelines (photometry, spectroscopy, asteroid detection) need both answers, so this one runs first. It runs in three stages, in order: pre-processing, processing, and post-processing. Each stage has its own folder and its own README with more detail.

Terms are defined in the [glossary](../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary). This README also defines each term the first time it appears. The terms used most here:

- **Sky coordinates.** Right Ascension (RA) is the east-west sky coordinate, like longitude, in degrees from 0 to 360. Declination (Dec) is the north-south coordinate, like latitude. Angles on the sky are given in degrees, arcminutes (1/60 degree) and arcseconds (1/3600 degree).
- **Plate solving** works out which part of the sky an image shows, and how it is turned and scaled, by matching the pattern of its stars to a reference star catalogue. The result is a **WCS** (World Coordinate System), the formula stored in the image's FITS header (the text header of the standard astronomy image file) that converts a pixel position to a sky position.
- **Plate scale** (or pixel scale) is how much sky one pixel covers, in arcseconds per pixel.
- **Catalogues.** **SIMBAD** is an online database of named astronomical objects. **Gaia DR3** is the third data release of the European Space Agency's Gaia satellite, which lists precise positions for over a billion stars. **VizieR** is an online service that hosts copies of many catalogues, Gaia DR3 among them.
- **Magnitude** is the astronomer's brightness scale: larger numbers are fainter, and 5 magnitudes is a factor of 100 in brightness. **G** is the magnitude Gaia measures in its own broad band (a band is the range of colours, or wavelengths, a filter or detector lets through). **V** is the older visual magnitude, measured through a standard green-yellow filter, which SIMBAD lists for many stars. V and G differ by a few tenths of a magnitude for most stars and by more than one for the reddest.
- **FWHM** (full width at half maximum) is the width of a star's image where its brightness falls to half its peak, used as the measure of star width.

## What the pipeline does, in order

1. **Load the image.** The pipeline accepts either a file path or an already-loaded image.
2. **Pre-process the image.** Find every star-like point of light in the image, and, when needed elsewhere, measure how sharp those points look.
3. **Process the detections.** Match the pattern of detected points against a reference star catalog to work out the exact map of the sky (plate solving), then look up each star's real identity in astronomical catalogs.
4. **Post-process each match.** Record how confident the pipeline is in each star's identity, based on how the match was made.
5. **Save the results.** The pipeline hands the stars, their catalog identities, and the solved sky coordinates back to the caller for storage.

## Where each piece lives

- `pipeline.py` runs steps 1 through 4 for a single image. It is the coordinator: it does not detect stars or query catalogs itself, but it calls the pieces that do, in the right order.
- `runner.py` sits one level above `pipeline.py`. It runs the pipeline across a whole observing session, writes the solved sky position back onto the target's record, and produces a summary of how well the run went.
  - It also saves the solved sky map (the WCS) into the image's FITS header, so the next program to open the file does not solve it again.
  - The save keeps the SIP distortion terms (`A_*`, `B_*`, `AP_*`, `BP_*`; SIP, Simple Imaging Polynomial, is a standard polynomial correction for lens distortion near the image edges), and keeps `-SIP` in `CTYPE1` and `CTYPE2`.
  - It first deletes the sky-map keywords from any earlier solve, so a re-solve leaves no contradictory cards (header lines).
  - The shared function `write_wcs_to_fits_header` in `shared/session_identification.py` does this work. `test/test_runner_wcs_write_back.py` checks that a saved map gives back the same corner positions as the original (partly measured, synthetic test; see [AST-15](../../../documentation/library_design/validation/astrometry.md#ast-15)).
- `pre_processing/`, `processing/`, and `post_processing/` hold the three stages described above.
- `utilities/` holds tools that support astrometry but are not part of running the pipeline on an image — for example, downloading a region of the star catalog ahead of time so a batch of images does not overwhelm the remote database.

## Searching the cached Gaia catalog near Right Ascension 0

The star identifier reads cached Gaia stars from a box around the field, and Right Ascension wraps from 360 deg back to 0 deg. When the box crosses that line, `_catalog_ra_ranges` in `processing/star_identifier.py` splits it into two RA ranges, one on each side, and the identifier runs one query per range. A field at RA = 0.01 deg therefore selects stars at 359.6 deg as well as at 0.4 deg. The shared helper `wrapped_ra_difference_deg` in `pipelines/shared/angles.py` handles the same wrap for code that compares two RA values.

## Two residual numbers: the plate-solve fit and the catalog match separation

A residual is the leftover distance between where the solved sky map puts a star and where a catalogue says the star is. The summary records two different residuals. They are separate fields because they measure different things. Both are root mean square (RMS) values: square each distance, average, take the root. A small value means a close fit.

- **Plate-solve fit residual** (`plate_solve_fit_residual_rms_arcsec`, arcseconds). The plate solver (solve-field, the command-line solver from the astrometry.net project) matches stars in the image to stars in its own reference catalog. For each matched star it reports where the fitted sky map puts the star and where the reference star sits. The residual is the RMS of those distances. The driver reads it from solve-field's `.corr` table (see `drivers/README.md`). `plate_solve_matched_star_count` is the number of stars in that table. Both are `None` when the solver gave no table, as with the online service.
- **Catalog match separation** (`catalog_match_separation_rms_arcsec`, arcseconds). The identifier measures the distance from each detected star to the nearest SIMBAD or Gaia star and takes the RMS. It counts only stars within the 10 arcsecond match radius, so the number cannot exceed that radius. Wrong matches and SIMBAD's uneven position precision make it larger. It is a rough check, not a measure of the fit. Summaries saved before 2026-10-10 hold it as `astrometricResidualRmsArcsec`, a name that read like the fit residual; reading such a summary moves the value to `catalog_match_separation_rms_arcsec`.

**Which number the gate uses.** The `astrometric_residual` gate judges the RMS against Gaia from the refit of the solution when there is one (see the next section). Otherwise it judges the plate-solve fit residual when the solver reported one, and falls back to the catalog match separation when the solver reported none. The gate's `detail` names the number it used.

**The limit.** The limit is half a star's width. It was set on catalog match separations, and a fit residual is normally smaller, so the limit is lenient for fit residuals (partly measured; see [AST-04](../../../documentation/library_design/validation/astrometry.md#ast-04)).

**What the fit residual measures.** Measured against Gaia, the solution places stars far better than its own fit residual says. Most of that residual comes from errors in the positions of the solver's own reference stars (its index files), not from the solution. A solution can also carry a constant shift that no gate measures; the refit described in the next section removes it (partly measured; see [AST-01](../../../documentation/library_design/validation/astrometry.md#ast-01)).

## Refitting the solution to Gaia

**Why.** The plate solver fits its solution to the few dozen stars of its own index files (its pre-built reference star patterns), so it can sit a pixel or more from the truth while agreeing with itself.

**What it does.** After the solve, `_refine_wcs_against_gaia` (in `processing/star_identifier.py`, the fit itself in `processing/wcs_refinement.py`) checks and corrects the solution against the Gaia DR3 stars of the field, moved to the date of the image:

1. Each detected star (all of them, not only the brightest ones that are named; in a globular cluster the brightest detections are the unresolved cluster core, where Gaia has no star to match) is matched to the nearest Gaia star within 4 pixels of the solver's solution. The 4 pixels must exceed the solver's error and stay small enough that a neighbouring star is rarely taken for the right one (partly measured; see [AST-02](../../../documentation/library_design/validation/astrometry.md#ast-02)).
2. A tangent-plane solution (a projection of the curved sky onto a flat plane touching it at the image centre) with second-order SIP distortion is fitted to the matches (`astropy.wcs.utils.fit_wcs_from_points`). Stars more than 3 robust standard deviations (a spread estimate that a few wild values cannot inflate) from the fit are dropped: blends (two stars too close to separate, measured as one), double stars and wrong matches. A limit of 3 keeps 99.7 percent of normally distributed errors. The match is then repeated within 1.5 pixels of the new solution, up to five times, until the set of stars stops changing.
3. The new solution replaces the solver's only when it fits the same stars better and moves no point of the image by more than 30 arcseconds. A solved field is already near the truth, so a larger move means the fit followed wrong matches; 30 arcseconds is more than ten times the largest solver error measured (partly measured; see [AST-02](../../../documentation/library_design/validation/astrometry.md#ast-02)). The refit is refused with fewer than 40 matched stars. The fit has two linear and ten distortion terms per axis plus the reference point, and 40 stars leaves a wide margin so the distortion terms do not follow noise (a design estimate; see the [design estimates](../../../documentation/library_design/validation/astrometry.md#design-estimates)). The reason is kept in `wcs_refinement`, and the summary's `astrometry_flags` gets `wcs_refined_against_gaia` or `wcs_refinement_skipped`.

**What it records.**

- `gaia_fit_rms_arcsec`: the RMS distance of the stars used to their Gaia positions under the solution that was kept;
- `gaia_fit_rms_before_arcsec`: the same stars under the solver's solution;
- `gaia_fit_star_count`;
- `wcs_refinement_shift_arcsec`: the largest move of the solution over a 5 by 5 grid.

The `astrometric_residual` gate judges `gaia_fit_rms_arcsec` first, because it does not depend on the solver. The refined solution is the one written back into the stack's header.

**Status.** Partly measured on real stacks (see [AST-02](../../../documentation/library_design/validation/astrometry.md#ast-02)) and checked on a synthetic field by `test/processing/test_wcs_refinement.py` (see [AST-03](../../../documentation/library_design/validation/astrometry.md#ast-03)).

**Limits.** The refit assumes detected centroids are unbiased, which saturated and blended stars are not (the clipping removes most of them).

## Gaia positions, proper motion and search depth

**Moving Gaia positions to the image date.** Gaia DR3 gives each star's position for the year 2016.0 (its epoch), plus its proper motion (how far it moves across the sky each year, in milliarcseconds per year). A star that moves 1 arcsecond per year sits 10 arcseconds from its Gaia position ten years later, which is the full match radius. The identifier therefore moves each Gaia position to the image's `DATE-OBS` (the observation date in the FITS header) before matching, using `SkyCoord.apply_space_motion`. The local Gaia cache stores `pmra` and `pmdec` for this.

**Flags.** The identifier leaves positions at epoch 2016.0 and records a flag in the summary's `astrometry_flags` when it cannot move them. The flags are:

- `gaia_proper_motion_unknown`: the Gaia rows carry no proper motion. A cache file written before proper motions were stored gets the columns added, and its old rows read as unknown until a new download replaces them.
- `gaia_epoch_unknown`: the image has no readable `DATE-OBS`.
- `gaia_depth_reduced`: every Gaia service failed at the depth asked for and a shallower answer was used.
- `wcs_refined_against_gaia`, `wcs_refinement_skipped`: whether the solution was refitted to Gaia (see the section above).

**How deep the search goes.** The Gaia search asks only for stars brighter than a G magnitude limit and returns the brightest first. The limit is the faintest detected star's magnitude plus one, kept between 14 and 20. Because the estimate uses V magnitudes (below) and V differs from G, the margin of one magnitude and the 14 to 20 range are design estimates, not measurements (see the [design estimates](../../../documentation/library_design/validation/astrometry.md#design-estimates)). The identifier estimates that magnitude from the detected stars that SIMBAD identified: each gives a zero point (catalog V magnitude plus 2.5 times log10 of the measured flux; the zero point is the constant that converts the camera's measured light to a catalogue magnitude), and the median zero point converts the faintest detection's flux to a magnitude. With fewer than five such stars, the limit is G < 18, the same depth the bulk cache download uses.

**Where it asks.** The search returns at most 50,000 rows. It asks VizieR's copy of Gaia DR3 first and the ESA Gaia archive second; if both fail it asks VizieR again one and then two magnitudes brighter (`_fetch_gaia_cone`). The order and the time limits are partly measured (see [AST-08](../../../documentation/library_design/validation/astrometry.md#ast-08)). A shallower answer records the flag `gaia_depth_reduced`, and the cache records the depth actually fetched. When the result hits the row limit, the identifier records the flag `gaia_row_limit_reached`, because faint stars in the field may be missing.

**The cache.** The same magnitude limit also filters cached rows. The cache is used only when a recorded download covers the whole circle, is at least as deep and was not cut short (see `catalog_store.py` in the drivers README).

## Pixel scale hint

**Why.** The identifier gives the plate solver a pixel scale range so the solver does not search every scale.

**The formula.** The scale is `206.265 * pixel size / focal length` in arcseconds per pixel, with the pixel size in micrometers and the focal length in millimeters. A radian is 206,265 arcseconds, and the micrometer-to-millimeter ratio (1/1000) turns that into 206.265.

**Which header value it uses.** Binning (combining neighbouring pixels, for example 2 by 2, into one larger pixel) changes the effective pixel size, so the identifier must count it exactly once:

- The pixel size is the header's `XPIXSZ`, which already includes binning: INDI/Ekos (libindi), NINA and Siril write the camera's pixel size times the binning factor there. The identifier therefore does not multiply `XPIXSZ` by `XBINNING`.
- When the header has no `XPIXSZ` but has `PIXSIZE1` (the camera's own, unbinned pixel size), the identifier uses `PIXSIZE1` times `XBINNING` (1 if absent).
- When the header has neither but has `PIXSCAL` (a scale in arcseconds per pixel), the identifier uses that value as the scale and applies no binning to it.
- When the X and Y cards differ (`XPIXSZ` and `YPIXSZ`, or `XBINNING` and `YBINNING` for the `PIXSIZE1` rule), the identifier uses their mean and records the flag `scale_hint_binning_mismatch`.

## The gate record

A gate is a pass/fail check on the run. The summary keeps one record per check in `gates`. Each gate is `passed`, `failed` or `not_checked`; a check that could not look is never recorded as passed. `plate_solve` is built in `runner.py`; the other four are built in `post_processing/run_gates.py`. The tests in `test/test_plate_solve_gate.py` and `test/post_processing/test_run_gates.py` give each gate input that must fail it.

| Gate | Fails when | Not checked when |
|---|---|---|
| `plate_solve` | The image was not solved to sky coordinates | Never |
| `source_detection` | No star was detected | Never |
| `catalog_matches` | Fewer than 20 stars were matched to a catalog, too few for the fit to be reliable | The image was not solved |
| `astrometric_residual` | The plate solution misses its stars by more than half a star's width (residual in arcseconds against the plate scale times the star width). The residual is the RMS against Gaia from the refit of the solution, else the solver's fit residual, else the catalog match separation; the detail says which | Neither residual, the plate scale or the star width was measured |
| `catalog_lookup` | Lookups were made and the catalog circuit breaker tripped, or half or more of them failed | No lookup was attempted, including when the breaker was already open from earlier failures in the same process |

The catalog circuit breaker stops further catalogue lookups after repeated failures, so a dead service does not slow every image.

**Limits.** The 50% failed-lookup limit of `catalog_lookup` is a design estimate: the first failures usually come from one slow or unreachable service, and half failing means most stars went unnamed. The 20-star limit of `catalog_matches` and the half-width limit of `astrometric_residual` are design estimates too. The half-width limit is tied to the equipment: the residual is judged against the star width in pixels times the plate scale, both recorded with each run. The half-width limit catches a failed solve and does not rank good ones. The evidence behind the two limits is in [AST-04](../../../documentation/library_design/validation/astrometry.md#ast-04) and [AST-05](../../../documentation/library_design/validation/astrometry.md#ast-05); an earlier library-wide solve check, which predates these gates, is [AST-14](../../../documentation/library_design/validation/astrometry.md#ast-14).

## SIMBAD object types

When a detected star is matched to SIMBAD, the star keeps every object type SIMBAD lists for it (`simbad_object_types`, such as `*|**|EB*|SB*|V*`; for example `EB*` is an eclipsing binary, a pair of stars whose orbit makes them pass in front of each other and dim; `SB*` a spectroscopic binary, a pair seen as one star whose spectrum shows the orbit; and `V*` a variable star), and `StellarObject.known_variability` reads them (see `models/known_variability.py`). The full list matters: the single main type of Algol, a textbook eclipsing binary, is only `SB*`, so a check on the main type alone would call it "not listed as variable". If a result carries only the main type, it is kept only when that type is itself a variable type; otherwise the star is recorded as unknown. Stars named from Gaia alone have no SIMBAD types and are unknown. Stars saved before this was recorded are also unknown until a backfill runs.

## A note on scope

This pipeline identifies stars and solves the sky position of an image. It does not measure star brightness (that is photometry) or star spectra (that is spectroscopy) — those pipelines depend on astrometry's output but live elsewhere.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
