# Astrometry pre-processing

This stage works directly on image pixels. It does not know about star catalogs, sky coordinates or star identities. It only finds bright points and measures how sharp they look. This keeps the pixel-level work separate from the catalog-lookup work that happens later, in processing.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What this stage does, in order

1. Detect star-like points. `source_detection.py` scans the image for points of light that stand out from the background, using photutils' DAOStarFinder. It first subtracts a map of the sky background that varies across the frame, so the glow of a nebula is not taken for stars (partly measured; see [AST-16](../../../../documentation/library_design/validation/astrometry.md#ast-16)). Each detection becomes a candidate star (a source), with a position and a brightness.
2. Remove duplicates. The pipeline merges detections that sit right on top of each other into one, so it does not count a single bright star twice.
3. Measure sharpness (when needed). A separate tool (`fwhm.py`) fits a bell-shaped curve (a Gaussian) to each of the brightest unsaturated stars and reports the median full width at half maximum (FWHM) in pixels. Unsaturated stars are stars whose brightest pixels stay below the highest value the camera can record. A narrower fit means a sharper image. The tool skips stars whose cores are clipped, because they fit wider than their neighbors. A caller can also pass a mask of pixels to avoid (`excluded_mask`), and the tool skips a star with a masked pixel near its center. The stacker uses this for stacks (images made by aligning and averaging many frames) combined from exposure groups (frames sharing one exposure length), where the replaced cores leave no clipped plateau to detect. The measurement agrees with an independent Gaussian fit. Siril (an external image-processing program) uses its own PSF fit (point spread function, the shape a point of light takes in the image), which reads wider by a roughly fixed factor (partly measured; see [AST-07](../../../../documentation/library_design/validation/astrometry.md#ast-07)). Do not compare FWHM values from different estimators.
4. Size the detection kernel. The detector looks for stars by sliding a star-shaped (Gaussian) template, the detection kernel, over the image. The kernel's width should match the stars. The astrometry stage sizes this kernel from a different number, the blob width (`measure_blob_width_from_data`). The blob width is the spread (second moment, a standard-deviation-like width) of all the light in a 30-pixel box around each of the brightest sources, and it reads about twice the FWHM. Sizing the kernel from the blob width gives more catalog matches and a smaller residual than sizing it from the FWHM (partly measured; see [AST-06](../../../../documentation/library_design/validation/astrometry.md#ast-06)). The residual is the leftover distance between solved and catalog star positions. The blob width is not a star width and is never reported as one.

## Settings

The detection defaults are set in `source_detection.py`: a 5σ threshold, sharpness and roundness ranges, and a 15 pixel merge distance (design estimate; see [astrometry design estimates](../../../../documentation/library_design/validation/astrometry.md#design-estimates)).

## Quality metrics

| Field | Unit | Limit (status) | High or low means | Read by |
| --- | --- | --- | --- | --- |
| Median FWHM from `fwhm.py` | px | None | Star width. A smaller value means the stars are more tightly focused. It says nothing about star identity or catalog matching, only about focus | Stacking, which grades how sharp a combined image turned out compared to its individual frames; the shared frame-quality tools; the astrometry quality summary for a session |

This measurement is shared. Astrometry is not the only pipeline that uses it.

## Files

- `source_detection.py`: star detection and duplicate removal.
- `fwhm.py`: the sharpness measurement and the blob width.

Photometry, asteroid detection and spectroscopy also need to find stars in an image, for their own reasons, without matching those stars to a catalog. Because detection is useful on its own, it is a reusable step that the other pipelines import from here. It lives in this folder, not in `pipelines/shared/`, although the [shared README](../../shared/README.md) says code that more than one pipeline uses belongs there.

For exact behavior, read the code.
