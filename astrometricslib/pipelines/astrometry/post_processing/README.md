# Post-Processing

Post-processing does not do any new matching or lookup work. It takes the outcome of a star's catalog match, already computed during processing, and records it as a clear, structured quality record. Without this step, that information would only ever be used once and then discarded.

Terms are defined in the [glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary). SIMBAD is an online database of named astronomical objects, and Gaia is the European Space Agency's catalogue of precise positions for over a billion stars.

## What happens here

1. **Record how the star was matched.** The pipeline marks every identified star with which catalog matched it — SIMBAD or Gaia.
2. **Record how far off the match was.** The pipeline stores the distance, in arcseconds (1/3600 of a degree), between the detected position and the catalog's own position, with the star. A smaller distance means a more confident match.
3. **Flag crowded matches.** If the pipeline picked the star out from two or more catalog entries that were too close together to tell apart, it records that too, so the uncertainty is visible later rather than hidden.

## The quality metrics this stage produces

Post-processing produces one quality record per identified star, made up of the three facts listed above: which catalog matched it, how far off the match was, and whether it was picked out of a crowded group. Together, these describe how much to trust that one star's identity.

These per-star records also feed a session-level number: the pipeline calculates the catalog match separation by taking the root mean square (RMS) of the match distances across every identified star. A smaller value means the detected positions line up more closely with the star catalogs. This number is a rough check. It counts only stars within the 10 arcsecond match radius, and wrong matches raise it. It is not the plate-solve fit residual, which the plate solver (the step that works out where in the sky the image points) reports separately and the `astrometric_residual` gate prefers (see "The gate record" in the pipeline README). The catalog match separation, along with how many stars were matched through each catalog, becomes part of the astrometry pipeline's quality summary for that session.

## The run gates

`run_gates.py` builds four of the run's pass/fail quality checks (gates) from the session summary: `source_detection` (was any star detected), `astrometric_residual` (does the solution miss its stars by more than half a star's width), `catalog_matches` (were at least 20 stars matched) and `catalog_lookup` (did the catalogue lookups work). The residual and matched-star limits are partly measured (see [AST-04](../../../../documentation/library_design/validation/astrometry.md#ast-04) and [AST-05](../../../../documentation/library_design/validation/astrometry.md#ast-05)). The pipeline README's "The gate record" lists when each passes, fails or is not checked.

## Why this step exists

This record travels with the star from this point on. Anyone reviewing the results later — a scientist checking a specific star, or a report summarizing a whole observing session — can see exactly how confident the identification was, instead of having to trust the match blindly or repeat the lookup.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
