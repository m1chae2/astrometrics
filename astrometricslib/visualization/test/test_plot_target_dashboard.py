"""Purpose: Unit tests for plot_target_dashboard and helpers.

Description: Verifies standalone stellar plotting, target plotting,
error checking, and layout generation.
"""

from unittest.mock import MagicMock

import matplotlib.pyplot as plt
import pytest

from astrometricslib.visualization.helpers import (
    plot_stellar_analyses,
    plot_stellar_analysis,
    plot_stellar_photometry,
    plot_stellar_spectroscopy,
    plot_target_dashboard,
)


def test_plot_stellar_photometry_renders_light_curve():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_photometry returns a figure.

    Tests basic photometry rendering when light curve data is present.
    """
    mock_light_curve = MagicMock()
    mock_light_curve.timestamps = [2459000.0, 2459001.0, 2459002.0]
    mock_light_curve.fluxes_detrended = [1.0, 0.98, 1.02]
    mock_light_curve.fluxes_normalized = [1.0, 0.98, 1.02]

    mock_star = MagicMock()
    mock_star.name = "Test Star Photometry"
    mock_star.light_curve = mock_light_curve
    mock_star.is_variable_candidate = True

    fig = plot_stellar_photometry(mock_star)
    assert isinstance(fig, plt.Figure)
    plt.close(fig)


def test_plot_stellar_photometry_raises_on_missing_light_curve():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_photometry raises ValueError.

    Tests error handling for missing photometry data.
    """
    mock_star = MagicMock()
    mock_star.light_curve = None

    with pytest.raises(ValueError, match="no light_curve attribute"):
        plot_stellar_photometry(mock_star)


def test_plot_stellar_spectroscopy_renders_spectrum():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_spectroscopy returns a figure.

    Tests basic spectroscopy rendering when spectrum data is present.
    """
    mock_star = MagicMock()
    mock_star.name = "Test Star Spectrum"
    mock_star.stellar_spectral_type = "G2V"
    mock_star.spectrum_data_processed = {
        "wavelengths_angstrom": [4000.0, 5000.0, 6000.0],
        "intensities": [10.0, 25.0, 15.0],
        "quantum_efficiency_corrected_intensities": [12.0, 28.0, 17.0],
    }

    fig = plot_stellar_spectroscopy(mock_star)
    assert isinstance(fig, plt.Figure)
    plt.close(fig)


def test_plot_stellar_spectroscopy_raises_on_missing_spectrum():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_spectroscopy raises ValueError.

    Tests error handling for missing spectrum data.
    """
    mock_star = MagicMock()
    mock_star.spectrum_data_processed = None

    with pytest.raises(ValueError, match="no processed spectrum data"):
        plot_stellar_spectroscopy(mock_star)


def test_plot_target_dashboard_raises_on_missing_stacked_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_target_dashboard raises ValueError.

    Tests target validation when stacked image is missing.
    """
    mock_target = MagicMock()
    mock_target.id = "M 13"
    mock_target.stacked_image = None
    mock_astrometrics = MagicMock()

    with pytest.raises(ValueError, match="has no stacked_image"):
        plot_target_dashboard(mock_target, mock_astrometrics.stars)


def test_plot_target_dashboard_raises_on_no_catalog_stars():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_target_dashboard raises ValueError.

    Tests star list validation when no catalog stars are present.
    """
    mock_target = MagicMock()
    mock_target.id = "M 13"
    mock_target.stacked_image = "/fake/path/stacked.fits"

    mock_star_synthetic = MagicMock()
    mock_star_synthetic.id = "Star_1"
    mock_star_synthetic.target_ids = ["M 13"]
    mock_star_synthetic.dispersion_angle = None

    mock_astrometrics = MagicMock()
    mock_astrometrics.stars.list_objects.return_value = [mock_star_synthetic]

    with pytest.raises(ValueError, match="No catalog-identified stars found"):
        plot_target_dashboard(mock_target, mock_astrometrics.stars)


def test_plot_target_dashboard_dynamic_layout_cases(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify dynamic figure layout generation.

    Tests 3-panel, 2-panel, and 1-panel dynamic layout cases.
    """
    mock_target = MagicMock()
    mock_target.id = "M 13"
    mock_target.stacked_image = "/fake/path/stacked.fits"

    # Mock AstrometricsImage so no disk read occurs
    mock_img_instance = MagicMock()
    mock_img_instance.data = [[1, 2], [3, 4]]
    monkeypatch.setattr(
        "astrometricslib.visualization.helpers.AstrometricsImage",
        lambda p: mock_img_instance,
    )

    # 1. Star with both photometry and spectroscopy
    star_catalog = MagicMock()
    star_catalog.id = "Gaia DR3 12345"
    star_catalog.target_ids = ["M 13"]
    star_catalog.dispersion_angle = None
    star_catalog.magnitude = 10.5
    star_catalog.name = "Gaia 12345"
    star_catalog.star_data = {"xcentroid": 100.0, "ycentroid": 100.0}

    mock_light_curve = MagicMock()
    mock_light_curve.timestamps = [1.0, 2.0]
    mock_light_curve.fluxes_detrended = [1.0, 1.1]
    mock_light_curve.fluxes_normalized = [1.0, 1.1]
    star_catalog.light_curve = mock_light_curve

    star_spectral = MagicMock()
    star_spectral.id = "Gaia DR3 12345::spectroscopy"
    star_spectral.target_ids = ["M 13"]
    star_spectral.dispersion_angle = 45.0
    star_spectral.name = "Gaia 12345 Spec"
    star_spectral.stellar_spectral_type = "G2V"
    star_spectral.spectrum_data_processed = {
        "wavelengths_angstrom": [4000.0, 5000.0],
        "intensities": [10.0, 20.0],
    }

    mock_astrometrics = MagicMock()
    mock_astrometrics.stars.list_objects.return_value = [star_catalog, star_spectral]

    # Both photometry + spectroscopy -> 3 axes
    fig_both = plot_target_dashboard(mock_target, mock_astrometrics.stars)
    assert len(fig_both.axes) == 3
    plt.close(fig_both)

    # Photometry only -> 2 axes
    star_catalog_no_spec = MagicMock()
    star_catalog_no_spec.id = "Gaia DR3 99999"
    star_catalog_no_spec.target_ids = ["M 13"]
    star_catalog_no_spec.dispersion_angle = None
    star_catalog_no_spec.magnitude = 11.0
    star_catalog_no_spec.star_data = {"xcentroid": 50.0, "ycentroid": 50.0}
    star_catalog_no_spec.light_curve = mock_light_curve

    mock_astrometrics.stars.list_objects.return_value = [star_catalog_no_spec]
    fig_photo_only = plot_target_dashboard(mock_target, mock_astrometrics.stars)
    assert len(fig_photo_only.axes) == 2
    plt.close(fig_photo_only)

    # Neither (star field only) -> 1 axis
    star_catalog_bare = MagicMock()
    star_catalog_bare.id = "Gaia DR3 88888"
    star_catalog_bare.target_ids = ["M 13"]
    star_catalog_bare.dispersion_angle = None
    star_catalog_bare.magnitude = 12.0
    star_catalog_bare.star_data = {"xcentroid": 20.0, "ycentroid": 20.0}
    star_catalog_bare.light_curve = None

    mock_astrometrics.stars.list_objects.return_value = [star_catalog_bare]
    fig_bare = plot_target_dashboard(mock_target, mock_astrometrics.stars)
    assert len(fig_bare.axes) == 1
    plt.close(fig_bare)


def test_plot_stellar_analysis_renders_both_panels():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_analysis renders both photometry and spectroscopy.

    Tests 2-panel figure generation when both data types are available.
    """
    mock_light_curve = MagicMock()
    mock_light_curve.timestamps = [1.0, 2.0]
    mock_light_curve.fluxes_detrended = [1.0, 0.9]
    mock_light_curve.fluxes_normalized = [1.0, 0.9]

    mock_star = MagicMock()
    mock_star.name = "Combined Star"
    mock_star.light_curve = mock_light_curve
    mock_star.stellar_spectral_type = "K0V"
    mock_star.spectrum_data_processed = {
        "wavelengths_angstrom": [4500.0, 5500.0],
        "intensities": [15.0, 25.0],
    }

    fig = plot_stellar_analysis(mock_star)
    assert isinstance(fig, plt.Figure)
    assert len(fig.axes) == 2
    plt.close(fig)

    # Alias check
    fig_alias = plot_stellar_analyses(mock_star)
    assert isinstance(fig_alias, plt.Figure)
    plt.close(fig_alias)


def test_plot_stellar_analysis_raises_on_empty_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_analysis raises ValueError on empty star.

    Tests error handling when neither photometry nor spectroscopy is present.
    """
    mock_star = MagicMock()
    mock_star.light_curve = None
    mock_star.spectrum_data_processed = None

    with pytest.raises(ValueError, match="neither light_curve nor spectrum"):
        plot_stellar_analysis(mock_star)
