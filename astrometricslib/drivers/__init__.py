"""Package initialization for Astrometrics drivers.

Defines and exposes low-level drivers, including the Siril and Logger
interfaces, plus everything that reads or writes data on the computer
itself: raw image pixels and FITS files (`fits_access.py`, `image.py`,
`quality_metrics.py`, `saturation.py`, `filter_detection.py`), and the
target/stellar catalog database and file-backed frame statistics
(`catalog_access.py`, `frame_statistics.py`, `background_measurement.py`).
"""

from astrometricslib.drivers.logger_interface import LoggerInterface
from astrometricslib.drivers.siril_interface import ImageProcessing

__all__ = [
    "ImageProcessing",
    "LoggerInterface",
]
