"""Provides ``PyIndi``, falling back to a stand-in when it is not installed.

``PyIndi`` is a SWIG binding to the INDI client C++ library, installed
separately from this project's Python dependencies (see
``indi_interface.py``'s header comment for install instructions).
Documentation builds, CI test runs, and developer machines without INDI
installed must still be able to import every module under
``wayfindinglib.drivers.indi``.
"""


class PyIndiStub:
    """Stand-in for the ``PyIndi`` module when it is not installed."""

    class BaseClient:
        """Stand-in for ``PyIndi.BaseClient`` when PyIndi is absent."""

        def __init__(self):  # ruff: ignore[missing-return-type-special-method]
            """Initialize the stub client with no server configured."""
            pass

        def setServer(self, host, port):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
            """Record the target server host and port (no-op stub)."""
            pass

        def connectServer(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
            """Report that the stub server connection always fails.

            Returns
            -------
            connected : `bool`
                Always `False`.
            """
            return False

        def isServerConnected(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
            """Report that the stub server is never connected.

            Returns
            -------
            connected : `bool`
                Always `False`.
            """
            return False

        def getHost(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
            """Return the placeholder server host name.

            Returns
            -------
            host : `str`
                Always ``"localhost"``.
            """
            return "localhost"

        def getPort(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
            """Return the placeholder server port number.

            Returns
            -------
            port : `int`
                Always ``7624``.
            """
            return 7624

        def getDevices(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
            """Return an empty device list, since PyIndi is absent.

            Returns
            -------
            devices : `list`
                Always an empty list.
            """
            return []

        def getDevice(self, name):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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


try:
    import PyIndi  # type: ignore[import-untyped, missing-import]
except ImportError:
    PyIndi = PyIndiStub()
