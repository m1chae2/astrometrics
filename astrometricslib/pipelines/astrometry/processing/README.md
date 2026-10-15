# Processing

Processing takes the star-like points that pre-processing found and turns them into named, identified stars with known sky positions. This is where the pipeline moves from "points in an image" to "specific stars we can look up."

Terms are defined in the [glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary). The terms used most here:

- **Plate solving**: working out where in the sky an image points, and how it is turned and scaled, by matching its pattern of stars to a reference star catalogue.
- **SIMBAD**: an online database of named astronomical objects. **Gaia**: the European Space Agency's star-mapping satellite, whose catalogue lists precise positions for over a billion stars.
- **Magnitude**: the astronomer's brightness scale, where larger numbers are fainter.
- **Proper motion**: a star's slow drift across the sky, per year. **Epoch**: the date a catalogue position refers to.

## What happens here

1. **Solve the field.** Using the brightest detected points, the pipeline works out exactly where in the sky the image is pointing and how it is oriented. This is called plate solving. If the pipeline detects fewer than 4 points, it skips plate solving for this image. The local solver (the copy of the solver installed on this computer) is tried only with at least 10 points; with fewer, the pipeline goes straight to the online solver (partly measured; see [AST-13](../../../../documentation/library_design/validation/astrometry.md#ast-13)). The pipeline gives the solver a pixel scale range (how much sky one pixel covers) computed from the header's pixel size and the focal length, from 5 percent below to 5 percent above that value. The pixel size is `XPIXSZ`, which already includes binning (combining neighbouring pixels into one); only when `XPIXSZ` is missing does the pipeline use `PIXSIZE1` (the camera's own unbinned pixel size) times `XBINNING`. When the solver reports how well its fit matched its reference stars, the pipeline keeps the fit residual (the typical leftover distance between fitted and reference star positions, in arcseconds, 1/3600 of a degree) and the matched-star count. The quality gate reads them.
2. **Look up each star.** Once the pipeline knows the image's sky position, it can translate every detected point into a real sky coordinate. It then checks each one against star catalogs in a fixed order:
   - First, it checks SIMBAD for a known, named star at that position.
   - If SIMBAD has no match, it checks the Gaia catalog instead. The Gaia search covers stars brighter than a magnitude limit set by the frame's depth, and the pipeline moves each Gaia position from epoch 2016.0 to the image's `DATE-OBS` (the observation date in the FITS image header) using the star's proper motion before it matches. Cached Gaia rows are reused only when the local cache has recorded a region that covers the whole search at least as deep and without hitting the row limit (see the drivers README).
   - If neither catalog has a match, it gives the star a name based on its coordinates, so the star still has a stable, permanent identity.
3. **Name the centre star without a solution.** A spectral stack (a stacked image taken through a grating, which spreads each star into a streak of colours) cannot be plate solved, so the pipeline names only the star at the centre of the frame, using the position the mount reported. The mount can be minutes of arc (sixtieths of a degree) off, so the nearest catalog entry is not always the right star. The pipeline picks the entry in this order:
   - If the caller gives the target's name and SIMBAD knows it, the pipeline uses that entry, as long as it lies within 5 arcminutes of the mount's position.
   - If SIMBAD does not know the name, the pipeline uses the brightest entry within 5 arcminutes. The star at the centre of a spectral frame is the target, and targets are bright.
   - If the caller gives no name, or the lookup fails, the pipeline uses the nearest entry.
   - When the target's name or its brightness picks the entry, the pipeline gives the name to the brightest detection within 10 percent of the frame's shorter side from the centre. That detection must reach 10 percent of the frame's brightest pixel. If none does (the star was not detected), the pipeline names nothing, rather than naming a noise blob.
   - The 5 arcminute radius and the two 10 percent limits sit between the cases seen on real spectral stacks (partly measured; see [AST-09](../../../../documentation/library_design/validation/astrometry.md#ast-09)).
   - If the target is a planet, the Moon or the Sun, the pipeline gives the centre star no catalog name. These bodies have no catalog entry, and the nearest star would be a chance background star.
4. **Handle crowded stars.** When two catalog entries sit close enough together that the image cannot tell them apart (within 3 arcseconds), the pipeline picks the brighter of the two rather than choosing at random (partly measured; see [AST-10](../../../../documentation/library_design/validation/astrometry.md#ast-10)).
5. **Record the match separation and the limits.** Each catalog match stores its distance from the detected star. The RMS (root mean square: square each distance, average, take the root) of those distances is the catalog match separation, a rough check that is separate from the solver's fit residual. The pipeline also records flags when the Gaia search hit its row limit, when proper motions or the observation date were missing, or when `XBINNING` and `YBINNING` differ. See the pipeline README for what each flag means.
6. **Limit how many stars are identified.** Catalog lookups take time, so a caller can limit the pipeline to identifying only the brightest handful of stars rather than every single one detected.

## Why this order

Plate solving has to happen before catalog lookup, because a catalog can only be searched once the pipeline knows which part of the sky it is looking at. Within catalog lookup, SIMBAD is checked before Gaia because SIMBAD carries a star's common name, which is more useful to a person reading the results than a catalog ID alone.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
