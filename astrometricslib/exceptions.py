"""Purpose: The errors a script catches, and the error helpers.

Description: The errors a script catches when a call fails, and the helpers
that turn an error into a plain record (`ErrorInfo`) that can travel as JSON,
and back again. Every error says how it is shown (``report``: ``"status"``,
``"error"`` or ``"notify"``) and the level it is logged at (``log_level``).
`wrap_outside_error` puts an error from Python or another package into a
category. `MovedError` is what reading a renamed method raises: its message
says where the method moved to. `FITS_READ_ERRORS` is the tuple of errors
that reading a damaged or missing FITS file can raise. Each name is imported
from the module where its code lives today, so moving the code later changes
this file only.
"""

from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.errors import (
    AstrometricsError,
    ConfigurationError,
    ConflictError,
    ErrorInfo,
    ExternalServiceError,
    HardwareError,
    InvalidArgumentError,
    MovedError,
    NotFoundError,
    ParameterUnavailableError,
    PermissionDeniedError,
    ProcessingError,
    StorageError,
    error_from_info,
    to_error_info,
    wrap_outside_error,
)
from astrometricslib.foundation.offline import OfflineModeError
from astrometricslib.foundation.storage.mount import StorageNotMountedError
from astrometricslib.foundation.storage.process_locks import DeviceInUseError
from astrometricslib.utilities.exceptions import PlateSolveFailedError

__all__ = [
    "FITS_READ_ERRORS",
    "AstrometricsError",
    "ConfigurationError",
    "ConflictError",
    "DeviceInUseError",
    "ErrorInfo",
    "ExternalServiceError",
    "HardwareError",
    "InvalidArgumentError",
    "MovedError",
    "NotFoundError",
    "OfflineModeError",
    "ParameterUnavailableError",
    "PermissionDeniedError",
    "PlateSolveFailedError",
    "ProcessingError",
    "StorageError",
    "StorageNotMountedError",
    "error_from_info",
    "to_error_info",
    "wrap_outside_error",
]
