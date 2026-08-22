"""Purpose: Live-hardware validation of AlignmentService.

Description: Mirrors wayfindinglib/test/test_indi_interface_live.py's
pattern -- connects to a real running indiserver (simulator drivers
are sufficient) instead of the in-process SimulatorIndiInterface fake,
because the fake overrides IndiInterface entirely and would never have
caught the bugs this test guards against: AlignmentService used to
call a nonexistent IndiInterface.slew_telescope() method, and passed
RA in decimal degrees where sync_coordinates()/slew() expect hours.
Neither of those is visible to a mocked indi double, only to a real
protocol connection. The whole module is skipped when no INDI server
is reachable.

Only imaging_service and star_identifier are mocked here -- plate
solving a simulator-generated image isn't a meaningful thing to
validate (the simulator CCD doesn't produce a solvable star field for
a specific target), so a fixed "solved" WCS position stands in for it.
Everything downstream of that (the arithmetic, and the real INDI mount
commands) is exercised for real.
"""

import os
import socket
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrometricslib import AppConfiguration
from backend.services.observatory.alignment_service import AlignmentService
from wayfindinglib.drivers.indi_interface import IndiInterface

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


class _StubImagingService:
    """Returns a fixed fake image path.

    The simulator CCD's real capture isn't needed since plate solving
    is stubbed too.
    """

    def capture_light_frame(self, exposure, iso, gain):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return {"path": "/fake/alignment.fits"}


class _StubStarIdentifier:
    """Returns a fixed solved RA/Dec (decimal degrees) via a fake WCS."""

    def __init__(self, solved_ra_deg: float, solved_dec_deg: float):  # ruff: ignore[missing-return-type-special-method]
        self._wcs = SimpleNamespace(wcs=SimpleNamespace(crval=[solved_ra_deg, solved_dec_deg]))

    def process_image(self, image_path, attempt_plate_solving=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return None, self._wcs


def _read_ra_hours_dec_degrees(telescope) -> tuple[float, float]:  # ruff: ignore[missing-type-function-argument]
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

    if indi_interface.isServerConnected():
        indi_interface.disconnectServer()
        time.sleep(0.3)


@pytest.fixture()
def telescope(live_interface):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Yield the telescope device.

    Yields
    ------
    device : `PyIndi.BaseDevice`
        The discovered telescope device.
    """
    device = live_interface.connect_to_telescope()
    assert device is not None, "Telescope Simulator device not found on live INDI server"
    yield device


def test_alignment_loop_syncs_real_mount_with_correct_units(live_interface, telescope):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a not-yet-aligned attempt issues correct real INDI commands.

    Regression test for two bugs: AlignmentService used to call
    IndiInterface.slew_telescope() (which doesn't exist -- only
    .slew() does) and passed RA in decimal degrees where
    sync_coordinates() expects hours. Against a real driver, the old
    code's sync_coordinates(solve_result.ra, ...) call would have sent
    an RA value far outside the property's valid range (e.g. ~165
    instead of 11.0 hours); the slew_telescope() call would have
    raised AttributeError, silently caught and mislabeled as a normal
    alignment failure.

    Chosen coordinates: target 150 deg (=10h), solved 165 deg (=11h)
    -- a full simulated hour of RA off, so error_magnitude is well
    outside the default 30 arcsec accuracy_threshold, guaranteeing the
    "warning" branch (which syncs and re-slews) rather than "aligned".
    """
    target_ra_deg, target_dec_deg = 150.0, 20.0
    solved_ra_deg, solved_dec_deg = 165.0, 21.0

    service = AlignmentService(
        indi_interface=live_interface,
        imaging_service=_StubImagingService(),
        star_identifier=_StubStarIdentifier(solved_ra_deg, solved_dec_deg),
    )
    service.settle_time = 0.0

    # Bound to exactly one attempt: the loop only rechecks the stop flag
    # between attempts, so setting it during capture still lets this
    # attempt's sync/slew calls run to completion for real.
    original_capture = service._imaging_service.capture_light_frame

    def _capture_then_stop(*args, **kwargs):  # ruff: ignore[missing-type-args, missing-type-kwargs, missing-return-type-private-function]
        service._stop_flag.set()
        return original_capture(*args, **kwargs)

    service._imaging_service.capture_light_frame = _capture_then_stop

    # Spy on the real methods (wraps=, so the real INDI calls still execute
    # against the live driver) to capture the exact arguments AlignmentService
    # sent -- more precise than inferring correctness from a position
    # readback, since the immediately-following slew can overtake sync's
    # effect before a poll ever observes it.
    with (
        patch.object(live_interface, "sync_coordinates", wraps=live_interface.sync_coordinates) as sync_spy,
        patch.object(live_interface, "slew", wraps=live_interface.slew) as slew_spy,
    ):
        service._alignment_loop(target_ra=target_ra_deg, target_dec=target_dec_deg)

    attempts = service.get_attempts()
    assert len(attempts) == 1, "Expected exactly one bounded attempt"
    assert attempts[0]["status"] == "warning", (
        "Expected 'warning' (not aligned, sync/slew issued); 'failed' here means the real "
        "IndiInterface calls raised -- the exact failure mode of the bugs this test guards against"
    )
    assert attempts[0]["deltaRaArcsec"] == pytest.approx((solved_ra_deg - target_ra_deg) * 3600.0)

    # Both real INDI calls must have been reached (proves .slew() exists
    # and was used instead of the old slew_telescope() typo) with RA
    # already converted to hours (not the raw ~150/~165 degrees values).
    sync_spy.assert_called_once_with(pytest.approx(solved_ra_deg / 15.0), pytest.approx(solved_dec_deg))
    slew_spy.assert_called_once_with(pytest.approx(target_ra_deg / 15.0), pytest.approx(target_dec_deg))

    # End-state sanity check against the real simulator: the mount should
    # converge to the target (10h), never to an un-converted degrees value.
    deadline = time.time() + 5.0
    ra_hours, dec_degrees = _read_ra_hours_dec_degrees(telescope)
    while time.time() < deadline and ra_hours != pytest.approx(target_ra_deg / 15.0, abs=0.05):
        time.sleep(0.2)
        ra_hours, dec_degrees = _read_ra_hours_dec_degrees(telescope)

    assert ra_hours == pytest.approx(target_ra_deg / 15.0, abs=0.05), (
        f"Real mount RA ended at {ra_hours}h; expected convergence to ~{target_ra_deg / 15.0}h. "
        "A value near 150 here would mean raw degrees were sent where hours were expected."
    )
    assert dec_degrees == pytest.approx(target_dec_deg, abs=0.05)
