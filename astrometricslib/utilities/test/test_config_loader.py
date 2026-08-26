"""Tests for AppConfiguration's identified-star ceiling default.

get_maximum_identified_stars used to default to 0 (unlimited): the
detected sources are real stars, and this same ceiling also bounds
identify_session_stars's seed population for photometry. On 2026-08-25
NGC 6888 seeded 2,439 stars this way across a 166-frame session and two
photometry workers were OOM-killed after exhausting an 8GB swap. 500
keeps a comfortable margin over the normalization ensemble's own target
size (100) while capping that worst case.
"""

from astrometricslib.utilities.config_loader import AppConfiguration


def _make_isolated_config(tmp_path) -> AppConfiguration:  # ruff: ignore[missing-type-function-argument]
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
    config.update_config({"Image Library": {"path": str(library_path), "frames_path": str(frames_path)}})
    return config


def test_a_fresh_config_defaults_the_identified_star_ceiling_to_500(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A fresh install must not default to unlimited identification."""
    config = _make_isolated_config(tmp_path)

    assert config.get_maximum_identified_stars() == 500


def test_an_explicit_zero_in_configuration_still_means_unlimited(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A caller who wants full completeness back can still opt in."""
    config = _make_isolated_config(tmp_path)
    config.app_config.set("Processing.Astrometry", "maximum_identified_stars", "0")

    assert config.get_maximum_identified_stars() is None
