# Astrometry post-processing

This stage does no new matching or lookup work. It takes the outcome of a star's catalog match, already computed during processing, and records it as a clear, structured quality record. Without this stage, that information would be used once and then discarded.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears. SIMBAD is an online database of named astronomical objects, and Gaia is the European Space Agency's catalog of precise positions for over a billion stars.

## What this stage does, in order

1. Record how the star was matched. The pipeline marks every identified star with which catalog matched it, SIMBAD or Gaia.
2. Record how far off the match was. The pipeline stores the distance, in arcsec (1/3600°), between the detected position and the catalog's own position, with the star. A smaller distance means a more confident match.
3. Flag crowded matches. If the pipeline picked the star out from two or more catalog entries that were too close together to tell apart, it records that too, so the uncertainty is visible later rather than hidden.
4. Build the run gates. `record_quality.py` builds four of the run's pass/fail quality entries (gates) from the session summary. `source_detection` asks whether any star was detected. `astrometric_residual` asks whether the solution misses its stars by more than half a star's width. `catalog_matches` asks whether at least 20 stars were matched. `catalog_lookup` asks whether the catalog lookups worked. The [astrometry README](../README.md#quality-outputs) lists when each gate passes, fails or is not checked.

5. Count the target's clipped pixels. `target_clipping.py` (`target_clipping_measurements`) runs after a successful solve of a stack. It turns the target's catalog position into a pixel on the stack with this run's plate solution. It requires a source there: the brightest pixel of the disc must stand at least `MINIMUM_PEAK_SIGMA` (5) times the sky noise above the local sky. The commanded pointing in a frame header is never used. It then moves the pixel onto each raw frame with the inverse of Siril's transform, read from the stack's registration record (`<stack name>_registration_frames.json`, written by the stacking run). For each frame it stores the share of the pixels in a disc of `TARGET_REGION_RADIUS_ARCSEC` (30 arcsec) that reach the clip ceiling (`astrometry.frame_target_clipped_fraction`) or the camera's linearity limit (`astrometry.frame_target_nonlinear_fraction`). It reads at most `MAXIMUM_FRAMES_READ` (200) frames, for at most `MAXIMUM_READ_SECONDS` (120) seconds; the others get not-measured rows with a reason. A stack made before the registration record existed, or a run without a plate solution, gets not-measured rows, so an existing stack needs a restack to get these rows. A high share means the target itself is clipped and its brightness is wrong. The photometry and review readers use it. Partly measured; see [AST-19](../../../../documentation/library_design/validation/astrometry.md#ast-19).

6. Find the expected position residual. `expected_residual.py` (`expected_residual`) runs after the Gaia refit and gives the distance from a Gaia position that noise alone should leave, as parts. `record_expected_residual_quality` stores them beside `astrometry.gaia_fit_rms_arcsec`: `astrometry.expected_centroid_noise_rms_arcsec` (from each refit star's peak over the sky noise, using the Cramér-Rao bound `CRAMER_RAO_PEAK_FACTOR`, the square root of 2 over pi), `astrometry.expected_pixel_phase_rms_arcsec` (from the table `PIXEL_PHASE_RMS_PX_BY_FWHM_PX`, found by injecting stars, for star widths of 1.5 to 5 px), `astrometry.expected_gaia_epoch_error_rms_arcsec` and `astrometry.expected_residual_star_count`. All distances are RMS values in arcsec over the refit stars. The Gaia epoch part is always stored as "not measured", because the cached Gaia stars carry no error columns, so the sum of the other two parts is a lower bound. A one-shot-color frame, a stack with no refit, or a star width outside the table gives "not measured" too. A measured RMS several times the sum of the parts points to distortion, tilt or a bad match, not noise. The constants are partly measured; see [AST-21](../../../../documentation/library_design/validation/astrometry.md#ast-21).

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| Residual limit in `record_quality.py` | Half a star's width (partly measured; see [AST-04](../../../../documentation/library_design/validation/astrometry.md#ast-04)) | Sets when `astrometric_residual` fails |
| Disc radius, frame cap, read time and least peak in `target_clipping.py` | 30 arcsec, 200 frames, 120 s, 5σ (partly measured; see [AST-19](../../../../documentation/library_design/validation/astrometry.md#ast-19)) | Sets which pixels and frames the target clipping counts |
| `CRAMER_RAO_PEAK_FACTOR` and `PIXEL_PHASE_RMS_PX_BY_FWHM_PX` in `expected_residual.py` | The square root of 2 over pi; 0.092 to 0.008 px by star width (partly measured; see [AST-21](../../../../documentation/library_design/validation/astrometry.md#ast-21)) | Sets the expected centroid noise and pixel-phase parts |
| Matched-star limit in `record_quality.py` | 20 stars (partly measured; see [AST-05](../../../../documentation/library_design/validation/astrometry.md#ast-05)) | Sets when `catalog_matches` fails |

## Quality metrics

This stage produces one quality record per identified star, made up of the first three facts listed above. Together, they describe how much to trust that one star's identity. These per-star records also feed a session-level number: the pipeline takes the root mean square (RMS) of the match distances across every identified star. A session is the frames from one continuous run with the same setup.

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| Catalog that matched (SIMBAD or Gaia) | None | None | Which catalog named the star. The counts per catalog become part of the quality summary | People reviewing a star; the quality summary |
| Match distance | arcsec | Within the 10 arcsec match radius (design estimate; see [astrometry design estimates](../../../../documentation/library_design/validation/astrometry.md#design-estimates)) | The distance between the detected and catalog positions. A smaller value is a more confident match | People reviewing a star |
| Picked from a crowded group | Flag | Within 3 arcsec (partly measured; see [AST-10](../../../../documentation/library_design/validation/astrometry.md#ast-10)) | True when two or more catalog entries were too close to tell apart | People reviewing a star |
| `catalog_match_separation_rms_arcsec` | arcsec | Within the 10 arcsec match radius (design estimate; see [astrometry design estimates](../../../../documentation/library_design/validation/astrometry.md#design-estimates)) | The RMS of the match distances. A smaller value means the detected positions line up more closely with the star catalogs. It is a rough check: wrong matches raise it. It is not the plate-solve fit residual, which the plate solver reports separately and the `astrometric_residual` gate prefers (see [two residual numbers](../README.md#two-residual-numbers-the-plate-solve-fit-and-the-catalog-match-separation)) | The `astrometric_residual` gate, as a fallback; the quality summary |

## Files

- `assess_match_quality.py`: the per-star match record.
- `target_clipping.py`: `target_clipping_measurements`, the target's clipped and nonlinear pixel shares for each frame. `record_target_clipping_quality` in `record_quality.py` stores them.
- `expected_residual.py`: `expected_residual` and `record_expected_residual_quality`, the position residual that noise alone should leave.
- `record_quality.py`: the four run gates and their limits, and the step that stores the run's numbers and all five gates as quality measurements (`record_astrometry_quality`). The metrics it stores are declared in `../metrics.py`.

This record travels with the star from this point on. A scientist checking a specific star, or a report summarizing a whole session, can see exactly how confident the identification was, instead of trusting the match blindly or repeating the lookup.

For exact behavior, read the code.
