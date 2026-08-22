"""Exceptions raised by the shared datastore package."""


class DeviceInUseError(Exception):
    """Raised when a locked resource is already held by another process."""
