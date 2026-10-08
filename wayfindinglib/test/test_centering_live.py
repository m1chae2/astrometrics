"""Purpose: Live-hardware check of centering the mount by plate solving.

Description: Mirrors `wayfindinglib/test/test_indi_interface_live.py`:
it connects to a real running indiserver (simulator drivers are enough)
instead of the in-process `SimulatorIndiInterface`, because only a real
protocol connection shows which mount commands are sent and in which
units. The whole module is skipped when no INDI server is reachable.

Only the camera frame and the plate solve are replaced -- solving a
simulator image is not meaningful (the simulator camera does not draw the
star field of a given target), so a fixed solved position stands in.
Everything after that (the arithmetic and the real INDI mount commands
of ``control.mount.slew(center=True)``) runs for real: the sync must send
the solved right ascension in hours, and the slew back the target's.
"""

import os
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from astrometricslib import AppConfiguration
from wayfindinglib import ObservatoryControl, SkyPosition
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.drivers.control_record_store import ControlRecordStore
from wayfindinglib.drivers.indi_interface import IndiInterface
from wayfindinglib.models.policy.delegation import DelegationState, ObservatoryCapability
from wayfindinglib.tasks.control_tasks import centering

INDI_HOST = "localhost"
INDI_PORT = 7624


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
    reason="No live INDI server available or ASTROMETRICS_FAST_TEST is set; skipping live validation.",
)


def _read_ra_hours_dec_degrees(telescope: Any) -> tuple[float, float]:
    """Read the mount's raw EQUATORIAL_EOD_COORD number vector.

    Returns
    -------
    coordinates : `tuple` [`float`, `float`]
        ``(ra_hours, dec_degrees)`` as currently reported by the
        driver.
    """
    coord = telescope.getNumber("EQUATORIAL_EOD_COORD")
    assert coord is not None, "EQUATORIAL_EOD_COORD not found on telescope simulator"
    return coord[0].value, coord[1].value


@pytest.fixture(scope="module")
def live_interface() -> Iterator[IndiInterface]:
    """Yield a real IndiInterface connected to the live server.

    Yields
    ------
    indi_interface : `IndiInterface`
        A connected interface with at least one device discovered.
    """
    config = AppConfiguration()

    indi_interface = IndiInterface(config=config)
    indi_interface.connect_to_server()

    deadline = time.time() + 10.0
    while time.time() < deadline and not indi_interface.deviceMap:
        indi_interface._ensure_connection()
        time.sleep(0.5)

    assert indi_interface.deviceMap, "No INDI devices discovered from the live server within timeout"
    yield indi_interface

    if indi_interface.isServerConnected():
        indi_interface.disconnectServer()
        time.sleep(0.3)


@pytest.fixture(scope="module")
def observatory(
    live_interface: IndiInterface, tmp_path_factory: pytest.TempPathFactory
) -> ObservatoryControl:
    """Build an authority-granted ObservatoryControl over `live_interface`.

    MOUNT_CONTROL/PLATE_SOLVE_ALIGNMENT are promoted to AUTHORITATIVE
    (via SHADOWED first, per the correction-capability shadow-precedence
    rule) so `control.mount.sync`/`control.mount.slew` actually dispatch
    to the driver rather than raising -- an isolated, temp-directory
    `DiskButler` backs the delegation policy so this never touches a
    real config/database file.

    Returns
    -------
    observatory : `ObservatoryControl`
        A control instance wrapping the live driver, with both
        capabilities this test needs already AUTHORITATIVE.
    """
    isolated_dir = tmp_path_factory.mktemp("centering_live")
    config = AppConfiguration()
    config._find_config_file = lambda: isolated_dir / "astrometrics.config.toml"
    config.update_config({"Wayfinding Library": {"path": str(isolated_dir / "wayfinding_library")}})

    observatory_control = ObservatoryControl(config=config, butler=DiskButler(app_config=config))
    observatory_control.driver = live_interface
    observatory_control._context.records = ControlRecordStore(str(isolated_dir / "logs.db"))
    observatory_control.safety.apply_promotion_decision(
        ObservatoryCapability.MOUNT_CONTROL, DelegationState.AUTHORITATIVE
    )
    observatory_control.safety.apply_promotion_decision(
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, DelegationState.SHADOWED
    )
    observatory_control.safety.apply_promotion_decision(
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT, DelegationState.AUTHORITATIVE
    )
    return observatory_control


@pytest.fixture()
def telescope(live_interface: IndiInterface) -> Iterator[Any]:
    """Yield the telescope device.

    Yields
    ------
    device : `PyIndi.BaseDevice`
        The discovered telescope device.
    """
    device = live_interface.connect_to_telescope()
    assert device is not None, "Telescope Simulator device not found on live INDI server"
    yield device


def test_centering_syncs_the_real_mount_with_correct_units(
    live_interface: IndiInterface,
    observatory: ObservatoryControl,
    telescope: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A round outside the tolerance syncs and slews back, in hours, for real.

    Target 150 degrees (10h), solved 165 degrees (11h): far outside the
    tolerance, so the round syncs to the solve and slews back to the
    target. One round only.
    """
    target_ra_deg, target_dec_deg = 150.0, 20.0
    solved_ra_deg, solved_dec_deg = 165.0, 21.0
    monkeypatch.setattr(centering, "SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(centering, "_capture_frame", lambda context, exposure_seconds: Path("frame.fits"))
    monkeypatch.setattr(centering, "_solve", lambda context, path: (solved_ra_deg, solved_dec_deg))

    with (
        patch.object(live_interface, "sync_coordinates", wraps=live_interface.sync_coordinates) as sync_spy,
        patch.object(live_interface, "slew", wraps=live_interface.slew) as slew_spy,
    ):
        centered = observatory.mount.slew(
            SkyPosition(ra_deg=target_ra_deg, dec_deg=target_dec_deg), center=True, max_iterations=1
        )

    assert centered is False
    sync_spy.assert_called_once_with(pytest.approx(solved_ra_deg / 15.0), pytest.approx(solved_dec_deg))
    assert slew_spy.call_count == 2
    slew_spy.assert_called_with(pytest.approx(target_ra_deg / 15.0), pytest.approx(target_dec_deg))

    deadline = time.time() + 5.0
    ra_hours, dec_degrees = _read_ra_hours_dec_degrees(telescope)
    while time.time() < deadline and ra_hours != pytest.approx(target_ra_deg / 15.0, abs=0.05):
        time.sleep(0.2)
        ra_hours, dec_degrees = _read_ra_hours_dec_degrees(telescope)

    assert ra_hours == pytest.approx(target_ra_deg / 15.0, abs=0.05)
    assert dec_degrees == pytest.approx(target_dec_deg, abs=0.05)
