"""Tests for the shift, rotation and scale fit that lines frames up.

On 2026-10-11, the `registration_drift` gate failed on M 13, M 81 and NGC
2403. Replaying the gate on the raw frames showed two causes: centroid noise
on faint and crowded stars (M 13 and M 81), and a real field rotation of
0.05 degrees over the night (NGC 2403, 1.8 px at the corner). The frames were
lined up by a shift alone, so the rotating field pushed far stars past the
1.5 px limit for a centroid. These tests check the fit that replaced it:

* `fit_frame_alignment` recovers a known shift, rotation and scale change,
  ignores a few wrong stars, and falls back to a shift alone when the
  anchors are too few or too bunched to tell a rotation from a shift.
* On a synthetic sequence that rotates, the worker follows every star (no
  centroid is refused) and the session summary reports the corner
  displacement that the gate judges.
"""

import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.gate_result import GateStatus
from astrometricslib.pipelines.photometry.post_processing import run_gates as rg
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    MINIMUM_ANCHORS_FOR_SIMILARITY,
    FrameAlignment,
    _process_single_frame_worker,
    centroid_offsets_px,
    fit_frame_alignment,
    summarize_centroid_shifts,
)
from astrometricslib.test.synthetic import SyntheticStar, make_drifted_sequence

FIELD_SHAPE = (3008, 3008)
TRUE_ROTATION = 8.0e-4
TRUE_SCALE_CHANGE = 1.5e-4
TRUE_SHIFT = (2.5, -1.5)
CENTRE = ((FIELD_SHAPE[1] - 1) / 2, (FIELD_SHAPE[0] - 1) / 2)


def make_anchors(count: int = 40, spread_px: float = 900.0, seed: int = 4) -> list[tuple[float, float]]:
    """Place anchors at random around the middle of the field.

    Parameters
    ----------
    count : `int`, optional
        How many anchors.
    spread_px : `float`, optional
        Half-width of the square they are drawn from, in pixels.
    seed : `int`, optional
        Seed of the random generator.

    Returns
    -------
    anchors : `list` [`tuple` [`float`, `float`]]
        The anchors' ``(x, y)`` in the reference frame.
    """
    generator = np.random.default_rng(seed)
    offsets = generator.uniform(-spread_px, spread_px, (count, 2))
    return [(CENTRE[0] + dx, CENTRE[1] + dy) for dx, dy in offsets]


def move_anchors(
    anchors: list[tuple[float, float]], noise_px: float = 0.0, seed: int = 5
) -> list[tuple[float, float] | None]:
    """Move anchors by the true alignment, as the real frame would show them.

    Parameters
    ----------
    anchors : `list` [`tuple` [`float`, `float`]]
        Positions in the reference frame.
    noise_px : `float`, optional
        Centroid noise added to each axis, in pixels.
    seed : `int`, optional
        Seed of the random generator.

    Returns
    -------
    located : `list` [`tuple` [`float`, `float`] or `None`]
        Where each anchor sits in the moved frame.
    """
    truth = FrameAlignment(
        TRUE_SHIFT[0],
        TRUE_SHIFT[1],
        CENTRE[0],
        CENTRE[1],
        rotation=TRUE_ROTATION,
        scale_change=TRUE_SCALE_CHANGE,
    )
    generator = np.random.default_rng(seed)
    moved = []
    for x, y in anchors:
        predicted_x, predicted_y = truth.predict(x, y)
        moved.append((
            predicted_x + generator.normal(0, noise_px),
            predicted_y + generator.normal(0, noise_px),
        ))
    return moved


def test_the_fit_recovers_a_known_shift_rotation_and_scale_change() -> None:
    """Anchors moved by a known alignment give that alignment back."""
    anchors = make_anchors()

    alignment = fit_frame_alignment(anchors, move_anchors(anchors), FIELD_SHAPE)

    assert alignment.includes_similarity
    assert alignment.shift_x == pytest.approx(TRUE_SHIFT[0], abs=1e-6)
    assert alignment.shift_y == pytest.approx(TRUE_SHIFT[1], abs=1e-6)
    assert alignment.rotation == pytest.approx(TRUE_ROTATION, abs=1e-9)
    assert alignment.scale_change == pytest.approx(TRUE_SCALE_CHANGE, abs=1e-9)


def test_centroid_noise_of_a_tenth_of_a_pixel_moves_a_corner_by_well_under_a_pixel() -> None:
    """The fit holds with realistic noise, so the gate is not fed noise."""
    anchors = make_anchors(count=50)

    alignment = fit_frame_alignment(anchors, move_anchors(anchors, noise_px=0.1), FIELD_SHAPE)

    truth_corner = math.hypot(TRUE_ROTATION, TRUE_SCALE_CHANGE) * alignment.half_diagonal_px
    assert alignment.corner_displacement_px == pytest.approx(truth_corner, abs=0.3)


def test_wrong_stars_are_clipped_out_of_the_fit() -> None:
    """Three anchors found 25 px away (blends) do not move the answer."""
    anchors = make_anchors(count=40)
    located = move_anchors(anchors)
    for index in (3, 11, 20):
        x, y = located[index]
        located[index] = (x + 25.0, y - 25.0)

    alignment = fit_frame_alignment(anchors, located, FIELD_SHAPE)

    assert alignment.rotation == pytest.approx(TRUE_ROTATION, abs=1e-8)
    assert alignment.scale_change == pytest.approx(TRUE_SCALE_CHANGE, abs=1e-8)
    assert alignment.anchor_count == 37


def test_anchors_bunched_in_a_cluster_core_give_a_shift_alone() -> None:
    """Anchors within 60 px of each other cannot tell rotation from shift."""
    anchors = make_anchors(count=40, spread_px=60.0)
    located = [(x + 1.0, y - 2.0) for x, y in anchors]

    alignment = fit_frame_alignment(anchors, located, FIELD_SHAPE)

    assert not alignment.includes_similarity
    assert alignment.rotation == pytest.approx(0.0)
    assert alignment.scale_change == pytest.approx(0.0)
    assert alignment.shift_x == pytest.approx(1.0)
    assert alignment.shift_y == pytest.approx(-2.0)


def test_too_few_anchors_give_a_shift_alone() -> None:
    """Below `MINIMUM_ANCHORS_FOR_SIMILARITY` anchors, only a shift is fit."""
    anchors = make_anchors(count=MINIMUM_ANCHORS_FOR_SIMILARITY - 1)

    alignment = fit_frame_alignment(anchors, move_anchors(anchors), FIELD_SHAPE)

    assert not alignment.includes_similarity
    assert alignment.rotation == pytest.approx(0.0)


def test_a_frame_with_almost_no_anchors_found_is_taken_as_not_moved() -> None:
    """Four located anchors are too few for any estimate: the shift is zero."""
    anchors = make_anchors(count=12)
    located = move_anchors(anchors)
    located = [position if index < 4 else None for index, position in enumerate(located)]

    alignment = fit_frame_alignment(anchors, located, FIELD_SHAPE)

    assert (alignment.shift_x, alignment.shift_y) == (0.0, 0.0)
    assert not alignment.includes_similarity
    assert alignment.anchor_count == 0


def test_a_shift_alone_predicts_the_same_move_everywhere() -> None:
    """With no rotation or scale, every star moves by the shift."""
    alignment = FrameAlignment(3.0, -1.0)

    assert alignment.predict(10.0, 20.0) == (13.0, 19.0)
    assert alignment.predict(2000.0, 900.0) == (2003.0, 899.0)
    assert alignment.corner_displacement_px == pytest.approx(0.0)


def test_the_corner_displacement_is_the_rotation_and_scale_at_the_corner() -> None:
    """A rotation of 8e-4 rad moves a corner of the ASI 533 field by 1.7 px."""
    alignment = FrameAlignment(
        0.0, 0.0, CENTRE[0], CENTRE[1], rotation=8.0e-4, half_diagonal_px=math.hypot(*CENTRE)
    )

    assert alignment.corner_displacement_px == pytest.approx(8.0e-4 * 2127.3, abs=0.01)


SEQUENCE_SHAPE = (400, 600)
SEQUENCE_ROTATION_DEG_PER_FRAME = 0.1
SEQUENCE_FRAME_COUNT = 8
SATURATION_ADU = 65535.0
STAR_FWHM_PX = 3.5
SEQUENCE_CENTRE = ((SEQUENCE_SHAPE[1] - 1) / 2, (SEQUENCE_SHAPE[0] - 1) / 2)

# Twelve stars on a ring and a cross, 90 to 235 px from the middle. The
# last frame is turned by 0.7 degrees, which moves the farthest star by
# 2.9 px: more than the 1.5 px a centroid may move, so a shift alone loses it.
STAR_OFFSETS_PX = [
    (235, 0),
    (-235, 0),
    (0, 150),
    (0, -150),
    (170, 100),
    (170, -100),
    (-170, 100),
    (-170, -100),
    (90, 0),
    (-90, 0),
    (0, 60),
    (0, -60),
]


def sequence_stars() -> list[SyntheticStar]:
    """Build the stars of the rotating sequence.

    Returns
    -------
    stars : `list` [`SyntheticStar`]
        The twelve stars in frame 0, all bright and apart from one another.
    """
    return [
        SyntheticStar(SEQUENCE_CENTRE[0] + dx, SEQUENCE_CENTRE[1] + dy, 30000.0, STAR_FWHM_PX)
        for dx, dy in STAR_OFFSETS_PX
    ]


def measure_rotating_sequence(tmp_path: Path, anchor_count: int) -> tuple[list[tuple], list]:
    """Run the worker on a rotating sequence.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder for the FITS files.
    anchor_count : `int`
        How many of the stars (the first ones) serve as alignment anchors.

    Returns
    -------
    results : `list` [`tuple`]
        The worker's result tuple for each frame after the first.
    reference : `list` [`tuple`]
        The reference stars as ``(id, x, y)``.
    """
    stars = sequence_stars()
    frames = make_drifted_sequence(
        stars,
        SEQUENCE_FRAME_COUNT,
        (0.0, 0.0),
        rotation_deg_per_frame=SEQUENCE_ROTATION_DEG_PER_FRAME,
        shape=SEQUENCE_SHAPE,
        add_noise=False,
    )
    reference = [(f"star{index}", star.x, star.y) for index, star in enumerate(stars)]
    anchors = [(star.x, star.y, 0.0) for star in stars[:anchor_count]]
    results = []
    for index, frame in enumerate(frames[1:], start=1):
        path = tmp_path / f"frame_{index:02d}.fits"
        hdu = fits.PrimaryHDU(frame)
        start = datetime(2026, 5, 24, 4, 0, 0) + timedelta(minutes=index)
        hdu.header["DATE-OBS"] = start.isoformat(timespec="milliseconds")
        hdu.header["EXPTIME"] = 1.0
        hdu.writeto(path)
        _, result = _process_single_frame_worker((
            str(path),
            reference,
            anchors,
            SATURATION_ADU,
            DetectorNoise(),
        ))
        assert isinstance(result, tuple), f"frame {index} was not measured: {result!r}"
        results.append(result)
    return results, reference


def test_the_worker_follows_every_star_through_a_rotating_field(tmp_path: Path) -> None:
    """With twelve anchors the rotation is fit and no centroid is refused."""
    results, reference = measure_rotating_sequence(tmp_path, anchor_count=12)
    reference_positions = {star_id: (x, y) for star_id, x, y in reference}

    for index, result in enumerate(results, start=1):
        positions, alignment = result[6], result[8]
        true_rotation = math.radians(SEQUENCE_ROTATION_DEG_PER_FRAME * index)
        assert alignment.includes_similarity
        assert alignment.rotation == pytest.approx(true_rotation, abs=2e-5)
        assert abs(alignment.shift_x) < 0.2
        assert all(position.is_refined for position in positions.values())
        offsets, fallbacks = centroid_offsets_px(
            positions, reference_positions, alignment.shift_x, alignment.shift_y, alignment=alignment
        )
        assert fallbacks == 0
        assert max(offsets) < 0.2


def test_a_shift_alone_loses_the_far_stars_that_the_fit_keeps(tmp_path: Path) -> None:
    """With six anchors, no rotation is fit and far stars are refused."""
    results, _ = measure_rotating_sequence(tmp_path, anchor_count=6)

    last_positions, last_alignment = results[-1][6], results[-1][8]

    assert not last_alignment.includes_similarity
    assert sum(not position.is_refined for position in last_positions.values()) >= 1


def test_the_session_summary_reports_the_corner_displacement_and_the_gate_judges_it(
    tmp_path: Path,
) -> None:
    """A field turned by 0.7 degrees fails on its corner displacement."""
    results, reference = measure_rotating_sequence(tmp_path, anchor_count=12)
    reference_positions = {star_id: (x, y) for star_id, x, y in reference}
    offsets_by_frame, alignments_by_frame = {}, {}
    measurement_count = fallback_count = 0
    for index, result in enumerate(results, start=1):
        timestamp = datetime(2026, 5, 24, 4, index, 0)
        alignment = result[8]
        offsets, fallbacks = centroid_offsets_px(
            result[6], reference_positions, alignment.shift_x, alignment.shift_y, alignment=alignment
        )
        offsets_by_frame[timestamp] = offsets
        alignments_by_frame[timestamp] = alignment
        measurement_count += len(result[6])
        fallback_count += fallbacks

    summary = summarize_centroid_shifts(
        offsets_by_frame, measurement_count, fallback_count, alignments_by_frame=alignments_by_frame
    )
    gate = rg._registration_drift_gate([0.1], [summary])

    assert summary is not None
    assert summary.fitted_frame_count == SEQUENCE_FRAME_COUNT - 1
    assert summary.worst_corner_frame == datetime(2026, 5, 24, 4, SEQUENCE_FRAME_COUNT - 1, 0)
    expected_worst = math.radians(0.7) * math.hypot(*SEQUENCE_CENTRE)
    assert summary.worst_corner_displacement_px == pytest.approx(expected_worst, abs=0.1)
    assert gate.status is GateStatus.FAILED
    assert gate.limit == pytest.approx(rg.MAXIMUM_CORNER_DISPLACEMENT_PX)
    assert "a corner moves" in gate.detail
