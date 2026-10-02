# Pre-Processing

Pre-processing works directly on image pixels. It does not know about star catalogs, sky coordinates, or star identities — it only finds bright points and measures how sharp they look. This keeps the pixel-level work separate from the catalog-lookup work that happens later, in processing.

## What happens here

1. **Detect star-like points.** The image is scanned for points of light that stand out from the background. Each one becomes a candidate star, with a position and a brightness.
2. **Remove duplicates.** The pipeline merges detections that sit right on top of each other into one, so it does not count a single bright star twice.
3. **Measure sharpness (when needed).** A separate tool measures how wide a star's light spreads across the pixels around it. A tighter, narrower spread means a sharper image.

## The quality metric this stage produces

Pre-processing produces one image-quality number: the sharpness measurement described above, expressed in pixels. A smaller value means the stars in the image are more tightly focused. This number does not evaluate anything about star identity or catalog matching — it is a statement about image focus alone.

Astrometry is not the only pipeline that uses this measurement. It is a shared quality metric: stacking uses it to grade how sharp a combined image turned out compared to its individual frames, and the shared frame-quality tools use it to describe a single frame's sharpness. Astrometry itself also carries this measurement forward into its own quality summary for an observing session.

## Why this step exists on its own

Several other pipelines in this library — photometry, asteroid detection, and spectroscopy — also need to find stars in an image, for their own reasons, without ever matching those stars to a catalog. Because detection is useful on its own, it lives here as a shared, reusable step rather than being built only for astrometry's needs.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
