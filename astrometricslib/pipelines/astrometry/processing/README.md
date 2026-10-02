# Processing

Processing takes the star-like points that pre-processing found and turns them into named, identified stars with known sky positions. This is where the pipeline moves from "points in an image" to "specific stars we can look up."

## What happens here

1. **Solve the field.** Using the brightest detected points, the pipeline works out exactly where in the sky the image is pointing and how it is oriented. This is called plate solving. If the pipeline detects too few points, it skips plate solving for this image.
2. **Look up each star.** Once the pipeline knows the image's sky position, it can translate every detected point into a real sky coordinate. It then checks each one against star catalogs in a fixed order:
   - First, it checks SIMBAD for a known, named star at that position.
   - If SIMBAD has no match, it checks the Gaia catalog instead.
   - If neither catalog has a match, it gives the star a name based on its coordinates, so the star still has a stable, permanent identity.
3. **Handle crowded stars.** When two catalog entries sit close enough together that the image cannot tell them apart, the pipeline picks the brighter of the two rather than choosing at random.
4. **Limit how many stars are identified.** Catalog lookups take time, so a caller can limit the pipeline to identifying only the brightest handful of stars rather than every single one detected.

## Why this order

Plate solving has to happen before catalog lookup, because a catalog can only be searched once the pipeline knows which part of the sky it is looking at. Within catalog lookup, SIMBAD is checked before Gaia because SIMBAD carries a star's common name, which is more useful to a person reading the results than a catalog ID alone.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
