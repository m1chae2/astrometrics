"""Tests for the per-star centroid offsets behind `registration_drift`.

A frame's global shift moves every reference position by the same amount.
When the field also rotates, each star is displaced by its own amount, so its
centroid sits away from the shifted reference position. These tests build
synthetic sequences whose true answer is known (`make_drifted_sequence`) and
check three things:

* A pure translation leaves the per-star offsets near zero.
* A rotation of 0.05 degrees per frame gives offsets that grow with the
  star's distance from the rotation center, and a 95th percentile above the
  median.
* The summary and the gate report that distribution.
"""

import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.gate_result import GateResult, GateStatus
from astrometricslib.pipelines.photometry.post_processing import run_gates as rg
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    CentroidShiftSummary,
    StarPosition,
    _process_single_frame_worker,
    centroid_offsets_px,
    summarize_centroid_shifts,
)
from astrometricslib.test.synthetic import SyntheticStar, make_drifted_sequence

SATURATION_ADU = 65535.0
SEQUENCE_SHAPE = (400, 600)
FRAME_COUNT = 8
ROTATION_DEG_PER_FRAME = 0.05
STAR_FWHM_PX = 3.5

# Star offsets from the frame center, in pixels. The first six are the
# alignment anchors. They are symmetric about the center, so the median of
# their displacements is zero and the global shift stays at zero under pure
# rotation. The rest are extra stars at other distances from the center. The
# farthest star is 197 px away, which a rotation of 0.35 degrees (the last
# frame) moves by 1.2 px, inside the 1.5 px limit for a centroid.
ANCHOR_OFFSETS_PX = [(170, 100), (170, -100), (-170, 100), (-170, -100), (120, 0), (-120, 0)]
EXTRA_OFFSETS_PX = [(30, 0), (0, 70), (0, -110), (-60, -40), (60, 40)]
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
        for dx, dy in ANCHOR_OFFSETS_PX + EXTRA_OFFSETS_PX
    ]


def _measure_offsets(
    tmp_path: Path, frames: list[np.ndarray], stars: list[SyntheticStar]
) -> tuple[dict[datetime, list[float]], list[list[float]], int, int]:
    """Run the frame worker on a sequence and collect the per-star offsets.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder for the FITS files.
    frames : `list` [`numpy.ndarray`]
        The frames, in time order. Frame 0 is the reference and is not
        measured.
    stars : `list` [`SyntheticStar`]
        The stars in frame 0. The first `len(ANCHOR_OFFSETS_PX)` are the
        alignment anchors.

    Returns
    -------
    offsets_by_frame : `dict` [`datetime.datetime`, `list` [`float`]]
        For each measured frame, the offset of every refined star, in pixels.
    per_star : `list` [`list` [`float`]]
        For each measured frame, the offset of each star in input order.
        A star whose centroid was refused has an offset of NaN.
    measurement_count : `int`
        Star measurements made.
    fallback_count : `int`
        Measurements that kept the shifted position.
    """
    reference = [(f"star{index}", star.x, star.y) for index, star in enumerate(stars)]
    reference_positions = {star_id: (x, y) for star_id, x, y in reference}
    anchors = [(star.x, star.y, 0.0) for star in stars[: len(ANCHOR_OFFSETS_PX)]]
    start = datetime(2026, 5, 24, 4, 0, 0)
    offsets_by_frame: dict[datetime, list[float]] = {}
    per_star: list[list[float]] = []
    measurement_count = fallback_count = 0
    for index, frame in enumerate(frames[1:], start=1):
        path = tmp_path / f"frame_{index:02d}.fits"
        timestamp = start + timedelta(minutes=index)
        hdu = fits.PrimaryHDU(frame)
        hdu.header["DATE-OBS"] = timestamp.isoformat(timespec="milliseconds")
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
        _, _, shift_x, shift_y, _, _, positions, _ = result
        offsets, fallbacks = centroid_offsets_px(positions, reference_positions, shift_x, shift_y)
        offsets_by_frame[timestamp] = offsets
        measurement_count += len(positions)
        fallback_count += fallbacks
        per_star.append([
            centroid_offsets_px({star_id: positions[star_id]}, reference_positions, shift_x, shift_y)[0][0]
            if positions[star_id].is_refined
            else math.nan
            for star_id, _, _ in reference
        ])
    return offsets_by_frame, per_star, measurement_count, fallback_count


def test_a_rotating_field_gives_offsets_that_grow_with_distance_from_the_center(
    tmp_path: Path,
) -> None:
    """Verifies 0.05 deg of rotation per frame spreads the per-star offsets.

    With no noise, the offset of a star in frame ``k`` equals its distance
    from the center times the rotation angle ``k * 0.05`` degrees in
    radians. In the last frame the nearest star is 30 px from the center and
    the farthest 197 px, so the offsets must rise with distance and match
    that product to 0.02 px. Over the whole sequence the 95th percentile must
    lie above the median.
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

    offsets_by_frame, per_star, measurements, fallbacks = _measure_offsets(tmp_path, frames, stars)

    distances = np.array([math.hypot(star.x - CENTER_PX[0], star.y - CENTER_PX[1]) for star in stars])
    last_frame_offsets = np.array(per_star[-1])
    angle_rad = math.radians(ROTATION_DEG_PER_FRAME * (FRAME_COUNT - 1))
    assert np.all(np.isfinite(last_frame_offsets))
    assert np.corrcoef(distances, last_frame_offsets)[0, 1] > 0.99
    assert last_frame_offsets == pytest.approx(distances * angle_rad, abs=0.02)

    summary = summarize_centroid_shifts(offsets_by_frame, measurements, fallbacks)
    assert summary is not None
    assert summary.fallback_count == 0
    assert summary.median_offset_px is not None
    assert summary.p95_offset_px is not None
    assert summary.p95_offset_px > summary.median_offset_px
    assert summary.p95_offset_px > 0.5
    assert summary.worst_frame == max(offsets_by_frame)


def test_a_pure_translation_leaves_the_per_star_offsets_near_zero(tmp_path: Path) -> None:
    """Verifies a drifting field with no rotation gives offsets under 0.1 px.

    The field drifts by (0.3, -0.2) px per frame on a noisy sky. The global
    shift takes up all of that motion, so each star's own centroid lands on
    the shifted reference position to within the centroid's noise.
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

    offsets_by_frame, _, measurements, fallbacks = _measure_offsets(tmp_path, frames, stars)

    summary = summarize_centroid_shifts(offsets_by_frame, measurements, fallbacks)
    assert summary is not None
    assert summary.fallback_count == 0
    assert summary.median_offset_px is not None
    assert summary.p95_offset_px is not None
    assert summary.median_offset_px < 0.05
    assert summary.p95_offset_px < 0.1


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
    offsets_by_frame, _, measurements, fallbacks = _measure_offsets(tmp_path, frames, stars)
    summary = summarize_centroid_shifts(offsets_by_frame, measurements, fallbacks)
    assert summary is not None
    return summary


def test_the_gate_fails_on_a_rotating_field_and_quotes_the_distribution(
    tmp_path: Path,
) -> None:
    """Verifies the drift gate fails when the field rotates 0.05 deg per frame.

    Over 7 frames the 95th percentile of the per-star offsets passes
    `MAXIMUM_STAR_OFFSET_P95_PX` (1 px). The gate fails. Its measured value
    is that percentile, and its detail quotes the median, the percentile and
    the worst frame.
    """
    summary = _sequence_summary(tmp_path, ROTATION_DEG_PER_FRAME, (0.0, 0.0))

    gate = _drift_gate([summary])

    assert summary.p95_offset_px is not None
    assert summary.median_offset_px is not None
    assert gate.name == rg.REGISTRATION_DRIFT_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(summary.p95_offset_px)
    assert gate.limit == pytest.approx(rg.MAXIMUM_STAR_OFFSET_P95_PX)
    assert f"median {summary.median_offset_px:.2f} px" in gate.detail
    assert f"95th percentile {summary.p95_offset_px:.2f} px" in gate.detail
    assert "worst frame 2026-05-24 04:07:00" in gate.detail


def test_the_gate_passes_a_drifting_field_with_no_rotation(tmp_path: Path) -> None:
    """Verifies a pure translation passes and its detail gives the offsets."""
    summary = _sequence_summary(tmp_path, 0.0, (0.3, -0.2))

    gate = _drift_gate([summary])

    assert gate.status is GateStatus.PASSED
    assert "95th percentile 0.0" in gate.detail
    assert "0.0% of star measurements kept the shifted position" in gate.detail


def test_centroid_offsets_count_fallbacks_and_skip_unknown_stars() -> None:
    """Verifies a refused centroid is counted and an unknown id skipped."""
    positions = {
        "a": StarPosition(10.3, 20.4),
        "b": StarPosition(50.0, 60.0, "saturated pixel in the centroid box"),
        "ghost": StarPosition(1.0, 1.0),
    }
    reference = {"a": (8.0, 20.0), "b": (48.0, 58.0)}

    offsets, fallbacks = centroid_offsets_px(positions, reference, 2.0, 0.0)

    assert offsets == pytest.approx([math.hypot(0.3, 0.4)])
    assert fallbacks == 1


def test_a_summary_of_nothing_measured_is_none() -> None:
    """Verifies a session with no measured frames has no summary."""
    assert summarize_centroid_shifts({}, 0, 0) is None
