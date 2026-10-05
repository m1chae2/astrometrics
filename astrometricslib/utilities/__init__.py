"""Small, shared helper code used throughout astrometricslib.

Application settings, calibration record-keeping, and other small
utility functions used by the rest of the library. Internal -- import
public symbols from the top-level `astrometricslib` package instead.
"""

from astrometricslib.foundation.enums import FilterType

from .exceptions import PlateSolveFailedError
from .spectroscopy_models import CameraConfig, ConfigLoader, SpectroscopyConfig
