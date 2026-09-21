"""Tests for AppConfiguration's identified-star ceiling default.

This ensures the `get_maximum_identified_stars` returns a safe default
(e.g., 500) so we don't run out of memory trying to process too many
stars at once.
"""

from pathlib import Path

from astrometricslib.utilities.config_loader import AppConfiguration


def _make_isolated_config(tmp_path: Path) -> AppConfiguration:
    """Build an AppConfiguration pointed at a fresh, empty tmp_path library.

    Returns
    -------
    AppConfiguration
        A configuration pointed at a fresh, empty library under tmp_path.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    frames_path = library_path / "frames"
    frames_path.mkdir(parents=True)

    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return config


def test_a_fresh_config_defaults_the_identified_star_ceiling_to_500(tmp_path: Path) -> None:
    """A fresh install must not default to unlimited identification."""
    config = _make_isolated_config(tmp_path)

    assert config.get_maximum_identified_stars() == 500


def test_an_explicit_zero_in_configuration_still_means_unlimited(tmp_path: Path) -> None:
    """A caller who wants full completeness back can still opt in."""
    config = _make_isolated_config(tmp_path)
    config.app_config.set("Processing.Astrometry", "maximum_identified_stars", "0")

    assert config.get_maximum_identified_stars() is None


def test_get_frames_path_defaults_to_frames_subfolder(tmp_path: Path) -> None:
    """Verify that get_frames_path defaults to a 'frames' subfolder."""
    config = _make_isolated_config(tmp_path)
    expected = (tmp_path / "library" / "frames").absolute()
    assert config.get_frames_path() == expected


def test_get_frames_path_reads_configured_frames_path(tmp_path: Path) -> None:
    """Verify that get_frames_path respects explicit frames_path config."""
    config = _make_isolated_config(tmp_path)
    custom_frames = (tmp_path / "external_frames").absolute()
    custom_frames.mkdir(parents=True)
    config.app_config.set("Image Library", "frames_path", str(custom_frames))
    assert config.get_frames_path() == custom_frames
