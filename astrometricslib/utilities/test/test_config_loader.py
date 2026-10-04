"""Tests for AppConfiguration's identified-star ceiling default.

This ensures the `get_maximum_identified_stars` returns a safe default
(e.g., 500) so we don't run out of memory trying to process too many
stars at once.
"""

from pathlib import Path

import pytest

from astrometricslib.utilities.config_loader import AppConfiguration


def test_a_fresh_config_defaults_the_identified_star_ceiling_to_500(
    config_in_tmp_path: AppConfiguration,
) -> None:
    """A fresh install must not default to unlimited identification."""
    config = config_in_tmp_path

    assert config.get_maximum_identified_stars() == 500


def test_an_explicit_zero_in_configuration_still_means_unlimited(
    config_in_tmp_path: AppConfiguration,
) -> None:
    """A caller who wants full completeness back can still opt in."""
    config = config_in_tmp_path
    config.app_config.set("Processing.Astrometry", "maximum_identified_stars", "0")

    assert config.get_maximum_identified_stars() is None


def test_get_frames_path_defaults_to_frames_subfolder(
    config_in_tmp_path: AppConfiguration, tmp_path: Path
) -> None:
    """Verify that get_frames_path defaults to a 'frames' subfolder."""
    config = config_in_tmp_path
    expected = (tmp_path / "library" / "frames").absolute()
    assert config.get_frames_path() == expected


def test_get_frames_path_reads_configured_frames_path(
    config_in_tmp_path: AppConfiguration, tmp_path: Path
) -> None:
    """Verify that get_frames_path respects explicit frames_path config."""
    config = config_in_tmp_path
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


def test_update_config_leaves_untouched_camera_calibration_fields_alone(
    config_in_tmp_path: AppConfiguration,
) -> None:
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
    config = config_in_tmp_path
    before = config.get_camera_config("Nikon D5300")["clip_ceiling_adu"]
    assert isinstance(before, dict)  # a real inline table, not a string

    config.update_config({"Image Library": {"frames_path": str(config.get_frames_path())}})

    after = config.get_camera_config("Nikon D5300")["clip_ceiling_adu"]
    assert isinstance(after, dict)
    assert after == before


def test_resending_an_already_stringified_field_would_corrupt_it(
    config_in_tmp_path: AppConfiguration,
) -> None:
    """Document the exact failure mode a sparse patch avoids.

    `_TomlSectionedConfig.set()` (`config.app_config`) is
    configparser-compatible: it stores `str(value)` unconditionally. If a
    caller ever resends a calibration field using the same stringified
    representation `system:get_config` hands the frontend, `update_config`
    would write that string back verbatim in place of the inline table.
    This is not new behavior to fix here -- it is why Settings must never
    resend a section it did not actually edit.
    """
    config = config_in_tmp_path
    already_stringified = str(config.get_camera_config("Nikon D5300")["clip_ceiling_adu"])

    config.update_config({"Observatory.Camera.Nikon D5300": {"clip_ceiling_adu": already_stringified}})

    corrupted = config.get_camera_config("Nikon D5300")["clip_ceiling_adu"]
    assert isinstance(corrupted, str)
    assert not isinstance(corrupted, dict)


def test_graxpert_is_off_unless_a_command_is_configured(config_in_tmp_path: AppConfiguration) -> None:
    """A blank or missing setting turns the gradient-removal step off."""
    config = config_in_tmp_path

    assert config.get_graxpert_executable() is None


def test_a_configured_graxpert_command_is_returned_as_written(config_in_tmp_path: AppConfiguration) -> None:
    """The command may be a path or a command with arguments."""
    config = config_in_tmp_path
    config.update_config({"Processing.GraXpert": {"graxpert_executable": "/opt/GraXpert-linux/GraXpert"}})

    assert config.get_graxpert_executable() == "/opt/GraXpert-linux/GraXpert"


def test_cosmic_clarity_is_off_unless_a_program_is_configured(config_in_tmp_path: AppConfiguration) -> None:
    """A blank or missing setting turns the denoise step off."""
    config = config_in_tmp_path

    assert config.get_cosmic_clarity_denoise_executable() is None


def test_the_denoise_strength_is_read_and_kept_between_zero_and_one(
    config_in_tmp_path: AppConfiguration,
) -> None:
    """A fresh install uses 0.9; out-of-range values are clipped."""
    config = config_in_tmp_path
    assert config.get_cosmic_clarity_denoise_strength() == pytest.approx(0.9)

    config.update_config({"Processing.CosmicClarity": {"denoise_strength": "0.4"}})
    assert config.get_cosmic_clarity_denoise_strength() == pytest.approx(0.4)

    config.update_config({"Processing.CosmicClarity": {"denoise_strength": "3"}})
    assert config.get_cosmic_clarity_denoise_strength() == pytest.approx(1.0)

    config.update_config({"Processing.CosmicClarity": {"denoise_strength": "not a number"}})
    assert config.get_cosmic_clarity_denoise_strength() == pytest.approx(0.9)


def test_get_stacks_path_defaults_to_the_frames_path(config_in_tmp_path: AppConfiguration) -> None:
    """Without a stacks_path entry, derived files go beside the raw frames."""
    config = config_in_tmp_path

    assert config.get_stacks_path() == config.get_frames_path()


def test_get_stacks_path_reads_the_configured_folder(
    config_in_tmp_path: AppConfiguration, tmp_path: Path
) -> None:
    """An explicit stacks_path sends derived files to another folder."""
    config = config_in_tmp_path
    other = (tmp_path / "other_disk" / "stacks").absolute()
    config.app_config.set("Image Library", "stacks_path", str(other))

    assert config.get_stacks_path() == other
    assert config.get_frames_path() != other


def test_a_blank_stacks_path_counts_as_unset(config_in_tmp_path: AppConfiguration) -> None:
    """An empty entry behaves as if the setting were left out."""
    config = config_in_tmp_path
    config.app_config.set("Image Library", "stacks_path", "")

    assert config.get_stacks_path() == config.get_frames_path()


def test_quarantine_of_bad_frames_is_on_by_default(config_in_tmp_path: AppConfiguration) -> None:
    """Stacking moves clouded and trailed frames aside unless told not to."""
    config = config_in_tmp_path
    assert config.get_quarantine_bad_frames_enabled() is True


def test_quarantine_of_bad_frames_can_be_turned_off(config_in_tmp_path: AppConfiguration) -> None:
    """The ``quarantine_bad_frames_enabled`` setting switches the step off."""
    config = config_in_tmp_path
    config.app_config.set("Processing.Siril", "quarantine_bad_frames_enabled", "false")
    assert config.get_quarantine_bad_frames_enabled() is False


def test_star_toning_of_the_preview_is_on_by_default(config_in_tmp_path: AppConfiguration) -> None:
    """The stack preview tones its stars unless told not to."""
    config = config_in_tmp_path
    assert config.get_preview_star_tone_enabled() is True


def test_star_toning_of_the_preview_can_be_turned_off(config_in_tmp_path: AppConfiguration) -> None:
    """The ``preview_star_tone_enabled`` setting switches the step off."""
    config = config_in_tmp_path
    config.app_config.set("Processing.Siril", "preview_star_tone_enabled", "false")
    assert config.get_preview_star_tone_enabled() is False


def test_keeping_the_previous_stack_is_on_by_default(config_in_tmp_path: AppConfiguration) -> None:
    """A restack keeps the stack it replaces unless told not to."""
    config = config_in_tmp_path
    assert config.get_keep_previous_stack_enabled() is True


def test_keeping_the_previous_stack_can_be_turned_off(config_in_tmp_path: AppConfiguration) -> None:
    """The ``keep_previous_stack_enabled`` setting switches the step off."""
    config = config_in_tmp_path
    config.app_config.set("Processing.Siril", "keep_previous_stack_enabled", "false")
    assert config.get_keep_previous_stack_enabled() is False


def test_trimming_noisy_stack_edges_is_on_by_default(config_in_tmp_path: AppConfiguration) -> None:
    """A finished stack has its noisy edges trimmed unless told not to."""
    config = config_in_tmp_path
    assert config.get_trim_noisy_stack_edges_enabled() is True


def test_trimming_noisy_stack_edges_can_be_turned_off(config_in_tmp_path: AppConfiguration) -> None:
    """The ``trim_noisy_stack_edges_enabled`` setting switches the trim off."""
    config = config_in_tmp_path
    config.app_config.set("Processing.Siril", "trim_noisy_stack_edges_enabled", "false")
    assert config.get_trim_noisy_stack_edges_enabled() is False


def test_unchanged_stacks_are_skipped_by_default(config_in_tmp_path: AppConfiguration) -> None:
    """A stack whose inputs have not changed is skipped unless told not to."""
    config = config_in_tmp_path
    assert config.get_skip_unchanged_stacks_enabled() is True


def test_skipping_unchanged_stacks_can_be_turned_off(config_in_tmp_path: AppConfiguration) -> None:
    """The ``skip_unchanged_stacks_enabled`` setting switches the skip off."""
    config = config_in_tmp_path
    config.app_config.set("Processing.Siril", "skip_unchanged_stacks_enabled", "false")
    assert config.get_skip_unchanged_stacks_enabled() is False
