"""Purpose: Delegation tests for the `Visualization` sub-API.

Description: `render_fits` and `plot` check their arguments and hand the
work to `pipelines.shared.image_conversions` and the `visualization`
package. The drawing code has its own tests. These tests check the
hand-off: each kind reaches the right function with the right arguments,
the result comes back unchanged, and an argument a kind does not use is
refused instead of being dropped.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.api.visualization import Visualization
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared import image_conversions
from astrometricslib.visualization import focus_plots, helpers


def _make_visualization(target: Target | None = None) -> tuple[Visualization, MagicMock, MagicMock]:
    """Build a `Visualization` over mock catalogs.

    Returns
    -------
    visualization : `Visualization`
        The sub-API under test.
    targets : `MagicMock`
        The mock target catalog. Its ``get`` returns ``target``.
    stars : `MagicMock`
        The mock star catalog.
    """
    targets = MagicMock()
    targets.get.return_value = target
    stars = MagicMock()
    config = MagicMock()
    return Visualization(config, MagicMock(), targets=targets, stars=stars), targets, stars


def test_render_fits_data_url_delegates_to_image_conversions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the data-URL kind passes its arguments and result through."""
    visualization, _, _ = _make_visualization()
    mock = MagicMock(return_value="picture")
    monkeypatch.setattr(image_conversions, "render_data_url", mock)

    result = visualization.render_fits(
        "/fake.fits", kind="data_url", max_dimensions=500, stretch=False, center=1.0, width=2.0
    )

    assert result == "picture"
    mock.assert_called_once_with("/fake.fits", "", 500, False, 1.0, 2.0)


def test_render_fits_finds_a_target_frame_by_gain_and_exposure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ``iso`` and ``exposure`` pick the frame through `get_frame`."""
    target = Target(id="M13")
    visualization, _, _ = _make_visualization(target)
    monkeypatch.setattr(image_conversions, "get_frame", MagicMock(return_value=__file__))
    render = MagicMock(return_value="picture")
    monkeypatch.setattr(image_conversions, "render_data_url", render)

    result = visualization.render_fits(target="M13", iso="800", exposure="60", index=2, kind="data_url")

    assert result == "picture"
    image_conversions.get_frame.assert_called_once_with(target, "800", "60", 2)
    render.assert_called_once_with(__file__, "M13", 2000, True, None, None)


def test_render_fits_refuses_a_crop_for_the_data_url_kind() -> None:
    """Verify an argument the kind does not use raises."""
    visualization, _, _ = _make_visualization()
    with pytest.raises(InvalidArgumentError):
        visualization.render_fits(
            "/fake.fits", kind="data_url", crop_center_x=1, crop_center_y=1, crop_size=5
        )


def test_render_fits_refuses_a_frame_named_two_ways() -> None:
    """Verify a path together with a target is refused."""
    visualization, _, _ = _make_visualization(Target(id="M13"))
    with pytest.raises(InvalidArgumentError):
        visualization.render_fits("/fake.fits", target="M13", file_name="001")


def test_get_last_captured_image_delegates_with_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the sub-API's own configuration is forwarded."""
    visualization, _, _ = _make_visualization()
    mock = MagicMock(return_value="picture")
    monkeypatch.setattr(image_conversions, "get_last_captured_image", mock)

    result = visualization.get_last_captured_image(stretch=False)

    assert result == "picture"
    mock.assert_called_once_with(visualization._config, False)


@pytest.mark.parametrize(
    ("kind", "helper_name"),
    [
        ("astrometry", "plot_astrometry"),
        ("photometry", "plot_target_photometry"),
        ("spectroscopy", "plot_target_spectroscopy"),
    ],
)
def test_target_plots_delegate_with_the_star_catalog(
    monkeypatch: pytest.MonkeyPatch, kind: str, helper_name: str
) -> None:
    """Verify each target chart reaches its helper with the star catalog."""
    target = Target(id="M13")
    visualization, _, stars = _make_visualization(target)
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, helper_name, mock)

    result = visualization.plot(kind, "M13", limit=5, figsize=(1, 1))

    assert result == "figure"
    mock.assert_called_once_with(target, stars, limit=5, figsize=(1, 1))


def test_dashboard_plot_passes_the_selected_star(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the dashboard gets the highlighted star as a record."""
    target = Target(id="M13")
    visualization, _, stars = _make_visualization(target)
    selected = MagicMock()
    stars.get.return_value = selected
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, "plot_target_dashboard", mock)

    result = visualization.plot("dashboard", target, selected_star="star-1")

    assert result == "figure"
    mock.assert_called_once_with(target, stars, limit=15, figsize=(16, 9), selected_star=selected)


def test_star_plot_delegates_to_plot_stellar_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the star chart gets both records unchanged."""
    from astrometricslib.models.stellar_source import StellarObject

    visualization, _, _ = _make_visualization()
    star = StellarObject(id="a")
    spectral_star = StellarObject(id="b")
    mock = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, "plot_stellar_analysis", mock)

    result = visualization.plot("star", star=star, spectral_star=spectral_star, figsize=(2, 2))

    assert result == "figure"
    mock.assert_called_once_with(star, spectral_star=spectral_star, figsize=(2, 2))


def test_focus_and_asteroid_plots_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the focus and asteroid charts reach their drawing functions."""
    target = Target(id="M13")
    visualization, _, _ = _make_visualization(target)
    focus = MagicMock(return_value="focus figure")
    asteroids = MagicMock(return_value="asteroid figure")
    monkeypatch.setattr(focus_plots, "plot_focus_vs_temperature", focus)
    monkeypatch.setattr(helpers, "plot_asteroid_detection", asteroids)

    assert visualization.plot("focus", target) == "focus figure"
    assert visualization.plot("asteroids", target, figsize=(3, 3)) == "asteroid figure"
    focus.assert_called_once_with(target, figsize=(14, 9))
    asteroids.assert_called_once_with(target, figsize=(3, 3))


def test_plot_refuses_arguments_the_kind_does_not_use() -> None:
    """Verify an unknown kind, a stray argument or a missing subject raises."""
    visualization, _, _ = _make_visualization(Target(id="M13"))
    with pytest.raises(InvalidArgumentError):
        visualization.plot("histogram", "M13")
    with pytest.raises(InvalidArgumentError):
        visualization.plot("focus", "M13", limit=3)
    with pytest.raises(InvalidArgumentError):
        visualization.plot("star")
    with pytest.raises(InvalidArgumentError):
        visualization.plot("astrometry")


def test_plot_of_an_unknown_target_raises_not_found() -> None:
    """Verify a target id that names no target raises `NotFoundError`."""
    visualization, _, _ = _make_visualization(None)
    with pytest.raises(NotFoundError):
        visualization.plot("astrometry", "No Such Target")
