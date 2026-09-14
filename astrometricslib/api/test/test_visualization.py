"""Purpose: Delegation-contract tests for the Visualization facade.

Description: Every method here is a thin pass-through to a function in
visualization.helpers or pipelines.shared.image_conversions. Nothing
about that logic needs re-testing -- the underlying functions already
have their own thorough tests -- but the forwarding itself has broken
silently before (a renamed kwarg swallowed into **kwargs, a helper
added but never wired into this class), so each method gets one test
asserting it calls the right function with the right arguments and
returns its result unchanged.
"""

from unittest.mock import MagicMock

from astrometricslib.api.visualization import Visualization
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared import image_conversions
from astrometricslib.visualization import helpers


def _make_visualization() -> Visualization:
    astrometrics = MagicMock()
    return Visualization(astrometrics)


def test_convert_fits_to_png_delegates_to_image_conversions(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify arguments and the return value both pass through unchanged."""
    visualization = _make_visualization()
    mock = MagicMock(return_value={"png": "data"})
    monkeypatch.setattr(image_conversions, "convert_fits_to_png", mock)

    result = visualization.convert_fits_to_png("/fake.fits", max_dimensions=500, stretch=False)

    assert result == {"png": "data"}
    mock.assert_called_once_with("/fake.fits", 500, False)


def test_convert_fits_to_png_with_stats_delegates_to_image_conversions(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify arguments and the return value both pass through unchanged."""
    visualization = _make_visualization()
    mock = MagicMock(return_value=(b"bytes", 1.0, 2.0))
    monkeypatch.setattr(image_conversions, "convert_fits_to_png_with_stats", mock)

    result = visualization.convert_fits_to_png_with_stats(
        "/fake.fits", max_dimensions=500, center=1.0, width=2.0, cmap="viridis", stretch=False
    )

    assert result == (b"bytes", 1.0, 2.0)
    mock.assert_called_once_with("/fake.fits", 500, 1.0, 2.0, "viridis", False)


def test_get_light_frame_data_delegates_to_image_conversions(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify arguments and the return value both pass through unchanged."""
    visualization = _make_visualization()
    target = Target(id="M13")
    mock = MagicMock(return_value={"png": "data"})
    monkeypatch.setattr(image_conversions, "get_light_frame_data", mock)

    result = visualization.get_light_frame_data(target, "800", "60", index=2, stretch=False)

    assert result == {"png": "data"}
    mock.assert_called_once_with(target, "800", "60", 2, False)


def test_get_last_captured_image_delegates_with_the_astrometrics_config(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the parent Astrometrics' config is forwarded, not a new one."""
    astrometrics = MagicMock()
    visualization = Visualization(astrometrics)
    mock = MagicMock(return_value={"png": "data"})
    monkeypatch.setattr(image_conversions, "get_last_captured_image", mock)

    result = visualization.get_last_captured_image(stretch=False)

    assert result == {"png": "data"}
    mock.assert_called_once_with(astrometrics.config, False)


def test_plot_target_dashboard_delegates_with_the_astrometrics_stars(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the parent Astrometrics' stars catalog is forwarded."""
    astrometrics = MagicMock()
    visualization = Visualization(astrometrics)
    target = Target(id="M13")
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, "plot_target_dashboard", mock)

    result = visualization.plot_target_dashboard(target, limit=5, figsize=(1, 1), selected_star=None)

    assert result == "figure"
    mock.assert_called_once_with(target, astrometrics.stars, limit=5, figsize=(1, 1), selected_star=None)


def test_plot_star_dashboard_delegates_to_plot_stellar_analysis(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify arguments and the return value both pass through unchanged."""
    visualization = _make_visualization()
    star = MagicMock()
    spectral_star = MagicMock()
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, "plot_stellar_analysis", mock)

    result = visualization.plot_star_dashboard(star, spectral_star=spectral_star, figsize=(2, 2))

    assert result == "figure"
    mock.assert_called_once_with(star, spectral_star=spectral_star, figsize=(2, 2))


def test_plot_astrometry_delegates_with_the_astrometrics_stars(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the parent Astrometrics' stars catalog is forwarded."""
    astrometrics = MagicMock()
    visualization = Visualization(astrometrics)
    target = Target(id="M13")
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, "plot_astrometry", mock)

    result = visualization.plot_astrometry(target, limit=5, figsize=(1, 1))

    assert result == "figure"
    mock.assert_called_once_with(target, astrometrics.stars, limit=5, figsize=(1, 1))


def test_plot_photometry_delegates_with_the_astrometrics_stars(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the parent Astrometrics' stars catalog is forwarded."""
    astrometrics = MagicMock()
    visualization = Visualization(astrometrics)
    target = Target(id="M13")
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, "plot_target_photometry", mock)

    result = visualization.plot_photometry(target, limit=5, figsize=(1, 1))

    assert result == "figure"
    mock.assert_called_once_with(target, astrometrics.stars, limit=5, figsize=(1, 1))


def test_plot_spectroscopy_delegates_with_the_astrometrics_stars(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the parent Astrometrics' stars catalog is forwarded."""
    astrometrics = MagicMock()
    visualization = Visualization(astrometrics)
    target = Target(id="M13")
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, "plot_target_spectroscopy", mock)

    result = visualization.plot_spectroscopy(target, limit=5, figsize=(1, 1))

    assert result == "figure"
    mock.assert_called_once_with(target, astrometrics.stars, limit=5, figsize=(1, 1))


def test_plot_asteroid_detection_delegates_to_helpers(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the newly wired method reaches visualization.helpers.

    This method exists in helpers.py but, until now, was never exposed
    on this facade -- so nothing outside the library could actually
    call it through the public API.
    """
    visualization = _make_visualization()
    target = Target(id="M13")
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, "plot_asteroid_detection", mock)

    result = visualization.plot_asteroid_detection(target, figsize=(3, 3))

    assert result == "figure"
    mock.assert_called_once_with(target, figsize=(3, 3))
