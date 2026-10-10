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
| `astrometric_residual` | The plate solution misses its stars by more than half a star's width (residual in arcseconds against the plate scale times the star width) | The residual, the plate scale or the star width was not measured |
| `catalog_lookup` | Lookups were made and the catalog circuit breaker tripped, or half or more of them failed | No lookup was attempted, including when the breaker was already open from earlier failures in the same process |

`source_detection` and `catalog_lookup` are new flags. The 50% failed-lookup limit is a design estimate. The two limits are design estimates, tied to the equipment: the residual is judged against the star width in pixels times the plate scale, both recorded with each run. On the library (2026-10-09) the 8 saved solves with a plate scale had residuals of 0.14 to 0.44 of a star's width, so the half-width limit catches a failed solve and does not rank good ones; and the four solves with fewer than 20 matched stars (4, 6, 11 and 19) are among the five with the largest residuals (5.8 to 9.0 arcsec).

## SIMBAD object types

When a detected star is matched to SIMBAD, the star keeps every object type SIMBAD lists for it (`simbad_object_types`, such as `*|**|EB*|SB*|V*`), and `StellarObject.known_variability` reads them (see `models/known_variability.py`). The full list matters: the single main type of Algol, a textbook eclipsing binary, is only `SB*`, so a check on the main type alone would call it "not listed as variable". If a result carries only the main type, it is kept only when that type is itself a variable type; otherwise the star is recorded as unknown. Stars named from Gaia alone have no SIMBAD types and are unknown. Stars saved before this was recorded are also unknown until a backfill runs.
