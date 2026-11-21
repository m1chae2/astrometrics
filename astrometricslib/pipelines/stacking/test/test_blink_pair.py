"""Tests for the blink check: reference star, typical frame and star mapping.

The frames are drawn: a field of Gaussian stars on noise. One copy is moved a
few pixels, as a frame that drifted, and one is turned half a revolution, as a
frame after a meridian flip.
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.stacking.pre_processing import blink_pair
from astrometricslib.pipelines.stacking.pre_processing.blink_pair import (
    choose_blink_pair,
    choose_reference_stars,
    choose_typical_path,
    frame_record_for_set_aside,
    match_frame_positions,
)

HEIGHT, WIDTH = 600, 800
SATURATION_ADU = 60000.0


def draw_field(stars: list[tuple[float, float, float]], seed: int = 1) -> np.ndarray:
    """Draw Gaussian stars of 2 pixels sigma on noise.

    Parameters
    ----------
    stars : `list` [`tuple`]
        ``(x, y, peak)`` of each star, 0-based pixels.
    seed : `int`, optional
        The noise seed.

    Returns
    -------
    image : `numpy.ndarray`
        The frame, 600 rows by 800 columns.
    """
    generator = np.random.default_rng(seed)
    image = generator.normal(1000.0, 8.0, size=(HEIGHT, WIDTH))
    rows, columns = np.mgrid[0:HEIGHT, 0:WIDTH]
    for x, y, peak in stars:
        window = (abs(rows - y) < 15) & (abs(columns - x) < 15)
        image += np.where(window, peak * np.exp(-((rows - y) ** 2 + (columns - x) ** 2) / (2 * 2.0**2)), 0.0)
    return image


def random_stars(count: int = 90, seed: int = 3) -> list[tuple[float, float, float]]:
    """Make star positions that keep apart from each other and from the edge.

    Returns
    -------
    stars : `list` [`tuple`]
        ``(x, y, peak)`` for each star.
    """
    generator = np.random.default_rng(seed)
    stars: list[tuple[float, float, float]] = []
    while len(stars) < count:
        x, y = generator.uniform(60, WIDTH - 60), generator.uniform(60, HEIGHT - 60)
        if all(np.hypot(x - other_x, y - other_y) > 40 for other_x, other_y, _ in stars):
            stars.append((float(x), float(y), float(generator.uniform(3000, 20000))))
    return stars


def write_frame(path: Path, image: np.ndarray) -> str:
    """Write an image as a FITS file.

    Returns
    -------
    path : `str`
        The file's path.
    """
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path)
    return str(path)


def test_the_reference_star_is_isolated_unsaturated_and_near_the_center() -> None:
    """A close pair and a saturated star are skipped; the pick is central."""
    center = (WIDTH / 2, HEIGHT / 2)
    stars = [
        (center[0] + 3, center[1], 90000.0),  # saturated, at the center
        (center[0] + 60, center[1] + 40, 15000.0),  # the one to choose
        (center[0] - 100, center[1], 15000.0),  # a close pair
        (center[0] - 112, center[1] + 2, 15000.0),
        (100.0, 100.0, 15000.0),  # good but far from the center
    ]
    image = np.minimum(draw_field(stars), 65000.0)

    chosen = choose_reference_stars(image, SATURATION_ADU)

    assert chosen, "no star was chosen"
    x, y = chosen[0]
    assert (x, y) == pytest.approx((center[0] + 60, center[1] + 40), abs=1.0)
    for pair_x in (center[0] - 100, center[0] - 112):
        assert all(abs(x - pair_x) > 3 for x, _ in chosen)
    assert all(abs(x - (center[0] + 3)) > 3 for x, _ in chosen)


def test_no_star_gives_an_empty_list() -> None:
    """A frame of noise has no reference star."""
    assert choose_reference_stars(draw_field([]), SATURATION_ADU) == []


def test_the_typical_frame_is_nearest_the_session_medians() -> None:
    """The most ordinary frame wins; the examined frame is skipped."""
    values = {
        "a": (1.10, 0.010),
        "b": (1.12, 0.011),
        "c": (1.11, 0.010),
        "bad": (1.60, 0.050),
        "far": (1.30, 0.020),
    }
    assert choose_typical_path(values, excluded_path="c") in ("a", "b")
    assert choose_typical_path(values) == "b"
    assert choose_typical_path({"x": (None, None)}) is None
    assert choose_typical_path({"a": (1.1, None), "b": (1.3, None), "c": (1.12, None)}) == "c"


def test_a_drifted_frame_maps_the_star_to_its_new_place(tmp_path: Path) -> None:
    """A frame moved by (6, -4) pixels puts the star 6 right and 4 up."""
    stars = random_stars()
    first = write_frame(tmp_path / "first.fits", draw_field(stars, seed=1))
    moved = [(x + 6.0, y - 4.0, peak) for x, y, peak in stars]
    second = write_frame(tmp_path / "second.fits", draw_field(moved, seed=2))

    match = match_frame_positions(first, second, [(stars[0][0] + 1, stars[0][1] + 1)])

    assert match.matched and not match.turned
    point = match.points[0]
    assert (point.mapped_x, point.mapped_y) == pytest.approx(
        (stars[0][0] + 1 + 6.0, stars[0][1] + 1 - 4.0), abs=1.0
    )


def test_a_frame_turned_half_a_revolution_maps_the_star_through_the_center(tmp_path: Path) -> None:
    """After a meridian flip the star is at the opposite place plus drift."""
    stars = random_stars()
    first_image = draw_field(stars, seed=1)
    first = write_frame(tmp_path / "first.fits", first_image)
    turned_stars = [(WIDTH - 1 - x + 5.0, HEIGHT - 1 - y + 3.0, peak) for x, y, peak in stars]
    second = write_frame(tmp_path / "second.fits", draw_field(turned_stars, seed=2))
    star_x, star_y = stars[1][0], stars[1][1]

    match = match_frame_positions(first, second, [(star_x + 1, star_y + 1)])

    assert match.matched and match.turned
    point = match.points[0]
    assert point.inside
    assert (point.mapped_x - 1, point.mapped_y - 1) == pytest.approx(
        (WIDTH - 1 - star_x + 5.0, HEIGHT - 1 - star_y + 3.0), abs=1.0
    )


def test_frames_with_different_stars_do_not_match(tmp_path: Path) -> None:
    """Two unrelated fields give no map, and the answer says so."""
    first = write_frame(tmp_path / "first.fits", draw_field(random_stars(seed=3), seed=1))
    second = write_frame(tmp_path / "second.fits", draw_field(random_stars(seed=9), seed=2))

    match = match_frame_positions(first, second, [(100.0, 100.0)])

    assert not match.matched and match.points == [] and match.reason


def make_record(path: str, timestamp: float, exposure: str = "60.0") -> FrameRecord:
    """Make a light frame record.

    Returns
    -------
    frame : `FrameRecord`
        The record.
    """
    return FrameRecord.model_validate({
        "path": path,
        "timestamp": timestamp,
        "exposure": exposure,
        "camera": "Cam",
    })


def test_the_pair_uses_the_session_of_the_frame_and_the_star_in_both_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flipped frame gets a typical frame of its session and the star."""
    stars = random_stars()
    bad = write_frame(tmp_path / "bad.fits", draw_field(stars, seed=1))
    turned = [(WIDTH - 1 - x + 4.0, HEIGHT - 1 - y + 2.0, peak) for x, y, peak in stars]
    typical = write_frame(tmp_path / "typical.fits", draw_field(turned, seed=2))
    other_night = write_frame(tmp_path / "other_night.fits", draw_field(turned, seed=3))
    edgy = write_frame(tmp_path / "edgy.fits", draw_field(turned, seed=4))
    usual = write_frame(tmp_path / "usual.fits", draw_field(turned, seed=5))
    frames = [
        make_record(typical, 1000.0),
        make_record(edgy, 1060.0),
        make_record(usual, 1090.0),
        make_record(other_night, 1000.0 + 3 * 86400.0),
        make_record(bad, 1120.0),
    ]
    numbers = {
        "elongation": {
            "T/typical.fits": 1.10,
            "T/usual.fits": 1.12,
            "T/edgy.fits": 1.40,
            "T/other_night.fits": 1.10,
        },
        "unevenness": {
            "T/typical.fits": 0.010,
            "T/usual.fits": 0.012,
            "T/edgy.fits": 0.030,
            "T/other_night.fits": 0.010,
        },
    }

    def stored(target_id: str, metric: str) -> dict[str, float]:
        """Give the made-up stored values of a metric.

        Returns
        -------
        values : `dict` [`str`, `float`]
            By subject id.
        """
        return numbers["elongation" if "elongation" in metric else "unevenness"]

    monkeypatch.setattr(blink_pair, "stored_frame_values", stored)
    monkeypatch.setattr(blink_pair, "frame_subject_id", lambda path, target_id: f"T/{Path(path).name}")

    pair = choose_blink_pair("T", bad, frames)

    assert pair.found, pair.reason
    assert pair.typical_path == usual
    assert pair.turned
    assert (pair.width, pair.height) == (WIDTH, HEIGHT)
    assert pair.session_frame_count == 3
    assert pair.star_x is not None and pair.typical_star_x is not None and pair.typical_star_y is not None
    assert pair.star_y is not None
    assert (pair.typical_star_x - 1, pair.typical_star_y - 1) == pytest.approx(
        (WIDTH - 1 - (pair.star_x - 1) + 4.0, HEIGHT - 1 - (pair.star_y - 1) + 2.0), abs=1.0
    )


def test_a_frame_alone_in_its_session_says_why(tmp_path: Path) -> None:
    """With no other frame in the stack the answer is a reason."""
    only = write_frame(tmp_path / "only.fits", draw_field(random_stars()))

    pair = choose_blink_pair("T", only, [make_record(only, 0.0)])

    assert not pair.found and "No other frame" in pair.reason


def test_a_set_aside_frame_takes_the_setup_of_a_frame_in_the_stack() -> None:
    """The record copies the stack's setup spelling and takes its own time."""
    lights = [make_record("/x/a.fits", 0.0, exposure="60")]

    record = frame_record_for_set_aside(lights, "/x/_excluded/b.fits", "Cam", "", 60.0, "2026-10-03 08:15:30")

    assert record is not None
    assert record.exposure == "60" and record.path == "/x/_excluded/b.fits"
    assert record.timestamp == pytest.approx(datetime(2026, 10, 3, 8, 15, 30, tzinfo=UTC).timestamp())
    assert frame_record_for_set_aside(lights, "/x/c.fits", "Cam", "", 30.0, "2026-10-03 08:15:30") is None
    assert frame_record_for_set_aside(lights, "/x/c.fits", "Cam", "", 60.0, "not a date") is None
