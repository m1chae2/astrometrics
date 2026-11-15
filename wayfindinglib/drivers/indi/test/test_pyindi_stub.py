"""Purpose: Unit tests for the stand-in used when PyIndi is not installed.

Description: Windows has no PyIndi, so the driver runs on `PyIndiStub`. The
driver code calls the same methods on it as on the real client, so the stub
must offer each of them. A missing one shows up as an ``AttributeError`` in the
backend log, as it did for ``disconnectServer`` when a stale client was
replaced.
"""

from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndiStub


def test_stub_client_can_be_closed_without_a_server() -> None:
    """Closing the stub client does nothing and does not raise."""
    client = PyIndiStub.BaseClient()
    assert client.disconnectServer()
    assert not client.isServerConnected()
