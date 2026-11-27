"""Purpose: Delegation tests for the `Visualization` sub-API.

Description: Each method is a thin wrapper that hands the work to one owner
in `pipelines.shared.image_conversions` or the `visualization` package. The
drawing code has its own tests. These tests check the hand-off: each method
reaches its owner with the catalog storage and its arguments, the result
comes back unchanged, and a bad star count is refused.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.api.visualization import Visualization
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared import image_conversions
from astrometricslib.visualization import focus_plots, helpers, image_display, sky_placement


def _make_visualization() -> Visualization:
    """Build a `Visualization` over mock settings and storage.

    Returns
    -------
    visualization : `Visualization`
        The sub-API under test.
    """
    return Visualization(MagicMock(), MagicMock())


def test_render_delegates_to_its_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `render` passes its arguments and result through."""
    visualization = _make_visualization()
    owner = MagicMock(return_value="picture")
    monkeypatch.setattr(image_conversions, "render_image", owner)

    result = visualization.render("/fake.fits", stretch=False, brightness=(1.0, 2.0), max_dimensions=500)

    assert result == "picture"
    owner.assert_called_once_with(
        "/fake.fits", stretch=False, brightness=(1.0, 2.0), crop=None, max_dimensions=500
    )


def test_render_newest_delegates_with_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the sub-API's own configuration is forwarded."""
    visualization = _make_visualization()
    owner = MagicMock(return_value="picture")
    monkeypatch.setattr(image_conversions, "render_newest_capture", owner)

    assert visualization.render_newest(stretch=False) == "picture"
    owner.assert_called_once_with(visualization._config, stretch=False, max_dimensions=None)


def test_plot_image_delegates_with_the_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the figure owner gets the catalog storage for the star names."""
    visualization = _make_visualization()
    owner = MagicMock(return_value="figure")
    monkeypatch.setattr(image_display, "plot_image", owner)

    assert visualization.plot_image("/fake.fits", overlays=["stars"], crop=(5, 5, 10)) == "figure"
    owner.assert_called_once_with(
        "/fake.fits",
        storage=visualization._storage,
        overlays=["stars"],
        crop=(5, 5, 10),
        stretch=True,
        brightness=None,
    )


@pytest.mark.parametrize(
    ("method", "owner_name", "takes_star"),
    [
        ("plot_target", "plot_target_dashboard", True),
        ("plot_photometry", "plot_target_photometry", True),
        ("plot_spectroscopy", "plot_target_spectroscopy", True),
        ("plot_astrometry", "plot_astrometry", False),
    ],
)
def test_target_charts_delegate_with_the_storage(
    monkeypatch: pytest.MonkeyPatch, method: str, owner_name: str, takes_star: bool
) -> None:
    """Verify each target chart reaches its owner with the storage."""
    target = Target(id="M13")
    star = StellarObject(id="a")
    visualization = _make_visualization()
    owner = MagicMock(return_value="figure")
    monkeypatch.setattr(helpers, owner_name, owner)

    if takes_star:
        result = getattr(visualization, method)(target, star=star, limit=5)
        owner.assert_called_once_with(target, visualization._storage, limit=5, selected_star=star)
    else:
        result = getattr(visualization, method)(target, limit=5)
        owner.assert_called_once_with(target, visualization._storage, limit=5)
    assert result == "figure"


def test_star_focus_and_asteroid_charts_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the star, focus and asteroid charts reach their owners."""
    target = Target(id="M13")
    star = StellarObject(id="a")
    visualization = _make_visualization()
    stellar = MagicMock(return_value="star figure")
    focus = MagicMock(return_value="focus figure")
    asteroids = MagicMock(return_value="asteroid figure")
    monkeypatch.setattr(helpers, "plot_stellar_analysis", stellar)
    monkeypatch.setattr(focus_plots, "plot_focus_vs_temperature", focus)
    monkeypatch.setattr(helpers, "plot_asteroid_detection", asteroids)

    assert visualization.plot_star(star) == "star figure"
    assert visualization.plot_focus(target) == "focus figure"
    assert visualization.plot_asteroids(target) == "asteroid figure"
    stellar.assert_called_once_with(star)
    focus.assert_called_once_with(target)
    asteroids.assert_called_once_with(target)


def test_a_star_count_below_one_is_refused() -> None:
    """Verify a chart refuses a limit below 1 instead of drawing nothing."""
    visualization = _make_visualization()
    with pytest.raises(InvalidArgumentError):
        visualization.plot_astrometry(Target(id="M13"), limit=0)


def test_get_sky_picture_delegates_to_its_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the sky placement owner gets the target."""
    target = Target(id="M13")
    owner = MagicMock(return_value=None)
    monkeypatch.setattr(sky_placement, "get_sky_picture", owner)

    assert _make_visualization().get_sky_picture(target) is None
    owner.assert_called_once_with(target)
