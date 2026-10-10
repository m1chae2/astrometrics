"""Purpose: Test the mid-exposure BJD_TDB times (review item S9).

Description: BJD_TDB is the exposure's middle, on a uniform time scale, as
seen from the solar system barycenter (the center of mass of the solar
system). The checks here do not repeat the code's formula. They use facts
that can be worked out separately:

* The middle of an exposure is half the exposure time after its start.
* TDB runs ahead of UTC by the 37 leap seconds plus 32.184 s, which is
  69.184 s in 2026. A target at the north ecliptic pole is at right angles to
  Earth's orbit, so its barycentric delay is only a few seconds at most.
* Six months move Earth to the far side of its orbit, 2 AU away. For a
  target in the direction of the Sun on the first date, the light-travel
  time changes by 2 AU / c = 16.6 minutes between the two dates.
"""

from datetime import datetime

import numpy as np
import pytest
from astropy import constants
from astropy.coordinates import get_sun
from astropy.time import Time

from astrometricslib.foundation.observatory_site import ObservatorySite
from astrometricslib.pipelines.photometry.pre_processing.observation_times import (
    TIME_BASIS_BJD_TDB,
    TIME_BASIS_BJD_TDB_GEOCENTRIC,
    barycentric_julian_dates,
    mid_exposure_times,
    time_basis_for,
)

SECONDS_PER_DAY = 86400.0
# Between TDB and UTC: 37 leap seconds (TAI - UTC since 2017) plus the fixed
# 32.184 s between TT and TAI.
TDB_MINUS_UTC_SECONDS = 37.0 + 32.184
KECK_LIKE_SITE = ObservatorySite(latitude_deg=19.8, longitude_deg=-155.5, elevation_m=4150.0)
# The north ecliptic pole, in the sky's ICRS coordinates.
ECLIPTIC_POLE_RA_DEG = 270.0
ECLIPTIC_POLE_DEC_DEG = 66.5607
FIRST_DATE = datetime(2026, 1, 3, 4, 0, 0)
SIX_MONTHS_LATER = datetime(2026, 7, 4, 4, 0, 0)


def test_the_middle_of_an_exposure_is_half_the_exposure_after_the_start() -> None:
    """Each exposure is shifted by half its length, to 1 ms."""
    starts = [FIRST_DATE, FIRST_DATE, FIRST_DATE]
    exposures = [30.0, 300.0, 1800.0]

    middles = mid_exposure_times(starts, exposures)
    shifts_s = (middles - Time(starts, scale="utc")).sec

    assert shifts_s == pytest.approx([15.0, 150.0, 900.0], abs=1e-3)


def test_the_bjd_shift_for_a_pole_target_equals_half_the_exposure() -> None:
    """A longer exposure moves the BJD_TDB value by half the extra exposure.

    The target is at the ecliptic pole, where Earth's motion does not change
    the light-travel time, so the whole difference is the half exposure.
    """
    short = barycentric_julian_dates(
        [FIRST_DATE], [0.0], ECLIPTIC_POLE_RA_DEG, ECLIPTIC_POLE_DEC_DEG, KECK_LIKE_SITE
    )[0]
    long = barycentric_julian_dates(
        [FIRST_DATE], [300.0], ECLIPTIC_POLE_RA_DEG, ECLIPTIC_POLE_DEC_DEG, KECK_LIKE_SITE
    )[0]

    assert (long - short) * SECONDS_PER_DAY == pytest.approx(150.0, abs=1e-3)


def test_tdb_runs_69_seconds_ahead_of_utc_for_a_target_at_the_ecliptic_pole() -> None:
    """BJD_TDB minus the UTC Julian Date is 69.184 s plus a few seconds."""
    bjd = barycentric_julian_dates(
        [FIRST_DATE], [0.0], ECLIPTIC_POLE_RA_DEG, ECLIPTIC_POLE_DEC_DEG, KECK_LIKE_SITE
    )[0]

    difference_s = (bjd - Time(FIRST_DATE, scale="utc").jd) * SECONDS_PER_DAY

    assert difference_s == pytest.approx(TDB_MINUS_UTC_SECONDS, abs=5.0)


def test_six_months_apart_the_light_travel_time_changes_by_two_au_over_c() -> None:
    """The delay swings by 16.6 minutes for a target toward the Sun.

    The target is placed in the direction of the Sun on the first date. Earth
    is then on the near side of the barycenter on one date and on the far
    side six months later, so the two corrections differ by the diameter of
    Earth's orbit divided by the speed of light. The orbit is slightly
    elliptical, so the check allows 3 percent.
    """
    sun = get_sun(Time(FIRST_DATE, scale="utc"))
    two_au_over_c_s = float((2.0 * constants.au / constants.c).to("s").value)

    corrections_s = []
    for date in (FIRST_DATE, SIX_MONTHS_LATER):
        bjd = barycentric_julian_dates([date], [0.0], sun.ra.deg, sun.dec.deg, KECK_LIKE_SITE)[0]
        utc_jd = Time(date, scale="utc").jd
        corrections_s.append((bjd - utc_jd) * SECONDS_PER_DAY - TDB_MINUS_UTC_SECONDS)

    assert two_au_over_c_s == pytest.approx(996.0, abs=3.0)
    assert abs(corrections_s[0] - corrections_s[1]) == pytest.approx(two_au_over_c_s, rel=0.03)
    # The two corrections have opposite signs: Earth moved to the far side.
    assert corrections_s[0] * corrections_s[1] < 0


def test_the_observer_position_changes_the_time_by_less_than_25_ms() -> None:
    """Leaving the site out shifts a value by under Earth's radius over c."""
    with_site = barycentric_julian_dates([FIRST_DATE], [30.0], 150.0, 2.0, KECK_LIKE_SITE)[0]
    without_site = barycentric_julian_dates([FIRST_DATE], [30.0], 150.0, 2.0, None)[0]

    assert abs(with_site - without_site) * SECONDS_PER_DAY < 0.025


def test_several_frames_are_converted_in_one_call_in_order() -> None:
    """Three frames a minute apart come back as three increasing values."""
    starts = [datetime(2026, 5, 24, 4, minute, 0) for minute in (0, 1, 2)]

    values = barycentric_julian_dates(starts, [30.0] * 3, 250.0, 36.0, KECK_LIKE_SITE)

    assert values.shape == (3,)
    assert np.all(np.diff(values) > 0)
    assert np.diff(values) * SECONDS_PER_DAY == pytest.approx([60.0, 60.0], abs=0.05)


def test_the_time_basis_names_the_site_choice() -> None:
    """A site gives the plain basis; no site gives the geocentric one."""
    assert time_basis_for(KECK_LIKE_SITE) == TIME_BASIS_BJD_TDB
    assert time_basis_for(None) == TIME_BASIS_BJD_TDB_GEOCENTRIC
    assert TIME_BASIS_BJD_TDB == "BJD_TDB, mid-exposure"
