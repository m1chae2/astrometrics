"""Purpose: Test who takes the exposures, and the start of a simulator session.

Description: Fakes stand in for the INDI properties, so nothing here needs
INDI or a network. The tests check that in ``app`` capture mode the driver
sets no exposure and files a frame that appears, that the ``driver`` mode
still takes its own exposures but not during a slew, that the launcher asks
for ``app`` and the harness for ``driver``, and that an interactive start
empties the stand-in folders only inside the world.
"""

import threading
import time
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest
from astropy.io import fits

from simulator import launch
from simulator import observatory as observatory_module
from simulator.exposure_driver import CAPTURE_APP, ExposureDriver
from simulator.observatory import SimulatedObservatory, SimulationKind, SimulationUnavailableError


def _write_frame(path: Path, exposure_seconds: float = 12.0) -> None:
    """Write a small camera frame with an exposure time.

    Parameters
    ----------
    path : `pathlib.Path`
        The file; its folder is made.
    exposure_seconds : `float`, optional
        The ``EXPTIME`` header value.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16))
    hdu.header["EXPTIME"] = exposure_seconds
    hdu.writeto(path)


class _FakeDriver(ExposureDriver):
    """A driver whose INDI calls are recorded and whose mount state is set."""

    def __init__(self, *arguments: Any, **keywords: Any) -> None:
        """Start with a still mount and no INDI calls recorded.

        Parameters
        ----------
        *arguments : `~typing.Any`
            The driver's arguments.
        **keywords : `~typing.Any`
            The driver's keyword arguments.
        """
        super().__init__(*arguments, **keywords)
        self.sent: list[str] = []
        self.mount_states = ["Ok"]
        self.state_reads = 0
        self.stop_after_frames: int | None = None

    def _get(self, name: str) -> str:
        """Answer the mount state from `mount_states`; nothing else.

        Parameters
        ----------
        name : `str`
            ``device.property.element``.

        Returns
        -------
        value : `str`
            The next state for a state read, else an empty text.
        """
        if name.endswith("._STATE"):
            index = min(self.state_reads, len(self.mount_states) - 1)
            self.state_reads += 1
            return self.mount_states[index]
        return ""

    def _set(self, *settings: str) -> None:
        """Record the settings; an exposure drops a frame into staging.

        Parameters
        ----------
        *settings : `str`
            The settings.
        """
        self.sent.extend(settings)
        if any("CCD_EXPOSURE" in setting for setting in settings):
            count = len(self.frames) + len(list(self.staging_directory.glob("*.fits"))) + 1
            _write_frame(self.staging_directory / f"M_13_Light_{count:03d}.fits", self.exposure_seconds)

    def write_stats(self) -> None:
        """Stop the loop once enough frames are filed (no file is written)."""
        if self.stop_after_frames is not None and len(self.frames) >= self.stop_after_frames:
            self.stop_event.set()


def _driver(tmp_path: Path, **keywords: Any) -> _FakeDriver:
    """Build a fake driver on M 13.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The stand-in home.
    **keywords : `~typing.Any`
        More driver keyword arguments.

    Returns
    -------
    driver : `_FakeDriver`
        The driver, with exposures of 0.1 s.
    """
    driver = _FakeDriver(1, tmp_path, None, tmp_path / "stats.json", 0.1, "M 13", 16.695, 36.46, **keywords)
    driver.staging_directory.mkdir(parents=True)
    return driver


def test_app_mode_sets_no_exposure_and_files_the_frame_that_appears(tmp_path: Path) -> None:
    """The app's frame is filed, stamped, and recorded; no exposure is sent."""
    driver = _driver(tmp_path, capture=CAPTURE_APP)
    driver.stop_after_frames = 1
    _write_frame(driver.staging_directory / "M_13_Light_001.fits", 12.0)
    worker = threading.Thread(target=driver.watch_for_frames)
    worker.start()
    worker.join(timeout=20)
    assert not worker.is_alive()
    assert not any("CCD_EXPOSURE" in setting for setting in driver.sent)
    filed = driver.pictures_directory / "M_13_Light_001.fits"
    assert filed.is_file()
    assert fits.getheader(filed)["SYNTHETC"] is True
    assert not list(driver.staging_directory.glob("*.fits"))
    frame = driver.frames[0]
    assert frame["name"] == "M_13_Light_001.fits"
    assert frame["appeared_at"] - frame["exposure_started_at"] == pytest.approx(12.0, abs=2.0)


def test_app_mode_follows_the_mount_when_idle_and_when_a_frame_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`follow_mount` runs while idle and right before a frame is filed."""
    driver = _driver(tmp_path, capture=CAPTURE_APP)
    calls: list[str] = []
    monkeypatch.setattr(driver, "follow_mount", lambda: calls.append("follow") or False)
    monkeypatch.setattr("simulator.exposure_driver.IDLE_FOLLOW_SECONDS", 0.0)
    monkeypatch.setattr("simulator.exposure_driver.WATCH_POLL_SECONDS", 0.01)
    worker = threading.Thread(target=driver.watch_for_frames)
    worker.start()
    time.sleep(0.2)
    assert calls
    idle_calls = len(calls)
    driver.stop_after_frames = 1
    _write_frame(driver.staging_directory / "M_13_Light_001.fits")
    worker.join(timeout=20)
    assert len(calls) > idle_calls
    assert len(driver.frames) == 1


def test_driver_mode_still_takes_its_own_exposures(tmp_path: Path) -> None:
    """In the default mode the driver sets the exposure and files the frame."""
    driver = _driver(tmp_path)
    driver.stop_after_frames = 2
    worker = threading.Thread(target=driver.take_exposures)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert sum("CCD_EXPOSURE" in setting for setting in driver.sent) >= 2
    assert len(list(driver.pictures_directory.glob("*.fits"))) == 2


def test_no_exposure_starts_while_the_mount_is_slewing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exposure waits until the mount state is no longer Busy."""
    driver = _driver(tmp_path)
    driver.mount_states = ["Busy", "Busy", "Ok"]
    driver.stop_after_frames = 1
    monkeypatch.setattr(driver.stop_event, "wait", lambda timeout=None: driver.stop_event.is_set())
    driver.take_exposures()
    assert driver.state_reads >= 3
    assert driver.sent[0].endswith("CCD_EXPOSURE_VALUE=0.1")


def test_without_a_readable_state_two_positions_decide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mount whose position changes in a second is slewing."""
    driver = _driver(tmp_path)
    positions = iter([(1.0, 2.0), (1.5, 2.0), (3.0, 4.0), (3.0, 4.0)])
    monkeypatch.setattr(driver, "mount_position", lambda: next(positions))
    monkeypatch.setattr(driver, "_get", lambda name: "")
    monkeypatch.setattr(driver.stop_event, "wait", lambda timeout=None: False)
    assert driver.mount_is_slewing() is True
    assert driver.mount_is_slewing() is False


def _observatory(root: Path, monkeypatch: pytest.MonkeyPatch, **keywords: Any) -> SimulatedObservatory:
    """Build a simulated-sky observatory in a folder with no processes.

    Parameters
    ----------
    root : `pathlib.Path`
        The world's root.
    monkeypatch : `pytest.MonkeyPatch`
        Used to skip the program check.
    **keywords : `~typing.Any`
        More observatory keyword arguments.

    Returns
    -------
    observatory : `SimulatedObservatory`
        The observatory.
    """
    monkeypatch.setattr(observatory_module, "missing_programs", lambda include_overlay=True: [])
    return SimulatedObservatory(root, SimulationKind.SIMULATED_SKY, 11000, 11001, **keywords)


def _driver_command(observatory: SimulatedObservatory, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Start the processes with `_launch` faked and return the driver command.

    Parameters
    ----------
    observatory : `SimulatedObservatory`
        The observatory.
    monkeypatch : `pytest.MonkeyPatch`
        Used to fake the launch.

    Returns
    -------
    command : `list` [`str`]
        The exposure driver's command line.
    """
    launched: dict[str, list[str]] = {}
    monkeypatch.setattr(
        observatory,
        "_launch",
        lambda name, command, log_name=None, extra_environment=None: launched.update({name: command}),
    )
    monkeypatch.setattr(observatory, "_wait_listening", lambda ports: None)
    observatory._start_processes()
    return launched["driver"]


def test_the_harness_asks_for_driver_mode_and_the_launcher_for_app_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The harness gets ``driver``; the app-capture world gets ``app``."""
    harness = _observatory(tmp_path / "harness", monkeypatch)
    command = _driver_command(harness, monkeypatch)
    assert command[command.index("--capture") + 1] == "driver"
    interactive = _observatory(tmp_path / "interactive", monkeypatch, capture_by_app=True)
    command = _driver_command(interactive, monkeypatch)
    assert command[command.index("--capture") + 1] == "app"


def test_the_launcher_builds_its_runner_with_app_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_world` makes the running observatory with `capture_by_app`."""
    made: list[dict[str, Any]] = []

    class Recorder:
        """An observatory that records how it was made and does nothing."""

        config_path = tmp_path / "astrometrics.config.toml"
        phd2 = None
        notes: ClassVar[list[str]] = []
        pointed_targets: ClassVar[list] = []

        def __init__(self, *args: object, **kwargs: object) -> None:
            """Keep the keyword arguments."""
            made.append(dict(kwargs))

        def prepare(self) -> None:
            """Do nothing."""

        def start(self) -> None:
            """Do nothing."""

        def wait_ready(self) -> None:
            """Do nothing."""

        def stop(self) -> None:
            """Do nothing."""

        def environment(self) -> dict[str, str]:
            """Give no variables.

            Returns
            -------
            variables : `dict` [`str`, `str`]
                Empty.
            """
            return {}

    monkeypatch.setattr(launch, "SimulatedObservatory", Recorder)
    launch.build_world(tmp_path, {}, fitter=lambda *arguments: None)
    assert [call.get("capture_by_app", False) for call in made] == [False, True]


def test_a_session_start_empties_the_stand_in_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old pictures and staged frames go; the folders and the world stay."""
    observatory = _observatory(tmp_path / "world", monkeypatch, capture_by_app=True)
    home = observatory.standin_home
    _write_frame(home / "Pictures" / "M 13" / "Light" / "old.fits")
    _write_frame(home / "camera_staging" / "M 13" / "Light" / "old.fits")
    (home / "Pictures" / "loose.txt").write_text("x")
    keep = observatory.root / "library" / "keep.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("x")
    observatory.start_fresh_night()
    assert list((home / "Pictures").iterdir()) == []
    assert list((home / "camera_staging").iterdir()) == []
    assert keep.is_file()


def test_the_harness_start_keeps_the_stand_in_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `capture_by_app` the start leaves earlier frames alone."""
    observatory = _observatory(tmp_path / "world", monkeypatch)
    old = observatory.standin_home / "Pictures" / "M 13" / "Light" / "old.fits"
    _write_frame(old)
    _driver_command(observatory, monkeypatch)
    assert old.is_file()


def test_emptying_refuses_a_folder_outside_the_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Pictures folder that is a link out of the world is not emptied."""
    observatory = _observatory(tmp_path / "world", monkeypatch, capture_by_app=True)
    outside = tmp_path / "outside"
    _write_frame(outside / "precious.fits")
    pictures = observatory.standin_home / "Pictures"
    pictures.parent.mkdir(parents=True)
    pictures.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SimulationUnavailableError):
        observatory.start_fresh_night()
    assert (outside / "precious.fits").is_file()


def test_emptying_removes_a_link_without_following_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link inside the folder is removed; what it points at is kept."""
    observatory = _observatory(tmp_path / "world", monkeypatch, capture_by_app=True)
    outside = tmp_path / "outside"
    _write_frame(outside / "precious.fits")
    pictures = observatory.standin_home / "Pictures"
    pictures.mkdir(parents=True)
    (pictures / "link").symlink_to(outside, target_is_directory=True)
    observatory.start_fresh_night()
    assert list(pictures.iterdir()) == []
    assert (outside / "precious.fits").is_file()


def test_an_unknown_capture_mode_is_refused(tmp_path: Path) -> None:
    """Only ``driver`` and ``app`` are modes."""
    with pytest.raises(ValueError, match="capture"):
        ExposureDriver(1, tmp_path, None, tmp_path / "s.json", 1.0, "M 13", 0.0, 0.0, capture="both")
