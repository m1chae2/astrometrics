"""Purpose: Live test of plate-solve alignment against the INDI simulators.

Description: Shows, end to end, that alignment works on the real path: the
real ``indiserver`` with the INDI telescope and CCD simulators, the app's own
mount control (`control.mount.slew` with ``center=True``), a real frame drawn
by the CCD simulator from the ``gsc`` star catalog, and the app's own plate
solve with Siril. Nothing is replaced by a stand-in.

The simulated mount goes to a place with a scattered go-to error (the
exposure driver draws a seeded offset for each new destination, see
`simulator.goto_scatter`, with a size like the user's own recorded first
attempts). Alignment then has to find the offset by plate solving, sync the
mount and slew again until the pointing error is within the tolerance. The
test checks that the first solve measured the offset the driver drew, that
the Siril solves succeeded, and that the last error is within the tolerance.
It runs two destinations, so the offsets differ in size and direction.

Nothing real is touched: the world is a temporary folder with its own INDI
server, library and settings, and the settings are never saved to the user's
file. The test is skipped when the INDI programs, the ``gsc`` catalog, Siril
or Siril's Gaia catalog are missing, and when ``ASTROMETRICS_FAST_TEST`` is
``1``. Siril needs the frames inside the home folder (its Flatpak cannot see
``/tmp``), so the world's folder must be too: the test suite's own
temporary folder is.

Run with ``.venv/bin/pytest simulator/test/test_alignment_live.py -q -s``.
"""

import math
import os
import shutil
import subprocess
import threading
import time
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from astropy.time import Time

from astrometricslib import AppConfiguration, SkyPosition
from simulator import observatory
from simulator.device_settings import device_settings_from_world
from simulator.exposure_driver import READY_FILE_NAME, ExposureDriver
from simulator.fit.fitting import GO_TO_SCATTER_SEED
from simulator.goto_scatter import GotoScatterSettings
from simulator.observatory import SIMULATED_TARGET_DEC_DEGREES, SIMULATED_TARGET_RA_HOURS
from simulator.settings import simulator_config_text
from simulator.simulated_calibration import record_simulated_calibrations
from wayfindinglib import Wayfinder

INDI_PROGRAMS = ("indiserver", "indi_setprop", "indi_getprop", *observatory.INDI_DRIVERS)
#: Siril's Gaia catalog file, in the places the app looks (see
#: `astrometricslib.scripts.set_up_siril_catalog`).
GAIA_CATALOG_NAME = "siril_cat_healpix8_astro.dat"
GAIA_CATALOG_FOLDERS = (
    "~/.local/share/astrometrics/catalogs",
    "~/.local/share/siril",
    "~/.var/app/org.siril.Siril/data/siril",
)
FLATPAK_SIRIL = "flatpak run org.siril.Siril"


def _siril_executable() -> str | None:
    """Find the command that starts Siril.

    Returns
    -------
    command : `str` or `None`
        The Flatpak command, ``siril-cli`` or ``siril``, whichever is
        installed, or `None`.
    """
    if shutil.which("flatpak"):
        listed = subprocess.run(
            ["flatpak", "info", "org.siril.Siril"], capture_output=True, check=False, timeout=30
        )
        if listed.returncode == 0:
            return FLATPAK_SIRIL
    return shutil.which("siril-cli") or shutil.which("siril")


def _gaia_catalog_installed() -> bool:
    """Say whether Siril's Gaia catalog is on the disk.

    Returns
    -------
    installed : `bool`
        `True` when the catalog file is in one of the folders.
    """
    return any((Path(folder).expanduser() / GAIA_CATALOG_NAME).is_file() for folder in GAIA_CATALOG_FOLDERS)


def _skip_reason() -> str | None:
    """Say why the live test cannot run, if it cannot.

    Returns
    -------
    reason : `str` or `None`
        The reason, or `None` when everything it needs is there.
    """
    if os.environ.get("ASTROMETRICS_FAST_TEST") == "1":
        return "ASTROMETRICS_FAST_TEST is set."
    missing = [name for name in INDI_PROGRAMS if shutil.which(name) is None]
    if missing:
        return f"The INDI programs are missing: {', '.join(missing)}."
    if not observatory.star_catalog_installed():
        return "The gsc star catalog is not installed, so the camera simulator draws no stars."
    if _siril_executable() is None:
        return "Siril is not installed."
    if not _gaia_catalog_installed():
        return "Siril's Gaia catalog is not installed."
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")

#: The sensor of the made-up camera: small, so a frame is quick, at the pixel
#: size of the real camera. The field is about half a degree.
SENSOR_PX = 1024
#: How far (arcseconds) a centering solve may be from the commanded position
#: for the run to count as centered.
TOLERANCE_ARCSEC = 60.0
#: The most centering rounds.
MAXIMUM_ITERATIONS = 8
#: The size of the go-to scatter: the median absolute deviations of the first
#: attempts the user recorded (51 first attempts, night 2026-10-03: 1485
#: arcsec in right ascension, 1372 in declination), times 1.4826. The middle
#: is zero here so the offsets point every which way.
SCATTER = GotoScatterSettings(
    mean_ra_arcsec=0.0,
    mean_dec_arcsec=0.0,
    sigma_ra_arcsec=1.4826 * 1485.0,
    sigma_dec_arcsec=1.4826 * 1372.0,
    seed=GO_TO_SCATTER_SEED,
    count=51,
)
#: How close the first solve must be to the drawn offset: this fraction of the
#: offset plus this many arcseconds. The simulator draws stars from a catalog
#: in the mean equinox of the date, with no aberration or nutation, which the
#: app's current-epoch frame includes (up to about 40 arcseconds), and the
#: telescope simulator's right ascension shift is not exactly the term it is
#: given for offsets of tens of arcminutes (measured: 3047 arcsec against
#: 2881 drawn, 6 percent).
FIRST_SOLVE_AGREEMENT_FRACTION = 0.10
FIRST_SOLVE_AGREEMENT_ARCSEC = 60.0


@dataclass
class World:
    """The running stand-ins and the app's handle on them.

    Attributes
    ----------
    wayfinder : `Wayfinder`
        The app's own library entry point, aimed at the simulators.
    driver : `ExposureDriver`
        The exposure driver, whose scatter drew the offsets.
    latitude, longitude : `float`
        The site of the simulated mount, in degrees.
    """

    wayfinder: Wayfinder
    driver: ExposureDriver
    latitude: float
    longitude: float


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """Start the INDI server, the exposure driver and the app's control.

    Yields
    ------
    world : `World`
        The running world.
    """
    root = tmp_path / "world"
    root.mkdir()
    indi_port = observatory.choose_free_port()
    text = simulator_config_text(root, indi_port, observatory.choose_free_port())
    for key, value in {"sensor_width_px": f'"{SENSOR_PX}"', "sensor_height_px": f'"{SENSOR_PX}"'}.items():
        text = observatory._set_key(text, "Observatory.Camera.ZWO ASI533MM Pro", key, value)
    text = observatory._set_key(text, "Processing.Siril", "siril_executable", f'"{_siril_executable()}"')
    config_path = root / "astrometrics.config.toml"
    config_path.write_text(text, encoding="utf-8")
    settings = tomllib.loads(text)
    location = settings["Observatory.Location"]
    (observatory.indi_home(root) / ".indi").mkdir(parents=True)
    standin = root / "standin"
    (standin / "Pictures").mkdir(parents=True)
    server = subprocess.Popen(
        observatory.indiserver_command(root, indi_port),
        env={**os.environ, **observatory.indiserver_environment(root)},
        stdout=(root / "indiserver.log").open("wb"),
        stderr=subprocess.STDOUT,
    )
    driver = ExposureDriver(
        indi_port,
        standin,
        None,
        root / "driver_stats.json",
        exposure_s=5.0,
        target="Simulated",
        ra_hours=SIMULATED_TARGET_RA_HOURS,
        dec_degrees=SIMULATED_TARGET_DEC_DEGREES,
        device_settings=device_settings_from_world(settings),
        capture="app",
        go_to_scatter=SCATTER,
    )
    driver_thread = threading.Thread(target=driver.run, name="exposure-driver", daemon=True)
    wayfinder = None
    try:
        driver_thread.start()
        ready = root / READY_FILE_NAME
        deadline = time.time() + 180
        while time.time() < deadline and not ready.exists() and driver_thread.is_alive():
            time.sleep(1)
        assert ready.exists(), "The exposure driver did not connect the simulators."
        monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
        world_configuration = AppConfiguration()
        # The plate-solve driver reads the process-wide settings, which
        # the test suite built before this world existed.
        monkeypatch.setattr("astrometricslib.foundation.config._instance", world_configuration)
        wayfinder = Wayfinder(world_configuration, simulated=False)
        record_simulated_calibrations(wayfinder.control, root)
        outcome = wayfinder.control.safety.set_mode(
            "controller", note="alignment live test", shadow_first=True
        )
        assert outcome is not None
        yield World(wayfinder, driver, float(location["latitude"]), float(location["longitude"]))
    finally:
        driver.stop_event.set()
        driver_thread.join(timeout=30)
        if wayfinder is not None:
            wayfinder.control.close()
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()


def _destinations(world: World) -> list[SkyPosition]:
    """Choose two sky positions well up in the simulated site's sky now.

    Returns
    -------
    positions : `list` [`SkyPosition`]
        Two ICRS positions, one on the meridian and one an hour east of it
        (so a slew between them is a new destination), at declinations that
        pass overhead.
    """
    lst_deg = float(Time.now().sidereal_time("mean", longitude=world.longitude).deg)
    return [
        SkyPosition(ra_deg=(lst_deg + 5.0) % 360.0, dec_deg=world.latitude - 5.0),
        SkyPosition(ra_deg=(lst_deg - 25.0) % 360.0, dec_deg=world.latitude + 12.0),
    ]


def test_alignment_finds_each_scattered_offset_syncs_and_centers(world: World) -> None:
    """Slew, solve with Siril, sync and slew again, ending within tolerance.

    For each destination the driver draws one offset. The first centering solve
    that Siril makes has to measure that offset, the mount is synced to the
    solved position, and the last solve has to be within the tolerance. Frames
    taken while the mount is still slewing cannot be solved (the app waits
    only `centering.SETTLE_SECONDS` after a slew); the run goes on to the next
    frame, which is why the rounds are more than the default of three.
    """
    control = world.wayfinder.control
    scatter = world.driver.go_to_scatter
    assert scatter is not None
    summary = []
    for destination in _destinations(world):
        draws_before = len(scatter.draws)
        started = time.time()

        control.mount.slew(
            destination, center=True, tolerance_arcsec=TOLERANCE_ARCSEC, max_iterations=MAXIMUM_ITERATIONS
        )

        assert len(scatter.draws) == draws_before + 1, "expected one new offset for this destination"
        drawn_ra_arcsec, drawn_dec_arcsec = scatter.draws[-1]
        attempts = sorted(
            (a for a in world.wayfinder.review.alignment.query().attempts if (a.timestamp or 0.0) >= started),
            key=lambda attempt: attempt.timestamp or 0.0,
        )
        solved = [a for a in attempts if a.status != "failed" and a.pointing_error_arcsec is not None]
        drawn_sky_arcsec = math.hypot(
            drawn_ra_arcsec * math.cos(math.radians(destination.dec_deg)), drawn_dec_arcsec
        )
        assert len(solved) >= 2, "a solve that finds the offset and a solve that confirms it are needed"
        assert solved[0].pointing_error_arcsec == pytest.approx(
            drawn_sky_arcsec, rel=FIRST_SOLVE_AGREEMENT_FRACTION, abs=FIRST_SOLVE_AGREEMENT_ARCSEC
        )
        assert solved[-1].pointing_error_arcsec <= TOLERANCE_ARCSEC
        summary.append({
            "drawn_offset_arcsec": [round(drawn_ra_arcsec), round(drawn_dec_arcsec)],
            "drawn_sky_arcsec": round(drawn_sky_arcsec),
            "rounds": len(attempts),
            "failed_rounds": len(attempts) - len(solved),
            "solved_errors_arcsec": [round(a.pointing_error_arcsec or 0.0, 1) for a in solved],
        })
    print("ALIGNMENT SUMMARY", summary)
