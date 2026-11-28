"""Purpose: Unit tests for the position map that moves a group stack.

Description: A group stack is moved onto the common grid either by a shift or
by the sky-based move Siril makes. `GroupPositionMap` describes both the same
way, and three jobs use it: resampling the group's rejection map, counting the
frames that cover each pixel of the combined stack, and reporting the move as a
shift, a turn and a scale. These tests build maps from shifts and from two
made-up plate solutions and check each job.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.pipelines.stacking.post_processing import stack_crop
from astrometricslib.pipelines.stacking.processing.group_alignment import (
    AlignmentResult,
    apply_alignment,
)
from astrometricslib.pipelines.stacking.processing.group_file_merging import merge_rejection_maps
from astrometricslib.pipelines.stacking.processing.group_position_map import GroupPositionMap
from astrometricslib.pipelines.stacking.processing.image_registration import resample_at_positions
from astrometricslib.pipelines.stacking.stack_runner import alignment_left_out_reason

SIZE = 200
IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def make_wcs(
    center_column: float, center_row: float, rotation_degrees: float = 0.0, scale_factor: float = 1.0
) -> WCS:
    """Make a tangent-plane sky map whose pixel (0, 0) is at the origin.

    Parameters
    ----------
    center_column, center_row : `float`
        The pixel (counted from 0) that holds the sky position (10, 20).
    rotation_degrees : `float`, optional
        How far the pixel grid is turned on the sky.
    scale_factor : `float`, optional
        The pixel size as a multiple of 1 arcsecond.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The sky map.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [10.0, 20.0]
    wcs.wcs.crpix = [center_column + 1, center_row + 1]
    angle = np.radians(rotation_degrees)
    step = scale_factor / 3600.0
    wcs.wcs.cd = step * np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    return wcs


def test_a_shift_reads_the_group_stack_where_the_image_came_from() -> None:
    """A map made from a shift says to read the pixel the shift moved from."""
    position_map = GroupPositionMap.from_shift((SIZE, SIZE), 3.0, -2.0)

    rows, columns = position_map.source_positions(np.array([10.0]), np.array([10.0]))

    assert (float(rows[0]), float(columns[0])) == pytest.approx((7.0, 12.0))
    assert position_map.is_shift
    assert position_map.center_shift() == pytest.approx((3.0, -2.0))
    assert position_map.turn_and_scale() == pytest.approx((0.0, 1.0))


def test_a_record_round_trips_for_a_shift_and_for_a_sky_move() -> None:
    """The stored record gives back the same positions."""
    sky = GroupPositionMap.from_sky_maps((SIZE, SIZE), make_wcs(100, 100), make_wcs(104, 97))
    shift = GroupPositionMap.from_shift((SIZE, SIZE), 1.5, 2.5)
    probe_rows, probe_columns = np.array([5.5, 120.0]), np.array([33.0, 180.5])

    for original in (sky, shift):
        restored = GroupPositionMap.from_record(original.to_record())
        assert restored is not None
        assert np.allclose(
            original.source_positions(probe_rows, probe_columns),
            restored.source_positions(probe_rows, probe_columns),
            atol=1e-3,
        )
    assert GroupPositionMap.from_record(None) is None


def test_two_plate_solutions_give_the_shift_between_the_grids() -> None:
    """A sky position that is 4 columns and -3 rows apart gives that shift."""
    position_map = GroupPositionMap.from_sky_maps((SIZE, SIZE), make_wcs(100, 100), make_wcs(104, 97))

    rows, columns = position_map.source_positions(np.array([100.0]), np.array([100.0]))

    assert (float(rows[0]), float(columns[0])) == pytest.approx((97.0, 104.0), abs=1e-3)
    assert position_map.center_shift()[0] == pytest.approx(99.5 - 96.5, abs=0.01)
    assert not position_map.is_shift


def test_a_turned_and_enlarged_group_reports_its_turn_and_scale() -> None:
    """The turn and the scale at the center are read from the sky maps."""
    position_map = GroupPositionMap.from_sky_maps(
        (SIZE, SIZE), make_wcs(99.5, 99.5), make_wcs(99.5, 99.5, rotation_degrees=30.0, scale_factor=1.01)
    )

    rotation, scale = position_map.turn_and_scale()

    assert abs(rotation) == pytest.approx(30.0, abs=0.05)
    assert scale == pytest.approx(1.01, abs=1e-4)


def test_a_sky_move_resamples_an_image_by_the_shift_it_holds() -> None:
    """A star in the group stack lands where the map says it should."""
    group = np.zeros((SIZE, SIZE), dtype=np.float32)
    group[97, 104] = 1.0
    position_map = GroupPositionMap.from_sky_maps((SIZE, SIZE), make_wcs(100, 100), make_wcs(104, 97))

    moved, covered = position_map.resample(group, 1)

    row, column = np.unravel_index(np.argmax(moved), moved.shape)
    assert (int(row), int(column)) == (100, 100)
    assert covered[100, 100]
    assert not covered[0, SIZE - 1]


def test_resampling_zeroes_the_pixels_with_no_source() -> None:
    """Pixels the map reads outside the image are zero and not covered."""
    moved, covered = resample_at_positions(
        np.ones((20, 20)), lambda rows, columns: (rows - 10.0, columns), 1, (20, 20)
    )

    assert not covered[:11].any()
    assert covered[12:-1, 1:-1].all()
    assert not moved[~covered].any()


def test_an_alignment_with_a_map_resamples_through_the_map() -> None:
    """`apply_alignment` uses the position map when the alignment has one."""
    group = np.random.default_rng(1).normal(size=(SIZE, SIZE)).astype(np.float32)
    position_map = GroupPositionMap.from_shift((SIZE, SIZE), 2.0, 3.0)
    alignment = AlignmentResult(2.0, 3.0, 0.0, method="siril", position_map=position_map)

    moved, _ = apply_alignment(group, alignment, order=1)

    assert np.allclose(moved[10:-10, 10:-10], np.roll(group, (2, 3), axis=(0, 1))[10:-10, 10:-10], atol=1e-5)


def test_the_crop_counts_the_pixels_a_sky_move_fills() -> None:
    """A group moved by hundreds of pixels does not cover the whole stack."""
    size = 300
    move = GroupPositionMap.from_sky_maps((size, size), make_wcs(150, 150), make_wcs(250, 150)).to_record()

    counts = stack_crop.frame_coverage_counts(
        [IDENTITY, IDENTITY], [True, True], size, size, step=4, alignments=[None, move]
    )

    assert counts[37, 5] == 2
    assert counts[37, 60] == 1


def test_the_group_record_for_the_crop_reads_the_position_map() -> None:
    """The crop reads each group's stored position map."""
    move = GroupPositionMap.from_shift((SIZE, SIZE), 0.0, 60.0).to_record()
    diagnostics = {
        "registration_frames": [{"homography": IDENTITY}] * 4,
        "registration_included": [True] * 4,
        "exposure_group_alignments": [
            {"frame_count": 2, "alignment": None},
            {"frame_count": 2, "alignment": move},
        ],
    }

    counts = stack_crop._coverage_from_diagnostics(diagnostics, SIZE, SIZE)

    assert counts is not None
    assert counts.max() == 4
    assert counts.min() == 2


def test_the_rejection_map_follows_the_group_stack(tmp_path: Path) -> None:
    """A marked block in a group's map moves with the group's stack."""
    rejection_map = np.zeros((SIZE, SIZE), np.float32)
    rejection_map[40:46, 60:66] = 1.0
    path = tmp_path / "group_RejMap.fits"
    fits.writeto(path, rejection_map)
    position_map = GroupPositionMap.from_shift((SIZE, SIZE), 5.0, -4.0)
    alignment = AlignmentResult(5.0, -4.0, 0.0, method="siril", position_map=position_map)

    merge_rejection_maps([str(path)], [1], str(tmp_path / "merged.fits"), alignments=[alignment])

    merged = fits.getdata(tmp_path / "merged.fits")
    assert merged[45:51, 56:62].sum() == pytest.approx(36.0, rel=0.05)


def test_a_group_left_out_gives_the_reason_of_the_method_that_failed() -> None:
    """The left-out reason names Siril's own account, or the correlation."""
    siril = AlignmentResult(
        0.0,
        0.0,
        0.0,
        method="siril",
        failure_reason="Siril found 2 star(s) in the stack and could not match them",
    )
    correlation = AlignmentResult(0.0, 0.0, 0.07)

    assert "Siril found 2 star(s)" in alignment_left_out_reason(50.0, siril)
    assert alignment_left_out_reason(60.0, correlation) == (
        "could not be lined up with the 60 s group (correlation 0.07)"
    )
