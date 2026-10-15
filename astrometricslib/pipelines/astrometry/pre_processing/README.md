# Pre-Processing

Pre-processing works directly on image pixels. It does not know about star catalogs, sky coordinates, or star identities — it only finds bright points and measures how sharp they look. This keeps the pixel-level work separate from the catalog-lookup work that happens later, in processing.

Terms are defined in the [glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary).

## What happens here

1. **Detect star-like points.** `source_detection.py` scans the image for points of light that stand out from the background, using photutils' DAOStarFinder. Each one becomes a candidate star (a source), with a position and a brightness.
2. **Remove duplicates.** The pipeline merges detections that sit right on top of each other into one, so it does not count a single bright star twice.
3. **Measure sharpness (when needed).** A separate tool (`fwhm.py`) fits a bell-shaped curve (a Gaussian) to each of the brightest unsaturated stars (stars whose brightest pixels stay below the highest value the camera can record) and reports the median full width at half maximum (FWHM) in pixels. A tighter, narrower fit means a sharper image. It skips stars whose cores are clipped, because they fit wider than their neighbours. A caller can also pass a mask of pixels to avoid (`excluded_mask`); a star with a masked pixel near its centre is skipped. The stacker uses this for stacks (images made by aligning and averaging many frames) combined from exposure groups (frames sharing one exposure length), where the replaced cores leave no clipped plateau to detect. On the 2026-10-02 Bubble Nebula stack it reads 2.5 px, which agrees with an independent fit (2.4 px). Siril (an external image-processing program) uses its own PSF fit (point spread function, the shape a point of light takes in the image), which reads about 20% wider (2.8 px). Do not compare FWHM values from different estimators.
4. **Size the detection kernel.** The detector looks for stars by sliding a star-shaped (Gaussian) template, the detection kernel, over the image; the kernel's width should match the stars. The astrometry stage sizes this kernel from a different number, the blob width (`measure_blob_width_from_data`): the spread (second moment, a standard-deviation-like width) of all the light in a 30-pixel box around each of the brightest sources. It reads about twice the FWHM (4.9 px on the same stack). Astrometry was validated with it, and sizing the filter from the FWHM instead cut the catalogue matches on that stack from 335 to 181 and raised the residual (the leftover distance between solved and catalogue star positions) from 1.04 to 5.0 arcseconds (1 arcsecond is 1/3600 degree). The blob width is not a star width and is never reported as one.

## The quality metric this stage produces

Pre-processing produces one image-quality number: the sharpness measurement described above, expressed in pixels. A smaller value means the stars in the image are more tightly focused. This number does not evaluate anything about star identity or catalog matching — it is a statement about image focus alone.

Astrometry is not the only pipeline that uses this measurement. It is a shared quality metric: stacking uses it to grade how sharp a combined image turned out compared to its individual frames, and the shared frame-quality tools use it to describe a single frame's sharpness. Astrometry itself also carries this measurement forward into its own quality summary for an observing session.

## Why this step exists on its own

Several other pipelines in this library — photometry, asteroid detection, and spectroscopy — also need to find stars in an image, for their own reasons, without ever matching those stars to a catalog. Because detection is useful on its own, it is written as a reusable step that the other pipelines import from here, rather than being built only for astrometry's needs. It lives in this folder, not in `pipelines/shared/`, although `shared/README.md` says code that more than one pipeline uses belongs there.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
