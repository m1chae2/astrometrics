"""Package initialization for Astrometrics drivers.

Defines and exposes low-level drivers, including the Siril and Logger
interfaces, the target/stellar catalog database
(`catalog_access.py`, `local_database.py`, `catalog_store.py`), and the
basic FITS-file I/O primitives every driver reads pixels through
(`fits_access.py`, `image.py`, `filter_detection.py`).

Pure image-quality measurement (FWHM, saturation, background level,
per-target frame statistics) is not a driver concern -- it doesn't own
an external resource -- and lives in `pipelines/shared/quality/`
instead, alongside the Siril-output text parsers in
`siril_output_parsing.py` that are specific to Siril's own file
formats rather than a generic quality measurement.
"""

from astrometricslib.drivers.logger_interface import LoggerInterface
from astrometricslib.drivers.siril_interface import ImageProcessing

__all__ = [
    "ImageProcessing",
    "LoggerInterface",
]
