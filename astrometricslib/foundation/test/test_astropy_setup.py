"""Tests for the offline Earth-rotation (IERS) setup in `astropy_setup`.

Astropy must never download its Earth-rotation table while a program runs.
By default the first altitude/azimuth conversion of a process downloaded it,
which stalled the first star click for 6 to 14 seconds and quietly needed the
internet. These tests check that importing either library turns the download
off, that the helper is safe to call again, and that conversions still work
for past and future dates without the download.
"""

import subprocess
import sys
import threading
import time

import astropy.units as u
import pytest
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
from astropy.utils import iers

import astrometricslib
from astrometricslib import Astrometrics
from astrometricslib.foundation import astropy_setup
from astrometricslib.foundation.astropy_setup import configure_offline_iers, warm_earth_orientation_data


@pytest.mark.parametrize("library", ["astrometricslib", "wayfindinglib"])
def test_importing_a_library_turns_the_download_off(library: str) -> None:
    """Importing either library in a fresh program leaves astropy offline.

    A fresh interpreter is used because the test process has already
    imported both libraries.
    """
    script = (
        f"import {library}\n"
        "from astropy.utils import iers\n"
        "print(iers.conf.auto_download, iers.conf.auto_max_age)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)

    assert result.stdout.strip().splitlines()[-1] == "False None"


def test_configure_offline_iers_is_safe_to_repeat() -> None:
    """Calling the helper twice gives the same settings."""
    configure_offline_iers()
    configure_offline_iers()

    assert iers.conf.auto_download is False
    assert iers.conf.auto_max_age is None


def test_altitude_conversion_works_for_past_and_future_dates_offline() -> None:
    """With the download off, no date from the past or the future raises."""
    configure_offline_iers()
    location = EarthLocation(lat=45.68 * u.deg, lon=-111.04 * u.deg, height=1500 * u.m)
    star = SkyCoord(315.7319 * u.deg, 68.7115 * u.deg)

    for date in ("2024-09-18T21:00:00", "2026-09-18T21:00:00", "2036-09-18T21:00:00"):
        altaz = star.transform_to(AltAz(obstime=Time(date, scale="utc"), location=location))
        assert -90.0 <= altaz.alt.deg <= 90.0


def test_warm_earth_orientation_data_runs_a_real_conversion() -> None:
    """The warm-up does one real conversion offline and reports its time."""
    elapsed = warm_earth_orientation_data()

    assert elapsed >= 0.0
    assert iers.conf.auto_download is False


def test_creating_astrometrics_starts_the_warmup_once_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Astrometrics(...)` starts one background warm-up, once.

    The patched warm-up is slow. The constructor must still return
    at once, and a second object must not start a second thread.
    """
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow_warmup() -> float:
        """Record the call, wait until released, then return.

        Returns
        -------
        float
            Always zero.
        """
        calls.append(1)
        started.set()
        release.wait(5.0)
        return 0.0

    monkeypatch.setattr(astropy_setup, "_warmup_started", False)
    monkeypatch.setattr(astropy_setup, "warm_earth_orientation_data", slow_warmup)

    began = time.monotonic()
    Astrometrics()
    Astrometrics()
    elapsed = time.monotonic() - began

    assert started.wait(5.0)
    assert elapsed < 4.0
    assert calls == [1]
    release.set()
    for thread in threading.enumerate():
        if thread.name == "earth-orientation-warmup":
            thread.join(5.0)
    assert not any(t.name == "earth-orientation-warmup" for t in threading.enumerate())


def test_a_failing_warmup_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A warm-up that fails inside its thread never raises."""

    def broken() -> float:
        """Stand in for a warm-up that cannot read the table.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("table unreadable")

    monkeypatch.setattr(astropy_setup, "warm_earth_orientation_data", broken)

    astropy_setup._run_earth_orientation_warmup()


@pytest.mark.parametrize("name", ["configure_offline_iers", "warm_earth_orientation_data"])
def test_the_old_public_names_raise_an_import_error_that_says_they_are_not_needed(name: str) -> None:
    """Importing a removed name explains that nothing replaces it."""
    with pytest.raises(ImportError, match="not needed"):
        getattr(astrometricslib, name)
