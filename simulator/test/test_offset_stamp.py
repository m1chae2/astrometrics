"""Purpose: Test that simulated frames carry the camera offset.

Description: The CCD simulator writes a ``GAIN`` header but no ``OFFSET``.
The exposure driver adds the offset it set on the camera while it moves each
frame from the camera's staging folder to the picture folder. The tests check
that only the header changes, that the move is one rename, and that an
existing ``OFFSET`` is left alone.
"""

import json
import time
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from simulator.exposure_driver import ExposureDriver, stamp_offset, unused_frame_name


def _write_frame(path: Path, extra: dict | None = None) -> np.ndarray:
    """Write a 16-bit frame the way the camera simulator does.

    Parameters
    ----------
    path : `pathlib.Path`
        The file.
    extra : `dict`, optional
        More header cards.

    Returns
    -------
    data : `numpy.ndarray`
        The unsigned 16-bit pixel values written.
    """
    data = np.arange(60, dtype=np.uint16).reshape(6, 10) * 1000
    hdu = fits.PrimaryHDU(data)
    hdu.header["GAIN"] = 0.0
    for key, value in (extra or {}).items():
        hdu.header[key] = value
    hdu.writeto(path)
    return data


def test_stamping_adds_the_offset_and_keeps_every_pixel(tmp_path: Path) -> None:
    """The header gains ``OFFSET``; the pixels and the other cards stay."""
    source, destination = tmp_path / "staging.fits", tmp_path / "frame.fits"
    data = _write_frame(source)
    stamp_offset(source, destination, 10)
    assert not source.exists()
    with fits.open(destination, memmap=False) as hdus:
        assert hdus[0].header["OFFSET"] == 10
        assert hdus[0].header["GAIN"] == pytest.approx(0.0)
        assert np.array_equal(hdus[0].data, data)
        assert hdus[0].data.dtype == data.dtype
    with fits.open(destination, memmap=False, do_not_scale_image_data=True) as raw:
        assert raw[0].header["BZERO"] == 32768


def test_the_stamped_file_appears_in_one_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The final name is made by a rename, so no half-written file is seen."""
    source, destination = tmp_path / "staging.fits", tmp_path / "out" / "frame.fits"
    destination.parent.mkdir()
    _write_frame(source)
    seen: list[tuple[bool, str]] = []
    original = Path.replace

    def spy(self: Path, target: Path) -> Path:
        """Record each rename and whether the destination existed before it.

        Parameters
        ----------
        self : `pathlib.Path`
            The file being renamed.
        target : `pathlib.Path`
            Where it goes.

        Returns
        -------
        target : `pathlib.Path`
            The renamed path.
        """
        seen.append((Path(target).exists(), Path(target).name))
        return original(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    stamp_offset(source, destination, 10)
    assert seen[-1] == (False, "frame.fits")
    assert [name for _, name in seen].count("frame.fits") == 1
    assert not list(tmp_path.glob(".*stamping"))


def test_an_offset_already_in_the_header_is_left_alone(tmp_path: Path) -> None:
    """The existing card wins and the file is only moved."""
    source, destination = tmp_path / "a.fits", tmp_path / "b.fits"
    _write_frame(source, {"OFFSET": 50})
    before = source.read_bytes()
    stamp_offset(source, destination, 10)
    assert destination.read_bytes() == before
    assert fits.getheader(destination)["OFFSET"] == 50


def test_no_offset_moves_the_frame_unchanged(tmp_path: Path) -> None:
    """Without an offset the file is only moved."""
    source, destination = tmp_path / "a.fits", tmp_path / "b.fits"
    _write_frame(source)
    before = source.read_bytes()
    stamp_offset(source, destination, None)
    assert destination.read_bytes() == before


def test_the_driver_stamps_the_offset_it_set(tmp_path: Path) -> None:
    """The offset to write is the ``CCD_OFFSET`` the operator sets."""
    from simulator.device_settings import DeviceSetting

    settings = [DeviceSetting("CCD Simulator", "CCD_OFFSET", (("OFFSET", 10.0),))]
    driver = ExposureDriver(1, tmp_path, Path("log"), Path("stats"), 1.0, "M 13", 0.0, 0.0, settings)
    assert driver.camera_offset() == pytest.approx(10.0)
    assert driver.staging_directory != driver.pictures_directory
    assert ExposureDriver(1, tmp_path, Path("l"), Path("s"), 1.0, "M 13", 0.0, 0.0).camera_offset() is None


class _CameraDriver(ExposureDriver):
    """A driver whose camera writes a tiny frame into the staging folder."""

    def _set(self, *settings: str) -> None:
        """Write a frame named 001 when an exposure is asked for.

        Parameters
        ----------
        *settings : `str`
            The INDI settings; only the exposure one is acted on.
        """
        if any("CCD_EXPOSURE" in text for text in settings):
            self.staging_directory.mkdir(parents=True, exist_ok=True)
            name = self.staging_directory / "M_13_Light_001.fits"
            if not name.exists():
                _write_frame(name)


def _driver(tmp_path: Path) -> _CameraDriver:
    """Build a driver with a camera that always numbers its frames 001.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The stand-in home.

    Returns
    -------
    driver : `_CameraDriver`
        The driver, offset 10, exposures of 0.1 s.
    """
    from simulator.device_settings import DeviceSetting

    settings = [DeviceSetting("CCD Simulator", "CCD_OFFSET", (("OFFSET", 10.0),))]
    return _CameraDriver(1, tmp_path, Path("log"), tmp_path / "stats.json", 0.1, "M 13", 0.0, 0.0, settings)


def test_each_frame_gets_its_own_name_and_is_recorded_when_it_appears(tmp_path: Path) -> None:
    """Frames that the camera numbers 001 again are filed 001, 002, 003."""
    import threading

    driver = _driver(tmp_path)
    worker = threading.Thread(target=driver.take_exposures)
    worker.start()
    try:
        deadline = time.time() + 20
        while time.time() < deadline and len(driver.frames) < 3:
            time.sleep(0.1)
    finally:
        driver.stop_event.set()
        worker.join(timeout=10)
    names = [frame["name"] for frame in driver.frames]
    assert names[:3] == ["M_13_Light_001.fits", "M_13_Light_002.fits", "M_13_Light_003.fits"]
    for frame in driver.frames:
        final = driver.pictures_directory / frame["name"]
        assert final.is_file()
        assert frame["appeared_at"] == pytest.approx(final.stat().st_mtime)
        assert frame["appeared_at"] >= frame["exposure_started_at"]
        assert fits.getheader(final)["OFFSET"] == 10
    assert not list(driver.staging_directory.glob("*.fits"))
    stats = json.loads((tmp_path / "stats.json").read_text())
    assert [frame["name"] for frame in stats["frames"]][:3] == names[:3]


def test_the_picture_folder_of_the_target_exists_before_the_first_frame(tmp_path: Path) -> None:
    """Connecting makes the target's picture folder the app lists early."""
    driver = _driver(tmp_path)
    driver._get = lambda name: "On"  # type: ignore[method-assign]
    assert driver.connect_devices()
    assert driver.pictures_directory.is_dir()
    assert driver.staging_directory.is_dir()


def test_a_name_that_is_taken_gets_the_next_number(tmp_path: Path) -> None:
    """The lowest free number is used."""
    (tmp_path / "M_13_Light_001.fits").touch()
    (tmp_path / "M_13_Light_003.fits").touch()
    assert unused_frame_name("M_13_Light_001.fits", tmp_path) == "M_13_Light_002.fits"
    assert unused_frame_name("Other_Light_007.fits", tmp_path) == "Other_Light_001.fits"


def test_a_drawn_frame_is_marked_synthetic_and_a_real_one_is_not(tmp_path: Path) -> None:
    """``SYNTHETC`` is added only when asked for."""
    marked, plain = tmp_path / "marked.fits", tmp_path / "plain.fits"
    _write_frame(tmp_path / "a.fits")
    _write_frame(tmp_path / "b.fits")
    stamp_offset(tmp_path / "a.fits", marked, 10, mark_synthetic=True)
    stamp_offset(tmp_path / "b.fits", plain, 10)
    assert fits.getheader(marked)["SYNTHETC"] is True
    assert "SYNTHETC" not in fits.getheader(plain)


def test_the_header_offset_is_the_camera_setting_not_the_pedestal_sent_to_indi(tmp_path: Path) -> None:
    """The driver stamps ``header_value``, not the pedestal."""
    from simulator.device_settings import DeviceSetting

    settings = [DeviceSetting("CCD Simulator", "CCD_OFFSET", (("OFFSET", 394.85),), header_value=10.0)]
    driver = ExposureDriver(1, tmp_path, Path("log"), Path("stats"), 1.0, "M 13", 0.0, 0.0, settings)
    assert driver.camera_offset() == pytest.approx(10.0)
