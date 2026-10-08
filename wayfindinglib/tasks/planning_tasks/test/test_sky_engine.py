"""Purpose: Tests for the planning sky engine and its sums.

Description: Checks that `SkyEngine` reads its site, and that the
coordinate, catalog and visibility sums it feeds give correct answers.
"""

import configparser
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import astropy.units as u
import pytest
from astropy.coordinates import EarthLocation
from astropy.time import Time

from astrometricslib import StellarObject, Target
from wayfindinglib.data_access.site_profile_reader import configured_observer_location
from wayfindinglib.tasks.planning_tasks.catalog_operations import astrometrics_catalog
from wayfindinglib.tasks.planning_tasks.coordinate_operations import compute_altaz
from wayfindinglib.tasks.planning_tasks.resolution_operations import get_library_star_summaries, get_sources
from wayfindinglib.tasks.planning_tasks.sky_engine import SkyEngine
from wayfindinglib.tasks.planning_tasks.visibility_operations import hour_angle_from_lst, rise_set_transit


def test_sky_initialization() -> None:
    """Verifies SkyEngine initializes coordinates correctly."""
    # Denver defaults
    sky = SkyEngine()
    assert pytest.approx(sky.latitude) == 39.7392
    assert pytest.approx(sky.longitude) == -104.9903
    assert pytest.approx(sky.elevation) == 1600.0


def test_local_sidereal_time() -> None:
    """Verifies Local Sidereal Time calculations."""
    sky = SkyEngine(latitude=39.7392, longitude=-104.9903, elevation=1600.0)

    # Vernal equinox midnight
    time = datetime(2026, 3, 20, 0, 0, 0)
    lst = sky.get_local_sidereal_time(time)
    assert 0.0 <= lst <= 24.0


def test_the_engine_location_is_the_given_site() -> None:
    """Verifies the engine's EarthLocation is built from the given site."""
    sky = SkyEngine(latitude=45.0, longitude=-90.0, elevation=100.0)
    assert sky.location.lat.deg == pytest.approx(45.0)
    assert sky.location.lon.deg == pytest.approx(-90.0)
    assert sky.location.height.to_value(u.m) == pytest.approx(100.0)


@patch("wayfindinglib.tasks.planning_tasks.catalog_operations.astrometrics_catalog")
@patch("wayfindinglib.tasks.planning_tasks.catalog_operations.global_catalog")
def test_get_sources(mock_global, mock_local) -> None:
    """Verifies regional source query delegation."""
    sky = SkyEngine()

    star = StellarObject(id="S1", name="Star 1", ra=12.0, dec=34.0)
    target = Target(id="T1", commonName="Target 1", ra="12h", dec="34d")

    mock_local.return_value = [target]
    mock_global.return_value = [star]

    # Mock the local Astrometrics instance on the engine
    sky._astrometrics = MagicMock()
    sky._astrometrics.targets.list.return_value = [target]
    sky._astrometrics.stars.query.return_value.objects = []

    # 1. Local only
    result_local = sky.get_sources(12.0, 34.0, 5.0, include_catalog=False)
    assert len(result_local) == 1
    assert result_local[0].id == "T1"

    # 2. Local + Global
    result_merged = sky.get_sources(12.0, 34.0, 5.0, include_catalog=True)
    assert len(result_merged) == 2
    assert {source.id for source in result_merged} == {"T1", "S1"}


def test_astrometrics_catalog_includes_letterless_plate_solved_targets() -> None:
    """Verifies astrometrics_catalog includes letterless targets.

    Covers Targets whose ra/dec are letterless sexagesimal strings
    (e.g. "16 41 40.80"), as produced by the plate solver and SIMBAD
    resolution. These previously raised "No unit specified" and were
    silently dropped from every catalog search, since the coordinate
    parsing omitted an explicit `unit=` on the SkyCoord construction.
    """
    plate_solved_target = Target(id="T1", commonName="Target 1", ra="16 41 40.80", dec="36 27 35.64")

    fake_astrometrics = MagicMock()
    fake_astrometrics.targets.list.return_value = [plate_solved_target]
    fake_astrometrics.stars.query.return_value.objects = []

    fake_sky = MagicMock()
    fake_sky._astrometrics = fake_astrometrics

    # Center/radius chosen to comfortably contain the target's
    # coordinates (in degrees).
    results = astrometrics_catalog(fake_sky, ra_deg=250.17, dec_deg=36.46, radius_deg=1.0)

    assert len(results) == 1
    assert results[0].id == "T1"


def test_astrometrics_catalog_skips_unparseable_target_without_dropping_others() -> None:
    """Verifies a malformed target does not drop other results.

    A single malformed target's coordinates should not prevent
    other, valid targets in the same batch from being returned.
    """
    good_target = Target(id="GOOD", commonName="Good Target", ra="16 41 40.80", dec="36 27 35.64")
    bad_target = Target(id="BAD", commonName="Bad Target", ra="not a coordinate", dec="also not one")

    fake_astrometrics = MagicMock()
    fake_astrometrics.targets.list.return_value = [good_target, bad_target]
    fake_astrometrics.stars.query.return_value.objects = []

    fake_sky = MagicMock()
    fake_sky._astrometrics = fake_astrometrics

    results = astrometrics_catalog(fake_sky, ra_deg=250.17, dec_deg=36.46, radius_deg=1.0)

    assert len(results) == 1
    assert results[0].id == "GOOD"


def test_astrometrics_catalog_filters_stellar_objects_by_radius() -> None:
    """Verifies the stellar-object branch returns only in-radius stars.

    Also covers the local-only (`include_catalog=False`) path of
    `SkyEngine.get_sources`, which used to return every stellar object
    regardless of the requested region.
    """
    near_star = StellarObject(id="NEAR", ra=250.17, dec=36.46)
    far_star = StellarObject(id="FAR", ra=10.0, dec=-60.0)
    no_coordinates_star = StellarObject(id="BLANK", ra="", dec="")

    fake_astrometrics = MagicMock()
    fake_astrometrics.targets.list.return_value = []
    fake_astrometrics.stars.query.return_value.objects = [near_star, far_star, no_coordinates_star]

    fake_sky = MagicMock()
    fake_sky._astrometrics = fake_astrometrics

    results = astrometrics_catalog(fake_sky, ra_deg=250.17, dec_deg=36.46, radius_deg=1.0)

    assert [result.id for result in results] == ["NEAR"]

    local_only = get_sources(fake_sky, 250.17, 36.46, 1.0, include_catalog=False)
    assert [result.id for result in local_only] == ["NEAR"]


def test_astrometrics_catalog_leaves_the_stars_alone_when_asked_not_to_load_them() -> None:
    """Verifies include_stars=False returns targets without touching the stars.

    Reading every star in full takes seconds on a large library, so a
    caller that gets the stars another way must be able to skip that. The
    stellar objects are made to raise if they are read at all.
    """
    target = Target(id="T1", commonName="Target 1", ra="16h 41m 40s", dec="+36d 27m 36s")

    fake_astrometrics = MagicMock()
    fake_astrometrics.targets.list.return_value = [target]
    fake_astrometrics.stars.query.side_effect = AssertionError("stars were loaded")

    fake_sky = MagicMock()
    fake_sky._astrometrics = fake_astrometrics

    results = get_sources(fake_sky, 250.17, 36.46, 1.0, include_catalog=False, include_stars=False)

    assert [result.id for result in results] == ["T1"]


def test_get_library_star_summaries_reads_the_region_summaries_from_astrometrics() -> None:
    """Verifies the summaries come from the library's fast region read."""
    fake_sky = MagicMock()
    fake_sky._astrometrics.stars.query.return_value.stars = [{"id": "S1"}]

    summaries = get_library_star_summaries(fake_sky, 250.17, 36.46, 2.0)

    assert summaries == [{"id": "S1"}]
    fake_sky._astrometrics.stars.query.assert_called_once_with(
        ra_deg=250.17, dec_deg=36.46, radius_deg=2.0, magnitude_min=None, magnitude_max=None, limit=None
    )


def test_compute_altaz_matches_recorded_indi_driver_baseline() -> None:
    """Locks compute_altaz's output to a recorded INDI driver baseline.

    Regression test locking compute_altaz's output to values recorded
    from the original inline SkyCoord/AltAz transform in
    indi_interface.py and mount_controller.py before they were
    consolidated onto this shared function. These two call sites feed
    live telemetry display and the slew-safety altitude gate, so a
    units/sign regression here must be caught.
    """
    location = EarthLocation(lat=39.7392 * u.deg, lon=-104.9903 * u.deg, height=1600.0 * u.m)
    obstime = Time("2026-07-05T12:00:00")

    # ra_hours=10.5 -> 157.5 deg, matching the driver's own
    # hours->degrees conversion.
    alt, az = compute_altaz(10.5 * 15.0, 45.0, location, obstime)

    assert alt == pytest.approx(-3.38825174036355, abs=1e-5)
    assert az == pytest.approx(14.476154024250459, abs=1e-5)


def test_meridian_status_and_flips() -> None:
    """Verifies hour angle and meridian flip triggers."""
    from wayfindinglib.tasks.planning_tasks.visibility_report import build_visibility_report, to_astropy_time

    sky = SkyEngine(latitude=40.0, longitude=-100.0, elevation=1000.0)
    sky.meridian_flip_delay_min = 10.0  # 10 minutes delay

    # Current Sidereal Time
    time = datetime(2026, 6, 21, 22, 0, 0)
    lst = sky.get_local_sidereal_time(time)

    # One target transiting (RA = LST), one past the meridian by 15 minutes.
    objects = [
        StellarObject(id="transiting", name="transiting", ra=lst * 15.0, dec=40.0),
        StellarObject(id="past", name="past", ra=(lst - 0.25) * 15.0, dec=40.0),
    ]
    report = build_visibility_report(
        sky, objects, to_astropy_time(time), None, None, frozenset({"meridian"}), 0.0, [], 0.0
    )
    transiting, past = (entry.meridian for entry in report.objects)
    assert transiting.hour_angle_hours == pytest.approx(0.0, abs=1e-3)
    assert not transiting.flip_required
    assert past.hour_angle_hours == pytest.approx(0.25, abs=1e-3)  # 15 minutes past
    assert past.flip_required  # Since 15 mins > 10 mins delay limit


def test_rise_set_transit_marks_circumpolar_and_never_rising_targets() -> None:
    """Verifies rise and set report circumpolar and never-rising targets."""
    sky = SkyEngine(latitude=40.0, longitude=-100.0, elevation=1000.0)
    time = Time(datetime(2026, 6, 21, 22, 0, 0))
    lst = sky.get_local_sidereal_time(time)

    def visibility(dec_deg: float) -> dict:
        """Return rise, set and transit times of a target at RA 180 degrees.

        Returns
        -------
        visibility : `dict`
            The rise_set_transit result.
        """
        alt, _ = compute_altaz(180.0, dec_deg, sky.location, time)
        return rise_set_transit(sky, dec_deg, alt, hour_angle_from_lst(lst, 12.0), time)

    circumpolar = visibility(85.0)
    assert circumpolar["rise_time"] == "Circumpolar"
    assert circumpolar["set_time"] == "Circumpolar"

    never_rises = visibility(-85.0)
    assert never_rises["rise_time"] == "Never Rises"
    assert never_rises["set_time"] == "Never Rises"


def test_query_online_catalogs_passes_the_magnitude_limit_to_every_driver() -> None:
    """A magnitude limit given to the dispatcher reaches each driver."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from wayfindinglib.tasks.planning_tasks.catalog_operations import query_online_catalogs

    driver = MagicMock()
    driver.driver_name = "gaia"
    driver.maximum_query_radius_degrees = 90.0
    driver.query_region.return_value = []
    sky = SimpleNamespace(_catalog_driver_registry={"gaia": driver})

    query_online_catalogs(sky, 250.0, 36.0, 2.0, ["gaia"], magnitude_limit=14.0)

    driver.query_region.assert_called_once_with(250.0, 36.0, 2.0, magnitude_limit=14.0)


def test_astrometrics_catalog_reads_only_the_stars_near_the_search_circle() -> None:
    """Verifies the stars are fetched for the search circle, not all at once.

    Holding every star of a 270,000-star library in memory cost gigabytes
    and got the backend killed, so the library is asked for just the stars
    inside the circle being searched.
    """
    fake_astrometrics = MagicMock()
    fake_astrometrics.targets.list.return_value = []
    fake_astrometrics.stars.query.return_value.objects = []
    fake_sky = MagicMock()
    fake_sky._astrometrics = fake_astrometrics

    astrometrics_catalog(fake_sky, ra_deg=250.17, dec_deg=36.46, radius_deg=1.0)

    fake_astrometrics.stars.query.assert_called_once_with(
        ra_deg=250.17, dec_deg=36.46, radius_deg=1.0, detail="objects", limit=None
    )


def test_resolve_target_coordinates_asks_the_library_for_the_named_star_only() -> None:
    """Verifies a name is looked up in the library, skipping unset positions.

    A star that matches the name but has no position yet (0.0, 0.0) must be
    passed over in favour of a later match that has one, as before.
    """
    from wayfindinglib.tasks.planning_tasks.resolution_operations import resolve_target_coordinates

    unset = StellarObject(id="Vega", name="Vega", ra=0.0, dec=0.0)
    placed = StellarObject(id="* alf Lyr", name="Vega", ra=279.23, dec=38.78)
    fake_sky = MagicMock()
    fake_sky._astrometrics.targets.list.return_value = []
    fake_sky._astrometrics.stars.query.return_value.objects = [unset, placed]

    resolved = resolve_target_coordinates(fake_sky, "Vega")

    assert resolved is placed
    fake_sky._astrometrics.stars.query.assert_called_once_with(name="Vega", detail="objects", limit=None)


def _location_config(**location: str) -> SimpleNamespace:
    """Build a config whose ``app_config`` holds the given location keys.

    Returns
    -------
    config : `types.SimpleNamespace`
        An object with a real `configparser.ConfigParser` as ``app_config``.
    """
    parser = configparser.ConfigParser()
    parser.add_section("Observatory.Location")
    for key, value in location.items():
        parser.set("Observatory.Location", key, value)
    return SimpleNamespace(app_config=parser)


def _sky_with(monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace) -> SkyEngine:
    """Build a `SkyEngine` for a config without the catalog and the library.

    Returns
    -------
    sky : `SkyEngine`
        A planning engine that read its site from the config.
    """
    import astrometricslib
    import wayfindinglib.drivers.catalog as catalog_module
    import wayfindinglib.tasks.planning_tasks.sky_engine as sky_engine_module

    monkeypatch.setattr(astrometricslib, "Astrometrics", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(catalog_module, "LocalDeepStarStore", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(sky_engine_module, "build_catalog_driver_registry", lambda **k: SimpleNamespace())
    return SkyEngine(config=config)


def test_sky_uses_the_configured_site_and_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Planning reads the same site and elevation as the live-status tools."""
    config = _location_config(latitude="45.76", longitude="-110.74")
    sky = _sky_with(monkeypatch, config)
    site = configured_observer_location(config)
    assert (sky.latitude, sky.longitude, sky.elevation) == (
        site["latitude"],
        site["longitude"],
        site["elevation"],
    )
    assert sky.elevation == pytest.approx(0.0)


def test_sky_warns_and_uses_denver_without_a_site(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With no site, planning uses Denver and says so."""
    with caplog.at_level("WARNING"):
        sky = _sky_with(monkeypatch, _location_config())
    assert (sky.latitude, sky.longitude, sky.elevation) == (39.7392, -104.9903, 1600.0)
    assert "No Observatory.Location" in caplog.text
