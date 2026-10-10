"""Provides ``PyIndi``, falling back to a stand-in when it is not installed.

``PyIndi`` is a SWIG binding to the INDI client C++ library, installed
separately from this project's Python dependencies (see
``indi_interface.py``'s header comment for install instructions).
Documentation builds, CI test runs, and developer machines without INDI
installed must still be able to import every module under
``wayfindinglib.drivers.indi``.

It also names ``INDI_ERRORS``, the errors a call into ``PyIndi`` can raise
when a device reports something unexpected.
"""

from typing import Any


class PyIndiStub:
    """Stand-in for the ``PyIndi`` module when it is not installed."""

    class BaseClient:
        """Stand-in for ``PyIndi.BaseClient`` when PyIndi is absent."""

        def __init__(self) -> None:
            """Initialize the stub client with no server configured."""
            pass

        def setServer(self, host: str, port: int) -> None:
            """Record the target server host and port (no-op stub)."""
            pass

        def connectServer(self) -> bool:
            """Report that the stub server connection always fails.

            Returns
            -------
            connected : `bool`
                Always `False`.
            """
            return False

        def isServerConnected(self) -> bool:
            """Report that the stub server is never connected.

            Returns
            -------
            connected : `bool`
                Always `False`.
            """
            return False

        def getHost(self) -> str:
            """Return the placeholder server host name.

            Returns
            -------
            host : `str`
                Always ``"localhost"``.
            """
            return "localhost"

        def getPort(self) -> int:
            """Return the placeholder server port number.

            Returns
            -------
            port : `int`
                Always ``7624``.
            """
            return 7624

        def getDevices(self) -> list[Any]:
            """Return an empty device list, since PyIndi is absent.

            Returns
            -------
            devices : `list`
                Always an empty list.
            """
            return []

        def getDevice(self, name: str) -> None:
            """Return `None`, since PyIndi is absent.

            Returns
            -------
            device : `None`
                Always `None`.
            """
            return None

    class BaseDevice:
        """Stand-in for ``PyIndi.BaseDevice`` when PyIndi is absent."""

        pass

    ISS_ON = 1
    ISS_OFF = 0

    IPS_IDLE = 0
    IPS_OK = 1
    IPS_BUSY = 2
    IPS_ALERT = 3


try:
    import PyIndi  # type: ignore[import-untyped, missing-import]
except ImportError:
    PyIndi = PyIndiStub()

#: The errors a call into ``PyIndi`` can raise when a device reports
#: something odd. SWIG, the tool that builds the binding, turns a C++ error
#: into ``RuntimeError`` and a wrong argument type into ``TypeError``. A
#: property that is missing or shorter than expected gives ``TypeError``
#: (from ``None``) or ``IndexError``, and a text value that is not a number
#: gives ``ValueError``. Code that talks to a device catches this tuple
#: instead of every exception, so a real bug still shows up.
INDI_ERRORS: tuple[type[Exception], ...] = (RuntimeError, TypeError, ValueError, IndexError)
