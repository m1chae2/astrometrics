"""Define domain-level typed exceptions for Astrometrics backend services.

All services import from this module. No service defines its own
exception classes. REQ: AGENT-4.1
"""


class AstrometricsServiceError(Exception):
    """Base class for all backend service errors."""

    pass


class InvalidArgumentError(AstrometricsServiceError):
    """Raised when a service method receives a missing or invalid argument."""

    pass


class TargetNotFoundError(AstrometricsServiceError):
    """Raised when a named target does not exist in the target library."""

    pass


class FilterNotFoundError(AstrometricsServiceError):
    """Raised when a filter name is not in the filter wheel configuration."""

    pass


class HardwareCommandError(AstrometricsServiceError):
    """Raised when an INDI command to hardware returns a failure state."""

    pass


class ToolNotFoundError(AstrometricsServiceError):
    """Raised when an LLM emits a tool name not present in the registry."""

    pass
