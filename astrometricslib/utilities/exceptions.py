"""Domain-level exception classes for astrolib logic.

This module has no dependencies on the outer backend layers (services,
routers). REQ: AGENT-4.1
"""

from datastore.exceptions import DeviceInUseError

__all__ = ["AstroLibError", "DeviceInUseError"]


class AstroLibError(Exception):
    """Base class for all astrolib domain exceptions."""

    pass
