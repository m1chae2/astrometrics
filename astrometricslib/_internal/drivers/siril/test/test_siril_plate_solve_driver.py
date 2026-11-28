"""Tests for `SirilPlateSolveDriver`, the app's local plate solver.

Siril and the online service are replaced by stand-ins. The tests check that
Siril solves every frame with a start position and nothing else is tried when
it fails, that only a frame with no position goes online, the time limit and
the default driver.
"""

from pathlib import Path
from typing import Any

import numpy as np
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

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        """Fail the test: the old local solver must never run.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("solve-field must not be called")

    monkeypatch.setattr(solve_driver, "solve_image", fake_solve_image)
    monkeypatch.setattr(AstrometryNetPlateSolveDriver, "_solve_locally", fail_if_called)
    monkeypatch.setattr(AstrometryNetPlateSolveDriver, "_solve_online_sources", fake_online_sources)
    monkeypatch.setattr(AstrometryNetPlateSolveDriver, "_solve_online_image", fake_online_image)
    config = AppConfiguration()
    monkeypatch.setattr(config, "get_siril_executable", lambda: "siril-cli")
    return SirilPlateSolveDriver(api_key="key", config=config), record


def write_frame(path: Path, with_position: bool = True) -> str:
    """Write a small frame with or without a header position.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it.
    with_position : `bool`, optional
        Whether the header holds ``RA`` and ``DEC``.

    Returns
    -------
    path : `str`
        The file.
    """
    header = fits.Header({"FOCALLEN": 405.0, "XPIXSZ": 3.76})
    if with_position:
        header["RA"], header["DEC"] = 250.4, 36.4
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32), header).writeto(path)
    return str(path)


def test_a_siril_solution_is_returned_without_going_online(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a solved image never reaches the online service."""
    solved = fits.Header({"CRVAL1": 250.0})
    driver, record = make_driver(monkeypatch, solved)

    header = driver.solve(image_path=write_frame(tmp_path / "a.fits"), sources=[{"x": 1, "y": 2}] * 3)

    assert header is solved
    assert record["siril"][0]["executable"] == "siril-cli"
    assert record["sources"] == [] and record["image"] == []


def test_a_frame_with_a_position_that_siril_fails_is_not_solved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a failed Siril solve is final: no online try, no fallback."""
    driver, record = make_driver(monkeypatch, None)

    header = driver.solve(image_path=write_frame(tmp_path / "a.fits"), sources=[{"x": 1, "y": 2}] * 50)

    assert header is None
    assert len(record["siril"]) == 1
    assert record["sources"] == [] and record["image"] == []


def test_a_frame_with_no_position_goes_online_and_not_to_siril(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a blind frame is solved online from its stars."""
    driver, record = make_driver(monkeypatch, fits.Header({"CRVAL1": 9.0}))
    stars = [{"x_centroid": 1.0, "y_centroid": 2.0}] * 20

    header = driver.solve(
        image_path=write_frame(tmp_path / "b.fits", with_position=False),
        sources=stars,
        image_width=100,
        image_height=100,
    )

    assert header is not None and header["CRVAL1"] == pytest.approx(1.0)
    assert record["siril"] == []
    assert record["sources"] == [stars]


def test_a_blind_frame_falls_back_to_the_whole_image_online(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a blind frame with no stars is uploaded whole."""
    driver, record = make_driver(monkeypatch, None)
    frame = write_frame(tmp_path / "c.fits", with_position=False)

    assert driver.solve(image_path=frame) is not None
    assert record["image"] == [frame]


def test_siril_runs_are_limited_to_its_own_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a long time limit is cut to Siril's and a short one is kept."""
    driver, record = make_driver(monkeypatch, fits.Header({"CRVAL1": 3.0}))
    frame = write_frame(tmp_path / "d.fits")

    driver.solve(image_path=frame, solve_timeout=300)
    driver.solve(image_path=frame, solve_timeout=20)
    driver.solve(image_path=frame)

    limits = [call["timeout_seconds"] for call in record["siril"]]
    assert limits == [SOLVE_TIMEOUT_SECONDS, 20.0, SOLVE_TIMEOUT_SECONDS]


def test_the_default_plate_solver_is_siril() -> None:
    """Verify the driver set builds the Siril driver."""
    assert isinstance(Drivers().plate_solve_or_default(), SirilPlateSolveDriver)
