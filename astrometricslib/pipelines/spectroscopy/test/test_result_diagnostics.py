"""Purpose: Tests for the new diagnostics fields on a spectrum result.

Description: The extractor records how it read the sky and how wide its box
was, and the analysis records whether the airmass correction was applied.
These tests check that both reach the star's saved `SpectroscopyResult`
when the pipeline builds it from a synthetic spectral frame, that the
diagnostics summary uses plain Python values, and that each new model
survives a round trip through its camelCase form.
"""

import numpy as np
import pytest

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.models.stellar_source import (
    ExtinctionCorrectionRecord,
    SpectralExtractionDiagnostics,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import (
    ExtractionDiagnostics,
    SpectrumExtractor,
)
from astrometricslib.test.synthetic import make_spectral_frame
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

CAMERA_NAME = "ZWO ASI 533MM Pro"
TARGET_AIRMASS = 1.4
REFERENCE_AIRMASS = 1.15
FRAME_SHAPE = (256, 900)
ZERO_ORDER_XY = (60.0, 128.0)


class _ArrayImage(AstrometricsImage):
    """An `AstrometricsImage` that wraps a NumPy array and a header dict."""

    def __init__(self, data: np.ndarray, header: dict[str, object] | None = None) -> None:
        """Wrap `data` so the pipeline can read it like a real image."""
        self._data = data
        self._header = header or {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """Image array data."""
        return self._data

    @property
    def header(self) -> dict[str, object]:
        """Image headers dict."""
        return self._header


def _build_pipeline(extraction_method: str) -> SpectroscopyPipeline:
    """Build a `SpectroscopyPipeline` for the ASI533 camera.

    Parameters
    ----------
    extraction_method : `str`
        The pipeline's extraction method, such as "traced".

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name=CAMERA_NAME,
        pixel_size_um=3.76,
        sensor_width_px=FRAME_SHAPE[1],
        sensor_height_px=FRAME_SHAPE[0],
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_distance_mm=16.5,
        dispersion_orientation="horizontal",
        dispersion_direction="positive",
        dispersion_start_px=200.0,
        extraction_method=extraction_method,
        use_flare_mask_extraction=True,
    )
    return SpectroscopyPipeline(config=config)


def _build_result(extraction_method: str) -> SpectroscopyResult:
    """Extract a synthetic star and build its saved result.

    Parameters
    ----------
    extraction_method : `str`
        The pipeline's extraction method, such as "traced".

    Returns
    -------
    result : `SpectroscopyResult`
        What the pipeline saved on the star.
    """
    frame = make_spectral_frame(
        zero_order_xy=ZERO_ORDER_XY, angle_deg=2.0, lines=(), shape=FRAME_SHAPE, add_noise=False
    )
    image = _ArrayImage(frame.image, header={"AIRMASS": TARGET_AIRMASS})
    pipeline = _build_pipeline(extraction_method)
    extraction = pipeline._process_single_star(image, ZERO_ORDER_XY, auto_detect_angle=False)
    star = StellarObject(id="TestStar")
    pipeline._apply_result_to_stellar_object(star, extraction, image)
    assert star.spectroscopy is not None
    return star.spectroscopy


@pytest.mark.parametrize("extraction_method", ["traced", "fixed"])
def test_a_pipeline_built_result_carries_both_records(extraction_method: str) -> None:
    """The saved result holds the diagnostics and the extinction record."""
    result = _build_result(extraction_method)

    diagnostics = result.extraction_diagnostics
    assert diagnostics is not None
    assert diagnostics.dominant_sky_mode is not None
    assert sum(diagnostics.sky_mode_counts.values()) > 0
    assert 0.0 <= diagnostics.contaminated_sky_fraction <= 1.0
    extinction = result.extinction_correction
    assert extinction is not None
    assert extinction.is_applied is True
    assert extinction.target_airmass == pytest.approx(TARGET_AIRMASS)
    assert extinction.reference_airmass == pytest.approx(REFERENCE_AIRMASS)
    assert extinction.reason is None


def test_a_traced_extraction_reports_the_box_half_width() -> None:
    """A traced extraction records the median and spread of the half-width."""
    diagnostics = _build_result("traced").extraction_diagnostics

    assert diagnostics is not None
    assert diagnostics.aperture_half_width_median_px is not None
    assert diagnostics.aperture_half_width_median_px > 0.0
    assert diagnostics.aperture_half_width_spread_px is not None
    assert diagnostics.aperture_half_width_spread_px >= 0.0


def test_the_extractor_summary_is_plain_python_values() -> None:
    """The summary holds `float` and `int` values, not NumPy scalars."""
    diagnostics = ExtractionDiagnostics(
        aperture_half_width_px=[4.0, 5.0, 5.0, 6.0],
        sky_mode_counts={"both_bands": 3, "upper_band_contaminated": 1},
    )

    summary = diagnostics.as_dict()

    assert summary["dominant_sky_mode"] == "both_bands"
    assert summary["contaminated_sky_fraction"] == pytest.approx(0.25)
    assert summary["aperture_half_width_median_px"] == pytest.approx(5.0)
    assert summary["aperture_half_width_spread_px"] == pytest.approx(np.std([4.0, 5.0, 5.0, 6.0]))
    for key in ("aperture_half_width_median_px", "aperture_half_width_spread_px"):
        assert type(summary[key]) is float
    assert all(type(count) is int for count in summary["sky_mode_counts"].values())


def test_an_untraced_extractor_has_no_half_width_summary() -> None:
    """With no recorded half-widths the median and spread are `None`."""
    summary = SpectrumExtractor().last_diagnostics.as_dict()

    assert summary["sky_mode_counts"] == {}
    assert summary["dominant_sky_mode"] is None
    assert summary["contaminated_sky_fraction"] == pytest.approx(0.0)
    assert summary["aperture_half_width_median_px"] is None
    assert summary["aperture_half_width_spread_px"] is None


def test_the_new_models_round_trip_through_camel_case() -> None:
    """Both models survive `model_dump(by_alias=True)` and back."""
    diagnostics = SpectralExtractionDiagnostics(
        sky_mode_counts={"both_bands": 9, "lower_band_contaminated": 1},
        dominant_sky_mode="both_bands",
        contaminated_sky_fraction=0.1,
        aperture_half_width_median_px=5.0,
        aperture_half_width_spread_px=0.3,
    )
    extinction = ExtinctionCorrectionRecord(
        is_applied=False,
        target_airmass=None,
        reference_airmass=REFERENCE_AIRMASS,
        curve_name="paranal",
        reason="no target airmass",
    )

    dumped = diagnostics.model_dump(by_alias=True)
    assert dumped["skyModeCounts"] == {"both_bands": 9, "lower_band_contaminated": 1}
    assert dumped["apertureHalfWidthMedianPx"] == pytest.approx(5.0)
    assert SpectralExtractionDiagnostics.model_validate(dumped) == diagnostics
    dumped_extinction = extinction.model_dump(by_alias=True)
    assert dumped_extinction["isApplied"] is False
    assert dumped_extinction["curveName"] == "paranal"
    assert ExtinctionCorrectionRecord.model_validate(dumped_extinction) == extinction


def test_a_result_round_trips_with_both_fields() -> None:
    """A `SpectroscopyResult` keeps both fields through its camelCase form."""
    result = SpectroscopyResult(
        extraction_diagnostics=SpectralExtractionDiagnostics(dominant_sky_mode="both_bands"),
        extinction_correction=ExtinctionCorrectionRecord(
            is_applied=True, target_airmass=1.4, reference_airmass=1.15, curve_name="paranal"
        ),
    )

    dumped = result.model_dump(by_alias=True)
    restored = SpectroscopyResult.model_validate(dumped)

    assert dumped["extractionDiagnostics"]["dominantSkyMode"] == "both_bands"
    assert dumped["extinctionCorrection"]["targetAirmass"] == pytest.approx(1.4)
    assert restored.extraction_diagnostics == result.extraction_diagnostics
    assert restored.extinction_correction == result.extinction_correction


def test_a_result_without_the_fields_loads_with_none() -> None:
    """A spectrum saved before the fields existed leaves both as `None`."""
    result = SpectroscopyResult.model_validate({})

    assert result.extraction_diagnostics is None
    assert result.extinction_correction is None


def test_the_extinction_record_matches_the_pipeline_dictionary() -> None:
    """The model accepts the dictionary the analysis stores."""
    record = {
        "is_applied": True,
        "target_airmass": 1.4,
        "reference_airmass": 1.15,
        "curve_name": "paranal",
        "reason": None,
    }

    assert ExtinctionCorrectionRecord.model_validate(record).model_dump() == record
