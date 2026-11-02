"""Tests for the per-aperture record and the line pattern (D21)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from astrometricslib.pipelines.stacking.post_processing.odd_even_apertures import (
    APERTURE_RECORD_SUFFIX,
    LinePatternOutcome,
    build_aperture_records,
    load_odd_even_aperture_record,
    measure_line_pattern,
    odd_even_apertures_path,
    save_odd_even_aperture_record,
)

SIZE = 600


def test_the_record_joins_position_distances_and_both_values() -> None:
    """Each usable aperture gets its place, distances and two values."""
    records = build_aperture_records(
        pixel_positions=((100.0, 200.0), (299.5, 299.5), (10.0, 10.0), (50.0, 60.0)),
        sum_values=(1.0, 2.0, np.nan, 4.0),
        difference_values=(0.5, 0.25, 3.0, np.nan),
        image_shape=(600, 600),
        target_pixel=(299.5, 299.5),
    )

    # The apertures with a NaN sum or difference are dropped.
    assert [record.index for record in records] == [0, 1]
    first, center = records
    assert (first.x, first.y) == (100.0, 200.0)
    assert (first.sum_value, first.difference_value) == (1.0, 0.5)
    assert first.distance_from_center_px == pytest.approx(math.hypot(100 - 299.5, 200 - 299.5))
    assert first.distance_from_target_px == pytest.approx(first.distance_from_center_px)
    assert center.distance_from_center_px == pytest.approx(0.0)
    assert center.distance_from_target_px == pytest.approx(0.0)


def test_without_a_target_position_the_target_distance_is_none() -> None:
    """A target of unknown position leaves its distance empty, not zero."""
    (record,) = build_aperture_records(((10.0, 20.0),), (1.0,), (2.0,), (100, 100), None)

    assert record.distance_from_target_px is None


def test_the_record_file_round_trips_and_sits_beside_the_stack(tmp_path: Path) -> None:
    """The file is named for the stack and reads back as written."""
    stack = str(tmp_path / "M99_Stacked.fits")
    records = build_aperture_records(((10.0, 20.0), (30.0, 40.0)), (1.0, 2.0), (3.0, 4.0), (100, 100), None)

    path = save_odd_even_aperture_record(stack, records, "v3-abc", "a note")

    assert path == odd_even_apertures_path(stack) == str(tmp_path / ("M99_Stacked" + APERTURE_RECORD_SUFFIX))
    data = load_odd_even_aperture_record(path)
    assert data is not None
    assert data["aperture_identity"] == "v3-abc" and data["note"] == "a note"
    assert [entry["sum"] for entry in data["apertures"]] == [1.0, 2.0]
    assert [entry["difference"] for entry in data["apertures"]] == [3.0, 4.0]
    assert data["apertures"][0]["distance_from_target_px"] is None


def test_a_missing_unreadable_or_other_version_record_reads_as_none(tmp_path: Path) -> None:
    """The reader never raises on a bad file."""
    assert load_odd_even_aperture_record(str(tmp_path / "none.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert load_odd_even_aperture_record(str(bad)) is None
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"version": 99, "apertures": []}))
    assert load_odd_even_aperture_record(str(other)) is None


def test_a_write_failure_is_logged_and_gives_none(tmp_path: Path) -> None:
    """A folder that does not exist loses the record, not the caller."""
    stack = str(tmp_path / "missing_folder" / "M99_Stacked.fits")

    assert save_odd_even_aperture_record(stack, [], "v3-abc", "note") is None


def _halves(seed: int, band: np.ndarray | None = None, axis: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Build two half stacks of white noise that may share a band pattern.

    Parameters
    ----------
    seed : `int`
        Seed of the noise.
    band : `numpy.ndarray`, optional
        One offset for each row (``axis`` 1) or column (``axis`` 0), added to
        both halves.
    axis : `int`, optional
        1 for a row pattern, 0 for a column pattern.

    Returns
    -------
    odd, even : `numpy.ndarray`
        The two halves, with a sky level of 100 so no pixel is exactly zero.
    """
    generator = np.random.default_rng(seed)
    images = [100.0 + generator.normal(0.0, 1.0, (SIZE, SIZE)) for _ in range(2)]
    if band is not None:
        shift = band[:, None] if axis == 1 else band[None, :]
        images = [image + shift for image in images]
    return images[0], images[1]


def test_white_noise_gives_equal_scatter_in_the_sum_and_the_difference() -> None:
    """Without a shared band pattern the two images scatter alike."""
    odd, even = _halves(1)

    outcome = measure_line_pattern(odd, even)

    assert outcome.result is not None
    line = outcome.result
    # A mean of 600 unit pixels scatters by 1 / sqrt(600); the images have
    # sqrt(2) times that.
    expected = math.sqrt(2.0) / math.sqrt(SIZE)
    for value in (line.difference_row, line.difference_column, line.sum_row, line.sum_column):
        assert value == pytest.approx(expected, rel=0.25)
    assert line.row_count > 500 and line.column_count > 500
    assert line.error(line.sum_row, line.row_count) == pytest.approx(
        1.1664 * line.sum_row / math.sqrt(line.row_count)
    )


@pytest.mark.parametrize("axis", [0, 1])
def test_a_band_pattern_both_halves_share_shows_in_the_sum_only(axis: int) -> None:
    """Banding along rows (or columns) stays in A + B and cancels in A - B."""
    band = np.random.default_rng(5).normal(0.0, 0.3, SIZE)
    odd, even = _halves(2, band, axis)

    line = measure_line_pattern(odd, even).result

    assert line is not None
    banded_sum, banded_difference, other_sum = (
        (line.sum_row, line.difference_row, line.sum_column)
        if axis == 1
        else (line.sum_column, line.difference_column, line.sum_row)
    )
    # The shared pattern (2 x 0.3 in the sum) is far above the white-noise
    # floor of about 0.06.
    assert banded_sum > 5 * banded_difference
    assert banded_difference == pytest.approx(math.sqrt(2.0) / math.sqrt(SIZE), rel=0.3)
    # The other direction sees no band.
    assert other_sum == pytest.approx(math.sqrt(2.0) / math.sqrt(SIZE), rel=0.3)


def test_a_smooth_gradient_is_not_counted_as_line_noise() -> None:
    """A slow trend is taken away before the scatter is measured."""
    gradient = np.linspace(0.0, 30.0, SIZE)
    odd, even = _halves(3, gradient, axis=1)

    line = measure_line_pattern(odd, even).result

    assert line is not None
    assert line.sum_row == pytest.approx(math.sqrt(2.0) / math.sqrt(SIZE), rel=0.3)


def test_lines_mostly_masked_give_a_reason_not_a_number() -> None:
    """With too few usable pixels in too many lines there is no scatter."""
    odd, even = _halves(4)
    odd[:, 50:] = 0.0  # only 50 usable pixels in every row
    odd[50:, :] = 0.0

    outcome = measure_line_pattern(odd, even)

    assert outcome.result is None
    assert "image lines hold" in outcome.reason


def test_halves_on_different_grids_are_refused() -> None:
    """The line pattern needs one pixel grid."""
    odd, _ = _halves(5)

    outcome = measure_line_pattern(odd, odd[:300])

    assert outcome == LinePatternOutcome(None, "the two half stacks are on different pixel grids")
