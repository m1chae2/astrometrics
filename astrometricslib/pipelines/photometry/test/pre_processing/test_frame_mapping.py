"""Tests for the map that places each star through a frame's plate solution.

On 2026-10-11, the registration gate failed on M 13, M 81 and NGC 2403.
Replaying the gate on the raw frames showed two causes: centroid noise
on faint and crowded stars (M 13 and M 81), and a real field rotation of
0.05 degrees over the night (NGC 2403, 1.8 px at the corner). Each frame's
own plate solution now places the stars (register entry PHO-24). These tests
check, on synthetic frames whose true solutions are known, that:

* `FrameMapping` recovers a known shift, rotation and scale change, and
  refuses a solution that does not map the frame.
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

from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.pipelines.photometry.post_processing import record_quality as rg
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.pre_processing.frame_mapping import (
    FrameMapping,
    FrameMappingError,
    wcs_from_solved_header,
)
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    _process_single_frame_worker,
    centroid_offsets_px,
    summarize_centroid_shifts,
)
from astrometricslib.test.synthetic import (
    SyntheticStar,
    drifted_frame_solves,
    drifted_stars,
    make_drifted_sequence,
)

FIELD_SHAPE = (3008, 3008)
TRUE_ROTATION_DEG = 0.0458366
TRUE_SCALE_CHANGE = 1.5e-4
TRUE_SHIFT = (2.5, -1.5)
CENTRE = ((FIELD_SHAPE[1] - 1) / 2, (FIELD_SHAPE[0] - 1) / 2)


def make_mapping(
    shift: tuple[float, float] = TRUE_SHIFT,
    rotation_deg: float = TRUE_ROTATION_DEG,
    scale_change: float = TRUE_SCALE_CHANGE,
) -> FrameMapping:
    """Build the mapping of frame 1 of a sequence with a known move.

    Parameters
    ----------
    shift : `tuple` [`float`, `float`], optional
        The shift of the field, in pixels.
    rotation_deg : `float`, optional
        The rotation about the middle of the frame, in degrees.
    scale_change : `float`, optional
        The change of scale, as a fraction.

    Returns
    -------
    mapping : `FrameMapping`
        The mapping from frame 0 to frame 1.
    """
    solves = drifted_frame_solves(["a", "b"], FIELD_SHAPE, shift, rotation_deg, scale_change)
    return FrameMapping.from_solutions(
        wcs_from_solved_header(solves["a"].header), wcs_from_solved_header(solves["b"].header), FIELD_SHAPE
    )


def test_the_mapping_recovers_a_known_shift_rotation_and_scale_change() -> None:
    """A star lands where the move that made the frame puts it."""
    mapping = make_mapping()
    stars = [SyntheticStar(x, y, 1000.0) for x, y in [(300.0, 400.0), (2500.0, 700.0), (1400.0, 2800.0)]]
    angle = math.radians(TRUE_ROTATION_DEG)
    for star in stars:
        rel_x, rel_y = star.x - CENTRE[0], star.y - CENTRE[1]
        stretch = 1.0 + TRUE_SCALE_CHANGE
        expected = (
            CENTRE[0] + stretch * (math.cos(angle) * rel_x - math.sin(angle) * rel_y) + TRUE_SHIFT[0],
            CENTRE[1] + stretch * (math.sin(angle) * rel_x + math.cos(angle) * rel_y) + TRUE_SHIFT[1],
        )
        assert mapping.predict(star.x, star.y) == pytest.approx(expected, abs=0.01)


def test_the_shift_is_the_move_of_the_middle_of_the_frame() -> None:
    """The shift is how far the middle of the frame moves."""
    mapping = make_mapping()

    assert mapping.shift_x == pytest.approx(TRUE_SHIFT[0], abs=0.01)
    assert mapping.shift_y == pytest.approx(TRUE_SHIFT[1], abs=0.01)


def test_the_corner_displacement_is_the_rotation_and_scale_at_the_corner() -> None:
    """A corner moves by the rotation and the scale change, not the shift."""
    mapping = make_mapping()
    half_diagonal = math.hypot(*CENTRE)
    expected = math.hypot(math.radians(TRUE_ROTATION_DEG), TRUE_SCALE_CHANGE) * half_diagonal

    assert mapping.corner_displacement_px == pytest.approx(expected, rel=0.02)


def test_a_shift_alone_has_no_corner_displacement() -> None:
    """A translation moves every corner by the shift, so nothing is left."""
    mapping = make_mapping(rotation_deg=0.0, scale_change=0.0)

    assert mapping.corner_displacement_px == pytest.approx(0.0, abs=1e-3)


def test_a_solution_that_places_nothing_is_refused() -> None:
    """A sky map that puts the frame at no finite pixel gives no mapping."""
    solves = drifted_frame_solves(["a", "b"], FIELD_SHAPE)
    reference_wcs = wcs_from_solved_header(solves["a"].header)
    far_header = solves["b"].header.copy()
    far_header["CRVAL2"] = -89.0

    with pytest.raises(FrameMappingError):
        FrameMapping.from_solutions(reference_wcs, wcs_from_solved_header(far_header), FIELD_SHAPE)


def test_a_header_without_a_sky_map_is_refused() -> None:
    """A header that holds no sky map is not a solution."""
    with pytest.raises(FrameMappingError):
        wcs_from_solved_header(fits.Header())


SEQUENCE_SHAPE = (400, 600)
SEQUENCE_ROTATION_DEG_PER_FRAME = 0.1
SEQUENCE_FRAME_COUNT = 8
SATURATION_ADU = 65535.0
STAR_FWHM_PX = 3.5
SEQUENCE_CENTRE = ((SEQUENCE_SHAPE[1] - 1) / 2, (SEQUENCE_SHAPE[0] - 1) / 2)

# Twelve stars on a ring and a cross, 90 to 235 px from the middle. The
# last frame is turned by 0.7 degrees, which moves the farthest star by
# 2.9 px: more than the 1.5 px a centroid may move, so a shift alone would
# lose it. The solution's position is exact, so no star is lost.
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


def measure_rotating_sequence(tmp_path: Path) -> tuple[list[tuple], list]:
    """Run the worker on a rotating sequence.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder for the FITS files.

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
    paths = [str(tmp_path / f"frame_{index:02d}.fits") for index in range(SEQUENCE_FRAME_COUNT)]
    solves = drifted_frame_solves(
        paths, SEQUENCE_SHAPE, (0.0, 0.0), rotation_deg_per_frame=SEQUENCE_ROTATION_DEG_PER_FRAME
    )
    reference_wcs = wcs_from_solved_header(solves[paths[0]].header)
    results = []
    for index, frame in enumerate(frames[1:], start=1):
        hdu = fits.PrimaryHDU(frame)
        start = datetime(2026, 5, 24, 4, 0, 0) + timedelta(minutes=index)
        hdu.header["DATE-OBS"] = start.isoformat(timespec="milliseconds")
        hdu.header["EXPTIME"] = 1.0
        hdu.writeto(paths[index])
        mapping = FrameMapping.from_solutions(
            reference_wcs, wcs_from_solved_header(solves[paths[index]].header), SEQUENCE_SHAPE
        )
        _, result = _process_single_frame_worker((
            paths[index],
            reference,
            mapping,
            SATURATION_ADU,
            DetectorNoise(),
        ))
        assert isinstance(result, tuple), f"frame {index} was not measured: {result!r}"
        results.append(result)
    return results, reference


def test_the_worker_follows_every_star_through_a_rotating_field(tmp_path: Path) -> None:
    """The solution places every star, so no centroid is refused."""
    results, reference = measure_rotating_sequence(tmp_path)
    reference_positions = {star_id: (x, y) for star_id, x, y in reference}
    stars = sequence_stars()

    for index, result in enumerate(results, start=1):
        positions, mapping = result[6], result[8]
        assert all(position.is_refined for position in positions.values())
        offsets, fallbacks = centroid_offsets_px(positions, reference_positions, mapping)
        assert fallbacks == 0
        assert max(offsets) < 0.2
        truth = drifted_stars(stars, index, (0.0, 0.0), SEQUENCE_ROTATION_DEG_PER_FRAME, SEQUENCE_SHAPE)
        for star, true_star in zip(stars, truth, strict=True):
            expected = mapping.predict(star.x, star.y)
            assert expected == pytest.approx((true_star.x, true_star.y), abs=0.01)


def test_the_session_summary_reports_the_corner_displacement_and_the_gate_judges_it(
    tmp_path: Path,
) -> None:
    """A field turned by 0.7 degrees fails on its corner displacement."""
    results, reference = measure_rotating_sequence(tmp_path)
    reference_positions = {star_id: (x, y) for star_id, x, y in reference}
    offsets_by_frame, mappings_by_frame = {}, {}
    measurement_count = fallback_count = 0
    for index, result in enumerate(results, start=1):
        timestamp = datetime(2026, 5, 24, 4, index, 0)
        mapping = result[8]
        offsets, fallbacks = centroid_offsets_px(result[6], reference_positions, mapping)
        offsets_by_frame[timestamp] = offsets
        mappings_by_frame[timestamp] = mapping
        measurement_count += len(result[6])
        fallback_count += fallbacks

    summary = summarize_centroid_shifts(
        offsets_by_frame, measurement_count, fallback_count, mappings_by_frame=mappings_by_frame
    )
    gate = rg._registration_drift_gate([0.1], [summary])

    assert summary is not None
    assert summary.mapped_frame_count == SEQUENCE_FRAME_COUNT - 1
    assert summary.worst_corner_frame == datetime(2026, 5, 24, 4, SEQUENCE_FRAME_COUNT - 1, 0)
    expected_worst = math.radians(0.7) * math.hypot(*SEQUENCE_CENTRE)
    assert summary.worst_corner_displacement_px == pytest.approx(expected_worst, abs=0.1)
    assert gate.status is GateStatus.FAILED
    assert gate.limit == pytest.approx(rg.MAXIMUM_CORNER_DISPLACEMENT_PX.reference_value)
    assert "a corner moves" in gate.detail


def test_a_star_the_solution_cannot_place_gets_no_measurement(tmp_path: Path) -> None:
    """A star with no finite place is recorded as unplaced."""
    results, _ = measure_rotating_sequence(tmp_path)
    mapping = results[0][8]
    path = tmp_path / "frame_01.fits"
    off_the_sky = [("lost", float("nan"), 10.0)]

    _, result = _process_single_frame_worker((
        str(path),
        off_the_sky,
        mapping,
        SATURATION_ADU,
        DetectorNoise(),
    ))

    assert isinstance(result, tuple)
    assert not result[6]["lost"].is_refined
    assert result[1]["lost"] == (0.0, False, 0.0)
    assert np.isnan(result[9]["lost"])
