"""Purpose: Tests for detection-input prep on colour vs monochrome frames.

Description: A debayered (colour) stack's demosaicing interpolation leaves
spatially correlated noise that a matched-filter point-source finder reads
as thousands of star-like features -- see `star_identifier`'s
`_COLOR_DETECTION_BIN_FACTOR` docstring for the synthetic measurements this
was built from. `StarIdentifier.detect_stars` handles this by detecting on
a block-averaged copy for colour frames and rescaling the results back to
full resolution, while monochrome frames instead get their detection
kernel matched to the image's own measured stellar FWHM. These tests cover
the pixel-math both paths depend on, and that each path only does what it
is supposed to.
"""

from unittest.mock import MagicMock, patch

import numpy as np
from scipy.ndimage import zoom

from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
    StarIdentifier,
    _block_average,
    _rescale_source_centroids,
)

# Reproduces this module's `_COLOR_DETECTION_BIN_FACTOR` synthetic
# validation as a locked-in regression test: a bilinear-demosaiced RGGB
# background with realistic per-channel gain and photon/read noise, no
# real stars, using a fixed RNG seed so the array itself is
# reproducible across runs and machines.
_BAYER_GAINS = {"R": 0.85, "G": 1.15, "B": 0.75}
_BASE_ADU = 800.0
_READ_NOISE_SIGMA = 8.0


def _synthetic_demosaiced_background(height: int, width: int, seed: int) -> np.ndarray:
    """Build a starless, bilinear-demosaiced mono luminance for testing.

    Parameters
    ----------
    height, width : `int`
        Output array dimensions; must be even.
    seed : `int`
        RNG seed, for a reproducible array.

    Returns
    -------
    mono : `numpy.ndarray`
        The demosaiced luminance, no real point sources.
    """
    rng = np.random.default_rng(seed)
    half_height, half_width = height // 2, width // 2

    def make_plane(gain: float) -> np.ndarray:
        plane = rng.poisson(_BASE_ADU * gain, size=(half_height, half_width)).astype(float)
        plane += rng.normal(0, _READ_NOISE_SIGMA, size=plane.shape)
        return plane

    red = make_plane(_BAYER_GAINS["R"])
    green = 0.5 * (make_plane(_BAYER_GAINS["G"]) + make_plane(_BAYER_GAINS["G"]))
    blue = make_plane(_BAYER_GAINS["B"])

    def upsample(plane: np.ndarray) -> np.ndarray:
        return zoom(plane, (height / plane.shape[0], width / plane.shape[1]), order=1)

    return (upsample(red) + upsample(green) + upsample(blue)) / 3.0


def _make_identifier() -> StarIdentifier:
    """Build a StarIdentifier with a mocked config and a stubbed detector.

    Returns
    -------
    identifier : `StarIdentifier`
        A ready-to-use identifier with a fake detector attached.
    """
    config = MagicMock()
    config.get_value.return_value = None
    identifier = StarIdentifier(config=config)
    identifier.detector = MagicMock()
    identifier.detector.fwhm = 4.0
    return identifier


def test_block_average_averages_disjoint_2x2_blocks():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify each output pixel is the mean of its own block."""
    data = np.array([
        [1.0, 3.0, 10.0, 30.0],
        [1.0, 3.0, 10.0, 30.0],
        [5.0, 7.0, 50.0, 70.0],
        [5.0, 7.0, 50.0, 70.0],
    ])

    binned = _block_average(data, factor=2)

    assert binned.shape == (2, 2)
    assert binned[0, 0] == 2.0  # ruff: ignore[float-equality-comparison] -- mean of [1,3,1,3]
    assert binned[0, 1] == 20.0  # ruff: ignore[float-equality-comparison] -- mean of [10,30,10,30]
    assert binned[1, 0] == 6.0  # ruff: ignore[float-equality-comparison] -- mean of [5,7,5,7]
    assert binned[1, 1] == 60.0  # ruff: ignore[float-equality-comparison] -- mean of [50,70,50,70]


def test_block_average_drops_a_trailing_partial_block_rather_than_padding():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a non-multiple-of-factor dimension is cropped, not padded.

    Padding would invent pixel values that were never in the frame;
    dropping the leftover row/column keeps every output pixel a real
    average of real input pixels.
    """
    data = np.ones((5, 5))

    binned = _block_average(data, factor=2)

    assert binned.shape == (2, 2)


def test_rescale_source_centroids_maps_binned_coordinates_to_full_resolution():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the rescale matches _block_average's own pixel-centre convention.

    Output pixel j of a factor-N block average covers input pixels
    [j*N, (j+1)*N), centred at j*N + (N-1)/2.
    """
    sources = [{"x_centroid": 0.0, "y_centroid": 0.0}, {"x_centroid": 10.0, "y_centroid": 5.0}]

    _rescale_source_centroids(sources, factor=2)

    assert sources[0]["x_centroid"] == 0.5  # ruff: ignore[float-equality-comparison]
    assert sources[0]["y_centroid"] == 0.5  # ruff: ignore[float-equality-comparison]
    assert sources[1]["x_centroid"] == 20.5  # ruff: ignore[float-equality-comparison]
    assert sources[1]["y_centroid"] == 10.5  # ruff: ignore[float-equality-comparison]


def test_rescale_source_centroids_handles_the_alternate_xcentroid_key():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the xcentroid/ycentroid key spelling also gets rescaled.

    SourceDetector.detect's output dicts use whichever spelling the
    underlying astropy/photutils table columns carried; both are
    handled elsewhere in this codebase (e.g. plate_solver.py), so the
    rescale must not silently skip one of them.
    """
    sources = [{"xcentroid": 4.0, "ycentroid": 4.0}]

    _rescale_source_centroids(sources, factor=3)

    assert sources[0]["xcentroid"] == 13.0  # ruff: ignore[float-equality-comparison]
    assert sources[0]["ycentroid"] == 13.0  # ruff: ignore[float-equality-comparison]


def test_color_frame_detection_runs_on_a_block_averaged_array():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the colour path hands the detector a binned copy."""
    identifier = _make_identifier()
    identifier.detector.detect.return_value = []
    identifier.detector.deduplicate.return_value = []
    data = np.arange(16.0).reshape(4, 4)

    identifier.detect_stars(data, is_color_frame=True)

    (detected_array,), _ = identifier.detector.detect.call_args
    assert detected_array.shape == (2, 2)
    assert not np.array_equal(detected_array, data)


def test_color_frame_detection_rescales_results_to_full_resolution():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify raw and deduplicated results both land at full resolution."""
    identifier = _make_identifier()
    raw = [{"x_centroid": 1.0, "y_centroid": 1.0, "flux": 100.0}]
    unique = [{"x_centroid": 1.0, "y_centroid": 1.0, "flux": 100.0}]
    identifier.detector.detect.return_value = raw
    identifier.detector.deduplicate.return_value = unique
    data = np.zeros((4, 4))

    sources, unique_sources = identifier.detect_stars(data, is_color_frame=True)

    assert sources[0]["x_centroid"] == 2.5  # ruff: ignore[float-equality-comparison] -- 1*2 + (2-1)/2
    assert unique_sources[0]["x_centroid"] == 2.5  # ruff: ignore[float-equality-comparison]


def test_color_frame_detection_does_not_widen_the_detection_kernel():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the colour path leaves detector.fwhm alone.

    Matching the kernel to a colour frame's interpolation-broadened PSF
    was measured to make the false-detection rate worse, not better
    (142 -> 427 false detections in the synthetic test this branch is
    built from) -- the fix is binning, not a wider kernel, and this
    path must not silently reintroduce that regression.
    """
    identifier = _make_identifier()
    identifier.detector.detect.return_value = []
    identifier.detector.deduplicate.return_value = []
    data = np.zeros((4, 4))

    identifier.detect_stars(data, is_color_frame=True)

    assert identifier.detector.fwhm == 4.0  # ruff: ignore[float-equality-comparison]


def test_mono_frame_detection_matches_the_kernel_to_the_measured_fwhm():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a successful FWHM measurement updates the detector."""
    identifier = _make_identifier()
    identifier.detector.detect.return_value = []
    identifier.detector.deduplicate.return_value = []
    data = np.zeros((4, 4))

    with patch(
        "astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier.measure_fwhm_from_data",
        return_value=6.75,
    ):
        identifier.detect_stars(data, is_color_frame=False)

    assert identifier.detector.fwhm == 6.75  # ruff: ignore[float-equality-comparison]
    identifier.detector.detect.assert_called_once_with(data)


def test_mono_frame_detection_keeps_the_default_fwhm_when_measurement_fails():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a field too sparse to measure (None) leaves fwhm untouched."""
    identifier = _make_identifier()
    identifier.detector.detect.return_value = []
    identifier.detector.deduplicate.return_value = []
    data = np.zeros((4, 4))

    with patch(
        "astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier.measure_fwhm_from_data",
        return_value=None,
    ):
        identifier.detect_stars(data, is_color_frame=False)

    assert identifier.detector.fwhm == 4.0  # ruff: ignore[float-equality-comparison]


def test_mono_frame_detection_survives_a_measurement_exception():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a raising FWHM measurement is swallowed, not propagated.

    A best-effort refinement must never turn into a hard failure of the
    whole detection pass.
    """
    identifier = _make_identifier()
    identifier.detector.detect.return_value = []
    identifier.detector.deduplicate.return_value = []
    data = np.zeros((4, 4))

    with patch(
        "astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier.measure_fwhm_from_data",
        side_effect=RuntimeError("boom"),
    ):
        sources, unique_sources = identifier.detect_stars(data, is_color_frame=False)

    assert sources == []
    assert unique_sources == []
    assert identifier.detector.fwhm == 4.0  # ruff: ignore[float-equality-comparison]


def test_binning_suppresses_demosaic_false_positives_on_a_real_detector():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the real SourceDetector, not just the wiring around it, benefits.

    Locks in the mechanism this module's fix depends on: a
    bilinear-demosaiced (colour-stack-shaped) background with no real
    stars produces far fewer false detections through
    `detect_stars(is_color_frame=True)` than through the unbinned,
    default detection path. Uses `StarIdentifier` end-to-end (a real
    `SourceDetector`, not a mock), so a change to detection internals
    that defeats this fix would be caught here, not just in the
    hand-derivation in `_COLOR_DETECTION_BIN_FACTOR`'s comment.
    """
    identifier = StarIdentifier(config=MagicMock(get_value=MagicMock(return_value=None)))
    background = _synthetic_demosaiced_background(height=400, width=400, seed=7)

    _, unbinned_unique = identifier.detect_stars(background, is_color_frame=False)
    _, binned_unique = identifier.detect_stars(background, is_color_frame=True)

    assert len(binned_unique) < len(unbinned_unique) / 3
