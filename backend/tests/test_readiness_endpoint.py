"""Tests for the startup warm-up readiness gate.

The desktop shell holds its splash screen until `/api/ready` reports that the
star catalog has been loaded, so the two properties that matter are that the
route reflects the warm-up state, and that a failed warm-up still releases the
gate rather than keeping the app from ever opening.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend import main_backend


@pytest.fixture
def unfinished_warmup() -> Iterator[None]:
    """Start with warm-up unfinished; restore the shared flag afterwards."""
    was_finished = main_backend.sky_catalog_warmup_finished.is_set()
    main_backend.sky_catalog_warmup_finished.clear()
    yield
    if was_finished:
        main_backend.sky_catalog_warmup_finished.set()
    else:
        main_backend.sky_catalog_warmup_finished.clear()


def test_ready_endpoint_reports_503_until_warmup_finishes(client: TestClient, unfinished_warmup: None):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The shell must keep waiting while the catalog is still loading."""
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False}

    main_backend.sky_catalog_warmup_finished.set()

    response = client.get("/api/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_warmup_marks_ready_after_loading_the_catalog(mocker, unfinished_warmup: None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A successful warm-up queries the sky engine, then releases the gate."""
    fake_container = mocker.patch.object(main_backend, "container")

    main_backend._warm_sky_catalog()

    fake_container.wayfinder.planning.get_sources.assert_called_once()
    assert main_backend.sky_catalog_warmup_finished.is_set()


def test_failed_warmup_still_marks_ready(mocker, unfinished_warmup: None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A broken warm-up means a slow first load, not a locked-out app."""
    fake_container = mocker.patch.object(main_backend, "container")
    fake_container.wayfinder.planning.get_sources.side_effect = RuntimeError("catalog unreadable")

    main_backend._warm_sky_catalog()

    assert main_backend.sky_catalog_warmup_finished.is_set()


def test_backend_never_downloads_earth_orientation_data():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Astropy must not go to the internet for its Earth-orientation table.

    Regression test: by default the first altitude/azimuth conversion in a
    process downloaded it, which stalled the first star click for 6 to 14
    seconds and quietly needed the internet.
    """
    from astropy.utils import iers

    assert iers.conf.auto_download is False
    assert iers.conf.auto_max_age is None


def test_altitude_conversion_works_for_past_and_future_dates_offline():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """With the download off, no date should make a conversion raise."""
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time

    location = EarthLocation(lat=45.68 * u.deg, lon=-111.04 * u.deg, height=1500 * u.m)
    star = SkyCoord(315.7319 * u.deg, 68.7115 * u.deg)

    for date in ("2024-09-18T21:00:00", "2026-09-18T21:00:00", "2036-09-18T21:00:00"):
        altaz = star.transform_to(AltAz(obstime=Time(date, scale="utc"), location=location))
        assert -90.0 <= altaz.alt.deg <= 90.0


def test_warmup_loads_earth_orientation_data_before_marking_ready(mocker, unfinished_warmup: None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The table is loaded under the splash, not on the first click."""
    mocker.patch.object(main_backend, "container")
    warm = mocker.patch.object(main_backend, "_warm_earth_orientation_data")

    main_backend._warm_sky_catalog()

    warm.assert_called_once()
    assert main_backend.sky_catalog_warmup_finished.is_set()


def test_failed_earth_orientation_warmup_still_marks_ready(mocker, unfinished_warmup: None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A failure loading that table must not lock the user out of the app."""
    mocker.patch.object(main_backend, "container")
    mocker.patch.object(
        main_backend, "_warm_earth_orientation_data", side_effect=RuntimeError("table unreadable")
    )

    main_backend._warm_sky_catalog()

    assert main_backend.sky_catalog_warmup_finished.is_set()


def test_earth_orientation_warmup_runs_a_real_conversion():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The warm-up itself works, and does not need the internet."""
    main_backend._warm_earth_orientation_data()
