"""Purpose: Test that the exposure driver follows the mount to another target.

Description: Fakes stand in for the INDI properties and for the backend's
target list, so nothing here needs INDI or a network. The tests check which
target the driver files frames under after the mount moves.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from simulator.exposure_driver import BackendTargetFinder, ExposureDriver, position_name, upload_prefix

#: M 13 and M 92 as the backend's ``target:get_targets`` reply gives them:
#: the position is text, as the app shows it, not degrees.
M13 = {"id": "M 13", "ra": "16h 41m 42s", "dec": "36° 27′ 40′′"}
M92 = {"id": "M 92", "ra": "17h 17m 07s", "dec": "43° 08′ 09′′"}


class _MountDriver(ExposureDriver):
    """A driver whose mount position is a settable pair of text values."""

    def __init__(self, *arguments: Any, **keywords: Any) -> None:
        """Start with the mount at M 13 and no INDI calls recorded.

        Parameters
        ----------
        *arguments : `~typing.Any`
            The driver's arguments.
        **keywords : `~typing.Any`
            The driver's keyword arguments.
        """
        super().__init__(*arguments, **keywords)
        self.mount = {"RA": "16.695", "DEC": "36.46"}
        self.sent: list[str] = []

    def _get(self, name: str) -> str:
        """Answer a mount coordinate from `mount`.

        Parameters
        ----------
        name : `str`
            ``device.property.element``.

        Returns
        -------
        value : `str`
            The coordinate text, or an empty text for any other property.
        """
        return self.mount.get(name.rsplit(".", 1)[1], "")

    def _set(self, *settings: str) -> None:
        """Record the settings."""
        self.sent.extend(settings)


def _driver(tmp_path: Path, find_target: Callable[[float, float], str | None] | None) -> _MountDriver:
    """Build a driver that starts on M 13.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The stand-in home.
    find_target : `~collections.abc.Callable` or `None`
        The target lookup.

    Returns
    -------
    driver : `_MountDriver`
        The driver.
    """
    return _MountDriver(
        1, tmp_path, None, tmp_path / "stats.json", 1.0, "M 13", 16.695, 36.46, None, None, find_target
    )


def test_slew_to_another_library_target_switches_folder_prefix_and_delivery(tmp_path: Path) -> None:
    """A slew to M 92 files frames under M 92 and delivers M 92 frames."""
    finder = BackendTargetFinder(lambda: [M13, M92])
    driver = _driver(tmp_path, finder)
    assert driver.follow_mount() is False
    driver.mount = {"RA": "17.285", "DEC": "43.14"}
    assert driver.follow_mount() is True
    assert driver.target == "M 92"
    assert driver.pictures_directory == tmp_path / "Pictures" / "M 92" / "Light"
    assert driver.pictures_directory.is_dir()
    assert "CCD Simulator.UPLOAD_SETTINGS.UPLOAD_PREFIX=M_92_Light_XXX" in driver.sent

    class _Source:
        """Records the target it is asked for."""

        def deliver(self, target: str, *_arguments: Any) -> bool:
            """Remember the target and decline.

            Parameters
            ----------
            target : `str`
                The target asked for.
            *_arguments : `~typing.Any`
                The rest of the delivery arguments, unused.

            Returns
            -------
            delivered : `bool`
                Always `False`.
            """
            self.target = target
            return False

    source = _Source()
    driver.frame_source = source
    driver.deliver_real_frame(tmp_path / "a.fits", tmp_path / "b.fits")
    assert source.target == "M 92"


def test_a_small_move_does_not_look_the_target_up_again(tmp_path: Path) -> None:
    """A move under the threshold costs no lookup."""
    calls: list[tuple[float, float]] = []

    def find(ra: float, dec: float) -> str | None:
        """Record the call and name M 13.

        Parameters
        ----------
        ra : `float`
            Right ascension in hours.
        dec : `float`
            Declination in degrees.

        Returns
        -------
        target : `str`
            Always M 13.
        """
        calls.append((ra, dec))
        return "M 13"

    driver = _driver(tmp_path, find)
    driver.follow_mount()
    driver.mount["DEC"] = "36.47"
    driver.follow_mount()
    assert len(calls) == 1
    driver.mount["DEC"] = "37.5"
    driver.follow_mount()
    assert len(calls) == 2


def test_no_target_nearby_files_frames_under_the_position(tmp_path: Path) -> None:
    """With no target within a degree the folder is named by position."""
    driver = _driver(tmp_path, lambda ra, dec: None)
    driver.follow_mount()
    assert driver.target == "RA 16h42m Dec +36d28m"
    assert driver.pictures_directory.name == "Light"
    assert driver.pictures_directory.parent.name == driver.target


def test_without_a_lookup_the_target_stays_fixed(tmp_path: Path) -> None:
    """No find_target means today's behavior: nothing is read or changed."""
    driver = _driver(tmp_path, None)
    driver.mount = {"RA": "3.0", "DEC": "10.0"}
    assert driver.follow_mount() is False
    assert driver.target == "M 13"
    assert driver.sent == []


def test_a_failed_lookup_keeps_the_target_and_reports_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An RPC failure is a status: one stderr line, target unchanged."""

    def find(ra: float, dec: float) -> str | None:
        """Fail like an unreachable backend.

        Parameters
        ----------
        ra : `float`
            Right ascension in hours.
        dec : `float`
            Declination in degrees.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("refused")

    driver = _driver(tmp_path, find)
    assert driver.follow_mount() is False
    assert driver.follow_mount() is False
    assert driver.target == "M 13"
    assert capsys.readouterr().err.count("target lookup failed") == 1


def test_an_unreadable_mount_keeps_the_target(tmp_path: Path) -> None:
    """Empty or non-numeric coordinates change nothing."""
    driver = _driver(tmp_path, lambda ra, dec: "M 92")
    driver.mount = {"RA": "", "DEC": "abc"}
    assert driver.follow_mount() is False
    assert driver.target == "M 13"


def test_finder_caches_the_list_and_refreshes_when_nothing_is_found() -> None:
    """The list is fetched once; a miss after the retry delay fetches again."""
    now = [0.0]
    fetches: list[int] = []

    def fetch() -> list[dict]:
        """Count the fetch and give M 13.

        Returns
        -------
        targets : `list` [`dict`]
            One target, M 13.
        """
        fetches.append(1)
        return [M13]

    finder = BackendTargetFinder(fetch, clock=lambda: now[0])
    assert finder(16.695, 36.46) == "M 13"
    assert finder(16.7, 36.4) == "M 13"
    assert len(fetches) == 1
    assert finder(3.0, 10.0) is None
    assert len(fetches) == 1
    now[0] = 40.0
    assert finder(3.0, 10.0) is None
    assert len(fetches) == 2
    with pytest.raises(RuntimeError):
        BackendTargetFinder(lambda: {"bad": 1})(1.0, 1.0)


def test_names_are_folder_safe() -> None:
    """Position names and prefixes hold no slash or odd characters."""
    assert position_name(16.695, -0.5) == "RA 16h42m Dec -00d30m"
    assert position_name(23.9999, 10.0).startswith("RA 00h00m")
    assert upload_prefix("RA 16h41m Dec +36d28m") == "RA_16h41m_Dec_36d28m_Light_XXX"
