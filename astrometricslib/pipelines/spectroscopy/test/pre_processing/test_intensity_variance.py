"""Purpose: Tests for the per-sample variance of an extracted spectrum.

Description: The extractor gives every sample a variance from the camera noise
model, and pre-processing carries it through the corrections. These tests check
the model against truth that is known. A synthetic frame with a known seed is
extracted 200 times with new noise each time, and the scatter of each sample
over those 200 extractions must match the error the extractor reported. They
also check the closed forms (the Poisson and read terms, the blend of two
columns, the median of a sky band), the scaling of the error through each
multiplicative correction, the stack scale, and the signal-to-noise numbers
built from the errors.
"""

import numpy as np
import pytest

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction import (
    ExtinctionCorrection,
    extinction_correction_factor,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    InstrumentResponse,
    apply_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance import (
    PixelNoiseModel,
    build_pixel_noise_model,
    combine_neighbour_variances,
    median_variance_factor,
    propagate_calibration_errors,
    resolution_element_signal_to_noise,
    scale_errors,
    summarize_spectrum_noise,
    variance_based_signal_to_noise,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    QuantumEfficiencyCurve,
    apply_quantum_efficiency_correction,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import ResolutionProfile
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import (
    SpectrumExtractor,
    measure_sky,
)
from astrometricslib.pipelines.spectroscopy.processing.spectrum_signal import (
    estimate_spectrum_signal_to_noise,
)
from astrometricslib.test.synthetic import make_spectral_frame

# A small frame keeps 200 extractions fast. The trail runs from the zero
# order at column 20, 2 degrees off horizontal.
FRAME_SHAPE = (96, 260)
ZERO_ORDER_XY = (20.0, 48.0)
TRAIL_ANGLE_DEG = 2.0
REALIZATIONS = 200
READ_NOISE_ADU = 5.0


class _ArrayImage(AstrometricsImage):
    """An `AstrometricsImage` that wraps a NumPy array."""

    def __init__(self, data: np.ndarray) -> None:
        """Wrap `data` so the extractor can read it like a real image."""
        self._data = data
        self._header: dict[str, object] = {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """Image array data."""
        return self._data

    @property
    def header(self) -> dict[str, object]:
        """Image headers dict."""
        return self._header


def _noise_model() -> PixelNoiseModel:
    """Build the noise model that matches the synthetic frame generator.

    Returns
    -------
    model : `PixelNoiseModel`
        Gain 1 electron per ADU and 5 electrons of read noise, which is how
        `make_spectral_frame` draws its Poisson and read noise.
    """
    return PixelNoiseModel(read_noise_e=READ_NOISE_ADU, read_noise_is_assumed=False)


def _noisy_frames(count: int) -> list[_ArrayImage]:
    """Make `count` noise realizations of one synthetic frame.

    Parameters
    ----------
    count : `int`
        How many frames to make. Frame `k` uses seed `k`.

    Returns
    -------
    frames : `list` [`_ArrayImage`]
        Frames with the same truth and different noise.
    """
    return [
        _ArrayImage(
            make_spectral_frame(
                zero_order_xy=ZERO_ORDER_XY,
                angle_deg=TRAIL_ANGLE_DEG,
                trail_length_px=200,
                shape=FRAME_SHAPE,
                lines=(),
                read_noise_adu=READ_NOISE_ADU,
                seed=seed,
            ).image
        )
        for seed in range(count)
    ]


def _scatter_over_error(profiles: list[np.ndarray], variances: list[list[float]]) -> np.ndarray:
    """Divide the mean reported error by the scatter, per sample.

    Parameters
    ----------
    profiles : `list` [`numpy.ndarray`]
        The extracted brightness of each realization.
    variances : `list` [`list` [`float`]]
        The variance the extractor reported for each realization.

    Returns
    -------
    ratio : `numpy.ndarray`
        For each sample, the mean reported 1-sigma error over the standard
        deviation of the reading across the realizations. 1 means the error
        is right.
    """
    scatter = np.std(np.array(profiles), axis=0, ddof=1)
    mean_error = np.mean(np.sqrt(np.array(variances)), axis=0)
    return mean_error / scatter


# --------------------------------------------------- the closed-form pieces


def test_box_variance_adds_the_poisson_and_read_terms() -> None:
    """A weighted box has `a * sum(w^2 p)` plus `b * sum(w^2)`."""
    model = PixelNoiseModel(gain_e_per_adu=2.0, read_noise_e=4.0, read_noise_is_assumed=False)
    pixels = np.array([100.0, 100.0])
    weights = np.array([1.0, 0.5])

    # a = 1 / 2 and b = (4 / 2)^2 = 4; sum(w^2) = 1.25.
    expected = 0.5 * (1.0 * 100.0 + 0.25 * 100.0) + 4.0 * 1.25
    assert model.box_variance(pixels, weights) == pytest.approx(expected)


def test_a_negative_poisson_sum_is_clipped_at_zero() -> None:
    """A box of dark, negative pixels still gets its read variance."""
    model = PixelNoiseModel(read_noise_e=3.0, read_noise_is_assumed=False)

    assert model.box_variance(np.array([-5.0, -5.0]), np.ones(2)) == pytest.approx(2 * 9.0)


def test_the_blend_of_two_columns_uses_squared_weights() -> None:
    """The variance of `(1-u) A + u B` is `(1-u)^2 Va + u^2 Vb`."""
    assert combine_neighbour_variances(4.0, 9.0, 0.25) == pytest.approx(0.75**2 * 4.0 + 0.25**2 * 9.0)
    assert combine_neighbour_variances(4.0, 9.0, 0.0) == pytest.approx(4.0)


def test_blended_neighbours_have_the_predicted_variance() -> None:
    """A Monte Carlo blend of two independent readings matches the formula."""
    rng = np.random.default_rng(1)
    lower = rng.normal(0.0, 2.0, 100_000)
    upper = rng.normal(0.0, 3.0, 100_000)

    blended = 0.7 * lower + 0.3 * upper

    assert np.var(blended) == pytest.approx(combine_neighbour_variances(4.0, 9.0, 0.3), rel=0.03)


@pytest.mark.parametrize("pixel_count", [4, 5, 9, 10, 11, 20])
def test_median_variance_factor_matches_a_simulation(pixel_count: int) -> None:
    """The variance of a median follows the even and odd forms."""
    rng = np.random.default_rng(pixel_count)
    medians = np.median(rng.normal(size=(60_000, pixel_count)), axis=1)

    assert np.var(medians) == pytest.approx(median_variance_factor(pixel_count), rel=0.04)


# --------------------------------------------------------- the noise model


def test_the_model_reads_the_header_gain_and_read_noise() -> None:
    """With no profile, `EGAIN` and `RDNOISE` set the gain and read noise."""
    model = build_pixel_noise_model(None, {"EGAIN": 2.5, "RDNOISE": 3.0}, np.full((4, 4), 1000.0))

    assert model.gain_e_per_adu == pytest.approx(2.5)
    assert model.read_noise_e == pytest.approx(3.0)
    assert not model.gain_is_assumed
    assert not model.read_noise_is_assumed
    assert model.adu_per_stored_unit == pytest.approx(1.0)
    assert model.frames_averaged == 1


def test_a_missing_gain_is_assumed_and_recorded() -> None:
    """Without a profile or header card, 1 electron per ADU is assumed."""
    model = build_pixel_noise_model(None, {}, np.full((4, 4), 1000.0))

    assert model.gain_e_per_adu == pytest.approx(1.0)
    assert model.gain_is_assumed
    assert model.read_noise_is_assumed
    assert model.as_record()["gain_is_assumed"] is True


def test_a_normalised_stack_is_converted_to_adu_and_averaged() -> None:
    """A Siril stack in fractions of full scale has 65535 ADU per unit."""
    data = np.full((4, 4), 0.5)
    model = build_pixel_noise_model(None, {"EGAIN": 2.0, "STACKCNT": 10}, data)

    assert model.adu_per_stored_unit == pytest.approx(65535.0)
    assert model.frames_averaged == 10
    # One pixel of 0.5 units is 32767.5 ADU in each frame. A raw frame has a
    # variance of ADU / gain; the mean of 10 has a tenth of it; and one stored
    # unit is 65535 ADU, so the variance in units squared divides by 65535^2.
    expected = (32767.5 / 2.0) / 10 / 65535.0**2
    assert model.box_variance(np.array([0.5]), np.array([1.0])) == pytest.approx(expected)


def test_a_stack_without_a_frame_count_assumes_one_frame() -> None:
    """The model records that the frame count was assumed."""
    model = build_pixel_noise_model(None, {}, np.full((4, 4), 0.5))

    assert model.frames_averaged == 1
    assert model.frames_are_assumed


# -------------------------------------------------------- the sky variance


def test_the_sky_level_variance_matches_its_scatter() -> None:
    """The sky-level variance gives an error within 10% of the truth."""
    rng = np.random.default_rng(3)
    model = _noise_model()
    levels = []
    variances = []
    for _ in range(4000):
        line = rng.poisson(150.0, size=96).astype(float) + rng.normal(0.0, READ_NOISE_ADU, size=96)
        sky = measure_sky(line, 48.0, 8.0, model)
        levels.append(sky.level_per_pixel)
        variances.append(sky.level_variance)

    ratio = np.sqrt(np.mean(variances) / np.var(levels))

    assert 0.9 < ratio < 1.05


def test_the_sky_variance_never_falls_below_the_camera_noise() -> None:
    """A band that scatters little still gets the photon-noise floor."""
    model = PixelNoiseModel(read_noise_e=0.0, read_noise_is_assumed=False)
    # 20 identical pixels: zero measured scatter, but 400 ADU of sky must
    # still scatter by sqrt(400) per pixel from photon noise.
    sky = measure_sky(np.full(96, 400.0), 48.0, 8.0, model)

    assert sky.level_variance > 0.0
    assert sky.level_variance == pytest.approx(400.0 * median_variance_factor(10) / 2.0, rel=0.05)
    assert measure_sky(np.full(96, 400.0), 48.0, 8.0).level_variance == pytest.approx(0.0)


# --------------------------------------------- the extractor against truth


def test_an_extractor_without_a_noise_model_stores_no_variance() -> None:
    """The variance is only computed when a noise model is given."""
    frame = _noisy_frames(1)[0]
    extractor = SpectrumExtractor(radius=8)

    extractor.extract_with_flare_mask(frame, ZERO_ORDER_XY, 30, 70, 8, "horizontal", angle_degrees=-2.0)

    assert extractor.last_diagnostics.sample_variance == []


def test_box_variance_matches_the_scatter_without_sky_subtraction() -> None:
    """Without sky subtraction the box model alone matches to 5%."""
    extractor = SpectrumExtractor(radius=8, subtract_sky_background=False, noise_model=_noise_model())
    profiles = []
    variances = []
    for frame in _noisy_frames(REALIZATIONS):
        profile, _, _ = extractor.extract_with_flare_mask(
            frame, ZERO_ORDER_XY, 30, 130, 8, "horizontal", angle_degrees=-2.0
        )
        profiles.append(profile)
        variances.append(extractor.last_diagnostics.sample_variance)

    ratio = _scatter_over_error(profiles, variances)

    assert np.median(ratio) == pytest.approx(1.0, abs=0.05)


def test_flare_mask_errors_match_the_scatter_over_200_noise_realizations() -> None:
    """The reported error of each sample matches its scatter to 10%.

    The flare-mask path reads whole columns, and the reported variance holds
    the Poisson and read noise of the box and the error of the sky level.
    """
    extractor = SpectrumExtractor(radius=8, noise_model=_noise_model())
    profiles = []
    variances = []
    for frame in _noisy_frames(REALIZATIONS):
        profile, _, _ = extractor.extract_with_flare_mask(
            frame, ZERO_ORDER_XY, 30, 70, 8, "horizontal", angle_degrees=-2.0
        )
        profiles.append(profile)
        variances.append(extractor.last_diagnostics.sample_variance)

    ratio = _scatter_over_error(profiles, variances)

    assert np.median(ratio) == pytest.approx(1.0, abs=0.10)
    pooled = np.sqrt(np.mean(np.array(variances)) / np.mean(np.var(np.array(profiles), axis=0, ddof=1)))
    assert pooled == pytest.approx(1.0, abs=0.10)
    # A single sample's scatter is known to about 5% from 200 realizations.
    assert np.mean(np.abs(ratio - 1.0) < 0.2) > 0.9


def test_dispersion_line_errors_match_the_scatter_between_columns() -> None:
    """The dispersion-line path blends two columns and still matches to 10%.

    Most samples sit between two columns, so the variance is the blend
    `(1-u)^2 V1 + u^2 V2` from `combine_neighbour_variances`.
    """
    extractor = SpectrumExtractor(radius=8, noise_model=_noise_model())
    direction = np.array([np.cos(np.radians(-TRAIL_ANGLE_DEG)), np.sin(np.radians(-TRAIL_ANGLE_DEG))])
    profiles = []
    variances = []
    for frame in _noisy_frames(REALIZATIONS):
        # Start half a column off the grid so that every sample is a blend.
        profiles.append(extractor.extract_line(frame, (50.5, 48.0), direction, 24))
        variances.append(extractor.last_diagnostics.sample_variance)

    ratio = _scatter_over_error(profiles, variances)

    assert np.median(ratio) == pytest.approx(1.0, abs=0.10)


def test_neighbouring_samples_of_a_blended_line_are_correlated() -> None:
    """Samples one step apart share a column, so their errors correlate.

    The error array holds each sample's own error and not this covariance.
    The test records that the correlation exists, which is the reason for
    the documented limit.
    """
    extractor = SpectrumExtractor(radius=8, subtract_sky_background=False, noise_model=_noise_model())
    direction = np.array([1.0, 0.0])
    profiles = np.array([
        extractor.extract_line(frame, (50.5, 48.0), direction, 12) for frame in _noisy_frames(REALIZATIONS)
    ])

    # Samples at half-column positions are (A_k + A_k+1) / 2, so neighbours
    # share one of two columns: the correlation is 0.5 for equal variances.
    correlation = np.corrcoef(profiles[:, 5], profiles[:, 6])[0, 1]
    assert correlation == pytest.approx(0.5, abs=0.15)


def test_traced_dispersion_line_errors_match_the_scatter() -> None:
    """The traced dispersion line reads a fitted box and matches to 15%."""
    extractor = SpectrumExtractor(radius=8, noise_model=_noise_model())
    direction = np.array([np.cos(np.radians(-TRAIL_ANGLE_DEG)), np.sin(np.radians(-TRAIL_ANGLE_DEG))])
    profiles = []
    variances = []
    for frame in _noisy_frames(60):
        profile, _, _ = extractor.extract_line_traced(frame, (50.0, 48.0), direction, 16)
        profiles.append(profile)
        variances.append(extractor.last_diagnostics.sample_variance)

    ratio = _scatter_over_error(profiles, variances)

    assert np.median(ratio) == pytest.approx(1.0, abs=0.15)


def test_traced_flare_mask_gives_one_finite_variance_per_sample() -> None:
    """The traced flare-mask path stores a positive variance for every step."""
    extractor = SpectrumExtractor(radius=8, noise_model=_noise_model())
    frame = _noisy_frames(1)[0]

    profile, _, _, _, _ = extractor.extract_with_flare_mask_traced(
        frame, ZERO_ORDER_XY, 30, 60, 8, "horizontal", angle_degrees=-2.0
    )
    variance = np.array(extractor.last_diagnostics.sample_variance)

    assert variance.shape == profile.shape
    assert np.all(np.isfinite(variance))
    assert np.all(variance > 0)


def test_a_box_off_the_image_has_no_flux_and_no_variance() -> None:
    """A sample whose box is not on the image is NaN in both arrays."""
    extractor = SpectrumExtractor(radius=8, noise_model=_noise_model())
    frame = _noisy_frames(1)[0]

    profile, _, _ = extractor.extract_with_flare_mask(
        frame, ZERO_ORDER_XY, 200, 280, 8, "horizontal", angle_degrees=-2.0
    )
    variance = np.array(extractor.last_diagnostics.sample_variance)

    assert np.isnan(profile[-1])
    assert np.isnan(variance[-1])
    assert np.array_equal(np.isnan(profile), np.isnan(variance))


def test_fractional_edge_weights_enter_the_variance_squared() -> None:
    """A box that ends part-way through a pixel counts that pixel's `w^2`."""
    model = PixelNoiseModel(read_noise_e=5.0, read_noise_is_assumed=False)
    extractor = SpectrumExtractor(radius=8, subtract_sky_background=False, noise_model=model)
    data = np.full((40, 30), 100.0)

    flux, variance = extractor._measure_aperture(data, 15, 20.0, 3.5, True)

    # Half-width 3.5 reaches 3.5 + 0.5 pixels from the middle: seven whole
    # pixels... plus two edge pixels of weight 0.5 each.
    weights = np.array([0.5, 1, 1, 1, 1, 1, 1, 1, 0.5])
    assert flux == pytest.approx(float(np.sum(weights) * 100.0))
    assert variance == pytest.approx(float(np.sum(weights**2) * (100.0 + 25.0)))


# ------------------------------------------ the corrections scale the error


def _quantum_efficiency_curve() -> QuantumEfficiencyCurve:
    """Build a sensitivity curve that falls toward the red.

    Returns
    -------
    curve : `QuantumEfficiencyCurve`
        Sensitivity from 0.8 at 400 nm down to 0.2 at 900 nm.
    """
    return QuantumEfficiencyCurve(np.array([400.0, 900.0]), np.array([0.8, 0.2]))


def _instrument_response() -> InstrumentResponse:
    """Build a smooth response that is valid from 4000 to 9000 Angstroms.

    Returns
    -------
    response : `InstrumentResponse`
        A response of `exp(0.3 x)` with `x` the scaled wavelength.
    """
    return InstrumentResponse(
        camera_name="test",
        coefficients=(0.3, 0.0),
        minimum_wavelength_angstrom=4000.0,
        maximum_wavelength_angstrom=9000.0,
        reference_type="A0V",
        source="test",
        reference_airmass=1.1,
    )


def test_the_quantum_efficiency_error_scales_like_the_brightness() -> None:
    """The error is multiplied by the brightness's own factor."""
    wavelength = np.linspace(4200.0, 8000.0, 50)
    brightness = np.full(50, 1000.0)
    errors = np.full(50, 30.0)
    curve = _quantum_efficiency_curve()

    corrected = apply_quantum_efficiency_correction(wavelength / 10.0, brightness, curve)
    propagated = propagate_calibration_errors(wavelength, errors, curve, None, None)

    assert propagated.response_corrected is None
    assert propagated.quantum_efficiency_corrected is not None
    assert propagated.quantum_efficiency_corrected == pytest.approx(errors * corrected / brightness)


def test_the_corrected_scatter_matches_the_scaled_error() -> None:
    """A Monte Carlo shows the variance scales by the factor squared."""
    rng = np.random.default_rng(7)
    wavelength = np.linspace(4200.0, 8000.0, 50)
    errors = np.linspace(20.0, 60.0, 50)
    curve = _quantum_efficiency_curve()
    draws = 1000.0 + rng.normal(size=(4000, 50)) * errors

    corrected = np.array([
        apply_quantum_efficiency_correction(wavelength / 10.0, row, curve) for row in draws
    ])
    propagated = propagate_calibration_errors(wavelength, errors, curve, None, None)

    assert propagated.quantum_efficiency_corrected is not None
    ratio = np.std(corrected, axis=0, ddof=1) / propagated.quantum_efficiency_corrected
    assert np.median(ratio) == pytest.approx(1.0, abs=0.03)


def test_the_response_error_scales_and_is_nan_outside_the_range() -> None:
    """Dividing by the response scales the error; NaN out of range."""
    wavelength = np.array([3500.0, 5000.0, 6000.0, 8000.0, 9500.0])
    brightness = np.full(5, 1000.0)
    errors = np.full(5, 20.0)
    curve = _quantum_efficiency_curve()
    response = _instrument_response()

    corrected = apply_instrument_response(wavelength, brightness, response)
    propagated = propagate_calibration_errors(wavelength, errors, curve, response, None)
    qe_factor = apply_quantum_efficiency_correction(wavelength / 10.0, np.ones(5), curve)

    assert propagated.response_corrected is not None
    expected = errors * corrected / brightness * qe_factor
    assert np.array_equal(np.isnan(propagated.response_corrected), np.isnan(expected))
    assert propagated.response_corrected[1:4] == pytest.approx(expected[1:4])
    assert np.isnan(propagated.response_corrected[0])
    assert np.isnan(propagated.response_corrected[-1])


def test_the_extinction_error_scales_only_when_the_correction_was_applied() -> None:
    """An applied extinction correction scales the error by its factor."""
    wavelength = np.linspace(4200.0, 8000.0, 20)
    errors = np.full(20, 10.0)
    curve = _quantum_efficiency_curve()
    response = _instrument_response()
    applied = ExtinctionCorrection(True, 1.4, 1.1, "kpno")
    skipped = ExtinctionCorrection(False, None, 1.1, "kpno", "no airmass")

    without = propagate_calibration_errors(wavelength, errors, curve, response, None)
    with_skipped = propagate_calibration_errors(wavelength, errors, curve, response, skipped)
    with_applied = propagate_calibration_errors(wavelength, errors, curve, response, applied)

    assert with_skipped.response_corrected is not None and without.response_corrected is not None
    assert with_applied.response_corrected is not None
    assert with_skipped.response_corrected == pytest.approx(without.response_corrected)
    factor = extinction_correction_factor(wavelength, 1.4, 1.1)
    assert with_applied.response_corrected == pytest.approx(without.response_corrected * factor)
    assert np.all(factor > 1.0)


def test_without_a_sensitivity_curve_no_corrected_errors_exist() -> None:
    """No quantum-efficiency curve means neither corrected error array."""
    propagated = propagate_calibration_errors(
        np.linspace(4200.0, 8000.0, 5), np.ones(5), None, _instrument_response(), None
    )

    assert propagated.quantum_efficiency_corrected is None
    assert propagated.response_corrected is None


def test_scale_errors_uses_the_absolute_factor() -> None:
    """A negative factor still gives a positive error."""
    assert scale_errors(np.array([2.0, 3.0]), -2.0) == pytest.approx([4.0, 6.0])


# ------------------------------------------------ the signal-to-noise numbers


def test_resolution_element_signal_to_noise_of_a_flat_spectrum() -> None:
    """Summing `n` samples of S/N 10 gives S/N `10 sqrt(n)`."""
    wavelength = np.arange(4000.0, 8000.0, 10.0)
    values = np.full(wavelength.shape, 100.0)
    errors = np.full(wavelength.shape, 10.0)

    # A 40 A element holds the samples within +-20 A: five samples, or four
    # when the window edge falls on a sample, so check a mid-spectrum sample.
    snr = resolution_element_signal_to_noise(wavelength, values, errors, 38.0)

    assert snr[200] == pytest.approx(10.0 * np.sqrt(3.0))
    wide = resolution_element_signal_to_noise(wavelength, values, errors, 78.0)
    assert wide[200] == pytest.approx(10.0 * np.sqrt(7.0))


def test_the_line_spread_profile_sets_the_element_width() -> None:
    """A wider element in the red gives a higher signal-to-noise there."""
    wavelength = np.arange(4000.0, 8000.0, 10.0)
    values = np.full(wavelength.shape, 100.0)
    errors = np.full(wavelength.shape, 10.0)
    profile = ResolutionProfile(np.array([4000.0, 8000.0]), np.array([38.0, 138.0]))

    snr = resolution_element_signal_to_noise(wavelength, values, errors, 45.0, profile)

    assert snr[350] > 1.5 * snr[10]


def test_bad_samples_do_not_poison_the_element_sum() -> None:
    """NaN samples leave a window NaN only if too many are bad."""
    wavelength = np.arange(4000.0, 5000.0, 10.0)
    values = np.full(wavelength.shape, 100.0)
    errors = np.full(wavelength.shape, 10.0)
    values[50] = np.nan
    values[70:80] = np.nan

    snr = resolution_element_signal_to_noise(wavelength, values, errors, 100.0)

    assert np.isfinite(snr[50])
    assert np.isfinite(snr[30])
    assert np.isnan(snr[75])


def test_the_summary_for_a_known_spectrum() -> None:
    """A flat spectrum of S/N 10 per sample has known summary numbers."""
    wavelength = np.arange(3800.0, 8200.0, 10.0)
    values = np.full(wavelength.shape, 100.0)
    errors = np.full(wavelength.shape, 10.0)

    summary = summarize_spectrum_noise(wavelength, values, errors, 38.0, post_hoc_signal_to_noise=20.0)

    assert summary.median_snr_per_resolution_element == pytest.approx(10.0 * np.sqrt(3.0))
    assert summary.fraction_samples_snr_below_5 == pytest.approx(0.0)
    # Elements of four samples have a mean with error 10 / 2, so S/N 20.
    assert summary.snr_estimate_ratio == pytest.approx(1.0, abs=0.01)


def test_the_fraction_below_five_counts_faint_samples() -> None:
    """Samples below five times their error count toward the fraction."""
    wavelength = np.arange(4200.0, 8000.0, 10.0)
    values = np.full(wavelength.shape, 100.0)
    errors = np.full(wavelength.shape, 10.0)
    values[:38] = 20.0  # S/N 2 in the first tenth of the range

    summary = summarize_spectrum_noise(wavelength, values, errors, 38.0, post_hoc_signal_to_noise=None)

    assert summary.fraction_samples_snr_below_5 == pytest.approx(38 / wavelength.size)
    assert summary.snr_estimate_ratio is None


def test_the_variance_estimate_agrees_with_the_post_hoc_estimate_on_noise() -> None:
    """On a noisy flat spectrum the two estimates of S/N agree to 20%.

    The post-hoc estimator measures the scatter between elements. The
    variance-based one predicts it from the errors. When the errors are right
    their ratio is near 1; when they are wrong by a factor 3 it is near 3.
    """
    rng = np.random.default_rng(11)
    wavelength = np.arange(3800.0, 9000.0, 11.0)
    errors = np.full(wavelength.shape, 10.0)
    values = 100.0 + rng.normal(size=wavelength.shape) * errors
    post_hoc = estimate_spectrum_signal_to_noise(wavelength, values, 44.0)
    assert post_hoc is not None

    right = variance_based_signal_to_noise(wavelength, values, errors, 44.0)
    wrong = variance_based_signal_to_noise(wavelength, values, errors / 3.0, 44.0)

    assert right is not None and wrong is not None
    assert right / post_hoc == pytest.approx(1.0, abs=0.2)
    assert wrong / post_hoc == pytest.approx(3.0, rel=0.25)
