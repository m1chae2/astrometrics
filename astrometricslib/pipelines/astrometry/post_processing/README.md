# Astrometry post-processing

This stage does no new matching or lookup work. It takes the outcome of a star's catalog match, already computed during processing, and records it as a clear, structured quality record. Without this stage, that information would be used once and then discarded.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears. SIMBAD is an online database of named astronomical objects, and Gaia is the European Space Agency's catalog of precise positions for over a billion stars.

## What this stage does, in order

1. Record how the star was matched. The pipeline marks every identified star with which catalog matched it, SIMBAD or Gaia.
2. Record how far off the match was. The pipeline stores the distance, in arcsec (1/3600°), between the detected position and the catalog's own position, with the star. A smaller distance means a more confident match.
3. Flag crowded matches. If the pipeline picked the star out from two or more catalog entries that were too close together to tell apart, it records that too, so the uncertainty is visible later rather than hidden.
4. Build the run gates. `run_gates.py` builds four of the run's pass/fail quality entries (gates) from the session summary. `source_detection` asks whether any star was detected. `astrometric_residual` asks whether the solution misses its stars by more than half a star's width. `catalog_matches` asks whether at least 20 stars were matched. `catalog_lookup` asks whether the catalog lookups worked. The [astrometry README](../README.md#quality-outputs) lists when each gate passes, fails or is not checked.

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| Residual limit in `run_gates.py` | Half a star's width (partly measured; see [AST-04](../../../../documentation/library_design/validation/astrometry.md#ast-04)) | Sets when `astrometric_residual` fails |
| Matched-star limit in `run_gates.py` | 20 stars (partly measured; see [AST-05](../../../../documentation/library_design/validation/astrometry.md#ast-05)) | Sets when `catalog_matches` fails |

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
- `run_gates.py`: the four run gates and their limits.

This record travels with the star from this point on. A scientist checking a specific star, or a report summarizing a whole session, can see exactly how confident the identification was, instead of trusting the match blindly or repeating the lookup.

For exact behavior, read the code.
