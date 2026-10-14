"""Tests for measuring a raw slitless-spectrum frame.

A made-up frame is used: flat noisy sky, a bright zero-order star, and a
vertical streak running down from it with a known width and brightness.
Further tests clip a synthetic frame at 16383 ADU, as a 14-bit camera does,
to check that the saturation level comes from the camera profile, and check
that a caller can give the zero-order position instead of searching for it.
"""

from pathlib import Path

import numpy as np
import pytest

from astrometricslib.pipelines.shared.quality.spectral_frame_check import (
    SATURATION_ADU,
    analyze_spectral_frame,
    measure_spectral_frame_file,
    resolve_saturation_threshold,
    summarize_spectral_frames,
)
from astrometricslib.test.synthetic.spectral_frame import make_spectral_frame

SKY = 400.0
STAR_ROW, STAR_COLUMN = 200, 300
OFFSET, LENGTH = 50.0, 300.0
GEOMETRY = {"vertical": True, "positive": True, "offset_px": OFFSET, "length_px": LENGTH}


def make_frame(
    streak_peak: float = 3000.0, width_sigma: float = 2.0, clip_at: int | None = None
) -> np.ndarray:
    """Build a noisy sky, a zero-order star and a vertical streak.

    Parameters
    ----------
    streak_peak : `float`
        The streak's height above the sky.
    width_sigma : `float`
        The streak's Gaussian width across.
    clip_at : `int`, optional
        A row inside the spectrum where a small saturated patch is placed.

    Returns
    -------
    frame : `numpy.ndarray`
        The image.
    """
    generator = np.random.default_rng(5)
    frame = generator.normal(SKY, 5.0, (700, 600))
    rows, columns = np.mgrid[:700, :600]
    frame += 66000.0 * np.exp(-((rows - STAR_ROW) ** 2 + (columns - STAR_COLUMN) ** 2) / 8.0)
    along = rows - STAR_ROW
    inside = (along >= OFFSET) & (along <= OFFSET + LENGTH)
    frame += np.where(inside, streak_peak, 0.0) * np.exp(
        -((columns - STAR_COLUMN) ** 2) / (2 * width_sigma**2)
    )
    if clip_at is not None:
        frame[clip_at : clip_at + 3, STAR_COLUMN - 1 : STAR_COLUMN + 2] = SATURATION_ADU + 100
    return frame


def analyze(frame: np.ndarray, predict: float | None = None) -> dict:
    """Run the analysis on a frame made by `make_frame`.

    Returns
    -------
    measurements : `dict`
        The result of `analyze_spectral_frame`.
    """
    return analyze_spectral_frame(
        frame, (STAR_ROW, STAR_COLUMN), exposure_seconds=2.0, predict_exposure_seconds=predict, **GEOMETRY
    )


def test_width_and_peak_of_the_streak_are_recovered() -> None:
    """A Gaussian of sigma 2 has a full width at half maximum of 4.7 px."""
    result = analyze(make_frame())
    assert 4.3 < result["spectrum_width_px"] < 5.1
    assert 2500 < result["spectrum_peak_above_sky_adu"] < 3300
    assert result["spectrum_pixels_saturated"] == 0


def test_the_zero_order_clipping_is_not_counted_as_spectrum_loss() -> None:
    """A clipped star core is reported on its own."""
    result = analyze(make_frame(), None)
    assert result["zero_order_saturated"] is True
    assert result["saturated_patches_in_spectrum"] == []


def test_a_clipped_patch_in_the_spectrum_is_located_along_it() -> None:
    """A patch 120 rows below the star is reported at 120 px."""
    result = analyze(make_frame(clip_at=STAR_ROW + 120))
    patch = result["saturated_patches_in_spectrum"][0]
    assert abs(patch["distance_along_spectrum_px"] - 121) <= 2
    assert patch["pixels"] == 9


def test_the_peak_is_predicted_at_another_exposure() -> None:
    """Doubling the exposure doubles the peak above the sky."""
    result = analyze(make_frame(), predict=4.0)
    prediction = result["predicted"]
    assert abs(prediction["spectrum_peak_adu"] - (2 * result["spectrum_peak_above_sky_adu"] + SKY)) < 2
    assert prediction["spectrum_would_saturate"] is False
    assert (
        analyze(make_frame(streak_peak=40000.0), predict=4.0)["predicted"]["spectrum_would_saturate"] is True
    )


def test_the_summary_groups_by_exposure_and_by_pier_side() -> None:
    """Tilts are summarised per pier side, and clipped frames per exposure."""
    base = {
        "trail_contrast": 500.0,
        "zero_order_saturated": True,
        "spectrum_pixels_saturated": 0,
        "spectrum_width_px": 5.0,
    }
    rows = [
        {**base, "exposure_seconds": 2.0, "pier_side": "EAST", "tilt_degrees": 4.1},
        {**base, "exposure_seconds": 2.0, "pier_side": "WEST", "tilt_degrees": 1.0},
        {
            **base,
            "exposure_seconds": 5.0,
            "pier_side": "WEST",
            "tilt_degrees": 1.2,
            "spectrum_pixels_saturated": 9,
        },
        {"exposure_seconds": 5.0, "pier_side": "WEST", "error": "no zero order"},
    ]
    summary = summarize_spectral_frames(rows, minimum_contrast=10.0)
    assert summary["by_exposure"]["5"]["frames"] == 2
    assert summary["by_exposure"]["5"]["measured"] == 1
    assert summary["by_exposure"]["5"]["spectrum_has_saturated_pixels"] == 1
    assert summary["by_pier_side"]["EAST"]["median_tilt_degrees"] == pytest.approx(4.1)
    assert summary["by_pier_side"]["WEST"]["largest_tilt_degrees"] == pytest.approx(1.2)


def test_a_frame_without_a_zero_order_star_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a frame with no clear zero order raises `ProcessingError`.

    It used to return ``{"error": ...}``, which a caller could mistake for
    a measurement.
    """
    from astropy.io import fits

    from astrometricslib.foundation.errors import ProcessingError
    from astrometricslib.pipelines.shared.quality import spectral_frame_check
    from astrometricslib.pipelines.stacking.processing import group_alignment

    path = tmp_path / "flat.fits"
    fits.PrimaryHDU(np.full((64, 64), SKY, dtype=np.float32)).writeto(path)
    monkeypatch.setattr(group_alignment, "find_zero_order_position", lambda plane: None)

    with pytest.raises(ProcessingError, match="zero-order"):
        spectral_frame_check.measure_spectral_frame_file(str(path), "camera", 5.0, GEOMETRY)


FOURTEEN_BIT_CLIP_ADU = 16383.0
ZERO_ORDER_XY = (60.0, 128.0)
HORIZONTAL_GEOMETRY = {"vertical": False, "positive": True, "offset_px": 50.0, "length_px": 600.0}


def make_fourteen_bit_frame(continuum_adu: float) -> np.ndarray:
    """Build a synthetic spectral frame clipped at 16383 ADU.

    Parameters
    ----------
    continuum_adu : `float`
        The trail's total flux in one column. A value near 3000 leaves the
        trail far below the clip. A value of 110,000 drives its centre past
        16383.

    Returns
    -------
    image : `numpy.ndarray`
        The frame, with the zero order always clipped at 16383.
    """
    frame = make_spectral_frame(
        zero_order_xy=ZERO_ORDER_XY, continuum_adu=continuum_adu, lines=((4861.0, 0.5),)
    )
    return np.minimum(frame.image, FOURTEEN_BIT_CLIP_ADU)


def analyze_horizontal(image: np.ndarray, **settings: float | str) -> dict:
    """Run the analysis on a frame made by `make_fourteen_bit_frame`.

    Returns
    -------
    measurements : `dict`
        The result of `analyze_spectral_frame`.
    """
    column, row = ZERO_ORDER_XY[0], ZERO_ORDER_XY[1]
    return analyze_spectral_frame(
        image, (row, column), exposure_seconds=2.0, **HORIZONTAL_GEOMETRY, **settings
    )


def test_the_nikon_profile_gives_a_level_a_14_bit_frame_can_reach() -> None:
    """The D5300 level is no higher than its clip, and the source names it."""
    threshold, source = resolve_saturation_threshold("Nikon D5300")

    assert threshold <= FOURTEEN_BIT_CLIP_ADU
    assert "Nikon D5300" in source


def test_a_16_bit_camera_keeps_its_profile_level() -> None:
    """The ASI533 profile threshold is used as it stands."""
    threshold, source = resolve_saturation_threshold("ZWO ASI533MM Pro")

    assert threshold == pytest.approx(65000.0)
    assert "ZWO ASI533MM Pro profile saturation threshold" in source


def test_an_unlisted_camera_uses_the_generic_profile_and_says_so() -> None:
    """A camera with no profile gets the generic level, with a source note."""
    threshold, source = resolve_saturation_threshold("Imaginary Camera 9000")

    assert threshold == pytest.approx(65000.0)
    assert "generic fallback" in source


def test_a_clipped_14_bit_zero_order_is_detected_with_the_camera_level() -> None:
    """With the D5300 level the clipped star counts; at 65000 it never does."""
    image = make_fourteen_bit_frame(3000.0)
    threshold, source = resolve_saturation_threshold("Nikon D5300")

    with_profile = analyze_horizontal(image, saturation_threshold_adu=threshold, saturation_source=source)
    with_default = analyze_horizontal(image)

    assert with_profile["zero_order_saturated"] is True
    assert with_profile["saturated_pixels"] > 0
    assert with_profile["spectrum_pixels_saturated"] == 0
    assert with_profile["saturation_threshold_adu"] == threshold
    assert with_profile["saturation_threshold_source"] == source
    assert with_default["zero_order_saturated"] is False
    assert with_default["saturated_pixels"] == 0
    assert with_default["saturation_threshold_adu"] == SATURATION_ADU


def test_a_clipped_14_bit_spectrum_is_counted_as_lost_data() -> None:
    """A trail driven past 16383 ADU clips pixels inside the spectrum."""
    image = make_fourteen_bit_frame(110_000.0)
    threshold, source = resolve_saturation_threshold("Nikon D5300")

    with_profile = analyze_horizontal(image, saturation_threshold_adu=threshold, saturation_source=source)
    with_default = analyze_horizontal(image)

    assert with_profile["spectrum_pixels_saturated"] > 0
    assert with_default["spectrum_pixels_saturated"] == 0


def test_the_prediction_uses_the_camera_level() -> None:
    """Doubling the exposure of a trail at 12000 ADU clips a 14-bit frame."""
    image = make_fourteen_bit_frame(75_000.0)
    threshold, source = resolve_saturation_threshold("Nikon D5300")

    result = analyze_spectral_frame(
        image,
        (ZERO_ORDER_XY[1], ZERO_ORDER_XY[0]),
        exposure_seconds=2.0,
        predict_exposure_seconds=4.0,
        saturation_threshold_adu=threshold,
        saturation_source=source,
        **HORIZONTAL_GEOMETRY,
    )

    assert result["predicted"]["spectrum_would_saturate"] is True
    assert result["predicted"]["zero_order_peak_is_lower_bound"] is True


def write_frame(path: Path, image: np.ndarray) -> None:
    """Write an image to a FITS file for `measure_spectral_frame_file`."""
    from astropy.io import fits

    fits.PrimaryHDU(image.astype(np.float32)).writeto(path)


def test_a_given_zero_order_position_skips_the_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The position is used as given, and the search is never called."""
    from astrometricslib.pipelines.stacking.processing import group_alignment, group_derotation

    def fail_if_called(plane: np.ndarray) -> None:
        """Fail the test when the search runs.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("the search should not run")

    path = tmp_path / "frame.fits"
    write_frame(path, make_fourteen_bit_frame(3000.0))
    monkeypatch.setattr(group_alignment, "locate_zero_order", fail_if_called)
    monkeypatch.setattr(group_derotation, "measure_trail_angle_degrees", lambda *args: (0.0, 0.0))

    result = measure_spectral_frame_file(
        str(path),
        "Nikon D5300",
        2.0,
        HORIZONTAL_GEOMETRY,
        zero_order_row_column=(ZERO_ORDER_XY[1], ZERO_ORDER_XY[0]),
    )

    assert result["zero_order_source"] == "given"
    assert result["zero_order_xy"] == [ZERO_ORDER_XY[0], ZERO_ORDER_XY[1]]
    assert result["zero_order_saturated"] is True
    assert "Nikon D5300" in result["saturation_threshold_source"]


def test_a_searched_position_is_reported_with_how_it_was_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A star is found by the point-source search and the row says so."""
    from astrometricslib.pipelines.stacking.processing import group_derotation

    frame = make_spectral_frame(zero_order_xy=(128.0, 128.0), trail_length_px=100, lines=(), shape=(256, 256))
    path = tmp_path / "frame.fits"
    write_frame(path, np.minimum(frame.image, FOURTEEN_BIT_CLIP_ADU))
    monkeypatch.setattr(group_derotation, "measure_trail_angle_degrees", lambda *args: (0.0, 0.0))
    geometry = {**HORIZONTAL_GEOMETRY, "length_px": 100.0}

    result = measure_spectral_frame_file(str(path), "Nikon D5300", 2.0, geometry)

    assert result["zero_order_source"] == "point source"
    assert result["zero_order_xy"] == [pytest.approx(128.0, abs=1.0), pytest.approx(128.0, abs=1.0)]
    assert result["saturation_threshold_adu"] <= FOURTEEN_BIT_CLIP_ADU
