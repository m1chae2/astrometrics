"""Tests for `SirilPlateSolveDriver`, the app's local plate solver.

Siril and the online service are replaced by stand-ins. The tests check the
order of the attempts (Siril first, then the online service for what Siril
cannot solve), the time limit, and which driver the driver set builds.
"""

from typing import Any

import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.astrometry_net_driver import AstrometryNetPlateSolveDriver
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.drivers.siril.astrometry import solve_driver
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import SOLVE_TIMEOUT_SECONDS
from astrometricslib._internal.drivers.siril.astrometry.solve_driver import SirilPlateSolveDriver
from astrometricslib.foundation.config import AppConfiguration


def make_driver(
    monkeypatch: pytest.MonkeyPatch, siril_answer: fits.Header | None
) -> tuple[SirilPlateSolveDriver, dict[str, Any]]:
    """Build a driver whose Siril and online solves are stand-ins.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the Siril solve and the two online solves.
    siril_answer : `astropy.io.fits.Header` or `None`
        What the Siril solve returns.

    Returns
    -------
    driver : `SirilPlateSolveDriver`
        The driver, with an API key.
    record : `dict`
        What the stand-ins were asked: ``siril`` (the hints of each Siril
        solve), ``sources`` and ``image`` (the online calls).
    """
    record: dict[str, Any] = {"siril": [], "sources": [], "image": []}
    online_header = fits.Header({"CRVAL1": 1.0})

    def fake_solve_image(
        image_path: str, siril_executable: str, hints: dict[str, Any], **options: Any
    ) -> fits.Header | None:
        """Stand in for the Siril solve.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The answer the test chose.
        """
        record["siril"].append({"executable": siril_executable, "hints": hints, **options})
        return siril_answer

    def fake_online_sources(
        self: Any, sources: list[dict[str, Any]], width: int, height: int, **kwargs: Any
    ) -> fits.Header:
        """Stand in for the online solve from star positions.

        Returns
        -------
        header : `astropy.io.fits.Header`
            A fixed header.
        """
        record["sources"].append(sources)
        return online_header

    def fake_online_image(self: Any, image_path: str, **kwargs: Any) -> fits.Header:
        """Stand in for the online solve of the whole image.

        Returns
        -------
        header : `astropy.io.fits.Header`
            A fixed header.
        """
        record["image"].append(image_path)
        return online_header

    monkeypatch.setattr(solve_driver, "solve_image", fake_solve_image)
    monkeypatch.setattr(AstrometryNetPlateSolveDriver, "_solve_online_sources", fake_online_sources)
    monkeypatch.setattr(AstrometryNetPlateSolveDriver, "_solve_online_image", fake_online_image)
    config = AppConfiguration()
    monkeypatch.setattr(config, "get_siril_executable", lambda: "siril-cli")
    return SirilPlateSolveDriver(api_key="key", config=config), record


def test_a_siril_solution_is_returned_without_going_online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a solved image never reaches the online service."""
    solved = fits.Header({"CRVAL1": 250.0})
    driver, record = make_driver(monkeypatch, solved)

    header = driver.solve(
        image_path="frame.fits", sources=[{"x": 1, "y": 2}] * 50, center_ra=250.0, center_dec=36.0
    )

    assert header is solved
    assert record["siril"][0]["executable"] == "siril-cli"
    assert record["siril"][0]["hints"]["center_ra"] == pytest.approx(250.0)
    assert record["sources"] == [] and record["image"] == []


def test_a_frame_siril_cannot_solve_goes_to_the_online_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a frame Siril cannot solve is solved online from its stars."""
    driver, record = make_driver(monkeypatch, None)
    stars = [{"x_centroid": 1.0, "y_centroid": 2.0}] * 20

    header = driver.solve(image_path="blind.fits", sources=stars, image_width=100, image_height=100)

    assert header is not None and header["CRVAL1"] == pytest.approx(1.0)
    assert len(record["siril"]) == 1
    assert record["sources"] == [stars]


def test_siril_is_tried_even_with_few_detected_stars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a low star count does not skip Siril, which finds its own."""
    driver, record = make_driver(monkeypatch, fits.Header({"CRVAL1": 3.0}))

    assert driver.solve(image_path="frame.fits", sources=[{"x": 1, "y": 2}] * 3) is not None
    assert len(record["siril"]) == 1


def test_siril_runs_are_limited_to_its_own_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a long time limit is cut to Siril's and a short one is kept."""
    driver, record = make_driver(monkeypatch, fits.Header({"CRVAL1": 3.0}))

    driver.solve(image_path="a.fits", solve_timeout=300)
    driver.solve(image_path="b.fits", solve_timeout=20)
    driver.solve(image_path="c.fits")

    assert [call["timeout_seconds"] for call in record["siril"]] == [
        SOLVE_TIMEOUT_SECONDS,
        20.0,
        SOLVE_TIMEOUT_SECONDS,
    ]


def test_the_driver_set_builds_siril_only_when_it_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the default is Siril when ready, else solve-field."""
    monkeypatch.setattr(solve_driver, "siril_is_ready", lambda config=None: True)
    assert isinstance(Drivers().plate_solve_or_default(), SirilPlateSolveDriver)

    monkeypatch.setattr(solve_driver, "siril_is_ready", lambda config=None: False)
    solver = Drivers().plate_solve_or_default()
    assert type(solver) is AstrometryNetPlateSolveDriver
