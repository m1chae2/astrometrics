"""Live-hardware characterization tests for indi_interface.

Unlike the rest of the wayfindinglib suite, these tests connect to a
*real* running INDI server (simulator drivers are sufficient) instead
of SimulatorIndiInterface, which overrides IndiInterface entirely and
therefore never exercises its actual PyIndi protocol code paths. The
whole module is skipped when no INDI server is reachable.

Written for the IndiInterface God Class reconciliation. Phase 0 pinned
down current behavior (including a known async-confirmation gap)
before any consolidation. Phase 2 consolidated
park()/unpark()/set_tracking() into MountController with a
wait-for-confirmation step (drivers/indi/property_wait.py), closing
that gap -- the tests below assert the fixed behavior directly rather
than xfail-documenting it.
"""

import os
import socket
import time

import pytest

from astrometricslib import AppConfiguration
from wayfindinglib.drivers.indi_interface import IndiInterface

INDI_HOST = "localhost"
INDI_PORT = 7624
CONVERGENCE_TIMEOUT_S = 20.0


def _indi_server_reachable(host: str, port: int) -> bool:
    """Best-effort check for a live INDI server, to skip the module cleanly.

    Returns
    -------
    reachable : `bool`
        `True` if a TCP connection to ``host:port`` succeeded.
    """
    try:
        probe_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe_socket.settimeout(1.5)
        probe_socket.connect((host, port))
        probe_socket.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    os.environ.get("ASTROMETRICS_FAST_TEST") == "1" or not _indi_server_reachable(INDI_HOST, INDI_PORT),
    reason=(
        "No live hardware available or ASTROMETRICS_FAST_TEST is set; skipping live characterization tests."
    ),
)


def _switch_state(device, property_name: str, fallback_name: str | None = None) -> dict:  # ruff: ignore[missing-type-function-argument]
    """Read all element name->state pairs for a switch property as a dict.

    Returns
    -------
    states : `dict`
        Mapping of switch element name to its current state.
    """
    property_vector = device.getSwitch(property_name)
    if property_vector is None and fallback_name:
        property_vector = device.getSwitch(fallback_name)
    assert property_vector is not None, f"Switch property {property_name} not found on device"
    return {property_vector[i].getName(): property_vector[i].getState() for i in range(len(property_vector))}


def _wait_for_switch_state(
    device,  # ruff: ignore[missing-type-function-argument]
    property_name: str,
    element_name: str,
    expected_state,  # ruff: ignore[missing-type-function-argument]
    fallback_name: str | None = None,
    timeout: float = CONVERGENCE_TIMEOUT_S,
) -> dict:
    """Poll a switch property until an element reaches the expected state.

    INDI's client-server property protocol is asynchronous
    (sendNewSwitch submits a request; the driver validates and pushes
    the confirmed value back via updateProperty), so a caller cannot
    assume the local cached property reflects a command's effect
    immediately after the send call returns. Tests must poll.

    Returns
    -------
    states : `dict`
        The last-read element name->state mapping, whether or not
        the expected state was reached before the timeout.
    """
    deadline = time.time() + timeout
    state = {}
    while time.time() < deadline:
        state = _switch_state(device, property_name, fallback_name)
        if state.get(element_name) == expected_state:
            return state
        time.sleep(0.25)
    return state


@pytest.fixture(scope="module")
def live_interface():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Yield a real IndiInterface connected to the live server, unblocked.

    Yields
    ------
    indi_interface : `IndiInterface`
        A connected interface with at least one device discovered.
    """
    config = AppConfiguration()
    config.app_config.set("Observatory.Telescope", "allow_commands", "true")

    indi_interface = IndiInterface(config=config)
    indi_interface.connect_to_server()

    deadline = time.time() + 10.0
    while time.time() < deadline and not indi_interface.deviceMap:
        indi_interface._ensure_connection()
        time.sleep(0.5)

    assert indi_interface.deviceMap, "No INDI devices discovered from the live server within timeout"
    yield indi_interface

    # PyIndi's BaseClient runs a background C++ thread reading the server
    # socket. Leaving it connected when the interpreter starts tearing
    # down objects at process exit intermittently segfaults inside the
    # native extension. Close it explicitly so the thread stops before
    # Python object teardown begins.
    if indi_interface.isServerConnected():
        indi_interface.disconnectServer()
        time.sleep(0.3)


@pytest.fixture()
def telescope(live_interface):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Yield the telescope device, park state reset to a known baseline.

    Yields
    ------
    device : `PyIndi.BaseDevice`
        The discovered telescope device.
    """
    device = live_interface.connect_to_telescope()
    assert device is not None, "Telescope Simulator device not found on live INDI server"
    yield device


def test_park_then_unpark_eventually_converges(live_interface, telescope) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify park and unpark both eventually reach the correct switch state.

    This is the core functional safety net: regardless of the
    async-confirmation gap documented below, the commands must still
    work when the caller is willing to wait for the server's actual
    response.
    """
    live_interface.park()
    parked = _wait_for_switch_state(telescope, "TELESCOPE_PARK", "PARK", 1, fallback_name="PARK")
    assert parked.get("PARK") == 1, (
        f"Telescope did not reach PARK=1 within {CONVERGENCE_TIMEOUT_S}s: {parked}"
    )

    live_interface.unpark()
    unparked = _wait_for_switch_state(telescope, "TELESCOPE_PARK", "UNPARK", 1, fallback_name="PARK")
    assert unparked.get("UNPARK") == 1, (
        f"Telescope did not reach UNPARK=1 within {CONVERGENCE_TIMEOUT_S}s: {unparked}"
    )


def test_set_tracking_eventually_converges(live_interface, telescope) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify tracking on/off both eventually reach the correct state."""
    live_interface.set_tracking(True)
    on_state = _wait_for_switch_state(telescope, "TELESCOPE_TRACK_STATE", "TRACK_ON", 1)
    assert on_state.get("TRACK_ON") == 1, (
        f"Tracking did not turn on within {CONVERGENCE_TIMEOUT_S}s: {on_state}"
    )

    live_interface.set_tracking(False)
    off_state = _wait_for_switch_state(telescope, "TELESCOPE_TRACK_STATE", "TRACK_OFF", 1)
    assert off_state.get("TRACK_OFF") == 1, (
        f"Tracking did not turn off within {CONVERGENCE_TIMEOUT_S}s: {off_state}"
    )


def test_unpark_return_value_implies_confirmed_state(live_interface, telescope) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify unpark() returning True means the device confirmed UNPARK=1.

    Previously (pre Phase 2) unpark() returned True as soon as
    sendNewSwitch() was called, without waiting for the driver's
    asynchronous confirmation -- roughly a coin flip whether the state
    had actually changed by the time this check ran.
    MountController.unpark() now polls via wait_for_switch_state()
    before returning.
    """
    live_interface.park()
    _wait_for_switch_state(telescope, "TELESCOPE_PARK", "PARK", 1, fallback_name="PARK")

    result = live_interface.unpark()
    # No wait here on purpose: this is checking whether the return
    # value alone (without polling) can be trusted as an immediate
    # confirmation.
    immediate_state = _switch_state(telescope, "TELESCOPE_PARK", fallback_name="PARK")
    assert result is True
    assert immediate_state.get("UNPARK") == 1, (
        "unpark() returned True but the device had not yet confirmed UNPARK=1 "
        f"immediately afterward: {immediate_state}"
    )


def test_set_tracking_return_value_implies_confirmed_state(live_interface, telescope) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify set_tracking() returning True means the change is confirmed."""
    live_interface.set_tracking(False)
    _wait_for_switch_state(telescope, "TELESCOPE_TRACK_STATE", "TRACK_OFF", 1)

    result = live_interface.set_tracking(True)
    immediate_state = _switch_state(telescope, "TELESCOPE_TRACK_STATE")
    assert result is True
    assert immediate_state.get("TRACK_ON") == 1, (
        "set_tracking(True) returned True but the device had not yet confirmed TRACK_ON=1 "
        f"immediately afterward: {immediate_state}"
    )


def test_park_unpark_repeated_trials_are_reliable(live_interface, telescope) -> None:  # ruff: ignore[missing-type-function-argument]
    """Regression guard: repeated park/unpark cycles must be honest, not ~50%.

    This is the specific scenario that caught the pre-Phase-2
    async-confirmation bug (manual testing showed a consistent ~50%
    failure rate across repeated trials, not just an occasional flake)
    -- run enough trials here that reintroducing it would reliably
    fail this test rather than getting lucky.
    """
    for trial in range(6):
        park_result = live_interface.park()
        parked_state = _switch_state(telescope, "TELESCOPE_PARK", fallback_name="PARK")
        assert park_result is True and parked_state.get("PARK") == 1, (
            f"trial {trial}: park() returned {park_result} but state was {parked_state}"
        )

        unpark_result = live_interface.unpark()
        unparked_state = _switch_state(telescope, "TELESCOPE_PARK", fallback_name="PARK")
        assert unpark_result is True and unparked_state.get("UNPARK") == 1, (
            f"trial {trial}: unpark() returned {unpark_result} but state was {unparked_state}"
        )


def test_is_server_responsive_matches_connection_manager(live_interface) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verify the inline responsiveness check and ConnectionManager's agree.

    These are two separate implementations of the same socket
    handshake (indi_interface.py's _is_server_responsive and
    drivers/indi/connection_manager.py's
    ConnectionManager.is_server_responsive); against a live, healthy
    server they must both report True.
    """
    assert live_interface._is_server_responsive() is True
    assert live_interface.connection_manager.is_server_responsive() is True
