"""Purpose: Test the real-frame source .

Description: Frames are tiny FITS files in a temporary folder. An in-process
fake library answers the backend calls the source makes (list a target's
files, read a header, delete a frame), so no backend runs and no real folder
is touched.
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from simulator import night_replay
from simulator.exposure_driver import ExposureDriver
from simulator.frame_source import (
    DELIVERED_KEYWORD,
    CaptureSetting,
    FrameSource,
    retime_header,
    settings_match,
    simulated_capture_setting,
)

CAMERA = "ZWO ASI533MM Pro"


def _write_frame(path: Path, **cards: object) -> Path:
    """Write a small FITS frame with the given header cards.

    Returns
    -------
    path : `pathlib.Path`
        The file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header = fits.Header()
    for key, value in cards.items():
        header[key.replace("_", "-")] = value
    fits.PrimaryHDU(np.full((4, 4), 7, dtype=np.uint16), header=header).writeto(path, overwrite=True)
    return path


def _real_card(taken: str, **overrides: object) -> dict:
    """Give the header cards of a real light frame.

    Returns
    -------
    cards : `dict`
        Header keywords with underscores for hyphens.
    """
    cards = {
        "IMAGETYP": "Light",
        "INSTRUME": CAMERA,
        "FILTER": "L",
        "GAIN": 100,
        "OFFSET": 10,
        "XBINNING": 1,
        "EXPTIME": 60.0,
        "DATE_OBS": taken,
        "DATE_END": taken,
        "MJD_OBS": 61000.5,
        "AIRMASS": 1.2,
        "OBJCTALT": 55.0,
        "OBJCTAZ": 120.0,
        "OBJCTHA": 1.5,
        "FOCPOS": 1234,
        "FOCTEMP": 4.5,
        "CCD_TEMP": -10.0,
        "PIERSIDE": "EAST",
    }
    cards.update(overrides)
    return cards


class FakeLibrary:
    """Stands in for the backend's three calls and keeps a list of frames."""

    def __init__(self, files: list[Path]) -> None:
        """Hold the files the library lists."""
        self.files = list(files)
        self.calls: list[str] = []
        self.fail_delete = False
        self.camera_names: dict[str, str] = {}

    def __call__(self, method: str, params: dict) -> object:
        """Answer one RPC method.

        Returns
        -------
        result : `object`
            What the backend would send.

        Raises
        ------
        AssertionError
            If the source calls a method the test does not expect.
        """
        self.calls.append(method)
        if method == "target:get_files":
            names = self.camera_names
            return {
                "files": [
                    {"path": str(path), "date": "", "camera": names.get(str(path), "")} for path in self.files
                ]
            }
        if method == "images:get_fits_header":
            with fits.open(params["path"], memmap=False) as hdus:
                return [
                    {"key": key, "value": str(hdus[0].header[key]), "comment": ""}
                    for key in hdus[0].header
                    if key
                ]
        if method == "images:delete":
            if self.fail_delete:
                return {"deleted": [], "failed": [{"path": params["paths"][0], "reason": "busy"}]}
            for text in params["paths"]:
                Path(text).unlink()
                self.files = [path for path in self.files if str(path) != text]
            return {"deleted": list(params["paths"]), "failed": []}
        raise AssertionError(method)


def _library(tmp_path: Path, specs: list[dict]) -> tuple[FakeLibrary, list[Path]]:
    """Make a library with one real frame per spec of header overrides.

    Returns
    -------
    library, files : `FakeLibrary`, `list` [`pathlib.Path`]
        The fake library and the frame paths in the order given.
    """
    files = [
        _write_frame(
            tmp_path / "library" / f"frame_{index}.fits",
            **_real_card(f"2026-10-0{index + 1}T03:00:00.000", **spec),
        )
        for index, spec in enumerate(specs)
    ]
    return FakeLibrary(files), files


def _simulated(tmp_path: Path, **overrides: object) -> Path:
    """Write a frame like the camera simulator's (no OFFSET, no filter name).

    Returns
    -------
    path : `pathlib.Path`
        The file.
    """
    cards = {"INSTRUME": "CCD Simulator", "GAIN": 100, "XBINNING": 1, "EXPTIME": 60.0}
    cards.update({"DATE_OBS": "2026-11-07T04:00:00.000", **overrides})
    return _write_frame(tmp_path / "staging" / "sim.fits", **cards)


def _source(tmp_path: Path, library: FakeLibrary, camera: str | None = CAMERA) -> FrameSource:
    """Make a source whose cache and output are in the temporary folder.

    Returns
    -------
    source : `FrameSource`
        The source.
    """
    return FrameSource(library, tmp_path / "cache", camera=camera)


def _deliver(source: FrameSource, simulated: Path, destination: Path, filter_name: str | None = "L") -> bool:
    """Deliver one frame for M 13 with offset 10.

    Returns
    -------
    delivered : `bool`
        What `FrameSource.deliver` says.
    """
    return source.deliver("M 13", simulated, destination, 10, filter_name)


@pytest.fixture(autouse=True)
def _no_real_folders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Name a fake "real" folder, so the refusal check works without settings.

    Returns
    -------
    real : `pathlib.Path`
        The folder the check refuses.
    """
    real = tmp_path / "REAL"
    monkeypatch.setattr(night_replay, "real_protected_paths", lambda: [real])
    return real


def test_settings_match_needs_camera_filter_gain_offset_and_binning() -> None:
    """Camera, filter, gain, offset and binning must all agree."""
    wanted = CaptureSetting(None, "L", 100, 10, 1, 60.0)
    real = CaptureSetting(CAMERA, "L", 100, 10, 1, 30.0)
    assert settings_match(wanted, real, "ASI533MM Pro")
    assert not settings_match(wanted, real, "Nikon D5100")
    assert not settings_match(wanted, CaptureSetting(CAMERA, "R", 100, 10, 1, 60.0), CAMERA)
    assert not settings_match(wanted, CaptureSetting(CAMERA, "L", 120, 10, 1, 60.0), CAMERA)
    assert not settings_match(wanted, CaptureSetting(CAMERA, "L", 100, 30, 1, 60.0), CAMERA)
    assert not settings_match(wanted, CaptureSetting(CAMERA, "L", 100, 10, 2, 60.0), CAMERA)
    assert settings_match(wanted, real, None)


def test_one_filter_spelled_two_ways_matches() -> None:
    """``L`` (the wheel) and ``Luminance`` (a real header) are one filter."""
    wanted = CaptureSetting(None, "L", 100, 10, 1, 60.0)
    assert settings_match(wanted, CaptureSetting(CAMERA, "Luminance", 100, 10, 1, 60.0), CAMERA)
    assert not settings_match(wanted, CaptureSetting(CAMERA, "Red", 100, 10, 1, 60.0), CAMERA)


def test_the_librarys_camera_name_is_used_and_simulator_frames_are_left_out(tmp_path: Path) -> None:
    """The library's camera name wins; drawn frames are not real ones."""
    library, files = _library(
        tmp_path,
        [
            {"INSTRUME": "ZWO CCD ASI533MM Pro"},
            {"INSTRUME": "CCD Simulator"},
            {"SYNTHETC": True},
        ],
    )
    library.camera_names = {str(files[0]): "ZWO ASI 533MM Pro"}
    source = _source(tmp_path, library, camera="ZWO ASI 533MM Pro")
    frames = source.real_frames("M 13")
    assert [frame.path for frame in frames] == [str(files[0])]
    assert frames[0].setting.camera == "ZWO ASI 533MM Pro"


def test_frames_come_in_the_order_taken_and_wrap_around(tmp_path: Path) -> None:
    """Three frames are delivered earliest first, then the first again."""
    library, files = _library(tmp_path, [{"FOCPOS": 1}, {"FOCPOS": 2}, {"FOCPOS": 3}])
    # The library lists them in a different order than they were taken.
    library.files = [files[2], files[0], files[1]]
    source = _source(tmp_path, library)
    delivered = []
    for number in range(4):
        destination = tmp_path / "pictures" / f"out_{number}.fits"
        assert _deliver(source, _simulated(tmp_path), destination)
        delivered.append(fits.getheader(destination)["FOCPOS"])
    assert delivered == [1, 2, 3, 1]


def test_the_requested_exposure_is_preferred(tmp_path: Path) -> None:
    """The requested exposure wins; else any exposure is used."""
    library, _ = _library(tmp_path, [{"EXPTIME": 30.0, "FOCPOS": 30}, {"EXPTIME": 60.0, "FOCPOS": 60}])
    source = _source(tmp_path, library)
    assert _deliver(source, _simulated(tmp_path, EXPTIME=60.0), tmp_path / "a.fits")
    assert fits.getheader(tmp_path / "a.fits")["FOCPOS"] == 60
    assert _deliver(source, _simulated(tmp_path, EXPTIME=10.0), tmp_path / "b.fits")
    assert fits.getheader(tmp_path / "b.fits")["FOCPOS"] == 30


def test_a_different_setting_keeps_the_simulator_frame(tmp_path: Path) -> None:
    """No match on filter, gain or target means no delivery and no deletion."""
    library, files = _library(tmp_path, [{"FILTER": "R"}])
    source = _source(tmp_path, library)
    assert not _deliver(source, _simulated(tmp_path), tmp_path / "out.fits", filter_name="L")
    assert not _deliver(source, _simulated(tmp_path, GAIN=200), tmp_path / "out.fits", filter_name="R")
    assert not (tmp_path / "out.fits").exists()
    assert "images:delete" not in library.calls
    assert files[0].exists()
    empty = _source(tmp_path, FakeLibrary([]))
    assert not _deliver(empty, _simulated(tmp_path), tmp_path / "out.fits")


def test_spectral_frames_are_delivered_like_any_other(tmp_path: Path) -> None:
    """A SPEC filter frame matches a SPEC capture."""
    library, _ = _library(tmp_path, [{"FILTER": "SPEC"}])
    assert _deliver(_source(tmp_path, library), _simulated(tmp_path), tmp_path / "s.fits", filter_name="SPEC")


def test_header_changes(tmp_path: Path) -> None:
    """The time is now, the originals are kept, the pointing values move."""
    library, _ = _library(tmp_path, [{}])
    destination = tmp_path / "pictures" / "out.fits"
    assert _deliver(_source(tmp_path, library), _simulated(tmp_path), destination)
    header = fits.getheader(destination)
    assert header["DATE-OBS"] == "2026-11-07T04:00:00.000"
    assert header["DATE-END"] == "2026-11-07T04:01:00.000"
    assert header["MJD-OBS"] == pytest.approx(61351.1666667)
    assert header["ODATEOBS"] == "2026-10-01T03:00:00.000"
    assert header["OMJDOBS"] == pytest.approx(61000.5)
    assert header["ODATEEND"] == "2026-10-01T03:00:00.000"
    for moved, original, value in (
        ("AIRMASS", "OAIRMASS", 1.2),
        ("OBJCTALT", "OOBJALT", 55.0),
        ("OBJCTAZ", "OOBJAZ", 120.0),
        ("OBJCTHA", "OOBJHA", 1.5),
    ):
        assert moved not in header
        assert header[original] == value
    assert (header["FOCPOS"], header["FOCTEMP"], header["CCD-TEMP"], header["PIERSIDE"]) == (
        1234,
        4.5,
        -10.0,
        "EAST",
    )
    assert header["SIMDELIV"] is True
    assert "SYNTHETC" not in header
    assert "ORIGIN" not in header
    assert not list(destination.parent.glob(".*"))


def test_origin_is_left_alone_and_only_simdeliv_marks_the_frame(tmp_path: Path) -> None:
    """A frame that had an ORIGIN keeps it; one that had none stays without."""
    library, _ = _library(tmp_path, [{"ORIGIN": "ZWO camera"}, {}])
    source = _source(tmp_path, library)
    with_origin, without_origin = tmp_path / "pictures" / "a.fits", tmp_path / "pictures" / "b.fits"
    assert _deliver(source, _simulated(tmp_path), with_origin)
    assert _deliver(source, _simulated(tmp_path), without_origin)
    first, second = fits.getheader(with_origin), fits.getheader(without_origin)
    assert first["ORIGIN"] == "ZWO camera"
    assert first["SIMDELIV"] is True
    assert "ORIGIN" not in second
    assert second["SIMDELIV"] is True
    # A later round reuses the cached copy and still leaves ORIGIN alone.
    third = tmp_path / "pictures" / "c.fits"
    assert _deliver(source, _simulated(tmp_path), third)
    assert fits.getheader(third)["ORIGIN"] == "ZWO camera"


def test_retime_header_uses_the_clock_when_asked() -> None:
    """The header gets the moment given, with DATE-END after the exposure."""
    header = fits.Header({"EXPTIME": 10.0, "AIRMASS": 1.0})
    retime_header(header, datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), None)
    assert header["DATE-OBS"] == "2026-01-01T00:00:00.000"
    assert header["DATE-END"] == "2026-01-01T00:00:10.000"
    assert header[DELIVERED_KEYWORD]


def test_simulated_setting_reads_header_and_fills_gaps(tmp_path: Path) -> None:
    """The driver's offset and filter fill what the header lacks."""
    setting, started = simulated_capture_setting(_simulated(tmp_path), 10, "L")
    assert (setting.gain, setting.offset, setting.filter_name, setting.binning) == (100, 10, "L", 1)
    assert started == datetime(2026, 11, 7, 4, 0, 0, tzinfo=UTC)


def test_a_delivery_hides_the_library_frame_and_keeps_one_copy(tmp_path: Path) -> None:
    """Two frames before; two after the download, none twice."""
    library, files = _library(tmp_path, [{"FOCPOS": 1}, {"FOCPOS": 2}])
    source = _source(tmp_path, library)
    pictures = tmp_path / "pictures"
    before = len(library.files)
    assert _deliver(source, _simulated(tmp_path), pictures / "out.fits")
    assert len(library.files) == before - 1
    assert not files[0].exists()
    # The app's download adds the delivered file as a new frame.
    library.files.append(pictures / "out.fits")
    assert len(library.files) == before
    assert len({fits.getheader(path).get("FOCPOS") for path in library.files}) == before
    # The second round lists only the real frames still to deliver.
    assert _deliver(source, _simulated(tmp_path), pictures / "out2.fits")
    assert fits.getheader(pictures / "out2.fits")["FOCPOS"] == 2


def test_a_failed_delete_delivers_nothing(tmp_path: Path) -> None:
    """If the backend cannot delete the frame, the simulator's is kept."""
    library, files = _library(tmp_path, [{}])
    library.fail_delete = True
    source = _source(tmp_path, library)
    assert not _deliver(source, _simulated(tmp_path), tmp_path / "out.fits")
    assert files[0].exists()
    assert not (tmp_path / "out.fits").exists()
    assert not list((tmp_path / "cache").rglob("*.fits"))


def test_delivery_refuses_a_destination_in_the_real_folders(tmp_path: Path, _no_real_folders: Path) -> None:
    """A real-folder destination is refused before anything happens."""
    library, files = _library(tmp_path, [{}])
    source = _source(tmp_path, library)
    with pytest.raises(RuntimeError, match="real folder"):
        _deliver(source, _simulated(tmp_path), _no_real_folders / "out.fits")
    assert library.calls == []
    assert files[0].exists()


def test_copy_frame_refuses_real_folders(tmp_path: Path, _no_real_folders: Path) -> None:
    """The copy helper never writes inside the real folders."""
    source = _write_frame(tmp_path / "in.fits")
    with pytest.raises(RuntimeError):
        night_replay.copy_frame(source, _no_real_folders / "x.fits")
    assert not _no_real_folders.exists()
    night_replay.copy_frame(source, tmp_path / "ok.fits")
    assert fits.getheader(tmp_path / "ok.fits")["ORIGIN"] == night_replay.SIMULATOR_STAMP_VALUE


def test_the_driver_uses_the_source_and_skips_the_synthetic_mark(tmp_path: Path) -> None:
    """A delivered frame replaces the drawn one and is not marked SYNTHETC."""
    library, _ = _library(tmp_path, [{}])
    driver = ExposureDriver(
        1, tmp_path / "standin", Path("log"), Path("stats"), 60.0, "M 13", 16.695, 36.46,
        frame_source=_source(tmp_path, library),
    )  # fmt: skip
    driver.current_filter_name = lambda: "L"
    driver.camera_offset = lambda: 10
    destination = tmp_path / "pictures" / "out.fits"
    assert driver.deliver_real_frame(_simulated(tmp_path), destination)
    assert "SYNTHETC" not in fits.getheader(destination)
    plain = ExposureDriver(1, tmp_path / "standin", Path("log"), Path("stats"), 60.0, "M 13", 16.695, 36.46)
    assert not plain.deliver_real_frame(_simulated(tmp_path), destination)
