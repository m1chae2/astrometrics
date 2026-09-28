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


def test_a_camera_section_is_found_whatever_the_spacing_case_or_punctuation() -> None:
    """Check the loose match that finds a camera's own config section."""
    import configparser

    parser = configparser.ConfigParser()
    parser.read_string("[Observatory.Camera.ZWO ASI 533MM Pro]\ngrating_lines_per_mm = 200\n")
    config = AppConfiguration()
    config.app_config = parser
    for spelling in ("ZWO ASI 533MM Pro", "ZWO ASI533MM Pro", "zwo-asi533mm-pro"):
        assert config.get_camera_config(spelling) == {"grating_lines_per_mm": "200"}


def _isolated_from_shared_config(tmp_path: Path) -> AppConfiguration:
    """Build an AppConfiguration over shared fixture data, writing to tmp_path.

    Reads the shared test fixture's real camera data but writes to its own
    tmp_path file, so a test that calls `update_config` can't leave the
    shared fixture mutated for later tests.

    Returns
    -------
    AppConfiguration
        A configuration seeded from the shared fixture, redirected to an
        isolated write target.
    """
    config = AppConfiguration()
    config.config_file_path = tmp_path / "isolated.config.toml"
    return config


def test_update_config_leaves_untouched_camera_calibration_fields_alone(tmp_path: Path) -> None:
    """A sparse update_config call must not disturb sections it wasn't given.

    Settings used to send `update_config` the *entire* fetched config
    back on every save, including camera sections the user never opened.
    Since `system:get_config` returns each camera's inline-table
    calibration fields (`clip_ceiling_adu`, etc.) already stringified,
    and `.set()` writes back whatever `str(value)` produces, resending
    them silently replaced the real TOML inline table with a quoted
    string, destroying the value/kind/source structure. The frontend fix
    is to only ever send a sparse patch of the fields actually edited;
    this test guards the assumption that fix relies on: `update_config`
    only touches the sections/keys it's given.
    """
    config = _isolated_from_shared_config(tmp_path)
    before = config.get_camera_config("Nikon D5300")["clip_ceiling_adu"]
    assert isinstance(before, dict)  # a real inline table, not a string

    config.update_config({"Image Library": {"frames_path": str(config.get_frames_path())}})

    after = config.get_camera_config("Nikon D5300")["clip_ceiling_adu"]
    assert isinstance(after, dict)
    assert after == before


def test_resending_an_already_stringified_field_would_corrupt_it(tmp_path: Path) -> None:
    """Document the exact failure mode a sparse patch avoids.

    `_TomlSectionedConfig.set()` (`config.app_config`) is
    configparser-compatible: it stores `str(value)` unconditionally. If a
    caller ever resends a calibration field using the same stringified
    representation `system:get_config` hands the frontend, `update_config`
    would write that string back verbatim in place of the inline table.
    This is not new behavior to fix here -- it is why Settings must never
    resend a section it did not actually edit.
    """
    config = _isolated_from_shared_config(tmp_path)
    already_stringified = str(config.get_camera_config("Nikon D5300")["clip_ceiling_adu"])

    config.update_config({"Observatory.Camera.Nikon D5300": {"clip_ceiling_adu": already_stringified}})

    corrupted = config.get_camera_config("Nikon D5300")["clip_ceiling_adu"]
    assert isinstance(corrupted, str)
    assert not isinstance(corrupted, dict)
