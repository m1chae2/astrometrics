"""Tests for the per-star centroid offsets behind `registration_drift`.

Each frame's plate solution places every reference star. The star's own
centroid then sits near that place, and what is left is the per-star offset.
These tests build synthetic sequences whose true solutions are known
(`make_drifted_sequence` and `drifted_frame_solves`) and check three things:

* A pure translation leaves the per-star offsets near zero.
* A rotating field leaves them near zero too, because each frame's solution
  follows the rotation. The rotation shows in the corner displacement.
* The summary and the gate report those numbers.
"""

import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.quality.gate_result import GateResult, GateStatus
from astrometricslib.pipelines.photometry.post_processing import record_quality as rg
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.pre_processing.frame_mapping import (
    FrameMapping,
    wcs_from_solved_header,
)
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    CentroidShiftSummary,
    StarPosition,
    _process_single_frame_worker,
    centroid_offsets_px,
    summarize_centroid_shifts,
)
from astrometricslib.test.synthetic import SyntheticStar, drifted_frame_solves, make_drifted_sequence

SATURATION_ADU = 65535.0
SEQUENCE_SHAPE = (400, 600)
FRAME_COUNT = 8
ROTATION_DEG_PER_FRAME = 0.05
STAR_FWHM_PX = 3.5

# Star offsets from the frame center, in pixels: first a ring, then more
# stars at other distances. The farthest star is 197 px
# away, which a rotation of 0.35 degrees (the last frame) moves by 1.2 px.
STAR_OFFSETS_PX = [(170, 100), (170, -100), (-170, 100), (-170, -100), (120, 0), (-120, 0)]
MORE_STAR_OFFSETS_PX = [(30, 0), (0, 70), (0, -110), (-60, -40), (60, 40)]
CENTER_PX = ((SEQUENCE_SHAPE[1] - 1) / 2, (SEQUENCE_SHAPE[0] - 1) / 2)


def _field_stars() -> list[SyntheticStar]:
    """Build the anchor stars and the extra stars around the frame center.

    Returns
    -------
    stars : `list` [`SyntheticStar`]
        The anchors first, then the extra stars.
    """
    return [
        SyntheticStar(CENTER_PX[0] + dx, CENTER_PX[1] + dy, 30000.0, STAR_FWHM_PX)
        for dx, dy in STAR_OFFSETS_PX + MORE_STAR_OFFSETS_PX
    ]


def _measure_offsets(
    tmp_path: Path,
    frames: list[np.ndarray],
    stars: list[SyntheticStar],
    drift_px: tuple[float, float],
    rotation_deg_per_frame: float,
) -> tuple[dict[datetime, list[float]], dict[datetime, FrameMapping], int, int]:
    """Run the frame worker on a sequence and collect the per-star offsets.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder for the FITS files.
    frames : `list` [`numpy.ndarray`]
        The frames, in time order. Frame 0 is the reference and is not
        measured.
    stars : `list` [`SyntheticStar`]
        The stars in frame 0.
    drift_px : `tuple` [`float`, `float`]
        The drift between frames, in pixels, which the solutions follow.
    rotation_deg_per_frame : `float`
        The rotation between frames, in degrees, which the solutions follow.

    Returns
    -------
    offsets_by_frame : `dict` [`datetime.datetime`, `list` [`float`]]
        For each measured frame, the offset of every refined star, in pixels.
    mappings_by_frame : `dict` [`datetime.datetime`, `FrameMapping`]
        For each measured frame, its mapping from the reference frame.
    measurement_count : `int`
        Star measurements made.
    fallback_count : `int`
        Measurements that kept the solution's position.
    """
    reference = [(f"star{index}", star.x, star.y) for index, star in enumerate(stars)]
    reference_positions = {star_id: (x, y) for star_id, x, y in reference}
    paths = [str(tmp_path / f"frame_{index:02d}.fits") for index in range(len(frames))]
    solves = drifted_frame_solves(paths, SEQUENCE_SHAPE, drift_px, rotation_deg_per_frame)
    reference_wcs = wcs_from_solved_header(solves[paths[0]].header)
    start = datetime(2026, 5, 24, 4, 0, 0)
    offsets_by_frame: dict[datetime, list[float]] = {}
    mappings_by_frame: dict[datetime, FrameMapping] = {}
    measurement_count = fallback_count = 0
    for index, frame in enumerate(frames[1:], start=1):
        timestamp = start + timedelta(minutes=index)
        hdu = fits.PrimaryHDU(frame)
        hdu.header["DATE-OBS"] = timestamp.isoformat(timespec="milliseconds")
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
        positions = result[6]
        offsets, fallbacks = centroid_offsets_px(positions, reference_positions, mapping)
        offsets_by_frame[timestamp] = offsets
        mappings_by_frame[timestamp] = mapping
        measurement_count += len(positions)
        fallback_count += fallbacks
    return offsets_by_frame, mappings_by_frame, measurement_count, fallback_count


def test_a_rotating_field_leaves_the_per_star_offsets_near_zero(tmp_path: Path) -> None:
    """Verifies 0.05 deg of rotation per frame does not spread the offsets.

    With no noise, every star's centroid sits within 0.02 px of the place its
    frame's solution gives, however far the star is from the rotation center,
    and no centroid is refused. The rotation shows in the corner displacement
    of the last frame, which is the rotation angle times the distance from
    the center to a corner.
    """
    stars = _field_stars()
    frames = make_drifted_sequence(
        stars,
        FRAME_COUNT,
        (0.0, 0.0),
        rotation_deg_per_frame=ROTATION_DEG_PER_FRAME,
        shape=SEQUENCE_SHAPE,
        add_noise=False,
    )

    offsets_by_frame, mappings_by_frame, measurements, fallbacks = _measure_offsets(
        tmp_path, frames, stars, (0.0, 0.0), ROTATION_DEG_PER_FRAME
    )

    assert max(max(offsets) for offsets in offsets_by_frame.values()) < 0.02
    summary = summarize_centroid_shifts(
        offsets_by_frame, measurements, fallbacks, mappings_by_frame=mappings_by_frame
    )
    assert summary is not None
    assert summary.fallback_count == 0
    assert summary.mapped_frame_count == FRAME_COUNT - 1
    angle_rad = math.radians(ROTATION_DEG_PER_FRAME * (FRAME_COUNT - 1))
    assert summary.worst_corner_displacement_px == pytest.approx(angle_rad * math.hypot(*CENTER_PX), abs=0.02)
    assert summary.worst_frame is not None


def test_a_pure_translation_leaves_the_per_star_offsets_near_zero(tmp_path: Path) -> None:
    """Verifies a drifting field with no rotation gives offsets under 0.1 px.

    The field drifts by (0.3, -0.2) px per frame on a noisy sky. The solution
    follows all of that motion, so each star's own centroid lands on the
    solution's place to within the centroid's noise.
    """
    stars = _field_stars()
    frames = make_drifted_sequence(
        stars,
        FRAME_COUNT,
        (0.3, -0.2),
        shape=SEQUENCE_SHAPE,
        sky_adu=200.0,
        read_noise_adu=5.0,
        seed=5,
    )

    offsets_by_frame, mappings_by_frame, measurements, fallbacks = _measure_offsets(
        tmp_path, frames, stars, (0.3, -0.2), 0.0
    )

    summary = summarize_centroid_shifts(
        offsets_by_frame, measurements, fallbacks, mappings_by_frame=mappings_by_frame
    )
    assert summary is not None
    assert summary.fallback_count == 0
    assert summary.median_offset_px is not None
    assert summary.p95_offset_px is not None
    assert summary.median_offset_px < 0.05
    assert summary.p95_offset_px < 0.1
    assert summary.worst_corner_displacement_px == pytest.approx(0.0, abs=1e-3)


def _drift_gate(summaries: list[CentroidShiftSummary]) -> GateResult:
    """Build only the ``registration_drift`` gate from summaries.

    Parameters
    ----------
    summaries : `list` [`CentroidShiftSummary`]
        The per-session summaries.

    Returns
    -------
    gate : `GateResult`
        The ``registration_drift`` gate, with one star's global drift of 3 px.
    """
    return rg._registration_drift_gate([3.0], summaries)


def _sequence_summary(
    tmp_path: Path, rotation_deg_per_frame: float, drift_px: tuple[float, float]
) -> CentroidShiftSummary:
    """Measure a noise-free sequence and summarize its centroid offsets.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder for the FITS files.
    rotation_deg_per_frame : `float`
        The rotation between frames, in degrees.
    drift_px : `tuple` [`float`, `float`]
        The drift between frames, in pixels.

    Returns
    -------
    summary : `CentroidShiftSummary`
        The summary of the sequence.
    """
    stars = _field_stars()
    frames = make_drifted_sequence(
        stars,
        FRAME_COUNT,
        drift_px,
        rotation_deg_per_frame=rotation_deg_per_frame,
        shape=SEQUENCE_SHAPE,
        add_noise=False,
    )
    offsets_by_frame, mappings_by_frame, measurements, fallbacks = _measure_offsets(
        tmp_path, frames, stars, drift_px, rotation_deg_per_frame
    )
    summary = summarize_centroid_shifts(
        offsets_by_frame, measurements, fallbacks, mappings_by_frame=mappings_by_frame
    )
    assert summary is not None
    return summary


def test_the_gate_reports_the_offsets_and_the_corner_displacement(tmp_path: Path) -> None:
    """Verifies a small rotation passes and the detail quotes the numbers.

    A rotation of 0.01 degrees per frame moves a corner of the last frame by
    0.4 px, under the limit of 1 px. The detail quotes the median offset, the
    95th percentile, the corner displacement and the worst frame.
    """
    summary = _sequence_summary(tmp_path, 0.01, (0.0, 0.0))

    gate = _drift_gate([summary])

    assert summary.p95_offset_px is not None
    assert summary.median_offset_px is not None
    assert summary.corner_displacement_p95_px is not None
    assert summary.corner_displacement_p95_px < rg.MAXIMUM_CORNER_DISPLACEMENT_PX.reference_value
    assert gate.name == rg.REGISTRATION_DRIFT_GATE_NAME
    assert gate.status is GateStatus.PASSED
    assert f"median {summary.median_offset_px:.2f} px" in gate.detail
    assert f"95th percentile {summary.p95_offset_px:.2f} px" in gate.detail
    assert f"a corner moves {summary.corner_displacement_p95_px:.2f} px" in gate.detail
    assert "worst frame 2026-05-24 04:0" in gate.detail


def test_the_gate_passes_a_drifting_field_with_no_rotation(tmp_path: Path) -> None:
    """Verifies a pure translation passes and its detail gives the offsets."""
    summary = _sequence_summary(tmp_path, 0.0, (0.3, -0.2))

    gate = _drift_gate([summary])

    assert gate.status is GateStatus.PASSED
    assert "95th percentile 0.0" in gate.detail
    assert "0.0% of star measurements kept the solution's position" in gate.detail


def _shift_by_two_mapping() -> FrameMapping:
    """Build the mapping of a frame whose field moved 2 px along x.

    Returns
    -------
    mapping : `FrameMapping`
        The mapping from frame 0 to frame 1 of a two-frame sequence.
    """
    solves = drifted_frame_solves(["a", "b"], SEQUENCE_SHAPE, (2.0, 0.0))
    return FrameMapping.from_solutions(
        wcs_from_solved_header(solves["a"].header),
        wcs_from_solved_header(solves["b"].header),
        SEQUENCE_SHAPE,
    )


def test_centroid_offsets_count_fallbacks_and_skip_unknown_stars() -> None:
    """Verifies a refused centroid is counted and an unknown id skipped."""
    positions = {
        "a": StarPosition(10.3, 20.4),
        "b": StarPosition(50.0, 60.0, "saturated pixel in the centroid box"),
        "ghost": StarPosition(1.0, 1.0),
    }
    reference = {"a": (8.0, 20.0), "b": (48.0, 58.0)}

    offsets, fallbacks = centroid_offsets_px(positions, reference, _shift_by_two_mapping())

    assert offsets == pytest.approx([math.hypot(0.3, 0.4)])
    assert fallbacks == 1


def test_a_summary_of_nothing_measured_is_none() -> None:
    """Verifies a session with no measured frames has no summary."""
    assert summarize_centroid_shifts({}, 0, 0) is None
