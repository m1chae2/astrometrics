# Processing

Processing takes the star-like points that pre-processing found and turns them into named, identified stars with known sky positions. This is where the pipeline moves from "points in an image" to "specific stars we can look up."

## What happens here

1. **Solve the field.** Using the brightest detected points, the pipeline works out exactly where in the sky the image is pointing and how it is oriented. This is called plate solving. If the pipeline detects too few points, it skips plate solving for this image. The pipeline gives the solver a pixel scale range computed from the header's pixel size (times the binning factor), the focal length and a 5 percent margin. When the solver reports how well its fit matched its reference stars, the pipeline keeps the fit residual (arcseconds) and the matched-star count. The quality gate reads them.
2. **Look up each star.** Once the pipeline knows the image's sky position, it can translate every detected point into a real sky coordinate. It then checks each one against star catalogs in a fixed order:
   - First, it checks SIMBAD for a known, named star at that position.
   - If SIMBAD has no match, it checks the Gaia catalog instead. The Gaia search covers stars brighter than a magnitude limit set by the frame's depth, and the pipeline moves each Gaia position from epoch 2016.0 to the image's `DATE-OBS` using the star's proper motion before it matches.
   - If neither catalog has a match, it gives the star a name based on its coordinates, so the star still has a stable, permanent identity.
3. **Name the centre star without a solution.** A spectral stack cannot be plate solved, so the pipeline names only the star at the centre of the frame, using the position the mount reported. The mount can be minutes of arc off, so the nearest catalog entry is not always the right star. The pipeline picks the entry in this order:
   - If the caller gives the target's name and SIMBAD knows it, the pipeline uses that entry, as long as it lies within 5 arcminutes of the mount's position.
   - If SIMBAD does not know the name, the pipeline uses the brightest entry within 5 arcminutes. The star at the centre of a spectral frame is the target, and targets are bright.
   - If the caller gives no name, or the lookup fails, the pipeline uses the nearest entry.
   - When the target's name or its brightness picks the entry, the pipeline gives the name to the brightest detection within 10 percent of the frame's shorter side from the centre. That detection must reach 10 percent of the frame's brightest pixel. If none does (the star was not detected), the pipeline names nothing, rather than naming a noise blob.
   - If the target is a planet, the Moon or the Sun, the pipeline gives the centre star no catalog name. These bodies have no catalog entry, and the nearest star would be a chance background star.
4. **Handle crowded stars.** When two catalog entries sit close enough together that the image cannot tell them apart, the pipeline picks the brighter of the two rather than choosing at random.
5. **Record the match separation and the limits.** Each catalog match stores its distance from the detected star. The RMS of those distances is the catalog match separation, a rough check that is separate from the solver's fit residual. The pipeline also records flags when the Gaia search hit its row limit, when proper motions or the observation date were missing, or when `XBINNING` and `YBINNING` differ. See the pipeline README for what each flag means.
6. **Limit how many stars are identified.** Catalog lookups take time, so a caller can limit the pipeline to identifying only the brightest handful of stars rather than every single one detected.

## Why this order

Plate solving has to happen before catalog lookup, because a catalog can only be searched once the pipeline knows which part of the sky it is looking at. Within catalog lookup, SIMBAD is checked before Gaia because SIMBAD carries a star's common name, which is more useful to a person reading the results than a catalog ID alone.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
