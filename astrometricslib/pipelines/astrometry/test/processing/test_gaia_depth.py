"""Tests the equipment-based prediction of the Gaia query depth."""

import math

import pytest

from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    ProvenanceKind,
    QuantumEfficiencyRecord,
    SensorSettingNoise,
    ValueProvenance,
)
from astrometricslib.pipelines.astrometry.processing.gaia_depth import (
    GaiaDepthPrediction,
    depth_exceeded,
    predict_gaia_query_depth,
)

# The ASI 533MM Pro setup of the hand calculation: 75 mm aperture, 405 mm
# focal length, 3.76 micron pixels, so 206.265 * 3.76 / 0.405 arcsec per pixel.
PIXEL_SCALE = 206.265 * 3.76 / 405.0
FLAT_QE = 0.8
READ_NOISE = 3.0


def _provenance() -> ValueProvenance:
    """Give a provenance note for test values.

    Returns
    -------
    provenance : `ValueProvenance`
        An assumed-value note.
    """
    return ValueProvenance(kind=ProvenanceKind.ASSUMED, source="test")


def _profile(with_qe: bool = True, with_noise: bool = True) -> CameraProfile:
    """Build a camera profile with a flat QE curve and one noise entry.

    Parameters
    ----------
    with_qe : `bool`
        Include the QE curve.
    with_noise : `bool`
        Include the read-noise entry.

    Returns
    -------
    profile : `CameraProfile`
        The profile.
    """
    qe = (
        QuantumEfficiencyRecord(
            wavelength_nm=(300.0, 1100.0),
            quantum_efficiency_fraction=(FLAT_QE, FLAT_QE),
            provenance=_provenance(),
        )
        if with_qe
        else None
    )
    noise = (
        (
            SensorSettingNoise(
                gain_setting="100",
                offset="10",
                binning="1x1",
                read_noise_e=ProvenancedValue(value=READ_NOISE, provenance=_provenance()),
            ),
        )
        if with_noise
        else ()
    )
    return CameraProfile(
        camera_name="Test camera",
        clip_ceiling_adu=ProvenancedValue(value=65535.0, provenance=_provenance()),
        saturation_threshold_adu=ProvenancedValue(value=65000.0, provenance=_provenance()),
        quantum_efficiency=qe,
        sensor_noise=noise,
    )


def _predict(profile: CameraProfile | None = None, **overrides: object) -> GaiaDepthPrediction:
    """Predict the depth for the test setup, with optional changes.

    Parameters
    ----------
    profile : `CameraProfile` or `None`, optional
        The profile; the default test profile when omitted.
    **overrides : `Any`
        Keyword arguments that replace the setup's values.

    Returns
    -------
    prediction : `GaiaDepthPrediction`
        The prediction.
    """
    arguments: dict[str, object] = {
        "gain_setting": "100",
        "offset": "10",
        "binning": "1x1",
        "aperture_diameter_mm": 75.0,
        "pixel_scale_arcsec_per_px": PIXEL_SCALE,
        "exposure_time_s": 60.0,
    }
    arguments.update(overrides)
    return predict_gaia_query_depth(_profile() if profile is None else profile, **arguments)  # type: ignore[arg-type]


def _hand_limit(exposure_s: float, read_noise_time_s: float) -> float:
    """Work out the limiting magnitude of the test setup step by step.

    Parameters
    ----------
    exposure_s : `float`
        The effective exposure, in seconds.
    read_noise_time_s : `float`
        The read-noise time, in seconds.

    Returns
    -------
    limit : `float`
        The G magnitude where a star reaches the detection signal-to-noise
        k = 11.6, with throughput 0.62, a 5 arcsec star and a sky of 18.7
        magnitudes per square arcsecond.
    """
    k = 11.6
    area_cm2 = math.pi * 3.75**2
    rate_g0 = 3.2e6 * area_cm2 * 0.62 * FLAT_QE
    sky_rate = rate_g0 * 10.0 ** (-0.4 * 18.7) * PIXEL_SCALE**2
    pixels = math.pi * (5.0 / PIXEL_SCALE) ** 2
    variance_empty = pixels * (sky_rate * exposure_s + READ_NOISE**2 * (exposure_s / read_noise_time_s) ** 2)
    electrons = (k**2 + math.sqrt(k**4 + 4.0 * k**2 * variance_empty)) / 2.0
    # Check that the star electrons really give k sigma.
    assert electrons / math.sqrt(electrons + variance_empty) == pytest.approx(k)
    return -2.5 * math.log10(electrons / exposure_s / rate_g0)


def test_sixty_second_frame_matches_hand_calculation() -> None:
    """A single 60 s frame gives the hand-calculated limit and depth."""
    prediction = _predict()
    expected = _hand_limit(60.0, 60.0)
    assert not prediction.is_fallback
    assert prediction.limiting_magnitude == pytest.approx(expected)
    assert prediction.query_depth == pytest.approx(expected + 1.0)
    assert 16.2 < expected < 16.4
    assert prediction.mean_qe == pytest.approx(FLAT_QE)


def test_ten_thousand_second_stack_matches_hand_calculation() -> None:
    """A 10,000 s stack of 60 s frames matches the hand value."""
    read_time = 60.0 * math.sqrt(10000.0 / 60.0)
    prediction = _predict(exposure_time_s=10000.0, read_noise_time_s=read_time)
    expected = _hand_limit(10000.0, read_time)
    assert prediction.limiting_magnitude == pytest.approx(expected)
    assert 19.0 < expected < 19.2
    assert prediction.query_depth == pytest.approx(expected + 1.0)


def test_a_very_long_stack_is_capped() -> None:
    """A stack deep enough to pass G 20 asks no deeper than the cap."""
    read_time = 60.0 * math.sqrt(1.0e6 / 60.0)
    prediction = _predict(exposure_time_s=1.0e6, read_noise_time_s=read_time)
    assert prediction.limiting_magnitude is not None and prediction.limiting_magnitude > 20.0
    assert prediction.query_depth == pytest.approx(21.0)


def test_longer_exposure_is_deeper() -> None:
    """More exposure time raises the limiting magnitude."""
    short = _predict(exposure_time_s=60.0)
    long = _predict(exposure_time_s=600.0)
    assert long.limiting_magnitude is not None and short.limiting_magnitude is not None
    assert long.limiting_magnitude > short.limiting_magnitude


def test_brighter_sky_is_shallower() -> None:
    """A higher measured sky rate lowers the limiting magnitude."""
    dark = _predict(sky_rate_e_per_s_per_px=0.5)
    bright = _predict(sky_rate_e_per_s_per_px=5.0)
    assert dark.limiting_magnitude is not None and bright.limiting_magnitude is not None
    assert bright.limiting_magnitude < dark.limiting_magnitude
    assert bright.sky_rate_is_measured


@pytest.mark.parametrize(
    ("profile", "overrides", "words"),
    [
        (None, {}, "camera profile"),
        (_profile(with_qe=False), {}, "quantum efficiency"),
        (_profile(with_noise=False), {}, "read noise"),
        (None, {"aperture_diameter_mm": None}, "aperture"),
        (None, {"pixel_scale_arcsec_per_px": 0.0}, "pixel scale"),
    ],
)
def test_missing_input_falls_back_with_reason(
    profile: CameraProfile | None, overrides: dict[str, object], words: str
) -> None:
    """A missing input gives the default depth, flagged, with a reason."""
    if profile is None and not overrides:
        prediction = predict_gaia_query_depth(
            None,
            gain_setting="100",
            offset="10",
            binning="1x1",
            aperture_diameter_mm=75.0,
            pixel_scale_arcsec_per_px=PIXEL_SCALE,
            exposure_time_s=60.0,
        )
    else:
        prediction = _predict(profile, **overrides)
    assert prediction.is_fallback
    assert prediction.query_depth == pytest.approx(18.0)
    assert prediction.limiting_magnitude is None
    assert words in prediction.reason


def test_depth_exceeded() -> None:
    """A star fainter than the query depth is flagged; unknown is not."""
    prediction = _predict()
    assert depth_exceeded(prediction, prediction.query_depth + 0.1)
    assert not depth_exceeded(prediction, prediction.query_depth - 0.1)
    assert not depth_exceeded(prediction, None)
