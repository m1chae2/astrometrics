"""Services the public API calls directly for plain reads and writes.

Everything here works with targets, frames, and images without running
any analysis pipeline: scanning a folder for FITS files
(`frame_scanning.py`), the target catalog's create/read/update/delete
(`target_records.py`), and turning a FITS file into a PNG for display
(`image_conversions.py`). Code that runs an actual pipeline (stacking,
astrometry, photometry, spectroscopy, asteroid recovery) does not
belong here.

The public API calls straight into this package for exactly this kind
of plain, non-analysis work -- it does not need to invent a fake
pipeline run just to list targets or read a FITS header. Where a
function here does need the database, it reaches for it through
`data_access.catalog_access.CatalogAccess`, the same repository object
every other layer uses, rather than opening a file or a connection
itself.
"""
