"""Pieces more than one pipeline needs, so no pipeline owns them alone.

Grouping frames into observing sessions, saving a found star to the
shared catalog, and the per-image working state every pipeline builds
up as it runs -- none of these belong to just one pipeline, so they
live here instead of inside any single pipeline's own folder.

Also holds the plain, non-analysis catalog work the public API calls
directly: scanning a folder for FITS files (`frame_scanning.py`), the
target catalog's create/read/update/delete (`target_records.py`), and
turning a FITS file into a PNG for display (`image_conversions.py`,
`image_scaling.py`). None of this runs an actual pipeline (stacking,
astrometry, photometry, spectroscopy, asteroid recovery).

`quality/` holds image-quality measurement (FWHM inputs, saturation,
background level, per-target frame statistics) for the same reason:
stacking, spectroscopy, and the API layer all need it, and it doesn't
own an external resource the way a driver does -- it's pure
measurement on already-loaded pixel data.
"""
