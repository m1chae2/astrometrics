# Pre-Processing

Pre-processing works directly on image pixels. It does not know about star catalogs, sky coordinates, or star identities — it only finds bright points and measures how sharp they look. This keeps the pixel-level work separate from the catalog-lookup work that happens later, in processing.

## What happens here

1. **Detect star-like points.** The image is scanned for points of light that stand out from the background. Each one becomes a candidate star, with a position and a brightness.
2. **Remove duplicates.** The pipeline merges detections that sit right on top of each other into one, so it does not count a single bright star twice.
3. **Measure sharpness (when needed).** A separate tool (`fwhm.py`) fits a bell-shaped curve (a Gaussian) to each of the brightest unsaturated stars and reports the median full width at half maximum (FWHM) in pixels. A tighter, narrower fit means a sharper image. It skips stars whose cores are clipped, because they fit wider than their neighbours. On the 2026-10-02 Bubble Nebula stack it reads 2.5 px, which agrees with an independent fit (2.4 px). Siril's own PSF fit reads about 20% wider (2.8 px). Do not compare FWHM values from different estimators.
4. **Size the detection kernel.** The astrometry stage sizes its star-detection filter from a different number, the blob width (`measure_blob_width_from_data`): the spread of all the light in a 30-pixel box. It reads about twice the FWHM (4.9 px on the same stack). Astrometry was validated with it, and sizing the filter from the FWHM instead cut the catalogue matches on that stack from 335 to 181 and raised the residual from 1.04 to 5.0 arcseconds. The blob width is not a star width and is never reported as one.

## The quality metric this stage produces

Pre-processing produces one image-quality number: the sharpness measurement described above, expressed in pixels. A smaller value means the stars in the image are more tightly focused. This number does not evaluate anything about star identity or catalog matching — it is a statement about image focus alone.

Astrometry is not the only pipeline that uses this measurement. It is a shared quality metric: stacking uses it to grade how sharp a combined image turned out compared to its individual frames, and the shared frame-quality tools use it to describe a single frame's sharpness. Astrometry itself also carries this measurement forward into its own quality summary for an observing session.

## Why this step exists on its own

Several other pipelines in this library — photometry, asteroid detection, and spectroscopy — also need to find stars in an image, for their own reasons, without ever matching those stars to a catalog. Because detection is useful on its own, it lives here as a shared, reusable step rather than being built only for astrometry's needs.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
