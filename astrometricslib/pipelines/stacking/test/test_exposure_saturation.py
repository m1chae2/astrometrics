"""Purpose: Unit tests for telling whether an exposure saturates a star.

Description: Users adjust their imaging session by knowing, per exposure
length, whether a star is clipped, and what exposure would not clip it. These
tests check that a clipped star is found (and a lone hot pixel at the ceiling
is not), that the ceiling comes from the pixels of a clipped frame and from the
camera otherwise, that the brightest unclipped star's peak is measured, that
the recommendation scales with exposure, and that a star too heavily clipped to
estimate gives no recommendation.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.stacking.exposure_saturation import (
    DEFAULT_CEILING_ADU,
    MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO,
    SATURATED_BLOB_MINIMUM_PIXELS,
    TARGET_PEAK_FRACTION,
    FrameSaturation,
    camera_ceiling_adu,
    group_is_saturated,
    measure_frame_saturation,
    recommend_exposure_seconds,
    recommend_stack_exposure_seconds,
)

SKY = 500.0
CEILING = 16383.0


def star_frame(
    peak_above_sky: float, size: int = 96, sigma: float = 1.3, hot_pixel: bool = False
) -> np.ndarray:
    """Build a frame: flat sky, one Gaussian star, maybe a hot pixel.

    Returns
    -------
    frame : `numpy.ndarray`
        A ``size`` x ``size`` float32 frame, clipped at `CEILING`.
    """
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    frame = SKY + peak_above_sky * np.exp(-((x - 48.3) ** 2 + (y - 47.6) ** 2) / (2 * sigma**2))
    if hot_pixel:
        frame[10, 10] = CEILING
    return np.minimum(np.round(frame), CEILING).astype(np.float32)


def test_an_unclipped_star_is_not_saturated_and_its_peak_is_measured() -> None:
    """A star peaking 6000 above the sky is below the ceiling, measured."""
    result = measure_frame_saturation(star_frame(6000.0), camera="Nikon DSLR DSC D5300")

    assert not result.saturated
    assert not result.peak_is_estimated
    assert result.ceiling_adu == CEILING
    assert result.peak_above_sky_adu == pytest.approx(
        6000.0 * np.exp(-((0.3**2) + (0.4**2)) / (2 * 1.3**2)), rel=0.05
    )


def test_a_clipped_star_is_saturated_and_the_frame_gives_its_own_ceiling() -> None:
    """A far brighter star clips a blob and the frame gives the ceiling."""
    result = measure_frame_saturation(star_frame(40000.0), camera="something unknown")

    assert result.saturated
    assert result.ceiling_adu == CEILING
    assert result.peak_is_estimated


def test_a_hot_pixel_at_the_ceiling_is_not_a_saturated_star() -> None:
    """One isolated pixel at the ceiling is below the blob size, not a star."""
    result = measure_frame_saturation(star_frame(3000.0, hot_pixel=True), camera="Nikon DSLR DSC D5300")

    assert not result.saturated
    # And the hot pixel is not taken as the star's peak.
    assert result.peak_above_sky_adu < 4000.0


def test_the_minimum_blob_size_separates_hot_pixels_from_stars() -> None:
    """A 2 x 2 patch at the ceiling is saturated; a lone pixel is not."""
    frame = np.full((40, 40), SKY, dtype=np.float32)
    frame[20, 20] = CEILING
    assert not measure_frame_saturation(frame).saturated

    frame[20:22, 20:22] = CEILING
    assert SATURATED_BLOB_MINIMUM_PIXELS == 4
    assert measure_frame_saturation(frame).saturated


@pytest.mark.parametrize(
    ("camera", "expected"),
    [
        ("ZWO CCD ASI533MM Pro", 65532.0),
        ("Nikon DSLR DSC D5300", 16383.0),
        ("Unknown camera", DEFAULT_CEILING_ADU),
        (None, DEFAULT_CEILING_ADU),
    ],
)
def test_the_camera_ceiling_is_looked_up_by_family(camera: str | None, expected: float) -> None:
    """The clipping value comes from the camera family, else 16-bit."""
    assert camera_ceiling_adu(camera) == expected


def make(saturated: bool, peak: float | None, sky: float = SKY, ceiling: float = CEILING) -> FrameSaturation:
    """Build a `FrameSaturation` for the group and recommendation tests.

    Returns
    -------
    frame : `FrameSaturation`
        The described frame.
    """
    return FrameSaturation(saturated, ceiling, sky, peak, saturated)


def test_a_group_is_saturated_when_half_its_frames_are() -> None:
    """One clipped frame in three (a cosmic ray, say) is not saturation."""
    assert not group_is_saturated([make(True, 1.0), make(False, 1.0), make(False, 1.0)])
    assert group_is_saturated([make(True, 1.0), make(True, 1.0), make(False, 1.0)])
    assert not group_is_saturated([])


def test_the_recommended_exposure_puts_the_peak_at_the_target_fraction() -> None:
    """A star at half the room in 10 s needs 16 s to reach 80%."""
    room = CEILING - SKY
    frames = [make(False, 0.5 * room), make(False, 0.5 * room)]

    assert recommend_exposure_seconds(10.0, frames) == pytest.approx(10.0 * TARGET_PEAK_FRACTION / 0.5)


def test_a_clipped_group_is_told_to_shorten_the_exposure() -> None:
    """A star at twice the room in 10 s needs 4 s to reach 80% of it."""
    room = CEILING - SKY
    frames = [make(True, 2.0 * room)]

    assert recommend_exposure_seconds(10.0, frames) == pytest.approx(10.0 * TARGET_PEAK_FRACTION / 2.0)


def test_no_recommendation_without_a_peak() -> None:
    """Frames that gave no peak, or an empty list, give no recommendation."""
    assert recommend_exposure_seconds(10.0, [make(True, None)]) is None
    assert recommend_exposure_seconds(10.0, []) is None


def test_a_star_clipped_far_beyond_the_estimate_limit_gives_no_peak() -> None:
    """A huge clipped core is too heavy to estimate, so it gives no peak."""
    result = measure_frame_saturation(star_frame(5.0e6, size=200, sigma=6.0), camera="Nikon DSLR DSC D5300")

    assert result.saturated
    assert result.peak_above_sky_adu is None
    assert MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO == pytest.approx(100.0)


def test_the_stack_recommendation_trusts_a_measured_group_over_an_estimated_one() -> None:
    """A measured 5 s group is used, not the clipped 30 s group's estimate."""
    room = CEILING - SKY
    short = [make(False, 0.2 * room)]
    long = [make(True, 3.0 * room)]

    recommended = recommend_stack_exposure_seconds([5.0, 30.0], [short, long])

    assert recommended == pytest.approx(5.0 * TARGET_PEAK_FRACTION / 0.2)


def test_the_stack_recommendation_uses_the_estimates_when_every_group_is_clipped() -> None:
    """With no measured group, the median of the estimates is used."""
    room = CEILING - SKY
    groups = [[make(True, 2.0 * room)], [make(True, 4.0 * room)], [make(True, 8.0 * room)]]

    recommended = recommend_stack_exposure_seconds([10.0, 10.0, 10.0], groups)

    assert recommended == pytest.approx(10.0 * TARGET_PEAK_FRACTION / 4.0)


def test_the_stack_recommendation_is_none_when_no_group_gives_one() -> None:
    """Groups with no peak at all give `None`."""
    assert recommend_stack_exposure_seconds([5.0], [[make(True, None)]]) is None
