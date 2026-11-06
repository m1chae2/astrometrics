"""Purpose: Unit test that the INDI client uses the configured port.

Description: `IndiInterface.sync_configuration` used to point the client at
port 7624 whatever the settings said. A stand-in INDI server on another port
(as the technical performance measures run uses) was then never reached.
The test builds the interface without a server and records the address it
sets.
"""

from unittest.mock import MagicMock

from wayfindinglib.drivers.indi_interface import IndiInterface


def test_sync_configuration_uses_the_configured_host_and_port() -> None:
    """The client is pointed at the configured host and ``indi_port``."""
    interface = IndiInterface.__new__(IndiInterface)
    interface.config = MagicMock()
    interface.config.get_telescope_hostname.return_value = "127.0.0.1"
    interface.config.get_indi_port.return_value = 45678
    recorded: list[tuple[str, int]] = []
    interface.setServer = lambda host, port: recorded.append((host, port))  # type: ignore[method-assign]

    interface.sync_configuration()

    assert recorded == [("127.0.0.1", 45678)]
    assert interface.hostname == "127.0.0.1"
