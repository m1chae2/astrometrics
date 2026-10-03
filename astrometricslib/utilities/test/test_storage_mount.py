"""Tests for the frames-drive mount check.

Checks that writes below ``frames_mount_point`` are refused while nothing is
mounted there, and that the check stays out of the way otherwise.
"""

import os
from pathlib import Path

import pytest

from astrometricslib.utilities.config_loader import AppConfiguration
from astrometricslib.utilities.storage_mount import StorageNotMountedError, require_mounted_storage


class _Settings:
    """Stand-in for the configuration, holding only the mount point."""

    def __init__(self, mount_point: str | None) -> None:
        """Remember the mount point to report."""
        self._mount_point = Path(mount_point) if mount_point else None

    def get_frames_mount_point(self) -> Path | None:
        """Return the mount point.

        Returns
        -------
        mount_point : `pathlib.Path` or `None`
            The mount point, or `None` when the check is off.
        """
        return self._mount_point


def _pretend_mounted(monkeypatch: pytest.MonkeyPatch, mounted: set[str]) -> None:
    """Make `os.path.ismount` say yes only for the paths in `mounted`."""
    monkeypatch.setattr(os.path, "ismount", lambda path: str(path) in mounted)


def test_no_check_when_no_mount_point_is_set() -> None:
    """Without the setting, any destination passes."""
    require_mounted_storage("/anywhere/at/all", _Settings(None))


def test_a_mounted_drive_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A destination below a mounted mount point is allowed."""
    _pretend_mounted(monkeypatch, {"/mnt/nas"})
    require_mounted_storage("/mnt/nas/frames/lights/M 31", _Settings("/mnt/nas"))


def test_an_unmounted_drive_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A destination below an unmounted mount point raises."""
    _pretend_mounted(monkeypatch, set())
    with pytest.raises(StorageNotMountedError, match="/mnt/nas"):
        require_mounted_storage("/mnt/nas/frames/lights/M 31", _Settings("/mnt/nas"))


def test_the_mount_point_itself_counts_as_below_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writing straight into the mount point is checked too."""
    _pretend_mounted(monkeypatch, set())
    with pytest.raises(StorageNotMountedError):
        require_mounted_storage("/mnt/nas", _Settings("/mnt/nas"))


def test_a_folder_that_only_shares_a_name_prefix_is_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/mnt/nas2`` is a different place from ``/mnt/nas``."""
    _pretend_mounted(monkeypatch, set())
    require_mounted_storage("/mnt/nas2/frames", _Settings("/mnt/nas"))


def test_a_path_outside_the_mount_point_is_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Destinations on the computer's own disk are never refused."""
    _pretend_mounted(monkeypatch, set())
    require_mounted_storage("/home/someone/library/frames", _Settings("/mnt/nas"))


def test_the_other_media_spelling_of_a_mounted_drive_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A drive mounted under /run/media satisfies a /media mount point."""
    _pretend_mounted(monkeypatch, {"/run/media/michael/OS"})
    require_mounted_storage("/media/michael/OS/Library/lights", _Settings("/media/michael/OS"))


def test_the_other_media_spelling_of_the_destination_is_still_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /run/media path below an unmounted /media mount point is refused."""
    _pretend_mounted(monkeypatch, set())
    with pytest.raises(StorageNotMountedError):
        require_mounted_storage("/run/media/michael/OS/Library", _Settings("/media/michael/OS"))


def _config_with_mount_point(tmp_path: Path, mount_point: str | None) -> AppConfiguration:
    """Build a configuration with the given ``frames_mount_point``.

    Returns
    -------
    config : `AppConfiguration`
        A configuration with that setting, or none if `mount_point` is `None`.
    """
    config = AppConfiguration()
    config.app_config.set("Image Library", "path", str(tmp_path / "library"))
    if mount_point is not None:
        config.app_config.set("Image Library", "frames_mount_point", mount_point)
    return config


def test_the_setting_is_read_from_the_image_library_section(tmp_path: Path) -> None:
    """The configured folder comes back as an absolute path."""
    config = _config_with_mount_point(tmp_path, "/mnt/nas")
    assert config.get_frames_mount_point() == Path("/mnt/nas")


def test_the_setting_is_off_when_missing_or_blank(tmp_path: Path) -> None:
    """A missing or empty entry turns the check off."""
    assert _config_with_mount_point(tmp_path, None).get_frames_mount_point() is None
    assert _config_with_mount_point(tmp_path, "").get_frames_mount_point() is None
