"""Tests for how a frame's camera, ISO and optic are recorded when scanning.

These replace the old rules that assumed this observatory's own equipment: the
ISO forced to 800 for Nikon cameras, the "unknown camera means ZWO" default,
and the telescope chosen by looking for "Nikkor 300mm" in the file path. They
now come from the config file and the camera profiles.
"""

import logging
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.frame_scanning import (
    PLACEHOLDER_RECORD_ISO,
    classify_and_sort_fits_files,
    create_frame_record_from_fits,
    is_stacked_output,
    scan_target_directory,
)
from astrometricslib.utilities.config_loader import AppConfiguration, _TomlSectionedConfig
from astrometricslib.utilities.warn_once import warn_once

EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parents[3] / "astrometrics.config.example.toml"


@pytest.fixture(autouse=True)
def fresh_warning_cache() -> None:
    """Forget which warnings were already logged, for each test."""
    warn_once.cache_clear()


def make_config(frames_root: Path | None = None) -> AppConfiguration:
    """Build a configuration from the shipped example, writing no file.

    Parameters
    ----------
    frames_root : `pathlib.Path`, optional
        The library folder to use, for tests that move files.

    Returns
    -------
    config : `AppConfiguration`
        The configuration.
    """
    parser = _TomlSectionedConfig()
    parser.read_string(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    if frames_root is not None:
        parser.set("Image Library", "path", str(frames_root))
    config = AppConfiguration()
    config.app_config = parser
    return config


def write_frame(path: Path, **header_cards: object) -> str:
    """Write a small FITS file with the given header cards.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the file.
    **header_cards : `object`
        The header keywords and values.

    Returns
    -------
    path : `str`
        The path just written, as text.
    """
    hdu = fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32))
    for key, value in header_cards.items():
        hdu.header[key] = value
    hdu.writeto(path, overwrite=True)
    return str(path)


def test_an_asi533_frame_is_recorded_under_the_librarys_spelling_with_its_gain(tmp_path: Path) -> None:
    """Check the camera spelling, gain and optic of an ASI533 frame."""
    path = write_frame(tmp_path / "a.fits", INSTRUME="ZWO CCD ASI533MM Pro", GAIN=0.0, FOCALLEN=405.0)
    record = create_frame_record_from_fits(path, config=make_config())
    assert record.camera == "ZWO ASI 533MM Pro"
    assert record.iso == "0.0"
    assert record.telescope == "Apertura 75Q"


def test_a_d5300_frame_keeps_the_iso_in_its_header(tmp_path: Path) -> None:
    """Check the change from the old forced 800.

    Real D5300 headers carry ISOSPEED 100, 200, 400, 800 and 1600, but the old
    code recorded 800 for all of them.
    """
    path = write_frame(tmp_path / "a.fits", INSTRUME="Nikon DSLR DSC D5300", ISOSPEED=100, FOCALLEN=300.0)
    record = create_frame_record_from_fits(path, config=make_config())
    assert record.camera == "Nikon DSLR DSC D5300"
    assert record.iso == "100"


def test_a_d5300_frame_with_no_iso_in_its_header_gets_the_configured_default(tmp_path: Path) -> None:
    """Check `default_iso`, found although the section name differs."""
    path = write_frame(tmp_path / "a.fits", INSTRUME="Nikon DSLR DSC D5300", FOCALLEN=300.0)
    record = create_frame_record_from_fits(path, config=make_config())
    assert record.iso == "800"


def test_a_camera_with_no_iso_and_no_default_gets_the_placeholder_and_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Check that the old stand-in is kept, but is no longer silent."""
    path = write_frame(tmp_path / "a.fits", INSTRUME="Acme Imager 9000")
    with caplog.at_level(logging.WARNING):
        record = create_frame_record_from_fits(path, config=make_config())
    assert record.iso == PLACEHOLDER_RECORD_ISO
    assert any("default_iso" in entry.getMessage() for entry in caplog.records)


def test_the_focal_length_in_the_header_picks_the_optic_not_the_folder_name(tmp_path: Path) -> None:
    """Check the recommended rule, on the case the real library contains."""
    folder = tmp_path / "Apertura 75Q"
    folder.mkdir()
    path = write_frame(folder / "a.fits", INSTRUME="Nikon DSLR DSC D5300", ISOSPEED=800, FOCALLEN=300.0)
    record = create_frame_record_from_fits(path, config=make_config())
    assert record.telescope == "Nikkor 300mm"


def test_a_d5300_frame_without_a_focal_length_uses_the_folder_name(tmp_path: Path) -> None:
    """Check the older rule as a fallback; it never invents an optic."""
    folder = tmp_path / "Nikkor 300mm"
    folder.mkdir()
    named = write_frame(folder / "a.fits", INSTRUME="Nikon DSLR DSC D5300", ISOSPEED=800)
    unnamed = write_frame(tmp_path / "b.fits", INSTRUME="Nikon DSLR DSC D5300", ISOSPEED=800)
    config = make_config()
    assert create_frame_record_from_fits(named, config=config).telescope == "Nikkor 300mm"
    assert create_frame_record_from_fits(unnamed, config=config).telescope == "Unknown"


def test_a_config_without_setups_records_the_telescope_as_unknown(tmp_path: Path) -> None:
    """Check what happens until an older config gets the new sections."""
    parser = _TomlSectionedConfig()
    parser.read_string('["Image Library"]\npath = "x"\n')
    config = AppConfiguration()
    config.app_config = parser
    path = write_frame(tmp_path / "a.fits", INSTRUME="ZWO CCD ASI533MM Pro", GAIN=0.0, FOCALLEN=405.0)
    assert create_frame_record_from_fits(path, config=config).telescope == "Unknown"


def test_a_camera_forced_by_the_caller_is_used_as_given(tmp_path: Path) -> None:
    """Check that the `camera` argument is not renamed."""
    path = write_frame(tmp_path / "a.fits", INSTRUME="ZWO CCD ASI533MM Pro", GAIN=0.0, FOCALLEN=405.0)
    record = create_frame_record_from_fits(path, camera="ZWO ASI533MM Pro", config=make_config())
    assert record.camera == "ZWO ASI533MM Pro"
    assert record.telescope == "Apertura 75Q"


def test_sorting_puts_a_light_under_the_optic_and_camera_folder_names(tmp_path: Path) -> None:
    """Check that the library layout is the same as before."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    write_frame(incoming / "light.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Light", GAIN=0.0)
    config = make_config(frames_root=tmp_path / "library")

    moved = classify_and_sort_fits_files([str(incoming)], "M31", config, "Apertura 75Q")

    assert moved == 1
    expected = tmp_path / "library" / "frames" / "lights" / "M31" / "Apertura 75Q" / "ZWO ASI 533MM Pro"
    assert (expected / "light.fits").exists()


def test_sorting_a_dark_uses_its_gain_in_the_folder_name(tmp_path: Path) -> None:
    """Check that the header's own gain names the dark's folder."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    write_frame(
        incoming / "dark.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Dark", GAIN=100.0, EXPTIME=30.0
    )
    config = make_config(frames_root=tmp_path / "library")

    classify_and_sort_fits_files([str(incoming)], "M31", config, "Apertura 75Q")

    expected = tmp_path / "library" / "frames" / "darks" / "ZWO ASI 533MM Pro" / "100.0" / "30.0"
    assert (expected / "dark.fits").exists()


def test_a_frame_with_no_camera_in_its_header_goes_under_the_primary_camera(tmp_path: Path) -> None:
    """Check the replacement for the old hard-coded ZWO default."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    write_frame(incoming / "light.fits", FRAME="Light", GAIN=0.0)
    # The example config names the ASI533 as the default primary camera.
    config = make_config(frames_root=tmp_path / "library")
    assert config.get_primary_camera_name() == "ZWO ASI533MM Pro"

    classify_and_sort_fits_files([str(incoming)], "M31", config, "Apertura 75Q")

    expected = tmp_path / "library" / "frames" / "lights" / "M31" / "Apertura 75Q" / "ZWO ASI 533MM Pro"
    assert (expected / "light.fits").exists()


def test_an_iso_written_with_a_decimal_is_recorded_without_it(tmp_path: Path) -> None:
    """Check the change to the stored ISO text: 800.0 becomes 800."""
    path = write_frame(tmp_path / "a.fits", INSTRUME="Nikon DSLR DSC D5300", ISOSPEED=800.0, FOCALLEN=300.0)
    assert create_frame_record_from_fits(path, config=make_config()).iso == "800"


def test_a_gain_is_recorded_exactly_as_the_header_writes_it(tmp_path: Path) -> None:
    """Check that session ids built from the gain text do not change."""
    path = write_frame(tmp_path / "a.fits", INSTRUME="ZWO CCD ASI533MM Pro", GAIN=0.0, FOCALLEN=405.0)
    assert create_frame_record_from_fits(path, config=make_config()).iso == "0.0"


def test_a_stacked_output_is_recognised_by_name_or_stack_count(tmp_path: Path) -> None:
    """Check both signals, and that a raw light is not mistaken for one."""
    raw = write_frame(tmp_path / "M31_Light_001.fits", FRAME="Light")
    by_count = write_frame(tmp_path / "result.fits", FRAME="Light", STACKCNT=44)

    assert is_stacked_output("NGC_2403_Processed.fits")
    assert is_stacked_output("M_13_stacked.fit")
    assert is_stacked_output("M31_starless.fits")
    assert is_stacked_output("result.fits", fits.getheader(by_count))
    assert not is_stacked_output("result.fits")
    assert not is_stacked_output("M31_Light_001.fits", fits.getheader(raw))


def test_an_unreadable_stack_count_is_not_a_stack() -> None:
    """Check a bad value is treated as a raw frame."""
    assert not is_stacked_output("a.fits", {"STACKCNT": "many"})
    assert not is_stacked_output("a.fits", {"STACKCNT": 0})


def test_scanning_a_target_folder_skips_a_stack_among_the_lights(tmp_path: Path) -> None:
    """Check the real-library case: a hand-made stack beside the raw frames.

    One stack here has an innocent file name and is found by STACKCNT alone.
    """
    folder = tmp_path / "lights" / "M31"
    folder.mkdir(parents=True)
    write_frame(folder / "M31_Light_001.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Light", EXPTIME=30.0)
    write_frame(folder / "M31_Light_002.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Light", EXPTIME=30.0)
    write_frame(
        folder / "M31_final.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Light", EXPTIME=60.0, STACKCNT=2
    )
    write_frame(folder / "M31_Processed.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Light", EXPTIME=60.0)
    target = Target(id="M31")

    scan_target_directory(target, str(tmp_path))

    assert sorted(Path(frame.path).name for frame in target.frames) == [
        "M31_Light_001.fits",
        "M31_Light_002.fits",
    ]
    assert target.exposure_sec == pytest.approx(60.0)


def test_sorting_a_stack_files_it_under_others_not_the_lights(tmp_path: Path) -> None:
    """Check a stack that says it is a light is not filed with the lights."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    write_frame(incoming / "final.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Light", STACKCNT=70)
    config = make_config(frames_root=tmp_path / "library")

    moved = classify_and_sort_fits_files([str(incoming)], "M31", config, "Apertura 75Q")

    assert moved == 1
    assert (tmp_path / "library" / "frames" / "others" / "final.fits").exists()
    assert not (tmp_path / "library" / "frames" / "lights").exists()


def test_sorting_reports_the_new_path_of_each_added_file(tmp_path: Path) -> None:
    """Check that `added_paths` lists a moved file's new location."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    write_frame(
        incoming / "dark.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Dark", GAIN=100.0, EXPTIME=30.0
    )
    config = make_config(frames_root=tmp_path / "library")
    added_paths: list[str] = []

    classify_and_sort_fits_files([str(incoming)], "M31", config, "Apertura 75Q", added_paths)

    folder = tmp_path / "library" / "frames" / "darks" / "ZWO ASI 533MM Pro" / "100.0" / "30.0"
    assert added_paths == [str(folder / "dark.fits")]


def test_sorting_does_not_report_a_file_that_replaced_one_of_the_same_name(tmp_path: Path) -> None:
    """Check that `added_paths` counts only new files."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    write_frame(
        incoming / "dark.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Dark", GAIN=100.0, EXPTIME=30.0
    )
    config = make_config(frames_root=tmp_path / "library")
    folder = tmp_path / "library" / "frames" / "darks" / "ZWO ASI 533MM Pro" / "100.0" / "30.0"
    folder.mkdir(parents=True)
    (folder / "dark.fits").write_bytes(b"old")
    added_paths: list[str] = []

    moved = classify_and_sort_fits_files([str(incoming)], "M31", config, "Apertura 75Q", added_paths)

    assert moved == 1
    assert added_paths == []


def test_sorting_refuses_and_moves_nothing_when_the_frames_drive_is_missing(tmp_path: Path) -> None:
    """Check that files stay in place if the frames mount point is empty."""
    from astrometricslib.utilities.storage_mount import StorageNotMountedError

    incoming = tmp_path / "incoming"
    incoming.mkdir()
    write_frame(incoming / "light.fits", INSTRUME="ZWO CCD ASI533MM Pro", FRAME="Light", GAIN=0.0)
    config = make_config(frames_root=tmp_path / "library")
    # tmp_path is a plain folder, not a mount, so this drive counts as missing.
    config.app_config.set("Image Library", "frames_mount_point", str(tmp_path / "library"))

    with pytest.raises(StorageNotMountedError):
        classify_and_sort_fits_files([str(incoming)], "M31", config, "Apertura 75Q")

    assert (incoming / "light.fits").exists()
    assert not (tmp_path / "library" / "frames" / "lights").exists()
