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

    crops = sc.crop_stack_edges(str(stack))

    assert crops is not None
    box = crops.stack
    assert box is not None
    assert crops.processed is None
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

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE, 0.95)

    assert box.x1 - box.x0 == box.y1 - box.y0
    assert box.x0 >= 100 - 4
    assert box.y1 <= SIZE - 40 + 4
    assert box.x1 <= SIZE
    assert box.y0 >= 0


def test_the_coverage_crop_leaves_only_pixels_that_enough_frames_cover() -> None:
    """Every kept pixel is covered by enough of the frames."""
    diagnostics = shifted_group_diagnostics(120, 12, 100.0, 40.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE, 0.95)

    kept = counts[box.y0 // 4 : (box.y1 - 1) // 4 + 1, box.x0 // 4 : (box.x1 - 1) // 4 + 1]
    assert kept.min() >= 114


def test_a_few_frames_shifted_a_little_cost_only_a_thin_margin() -> None:
    """A group shifted 10 px cuts about 10 px from the crop, not hundreds."""
    diagnostics = shifted_group_diagnostics(100, 10, 10.0, 0.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE, 0.95)

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

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE, 0.95)

    assert "left" in box.reason
    assert "108 of 120 frames" in box.reason
    assert "at least 114 of 120 frames (95%)" in box.reason


def test_frames_that_are_not_included_are_not_counted() -> None:
    """A shifted frame left out of the stack does not shrink the crop."""
    diagnostics = shifted_group_diagnostics(20, 1, 300.0, 0.0)
    diagnostics["registration_included"][-1] = False
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE, 0.95)

    assert (box.x0, box.x1) == (0, SIZE)


def test_a_group_shifted_over_a_quarter_of_the_side_leaves_the_stack_whole() -> None:
    """A crop that large means something else is wrong; nothing is cut."""
    diagnostics = shifted_group_diagnostics(50, 10, 400.0, 0.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    assert sc.find_coverage_crop_box(counts, 4, SIZE, SIZE, 0.95).x0 == 0


def test_cropping_a_file_by_coverage_records_the_reason(tmp_path: Path) -> None:
    """The file is cropped to the box, which carries the reason."""
    stack = tmp_path / "NGC_7331_L_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))

    crops = sc.crop_stack_edges(str(stack), shifted_group_diagnostics(120, 70, 100.0, 40.0))

    assert crops is not None
    box = crops.stack
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
    diagnostics = shifted_group_diagnostics(120, 70, 100.0, 40.0)

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


def test_the_stack_keeps_edges_that_more_than_half_the_frames_cover(tmp_path: Path) -> None:
    """25 of 120 frames shifted 100 px leave the stack whole."""
    stack = tmp_path / "NGC_7331_L_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))
    diagnostics = shifted_group_diagnostics(120, 25, 100.0, 40.0)

    crops = sc.crop_stack_edges(str(stack), diagnostics)

    assert crops is not None
    assert crops.stack is None
    with fits.open(stack, memmap=False) as hdul:
        assert hdul[0].data.shape == (SIZE, SIZE)


def test_a_stack_that_is_trimmed_only_below_half_coverage(tmp_path: Path) -> None:
    """With 70 of 120 frames shifted, the strip only 50 cover is cut."""
    diagnostics = shifted_group_diagnostics(120, 70, 100.0, 0.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)

    assert 100 - 4 <= box.x0 <= 100 + 4
    assert "at least 60 of 120 frames (50%)" in box.reason


def test_the_processed_picture_is_cropped_to_85_percent_coverage_in_the_stack_shape() -> None:
    """The picture box holds pixels 85% of frames cover, in shape."""
    diagnostics = shifted_group_diagnostics(120, 25, 100.0, 40.0)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None
    stack_box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)

    box = sc.find_coverage_crop_box(
        counts,
        4,
        SIZE,
        SIZE,
        sc.PROCESSED_MINIMUM_COVERAGE_FRACTION.default,
        within=stack_box,
        limit_trim=False,
    )

    assert box.x1 - box.x0 == box.y1 - box.y0
    assert box.x0 >= 100 - 4
    kept = counts[box.y0 // 4 : (box.y1 - 1) // 4 + 1, box.x0 // 4 : (box.x1 - 1) // 4 + 1]
    assert kept.min() >= 102
    assert box.x0 >= stack_box.x0 and box.x1 <= stack_box.x1


def test_the_processed_box_is_given_on_the_trimmed_stack(tmp_path: Path) -> None:
    """The picture box counts from the trimmed stack's corner."""
    stack = tmp_path / "NGC_7331_L_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))
    # 50 frames sit still, 30 are shifted 100 px and 40 are shifted 130 px, so
    # the first 100 columns are covered by 50 frames (42%), the next 30 by 80
    # (67%) and the rest by all.
    shifts = [0.0] * 50 + [100.0] * 30 + [130.0] * 40
    diagnostics = {
        "registration_frames": [{"homography": shift_matrix(dx, 0.0)} for dx in shifts],
        "registration_included": [True] * 120,
    }

    crops = sc.crop_stack_edges(str(stack), diagnostics)

    assert crops is not None and crops.stack is not None and crops.processed is not None
    trimmed_width = crops.stack.x1 - crops.stack.x0
    assert 0 <= crops.processed.x0 < crops.processed.x1 <= trimmed_width
    assert crops.processed.x1 - crops.processed.x0 == crops.processed.y1 - crops.processed.y0


def test_trimming_records_the_processed_box_in_the_diagnostics(tmp_path: Path) -> None:
    """The diagnostics carry the picture box; the stack is left alone."""
    from astrometricslib.pipelines.stacking.stack_settings import trim_noisy_stack_edges

    stack = tmp_path / "NGC_7331_L_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))
    diagnostics = shifted_group_diagnostics(120, 25, 100.0, 40.0)

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

    offset = trim_noisy_stack_edges(str(stack), Configuration(), is_spectral=False, diagnostics=diagnostics)

    assert offset == (0, 0)
    assert "stack_crop" not in diagnostics
    x0, x1, y0, y1 = diagnostics["processed_crop"]
    assert x0 >= 96 and x1 - x0 == y1 - y0


def test_a_crop_keeps_a_stars_sky_position_exact(tmp_path: Path) -> None:
    """A star keeps its sky position on the cropped file (with SIP)."""
    from astropy.wcs import WCS

    from astrometricslib.pipelines.stacking.post_processing.stack_preview import picture_crop_box

    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [500.0, 480.0]
    wcs.wcs.crval = [250.0, 36.0]
    wcs.wcs.cdelt = [-0.0003, 0.0003]
    wcs.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
    wcs.sip = __import__("astropy.wcs", fromlist=["Sip"]).Sip(
        np.array([[0, 0, 1e-7], [0, 2e-7, 0], [1e-7, 0, 0]]),
        np.array([[0, 0, 0], [0, 1e-7, 0], [2e-7, 0, 0]]),
        None,
        None,
        wcs.wcs.crpix,
    )
    header = wcs.to_header(relax=True)
    stack = tmp_path / "M_1_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32), header)
    box = sc.CropBox(120, 920, 130, 930)

    sc.crop_file(str(stack), box, note_crop=True)

    with fits.open(stack, memmap=False) as hdul:
        cropped_header = hdul[0].header
        assert (cropped_header["CROPX0"], cropped_header["CROPY0"]) == (120, 130)
        assert (cropped_header["CROPW"], cropped_header["CROPH"]) == (800, 800)
        cropped_wcs = WCS(cropped_header)
    for column, row in [(300.0, 400.0), (700.5, 250.25), (130.0, 900.0)]:
        before = wcs.all_pix2world([[column, row]], 0)
        after = cropped_wcs.all_pix2world([[column - 120, row - 130]], 0)
        assert np.allclose(before, after, atol=1e-9)
    assert picture_crop_box(str(tmp_path / "missing.fits")) is None


def test_turning_a_box_matches_turning_the_image() -> None:
    """The turned box holds exactly the pixels the original box held."""
    from astrometricslib.pipelines.stacking.post_processing.quarter_turn import turn_image

    image = np.arange(60 * 100).reshape(60, 100)
    box = sc.CropBox(10, 70, 5, 45)
    for degrees in (0, 90, 180, 270):
        turned_box = sc.turn_box(box, degrees, 100, 60)
        expected = turn_image(image[box.y0 : box.y1, box.x0 : box.x1], degrees)
        turned = turn_image(image, degrees)[turned_box.y0 : turned_box.y1, turned_box.x0 : turned_box.x1]
        assert np.array_equal(turned, expected)


@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
def test_the_picture_crop_composes_with_a_quarter_turn(tmp_path: Path, degrees: int) -> None:
    """The turned stack, cropped, is the turned crop of the registered one."""
    from astrometricslib.pipelines.shared.registration_record import (
        RegistrationRecord,
        read_registration_record,
        write_registration_record,
    )
    from astrometricslib.pipelines.stacking.post_processing.quarter_turn import turn_image
    from astrometricslib.pipelines.stacking.post_processing.stack_preview import picture_crop_box

    registered = np.arange(60 * 100, dtype=np.float32).reshape(60, 100)
    stack = tmp_path / "M_1_Stacked.fits"
    header = fits.Header()
    header["QTURN"] = degrees
    fits.writeto(stack, turn_image(registered, degrees), header)
    record = RegistrationRecord(
        ("a.fits",), (tuple(shift_matrix(0, 0)),), (True,), (0, 0), None, False, degrees, (10, 70, 5, 45)
    )
    write_registration_record(str(stack), record)
    assert read_registration_record(str(stack)).processed_crop == (10, 70, 5, 45)

    box = picture_crop_box(str(stack))

    assert box is not None
    with fits.open(stack, memmap=False) as hdul:
        picture = hdul[0].data[box.y0 : box.y1, box.x0 : box.x1]
    assert np.array_equal(picture, turn_image(registered[5:45, 10:70], degrees))


def similarity_record(move: list[float]) -> dict:
    """Write the position map of a move given as shift, turn and scale.

    The move sends a group position ``z`` to ``a * (z - c) + c + t`` (``c`` is
    the image center and ``z = column + 1j * row``), so the map reads the group
    at the inverse.

    Parameters
    ----------
    move : `list` [`float`]
        The shift along the rows, shift along the columns, rotation in degrees
        and scale.

    Returns
    -------
    record : `dict`
        The record `GroupPositionMap.to_record` writes.
    """
    from astrometricslib.pipelines.stacking.processing.group_position_map import GroupPositionMap

    nodes = np.linspace(0.0, SIZE - 1.0, 126)
    rows, columns = np.meshgrid(nodes, nodes, indexing="ij")
    center = complex((SIZE - 1) / 2.0, (SIZE - 1) / 2.0)
    turn = move[3] * np.exp(1j * np.radians(move[2]))
    source = center + (columns + 1j * rows - center - complex(move[1], move[0])) / turn
    position_map = GroupPositionMap((SIZE, SIZE), node_rows=source.imag, node_columns=source.real)
    return position_map.to_record()


def aligned_groups_diagnostics(first: int, second: int, move: list[float]) -> dict:
    """Build the registration facts of two exposure groups, the second moved.

    Every frame sits on its group's grid with no shift of its own, so any
    difference in coverage comes from the move of the second group.

    Parameters
    ----------
    first, second : `int`
        Number of frames in the reference group and in the moved group.
    move : `list` [`float`]
        The second group's shift along the rows, shift along the columns,
        rotation in degrees and scale.

    Returns
    -------
    diagnostics : `dict`
        The ``registration_frames``, ``registration_included`` and
        ``exposure_group_alignments`` entries.
    """
    diagnostics = shifted_group_diagnostics(first + second, 0, 0.0, 0.0)
    diagnostics["exposure_group_alignments"] = [
        {"frame_count": first, "alignment": None},
        {"frame_count": second, "alignment": similarity_record(move)},
    ]
    return diagnostics


def test_a_group_moved_onto_the_reference_leaves_its_uncovered_strip_out_of_the_count() -> None:
    """A group moved 300 rows and turned 180 degrees misses the first rows."""
    diagnostics = aligned_groups_diagnostics(35, 15, [300.0, 0.0, 180.0, 1.0])

    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)

    assert counts is not None
    # The turn about the centre sends row r to 999 - r + 300, so the moved
    # group covers rows 300 to 999 (less its resampled margin). Before that,
    # only the 35 frames of the first group cover. The first and last columns
    # are the moved group's margin.
    assert counts[: 300 // 4].max() == 35
    assert counts[304 // 4 :, 2:-2].min() == 50


def test_the_processed_box_leaves_out_the_strip_only_the_reference_group_covers() -> None:
    """The 85% box starts below the strip the moved group does not reach."""
    diagnostics = aligned_groups_diagnostics(35, 15, [300.0, 0.0, 180.0, 1.0])
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None

    stack_box = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE)
    processed = sc.find_coverage_crop_box(counts, 4, SIZE, SIZE, 0.85, within=stack_box, limit_trim=False)

    # 35 of 50 frames is 70%: kept by the 50% rule, cut from the picture.
    assert stack_box.y0 == 0
    assert processed.y0 >= 300
    assert processed.x1 - processed.x0 == processed.y1 - processed.y0


def test_the_count_without_the_group_alignments_thinks_every_frame_covers_everything() -> None:
    """Left out, the groups' alignments make the count full everywhere."""
    diagnostics = aligned_groups_diagnostics(35, 15, [300.0, 0.0, 180.0, 1.0])
    del diagnostics["exposure_group_alignments"]

    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)

    assert counts is not None
    assert counts.min() == 50


@pytest.mark.parametrize(
    "move", [[60.0, -35.0, 20.0, 1.002], [-80.0, 25.0, -7.5, 1.0], [10.0, 90.0, 180.0, 1.0]]
)
def test_the_coverage_matches_the_pixels_the_alignment_moves_in(move: list[float]) -> None:
    """The cells counted as covered are those `apply_alignment` fills."""
    from astrometricslib.pipelines.stacking.processing.group_alignment import AlignmentResult, apply_alignment
    from astrometricslib.pipelines.stacking.processing.group_position_map import GroupPositionMap

    diagnostics = aligned_groups_diagnostics(1, 1, move)
    counts = sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE)
    assert counts is not None
    result = AlignmentResult(
        move[0],
        move[1],
        1.0,
        method="siril",
        position_map=GroupPositionMap.from_record(similarity_record(move)),
    )
    _, filled = apply_alignment(np.ones((SIZE, SIZE), dtype=np.float32), result, order=1)

    sampled = filled[::4, ::4]
    counted = counts == 2
    # They agree except in the few cells at the border (the margin).
    assert np.mean(counted != sampled) < 0.01
    assert not np.any(counted & ~sampled)


def test_group_frame_counts_that_do_not_match_the_frames_give_no_coverage() -> None:
    """When a frame cannot be given its group's move, no count is made."""
    diagnostics = aligned_groups_diagnostics(35, 15, [300.0, 0.0, 180.0, 1.0])
    diagnostics["exposure_group_alignments"][0]["frame_count"] = 30

    assert sc._coverage_from_diagnostics(diagnostics, SIZE, SIZE) is None


def test_trimming_a_stack_of_aligned_groups_crops_the_picture_past_the_uncovered_strip(
    tmp_path: Path,
) -> None:
    """`crop_stack_edges` gives a processed box that starts below the strip."""
    stack = tmp_path / "NGC_6992_NONE_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))
    diagnostics = aligned_groups_diagnostics(47, 17, [300.0, 4.0, 180.0, 1.0])

    crops = sc.crop_stack_edges(str(stack), diagnostics)

    assert crops is not None and crops.processed is not None
    top = 0 if crops.stack is None else crops.stack.y0
    assert top + crops.processed.y0 >= 300


def test_a_box_kept_inside_another_never_reaches_past_it() -> None:
    """The picture crop stays inside the stack crop, shape kept."""
    step = 4
    counts = np.full((101, 151), 10, dtype=np.int32)
    counts[:, :20] = 7
    within = sc.CropBox(4, 599, 4, 399)
    box = sc.find_coverage_crop_box(counts, step, 600, 400, 0.85, within=within, limit_trim=False)
    assert within.x0 <= box.x0 < box.x1 <= within.x1
    assert within.y0 <= box.y0 < box.y1 <= within.y1
