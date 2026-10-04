"""Tests for the night-planning table.

The table is checked against facts that can be worked out by hand: an object
that crosses the meridian a known time after the start, a blocked range that
removes part of the night, the Sun's dark span, and the request errors.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import astropy.units as u
import pytest
from astropy.coordinates import EarthLocation
from astropy.time import Time

from wayfindinglib.tasks.planning_tasks.visibility_over_time import build_visibility_over_time

START = 1791061200.0  # 2026-10-03T21:00:00Z, about 15:00 in Montana
"""A start time, in Unix seconds, in the afternoon at the test site."""


def _sky() -> SimpleNamespace:
    """Build a stand-in for `Sky` at a Montana site.

    Returns
    -------
    sky : `types.SimpleNamespace`
        An object with the location, sidereal time and flip delay.
    """
    location = EarthLocation(lat=45.76 * u.deg, lon=-110.74 * u.deg, height=0 * u.m)

    def sidereal_hours(time: Time) -> float:
        """Give the local sidereal time in hours.

        Parameters
        ----------
        time : `astropy.time.Time`
            The moment.

        Returns
        -------
        hours : `float`
            Local apparent sidereal time.
        """
        return float(time.sidereal_time("apparent", longitude=location.lon).hour)

    return SimpleNamespace(
        location=location,
        latitude=45.76,
        longitude=-110.74,
        elevation=0.0,
        meridian_flip_delay_min=5.0,
        get_local_sidereal_time=sidereal_hours,
    )


def _object_crossing_after(sky: SimpleNamespace, hours: float) -> SimpleNamespace:
    """Make an object that crosses the meridian a set time after the start.

    Parameters
    ----------
    sky : `types.SimpleNamespace`
        The stand-in sky.
    hours : `float`
        Hours after `START` at which it crosses.

    Returns
    -------
    sky_object : `types.SimpleNamespace`
        An object whose coordinates are text, like a library target.
    """
    sidereal_start = sky.get_local_sidereal_time(Time(START, format="unix"))
    right_ascension_hours = (sidereal_start + hours * 1.0027379) % 24.0
    # A plain number is read as hours of right ascension (like a target).
    return SimpleNamespace(id="X", ra=str(right_ascension_hours), dec="40.0", common_name="X")


def _table(sky: SimpleNamespace, sky_object: SimpleNamespace, **overrides: object) -> dict:
    """Build a 12 hour table with default settings.

    Parameters
    ----------
    sky : `types.SimpleNamespace`
        The stand-in sky.
    sky_object : `types.SimpleNamespace`
        The object to tabulate.
    **overrides : `object`
        Arguments to change from the defaults.

    Returns
    -------
    table : `dict`
        The result of `build_visibility_over_time`.
    """
    arguments = {
        "objects": [sky_object],
        "start_epoch": START,
        "end_epoch": START + 12 * 3600.0,
        "step_minutes": 30.0,
        "minimum_altitude_deg": 0.0,
        "horizon_zones": None,
        "timezone_offset_hours": 0.0,
        "include_samples": True,
    }
    arguments.update(overrides)
    return build_visibility_over_time(sky, **arguments)


def test_the_meridian_crossing_is_found_at_the_expected_time() -> None:
    """An object set to cross 6 hours after the start does so then."""
    sky = _sky()
    entry = _table(sky, _object_crossing_after(sky, 6.0))["objects"][0]
    crossing = datetime.fromisoformat(entry["meridian"][0]["meridian_crossing"])
    expected = datetime(2026, 10, 4, 3, 0, tzinfo=UTC)
    assert abs((crossing - expected).total_seconds()) < 60.0
    flip = datetime.fromisoformat(entry["meridian"][0]["flip_due"])
    assert 290.0 < (flip - crossing).total_seconds() < 310.0


def test_a_blocked_range_removes_the_time_the_object_is_inside_it() -> None:
    """A limit of 89 degrees at every azimuth leaves no clear time."""
    sky = _sky()
    zones = [{"azimuth_start_deg": 0, "azimuth_end_deg": 359.9, "min_clear_altitude_deg": 89}]
    entry = _table(sky, _object_crossing_after(sky, 6.0), horizon_zones=zones)["objects"][0]
    assert entry["clear_of_horizon"] == []
    assert entry["usable"] == []


def test_usable_time_is_inside_astronomical_night() -> None:
    """The usable span starts once the Sun is below -18 degrees."""
    sky = _sky()
    table = _table(sky, _object_crossing_after(sky, 6.0))
    night = table["astronomical_night"][0]
    usable = table["objects"][0]["usable"][0]
    assert usable["from"] >= night["from"]
    assert usable["to"] <= night["to"]
    assert table["objects"][0]["clear_of_horizon"][0]["hours"] > usable["hours"] - 0.01


def test_times_are_written_in_the_requested_offset() -> None:
    """A -6 hour offset shows local times with the offset."""
    sky = _sky()
    table = _table(sky, _object_crossing_after(sky, 6.0), timezone_offset_hours=-6.0)
    assert table["window"]["from"] == "2026-10-03T15:00:00-06:00"


def test_bad_requests_are_errors() -> None:
    """Too many steps, a reversed span, a bad zone or no objects: error."""
    sky = _sky()
    sky_object = _object_crossing_after(sky, 1.0)
    assert "time steps" in _table(sky, sky_object, step_minutes=1.0)["error"]
    assert "before the start" in _table(sky, sky_object, end_epoch=START - 10)["error"]
    assert "horizon zone" in _table(sky, sky_object, horizon_zones=[{"azimuth_start_deg": 1}])["error"]
    assert "at least one" in _table(sky, sky_object, objects=[])["error"]


@pytest.mark.parametrize("include_samples", [True, False])
def test_samples_can_be_left_out(include_samples: bool) -> None:
    """The row-by-row table appears only when asked for."""
    sky = _sky()
    entry = _table(sky, _object_crossing_after(sky, 6.0), include_samples=include_samples)["objects"][0]
    assert ("samples" in entry) is include_samples


def test_one_moment_gives_the_single_time_answer() -> None:
    """End equal to start gives one row and an at_start block."""
    sky = _sky()
    sky_object = _object_crossing_after(sky, 2.0)
    entry = _table(sky, sky_object, end_epoch=START)["objects"][0]
    assert len(entry["samples"]) == 1
    now = entry["at_start"]
    assert now["hour_angle_hours"] == pytest.approx(-2.0, abs=0.01)
    assert now["flip_required"] is False
    assert now["time_to_flip_seconds"] == pytest.approx(7200.0, abs=60.0)


def test_twilight_stages_come_in_order_and_end_where_the_dark_span_starts() -> None:
    """Sunset, then civil, nautical and astronomical twilight, going down."""
    sky = _sky()
    table = _table(sky, _object_crossing_after(sky, 6.0))
    events = table["sun_events"]
    going_down = [event for event in events if event["going"] == "down"]
    assert [event["event"].split("(")[1] for event in going_down] == [
        "sunset or sunrise)",
        "civil twilight)",
        "nautical twilight)",
        "astronomical twilight)",
    ]
    times = [event["time"] for event in going_down]
    assert times == sorted(times)
    last = datetime.fromisoformat(going_down[-1]["time"])
    night_start = datetime.fromisoformat(table["astronomical_night"][0]["from"])
    assert abs((last - night_start).total_seconds()) < 30 * 60
