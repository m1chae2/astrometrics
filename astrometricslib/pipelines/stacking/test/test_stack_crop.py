"""Tests for trimming the noisy edges off a finished stack.

The synthetic stacks have flat sky and a known noise, with extra noise added in
strips along chosen edges, as fewer overlapping frames would make.
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.post_processing import stack_crop as sc

SIZE = 1000
SKY = 1000.0
NOISE = 0.01


def make_stack(left_noisy: int = 0, bottom_noisy: int = 0, seed: int = 0) -> np.ndarray:
    """Build a flat sky whose first columns and rows are noisier.

    Parameters
    ----------
    left_noisy : `int`
        Number of columns at the left edge with 1.4 times the noise.
    bottom_noisy : `int`
        Number of rows at the start of the array with 1.4 times the noise.
    seed : `int`
        Seed of the noise.

    Returns
    -------
    stack : `numpy.ndarray`
        A square image with the given sky level.
    """
    scale = np.full((SIZE, SIZE), NOISE)
    scale[:, :left_noisy] *= 1.4
    scale[:bottom_noisy, :] *= 1.4
    return SKY * (1.0 + scale * np.random.default_rng(seed).standard_normal((SIZE, SIZE)))


def test_a_clean_stack_is_not_trimmed() -> None:
    """With no noisy edge, the whole image is kept."""
    box = sc.find_edge_crop_box(make_stack())

    assert (box.x0, box.x1, box.y0, box.y1) == (0, SIZE, 0, SIZE)


def test_noisy_edges_are_trimmed_and_the_stack_stays_square() -> None:
    """The result is square, clear of the noisy strips, and near the centre."""
    box = sc.find_edge_crop_box(make_stack(left_noisy=100, bottom_noisy=200))

    assert box.x1 - box.x0 == box.y1 - box.y0
    assert box.x0 >= 75
    assert box.y0 >= 175
    assert box.x1 <= SIZE
    assert box.y1 <= SIZE
    assert box.y1 - box.y0 >= SIZE - 250
    assert abs((box.x0 + box.x1) / 2 - SIZE / 2) <= abs(SIZE / 2 - (75 + SIZE) / 2) + 1


def test_the_square_is_placed_as_near_the_centre_as_the_clean_area_allows() -> None:
    """A wide clean area gives a centred square; a narrow one a fitted one."""
    wide = sc._same_shape_box(sc.CropBox(0, 1000, 300, 1000), 1000, 1000)
    narrow = sc._same_shape_box(sc.CropBox(400, 1000, 0, 1000), 1000, 1000)

    assert (wide.y0, wide.y1) == (300, 1000)
    assert wide.x1 - wide.x0 == 700
    assert 0 <= wide.x0 <= 300
    assert (narrow.x0, narrow.x1) == (400, 1000)
    assert narrow.y1 - narrow.y0 == 600


def test_a_non_square_stack_keeps_its_proportions() -> None:
    """A 2:1 image trimmed on one side is still 2:1."""
    box = sc._same_shape_box(sc.CropBox(100, 2000, 0, 1000), 2000, 1000)

    assert (box.x1 - box.x0) == 2 * (box.y1 - box.y0)
    assert box.x0 >= 100


def test_a_slightly_noisier_edge_is_kept() -> None:
    """An edge only 5% noisier than the middle is within the limit."""
    scale = np.full((SIZE, SIZE), NOISE)
    scale[:, :100] *= 1.05
    stack = SKY * (1.0 + scale * np.random.default_rng(3).standard_normal((SIZE, SIZE)))

    assert sc.find_edge_crop_box(stack).x0 == 0


def test_a_stack_that_is_mostly_noisy_edge_is_left_whole() -> None:
    """If a side would lose more than a quarter, something else is wrong."""
    box = sc.find_edge_crop_box(make_stack(left_noisy=400))

    assert box.x0 == 0


def test_a_blank_stack_is_left_whole() -> None:
    """With no sky to measure, nothing is trimmed."""
    box = sc.find_edge_crop_box(np.zeros((SIZE, SIZE)))

    assert (box.x0, box.x1, box.y0, box.y1) == (0, SIZE, 0, SIZE)


def test_cropping_a_file_trims_the_stack_and_its_rejection_map_and_moves_the_reference(
    tmp_path: Path,
) -> None:
    """Both files shrink alike and the sky-coordinate reference follows."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    rejection = tmp_path / "M_27_L_Stacked_RejMap.fits"
    header = fits.Header()
    header["CRPIX1"] = 500.0
    header["CRPIX2"] = 600.0
    data = make_stack(left_noisy=100, bottom_noisy=200).astype(np.float32)
    fits.writeto(stack, data, header)
    fits.writeto(rejection, np.ones((SIZE, SIZE), dtype=np.float32), header)

    box = sc.crop_stack_edges(str(stack))

    assert box is not None
    with fits.open(stack, memmap=False) as hdul:
        assert hdul[0].data.shape == (box.y1 - box.y0, box.x1 - box.x0)
        assert hdul[0].data.shape[0] == hdul[0].data.shape[1]
        assert hdul[0].header["CRPIX1"] == pytest.approx(500.0 - box.x0)
        assert hdul[0].header["CRPIX2"] == pytest.approx(600.0 - box.y0)
        assert hdul[0].data[0, 0] == pytest.approx(data[box.y0, box.x0])
    with fits.open(rejection, memmap=False) as hdul:
        assert hdul[0].data.shape == (box.y1 - box.y0, box.x1 - box.x0)


def test_cropping_a_clean_file_changes_nothing(tmp_path: Path) -> None:
    """A stack with clean edges is not rewritten."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))
    before = stack.stat().st_mtime_ns

    assert sc.crop_stack_edges(str(stack)) is None
    assert stack.stat().st_mtime_ns == before


def shift_matrix(dx: float, dy: float) -> list[float]:
    """Give the nine numbers of a transform that only shifts a frame.

    Parameters
    ----------
    dx, dy : `float`
        The shift in pixels, along the columns and along Siril's y axis.

    Returns
    -------
    values : `list` [`float`]
        The 3 x 3 transform in row order.
    """
    return [1.0, 0.0, dx, 0.0, 1.0, dy, 0.0, 0.0, 1.0]


def shifted_group_diagnostics(count: int, shifted: int, dx: float, dy: float) -> dict:
    """Build the registration facts of frames where the last few are shifted.

    Parameters
    ----------
    count : `int`
        Number of frames.
    shifted : `int`
        Number of frames, from the end, shifted by (dx, dy).
    dx, dy : `float`
        The shift of those frames.

    Returns
    -------
    diagnostics : `dict`
        The ``registration_frames`` and ``registration_included`` entries.
    """
    frames = [
        {"homography": shift_matrix(dx, dy) if i >= count - shifted else shift_matrix(0, 0)}
        for i in range(count)
    ]
    return {"registration_frames": frames, "registration_included": [True] * count}


def test_a_thin_edge_covered_by_few_frames_is_cut_and_the_shape_is_kept() -> None:
    """A group shifted 100 px leaves a thin edge; the crop is square."""
    diagnostics = shifted_group_diagnostics(120, 12, 100.0, 40.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)

    assert box.x1 - box.x0 == box.y1 - box.y0
    assert box.x0 >= 100 - 4
    assert box.y1 <= SIZE - 40 + 4
    assert box.x1 <= SIZE
    assert box.y0 >= 0


def test_the_coverage_crop_leaves_only_pixels_that_enough_frames_cover() -> None:
    """Every kept pixel is covered by at least 95% of the frames."""
    diagnostics = shifted_group_diagnostics(120, 12, 100.0, 40.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)

    kept = counts[box.y0 // 4 : (box.y1 - 1) // 4 + 1, box.x0 // 4 : (box.x1 - 1) // 4 + 1]
    assert kept.min() >= 114


def test_a_few_frames_shifted_a_little_cost_only_a_thin_margin() -> None:
    """A group shifted 10 px cuts about 10 px from the crop, not hundreds."""
    diagnostics = shifted_group_diagnostics(100, 10, 10.0, 0.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)

    assert SIZE - 30 <= box.x1 - box.x0 < SIZE


def test_full_coverage_does_not_crop() -> None:
    """Frames that all cover the whole stack leave it whole, with no reason."""
    diagnostics = shifted_group_diagnostics(50, 0, 0.0, 0.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)

    assert (box.x0, box.x1, box.y0, box.y1) == (0, SIZE, 0, SIZE)
    assert box.reason == ""


def test_the_reason_names_the_cut_sides_the_frame_counts_and_the_rule() -> None:
    """The reason names the edge, the pixels, the frame count and the rule."""
    diagnostics = shifted_group_diagnostics(120, 12, 100.0, 0.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)

    assert "left" in box.reason
    assert "108 of 120 frames" in box.reason
    assert "at least 114 of 120 frames (95%)" in box.reason


def test_frames_that_are_not_included_are_not_counted() -> None:
    """A shifted frame left out of the stack does not shrink the crop."""
    diagnostics = shifted_group_diagnostics(20, 1, 300.0, 0.0)
    diagnostics["registration_included"][-1] = False
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)

    assert (box.x0, box.x1) == (0, SIZE)


def test_a_group_shifted_over_a_quarter_of_the_side_leaves_the_stack_whole() -> None:
    """A crop that large means something else is wrong; nothing is cut."""
    diagnostics = shifted_group_diagnostics(50, 10, 400.0, 0.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    assert sc.find_coverage_crop_box(counts, 4, SIZE, SIZE).x0 == 0


def test_cropping_a_file_by_coverage_records_the_reason(tmp_path: Path) -> None:
    """The file is cropped to the box, which carries the reason."""
    stack = tmp_path / "NGC_7331_L_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))

    box = sc.crop_stack_edges(str(stack), shifted_group_diagnostics(120, 12, 100.0, 40.0))

    assert box is not None
    assert "covered by as few as" in box.reason
    with fits.open(stack, memmap=False) as hdul:
        assert hdul[0].data.shape == (box.y1 - box.y0, box.x1 - box.x0)


def test_the_crop_reason_is_stored_as_a_notice_row_of_the_stack() -> None:
    """The crop reason becomes a notice row holding the area share."""
    from astrometricslib.models.quality.quality_summary import (
        StackingPipelineQualityMetrics,
        StackQualitySummary,
    )
    from astrometricslib.pipelines.stacking.post_processing.record_quality import (
        stacking_notice_measurements,
    )
    from astrometricslib.pipelines.stacking.stage import _record_stack_crop

    summary = StackQualitySummary(
        targetId="NGC 7331",
        stackingMetrics=StackingPipelineQualityMetrics(isSpectral=False, framesSubmitted=3, framesStacked=3),
    )
    _record_stack_crop(
        summary, {"stack_crop": {"reason": "Cropped to the area covered.", "area_fraction": 0.2}}
    )
    context = PipelineRunContext.from_options(
        "NGC 7331", "stacking", "1", {"job_id": "job-1", "measured_at": datetime(2026, 11, 20, tzinfo=UTC)}
    )

    rows = [
        row
        for row in stacking_notice_measurements(summary, context, "NGC_7331_Stacked.fits")
        if row.metric == "stacking.notice_stack_cropped"
    ]

    assert len(rows) == 1
    assert rows[0].value == pytest.approx(0.2)
    assert "Cropped to the area covered." in rows[0].reason


def test_trimming_a_stack_puts_the_reason_in_the_run_diagnostics(tmp_path: Path) -> None:
    """Trimming leaves the crop's reason and area share in the diagnostics."""
    from astrometricslib.pipelines.stacking.stack_settings import trim_noisy_stack_edges

    stack = tmp_path / "NGC_7331_L_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))
    diagnostics = shifted_group_diagnostics(120, 12, 100.0, 40.0)

    class Configuration:
        """A configuration with the trim switched on."""

        def get_trim_noisy_stack_edges_enabled(self) -> bool:
            """Say that trimming is on.

            Returns
            -------
            enabled : `bool`
                Always `True`.
            """
            return True

    trim_noisy_stack_edges(str(stack), Configuration(), is_spectral=False, diagnostics=diagnostics)

    assert "covered by as few as" in diagnostics["stack_crop"]["reason"]
    assert 0.0 < diagnostics["stack_crop"]["area_fraction"] < 0.5
