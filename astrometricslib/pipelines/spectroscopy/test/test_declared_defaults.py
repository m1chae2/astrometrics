"""Tests for values that used to be hidden defaults in function signatures.

Several spectroscopy functions used to carry a number as an argument default
(an extraction radius, a polynomial degree, a floor, a smoothing window).
Each is now a declared parameter, or a required argument. These tests check
that a required argument really is required, that a declared value gives
the old number on the reference setup, that it follows a recorded basis, and
that the code reading it uses the declaration.
"""

import numpy as np
import pytest

from astrometricslib.foundation.parameter_context import parameter_context, record_basis
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES
from astrometricslib.models.tuned_parameter import RunBasis
from astrometricslib.pipelines.spectroscopy.post_processing import compare_to_gaia_xp
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    MINIMUM_QUANTUM_EFFICIENCY_FRACTION,
    QuantumEfficiencyCurve,
    apply_quantum_efficiency_correction,
    quantum_efficiency_correction_factor,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_calibrator import (
    SPECTRUM_SMOOTHING_WINDOW_ANGSTROM,
    SpectrumCalibrator,
    smoothing_window_samples,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor import (
    CENTROID_BOX_HALF_WIDTH_PX,
    CENTROID_WINDOW_HALF_WIDTH_PX,
    SpectrumExtractor,
)
from astrometricslib.pipelines.spectroscopy.processing import class_gap_calibration
from astrometricslib.scripts.calibrate_class_gap_against_snr import _build_argument_parser
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig
from astrometricslib.utilities.spectroscopy_models import (
    CENTERLINE_POLYNOMIAL_DEGREE,
    EXTRACTION_RADIUS_PX,
)


def _config() -> SpectroscopyConfig:
    """Build a small config that sets no extraction radius.

    Returns
    -------
    config : `SpectroscopyConfig`
        A config with every extraction setting at its default.
    """
    camera = CameraConfig(name="Test", pixel_size_μm=3.76, sensor_width_px=100, sensor_height_px=100)
    return SpectroscopyConfig(camera=camera, grating_distance_mm=16.5)


def test_the_extractor_needs_a_radius() -> None:
    """Verify the extractor no longer has a hidden radius of 10."""
    with pytest.raises(TypeError):
        SpectrumExtractor()  # type: ignore[call-arg]


def test_the_traced_extractions_need_a_polynomial_degree() -> None:
    """Verify neither traced extraction has a hidden degree of 2."""
    extractor = SpectrumExtractor(radius=10)
    with pytest.raises(TypeError):
        extractor.extract_line_traced(None, (0.0, 0.0), np.array([1.0, 0.0]), 10)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        extractor.extract_with_flare_mask_traced(None, (0.0, 0.0), 0.0, 10.0, 10)  # type: ignore[call-arg]


def test_the_extraction_radius_is_ten_on_the_reference_setup() -> None:
    """Verify the config radius is the old 10 with no basis measured."""
    with parameter_context():
        assert _config().extraction_radius == 10
        assert resolve(EXTRACTION_RADIUS_PX).value == 10


def test_the_extraction_radius_follows_the_trail_width() -> None:
    """Verify a wider trail gives a wider box, and a set value still wins."""
    with parameter_context():
        record_basis(RunBasis.TRAIL_WIDTH_PX, 2.0 * REFERENCE_SETUP_BASES[RunBasis.TRAIL_WIDTH_PX])
        assert _config().extraction_radius == 20
        assert _config().with_overrides(extraction_radius=7).extraction_radius == 7


def test_the_centerline_degree_is_a_setting_with_the_old_value() -> None:
    """Verify the config degree comes from the declared processing setting."""
    assert CENTERLINE_POLYNOMIAL_DEGREE.config_key == (
        "Processing.Spectroscopy",
        "centerline_polynomial_degree",
    )
    assert _config().centerline_polynomial_degree == 2


def test_the_centroid_box_and_window_are_the_old_numbers_and_scale() -> None:
    """Verify box 10 and window 8, doubled at twice the star width."""
    with parameter_context():
        assert resolve(CENTROID_BOX_HALF_WIDTH_PX).value == 10
        assert resolve(CENTROID_WINDOW_HALF_WIDTH_PX).value == pytest.approx(8.0)
    with parameter_context():
        record_basis(RunBasis.STAR_WIDTH_PX, 2.0 * REFERENCE_SETUP_BASES[RunBasis.STAR_WIDTH_PX])
        assert resolve(CENTROID_BOX_HALF_WIDTH_PX).value == 20
        assert resolve(CENTROID_WINDOW_HALF_WIDTH_PX).value == pytest.approx(16.0)


def test_the_centroid_window_default_is_the_declared_window() -> None:
    """Verify leaving the window out equals passing the declared value."""
    rows, columns = np.indices((21, 21))
    box = np.exp(-((rows - 9.6) ** 2 + (columns - 10.3) ** 2) / 8.0)
    with parameter_context():
        default = SpectrumExtractor._recentre_centroid_window(box, 10.0, 10.0)
        explicit = SpectrumExtractor._recentre_centroid_window(box, 10.0, 10.0, half_width=8.0)
    assert default == pytest.approx(explicit)


def test_the_quantum_efficiency_floor_is_declared_once() -> None:
    """Verify the floor caps the correction at 100 times in both functions."""
    curve = QuantumEfficiencyCurve(
        wavelength_nm=np.array([400.0, 500.0]), quantum_efficiency_fraction=np.array([0.0, 0.0])
    )
    wavelength = np.array([450.0])
    floor = resolve(MINIMUM_QUANTUM_EFFICIENCY_FRACTION).value
    assert floor == pytest.approx(0.01)
    np.testing.assert_allclose(quantum_efficiency_correction_factor(wavelength, curve), [100.0])
    np.testing.assert_allclose(
        apply_quantum_efficiency_correction(wavelength, np.array([2.0]), curve), [200.0]
    )


def test_the_smoothing_window_is_five_samples_on_the_reference_setup() -> None:
    """Verify the window is 5 samples and that apply_smoothing uses it."""
    with parameter_context():
        assert smoothing_window_samples() == 5
        spike = np.zeros(21)
        spike[10] = 5.0
        smoothed = SpectrumCalibrator.apply_smoothing(object(), spike)  # type: ignore[arg-type]
    np.testing.assert_allclose(smoothed[8:13], 1.0)
    assert smoothed[7] == pytest.approx(0.0)


def test_the_smoothing_window_follows_the_resolution_and_dispersion() -> None:
    """Twice the resolution widens it; twice the dispersion narrows it."""
    resolution = REFERENCE_SETUP_BASES[RunBasis.SPECTRAL_RESOLUTION_ANGSTROM]
    dispersion = REFERENCE_SETUP_BASES[RunBasis.DISPERSION_ANGSTROM_PER_PX]
    with parameter_context():
        record_basis(RunBasis.SPECTRAL_RESOLUTION_ANGSTROM, 2.0 * resolution)
        assert smoothing_window_samples() == 11
    with parameter_context():
        record_basis(RunBasis.DISPERSION_ANGSTROM_PER_PX, 2.0 * dispersion)
        assert smoothing_window_samples() == 3
    assert SPECTRUM_SMOOTHING_WINDOW_ANGSTROM.basis is RunBasis.SPECTRAL_RESOLUTION_ANGSTROM


def test_the_gaia_xp_blur_nodes_are_the_measured_ones() -> None:
    """Verify the node table holds the 12-star medians."""
    assert compare_to_gaia_xp.XP_RESOLUTION_FWHM_NODES_ANGSTROM == (
        (4340.0, 95.0),
        (4861.0, 130.0),
        (6563.0, 70.0),
        (8800.0, 185.0),
    )


def test_the_class_gap_script_reads_the_declared_counts() -> None:
    """Verify the script defaults are the declared draw and resample counts."""
    arguments = _build_argument_parser().parse_args([])
    assert arguments.draws == resolve(class_gap_calibration.DRAWS_PER_SPECTRUM).value == 5
    assert arguments.bootstrap == resolve(class_gap_calibration.BOOTSTRAP_COUNT).value == 400
