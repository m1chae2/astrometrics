"""Unit tests for the Sky coordinate and visibility calculations.

Defines mathematical correctness verification tests.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import astropy.units as u
import pytest
from astropy.coordinates import EarthLocation
from astropy.time import Time

from astrometricslib import StellarObject, Target
from wayfindinglib.sky import Sky
from wayfindinglib.skylib.catalog_operations import astrometrics_catalog
from wayfindinglib.skylib.coordinate_operations import compute_altaz


def test_sky_initialization() -> None:
    """Verifies Sky initializes coordinates correctly."""
    # Denver defaults
    sky = Sky()
    assert pytest.approx(sky.latitude) == 39.7392
    assert pytest.approx(sky.longitude) == -104.9903
    assert pytest.approx(sky.elevation) == 1600.0


def test_local_sidereal_time() -> None:
    """Verifies Local Sidereal Time calculations."""
    sky = Sky(latitude=39.7392, longitude=-104.9903, elevation=1600.0)

    # Vernal equinox midnight
    time = datetime(2026, 3, 20, 0, 0, 0)
    lst = sky.get_local_sidereal_time(time)
    assert 0.0 <= lst <= 24.0


def test_coordinate_projections() -> None:
    """Verifies bidirectional conversions between RA/Dec and Alt/Az."""
    sky = Sky(latitude=45.0, longitude=-90.0, elevation=100.0)
    time = datetime(2026, 6, 21, 22, 0, 0)

    # Define a test source (RA=10h, Dec=45°)
    ra, dec = 150.0, 45.0
    alt, az = sky.radec_to_altaz(ra, dec, time)

    # Convert back
    ra_rec, dec_rec = sky.altaz_to_radec(alt, az, time)
    assert pytest.approx(ra, abs=1e-5) == ra_rec
    assert pytest.approx(dec, abs=1e-5) == dec_rec


def test_tracking_rates() -> None:
    """Verifies tracking rates are calculated as valid floats."""
    sky = Sky(latitude=45.0, longitude=-90.0, elevation=100.0)
    time = datetime(2026, 6, 21, 22, 0, 0)

    rates = sky.get_tracking_rates(150.0, 45.0, time)
    assert "alt_rate" in rates
    assert "az_rate" in rates
    assert isinstance(rates["alt_rate"], float)
    assert isinstance(rates["az_rate"], float)


@patch("wayfindinglib.skylib.catalog_operations.astrometrics_catalog")
@patch("wayfindinglib.skylib.catalog_operations.global_catalog")
def test_get_sources(mock_global, mock_local) -> None:  # ruff: ignore[missing-type-function-argument]
    """Verifies regional source query delegation."""
    sky = Sky()

    star = StellarObject(id="S1", name="Star 1", ra=12.0, dec=34.0)
    target = Target(id="T1", commonName="Target 1", ra="12h", dec="34d")

    mock_local.return_value = [target]
    mock_global.return_value = [star]

    # Mock local Astrometrics instance on Sky
    sky._astrometrics = MagicMock()
    sky._astrometrics.targets.list.return_value = [target]
    sky._astrometrics.stellar_objects = []

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
    fake_astrometrics.stellar_objects = []

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
    fake_astrometrics.stellar_objects = []

    fake_sky = MagicMock()
    fake_sky._astrometrics = fake_astrometrics

    results = astrometrics_catalog(fake_sky, ra_deg=250.17, dec_deg=36.46, radius_deg=1.0)

    assert len(results) == 1
    assert results[0].id == "GOOD"


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
    sky = Sky(latitude=40.0, longitude=-100.0, elevation=1000.0)
    sky.meridian_flip_delay_min = 10.0  # 10 minutes delay

    # Current Sidereal Time
    time = datetime(2026, 6, 21, 22, 0, 0)
    lst = sky.get_local_sidereal_time(time)

    # Target transiting (RA = LST)
    ra_transiting = lst * 15.0
    status_transiting = sky.get_meridian_status(ra_transiting, 40.0, time)
    assert pytest.approx(status_transiting["hour_angle"], abs=1e-3) == 0.0
    assert not status_transiting["flip_required"]

    # Target past meridian (RA = LST - 15 minutes / 3.75 degrees)
    ra_past = (lst - 0.25) * 15.0
    status_past = sky.get_meridian_status(ra_past, 40.0, time)
    assert pytest.approx(status_past["hour_angle"], abs=1e-3) == 0.25  # 15 minutes past
    assert status_past["flip_required"]  # Since 15 mins > 10 mins delay limit


def test_object_visibility() -> None:
    """Verifies rise, set, and transit estimations."""
    sky = Sky(latitude=40.0, longitude=-100.0, elevation=1000.0)

    time = datetime(2026, 6, 21, 22, 0, 0)

    # Test circumpolar target (high declination)
    vis_circumpolar = sky.get_object_visibility(180.0, 85.0, time)
    assert vis_circumpolar["rise_time"] == "Circumpolar"
    assert vis_circumpolar["set_time"] == "Circumpolar"

    # Test never-rises target (opposite hemisphere declination)
    vis_subhorizon = sky.get_object_visibility(180.0, -85.0, time)
    assert vis_subhorizon["rise_time"] == "Never Rises"
    assert vis_subhorizon["set_time"] == "Never Rises"
