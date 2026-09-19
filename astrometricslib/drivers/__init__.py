"""Tools that talk directly to hardware, files, and other programs.

This includes the Siril and Logger interfaces, the target/stellar
catalog database (`catalog_access.py`, `local_database.py`,
`catalog_store.py`), and the basic tools every driver uses to read and
write FITS image files (`fits_access.py`, `image.py`,
`filter_detection.py`).

Pure image-quality measurement (FWHM, saturation, background level,
per-target frame statistics) doesn't belong here: it doesn't manage an
external resource (a subprocess, a database connection) the way these
tools do, so it lives in `pipelines/shared/quality/` instead, alongside
the Siril-output text parsers in `siril_output_parsing.py` that are
specific to Siril's own file formats rather than a generic quality
measurement.
"""

from astrometricslib.drivers.logger_interface import LoggerInterface
from astrometricslib.drivers.siril_interface import ImageProcessing

__all__ = [
    "ImageProcessing",
    "LoggerInterface",
]
