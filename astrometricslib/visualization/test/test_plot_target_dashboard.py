"""Purpose: Unit tests for plot_target_dashboard and helpers.

Description: Verifies standalone stellar plotting, target plotting,
error checking, and layout generation.
"""

from unittest.mock import MagicMock

import matplotlib.pyplot as plt
import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage, FrameDetection
from astrometricslib.models.stellar_source import SpectroscopyResult
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
    mock_star.photometry = mock_light_curve
    mock_star.is_variable_candidate = True

    fig = plot_stellar_photometry(mock_star)
    assert isinstance(fig, plt.Figure)
    plt.close(fig)


def test_plot_stellar_photometry_raises_on_missing_light_curve():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_photometry raises ValueError.

    Tests error handling for missing photometry data.
    """
    mock_star = MagicMock()
    mock_star.photometry = None

    with pytest.raises(ValueError, match="no photometry attribute"):
        plot_stellar_photometry(mock_star)


def test_plot_stellar_spectroscopy_renders_spectrum():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_spectroscopy returns a figure.

    Tests basic spectroscopy rendering when spectrum data is present.
    """
    mock_star = MagicMock()
    mock_star.name = "Test Star Spectrum"
    mock_star.stellar_spectral_type = "G2V"
    mock_star.spectroscopy = SpectroscopyResult(
        wavelengths_angstrom=[4000.0, 5000.0, 6000.0],
        intensities=[10.0, 25.0, 15.0],
        quantum_efficiency_corrected_intensities=[12.0, 28.0, 17.0],
    )

    fig = plot_stellar_spectroscopy(mock_star)
    assert isinstance(fig, plt.Figure)
    plt.close(fig)


def test_plot_stellar_spectroscopy_raises_on_missing_spectrum():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify plot_stellar_spectroscopy raises ValueError.

    Tests error handling for missing spectrum data.
    """
    mock_star = MagicMock()
    mock_star.spectroscopy = None

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
    mock_star_synthetic.spectroscopy.dispersion_angle = None

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

    # 1. One star row holding both photometry and spectroscopy
    star_catalog = MagicMock()
    star_catalog.id = "Gaia DR3 12345"
    star_catalog.target_ids = ["M 13"]
    star_catalog.stellar_spectral_type = "G2V"
    star_catalog.spectroscopy = SpectroscopyResult(
        wavelengths_angstrom=[4000.0, 5000.0],
        intensities=[10.0, 20.0],
        dispersion_angle=45.0,
    )
    star_catalog.magnitude = 10.5
    star_catalog.name = "Gaia 12345"
    star_catalog.star_data = {"xcentroid": 100.0, "ycentroid": 100.0}

    mock_light_curve = MagicMock()
    mock_light_curve.timestamps = [1.0, 2.0]
    mock_light_curve.fluxes_detrended = [1.0, 1.1]
    mock_light_curve.fluxes_normalized = [1.0, 1.1]
    star_catalog.photometry = mock_light_curve

    mock_astrometrics = MagicMock()
    mock_astrometrics.stars.list_objects.return_value = [star_catalog]

    # Both photometry + spectroscopy -> 3 axes
    fig_both = plot_target_dashboard(mock_target, mock_astrometrics.stars)
    assert len(fig_both.axes) == 3
    plt.close(fig_both)

    # Photometry only -> 2 axes
    star_catalog_no_spec = MagicMock()
    star_catalog_no_spec.id = "Gaia DR3 99999"
    star_catalog_no_spec.target_ids = ["M 13"]
    star_catalog_no_spec.spectroscopy.dispersion_angle = None
    star_catalog_no_spec.magnitude = 11.0
    star_catalog_no_spec.star_data = {"xcentroid": 50.0, "ycentroid": 50.0}
    star_catalog_no_spec.photometry = mock_light_curve

    mock_astrometrics.stars.list_objects.return_value = [star_catalog_no_spec]
    fig_photo_only = plot_target_dashboard(mock_target, mock_astrometrics.stars)
    assert len(fig_photo_only.axes) == 2
    plt.close(fig_photo_only)

    # Neither (star field only) -> 1 axis
    star_catalog_bare = MagicMock()
    star_catalog_bare.id = "Gaia DR3 88888"
    star_catalog_bare.target_ids = ["M 13"]
    star_catalog_bare.spectroscopy.dispersion_angle = None
    star_catalog_bare.magnitude = 12.0
    star_catalog_bare.star_data = {"xcentroid": 20.0, "ycentroid": 20.0}
    star_catalog_bare.photometry = None

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
    mock_star.photometry = mock_light_curve
    mock_star.stellar_spectral_type = "K0V"
    mock_star.spectroscopy = SpectroscopyResult(
        wavelengths_angstrom=[4500.0, 5500.0],
        intensities=[15.0, 25.0],
    )

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
    mock_star.photometry = None
    mock_star.spectroscopy = None

    with pytest.raises(ValueError, match="neither photometry nor spectrum"):
        plot_stellar_analysis(mock_star)


def test_plot_target_dashboard_draws_asteroid_candidates_on_the_star_field(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a target's asteroid_candidates are drawn on the astrometry panel.

    Uses a real stacked-image FITS file (with a real WCS) rather than
    the monkeypatched `AstrometricsImage` the layout-cases test uses,
    since drawing a candidate's track requires projecting its RA/Dec
    through that WCS.
    """
    stack_path = tmp_path / "stack.fits"
    header = fits.Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 30.0
    header["CRPIX1"] = 32.0
    header["CRPIX2"] = 32.0
    header["CD1_1"] = -0.0005
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = 0.0005
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"
    fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(stack_path, overwrite=True)

    candidate = AsteroidDetectionCandidate(
        id="candidate-1",
        target_id="M 13",
        frame_detections=[
            FrameDetection(
                frame_path="/fake/frame0.fits",
                timestamp=0.0,
                pixel_x=5.0,
                pixel_y=5.0,
                right_ascension_deg=150.0,
                declination_deg=30.0,
            ),
        ],
        cascade_stage=CascadeStage.RATE_LINEARITY_CONFIRMED,
    )

    mock_target = MagicMock()
    mock_target.id = "M 13"
    mock_target.stacked_image = str(stack_path)
    mock_target.asteroid_candidates = [candidate]

    star_catalog_bare = MagicMock()
    star_catalog_bare.id = "Gaia DR3 88888"
    star_catalog_bare.target_ids = ["M 13"]
    star_catalog_bare.spectroscopy.dispersion_angle = None
    star_catalog_bare.magnitude = 12.0
    star_catalog_bare.star_data = {"xcentroid": 20.0, "ycentroid": 20.0}
    star_catalog_bare.photometry = None

    mock_astrometrics = MagicMock()
    mock_astrometrics.stars.list_objects.return_value = [star_catalog_bare]

    fig = plot_target_dashboard(mock_target, mock_astrometrics.stars)

    (ax_astrometry,) = fig.axes
    assert len(ax_astrometry.lines) == 1
    plt.close(fig)
