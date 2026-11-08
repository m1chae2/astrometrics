"""Purpose: Test that every outgoing client of astrometricslib honors offline.

Description: When the launch says ``offline = true`` (see
`astrometricslib.foundation.launch_identity`), nothing may leave the
computer. Each test here turns the switch on, makes any attempt to open a
connection or look a name up fail the test, and calls one client. The client
must refuse with the error its callers already handle as "unavailable".
Nothing in these tests uses the network.
"""

import socket
from pathlib import Path

import numpy as np
import pytest
from astropy.table import Table

from astrometricslib.drivers import LaunchIdentity
from astrometricslib.exceptions import ExternalServiceError
from astrometricslib.foundation import offline


@pytest.fixture(autouse=True)
def offline_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the offline switch on and forbid every connection and name lookup.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the settings the switch reads and the socket calls.
    """

    class _Configuration:
        """A settings object whose launch is offline."""

        @staticmethod
        def get_launch_identity() -> LaunchIdentity:
            """Give an offline identity.

            Returns
            -------
            identity : `LaunchIdentity`
                One with ``offline`` set.
            """
            return LaunchIdentity(offline=True)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Fail the test: an offline client opened a connection."""
        pytest.fail("an offline client touched the network")

    monkeypatch.setattr(offline, "get_configuration", _Configuration)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_simbad_refuses_every_query_without_pausing() -> None:
    """The SIMBAD driver raises its own "service" error and starts no pause."""
    from astrometricslib._internal.drivers import astroquery_simbad_driver as simbad

    driver = simbad.AstroquerySimbadDriver()

    with pytest.raises(ExternalServiceError, match="SIMBAD") as refusal:
        driver.query_object("M 31")
    assert refusal.value.details["offline"] is True
    with pytest.raises(ExternalServiceError):
        driver.query_region(object(), "0.5d")
    with pytest.raises(ExternalServiceError):
        driver.query_tap("SELECT 1")
    assert not simbad._paused_until  # no cooldown: nothing was tried


def test_gaia_cone_clients_refuse() -> None:
    """Both Gaia cone clients (VizieR and the ESA archive) refuse."""
    from astrometricslib._internal.drivers.gaia_cone_driver import query_gaia_cone_esa, query_gaia_cone_vizier

    with pytest.raises(ConnectionError, match="VizieR"):
        query_gaia_cone_vizier(10.0, 20.0, 0.5, 16.0, 100, 5.0)
    with pytest.raises(ConnectionError, match="Gaia"):
        query_gaia_cone_esa(10.0, 20.0, 0.5, 16.0, 100)


def test_the_star_identifier_skips_gaia_quietly(caplog: pytest.LogCaptureFixture) -> None:
    """The cone fetch gives up at once, with no warning or mark."""
    from astrometricslib.pipelines.astrometry.processing import star_identifier

    with caplog.at_level("DEBUG"):
        table = star_identifier._fetch_gaia_cone(10.0, 20.0, 0.5, 16.0, 100, "test")

    assert table is None
    assert not [record for record in caplog.records if record.levelname in ("WARNING", "ERROR")]
    assert not star_identifier._is_gaia_service_unreachable("VizieR")


def test_gaia_reddening_refuses_as_an_external_service_error() -> None:
    """The reddening driver wraps the refusal like any failed query."""
    from astrometricslib._internal.drivers.astroquery_gaia_reddening_driver import (
        AstroqueryGaiaReddeningDriver,
    )

    driver = AstroqueryGaiaReddeningDriver()

    with pytest.raises(ExternalServiceError, match="sends nothing off"):
        driver.get_reddening(4472832130942575872)
    with pytest.raises(ExternalServiceError, match="sends nothing off"):
        driver.prefetch_reddening([4472832130942575872])
    assert driver._consecutive_failures == 0


def test_gaia_xp_download_refuses_without_pausing_downloads(tmp_path: Path) -> None:
    """An uncached XP spectrum is not downloaded, and no pause starts."""
    from astrometricslib._internal.drivers.gaia_xp_driver import AstroqueryGaiaXpDriver
    from astrometricslib._internal.drivers.interfaces.gaia_xp_driver import GaiaXpDownloadError

    driver = AstroqueryGaiaXpDriver(cache_directory=tmp_path)

    with pytest.raises(GaiaXpDownloadError, match="Gaia archive"):
        driver.sampled_spectrum(4472832130942575872)
    assert not driver._skip_downloads_until


def test_the_online_plate_solver_is_not_asked() -> None:
    """With an API key the online solves return nothing, unasked."""
    from astrometricslib._internal.drivers.astrometry_net_driver import AstrometryNetPlateSolveDriver

    driver = AstrometryNetPlateSolveDriver(api_key="a-key")

    assert driver._solve_online_sources([{"x_centroid": 1.0, "y_centroid": 2.0}], 100, 100) is None
    assert driver._solve_online_image("/does/not/exist.fits") is None
    assert not driver.astrometry_net.solve_from_source_list.called
    assert not driver.astrometry_net.solve_from_image.called


def test_skybot_is_not_asked_and_counts_no_attempt() -> None:
    """The ephemeris search returns nothing and counts no failure."""
    from astrometricslib.models.moving_object_config import MovingObjectConfig
    from astrometricslib.pipelines.asteroid_detection.ephemeris import EphemerisCrossMatcher

    matcher = EphemerisCrossMatcher(MovingObjectConfig(), min_seconds_between_queries=0.0)

    assert matcher.query_field(10.0, 20.0, 1.7e9, 0.5) is None
    assert matcher.queries_attempted == 0
    assert matcher.queries_failed == 0


def test_the_variable_star_cross_match_refuses() -> None:
    """The CDS cross-match raises before it calls the service."""
    from astrometricslib.pipelines.shared.variable_star_catalogs import run_crossmatch

    calls: list[object] = []
    positions = Table({"ra": np.array([1.0]), "dec": np.array([2.0])})

    with pytest.raises(ConnectionError, match="cross-match"):
        run_crossmatch(lambda **kwargs: calls.append(kwargs), positions, "vsx")
    assert calls == []


def test_the_drive_scripts_refuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The Drive upload and integrity check start no rclone."""
    from astrometricslib.scripts import check_drive_integrity, upload_library

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Fail the test: an offline script started a program."""
        pytest.fail("an offline script started a program")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)

    with pytest.raises(RuntimeError, match="Google Drive"):
        check_drive_integrity.fetch_manifest("rclone", "drive", "Library")
    assert upload_library.main(["--library", str(tmp_path), "--no-notify"]) == 2
