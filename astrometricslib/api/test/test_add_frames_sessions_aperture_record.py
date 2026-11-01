"""Purpose: Test add_frames, sessions and get_aperture_record.

Description: `TargetCatalog.add_frames` and `CalibrationCatalog.add_frames`
sort new FITS files into the library and return where they went. `sessions`
groups a target's frames by night, and `QualityRecords.get_aperture_record`
reads the per-aperture noise file beside a stack.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from astropy.io import fits

from astrometricslib.api.processing import CalibrationCatalog
from astrometricslib.api.quality import QualityRecords
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.config import AppConfiguration, _TomlSectionedConfig
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.target_sessions import compute_session_night

_EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "astrometrics.config.example.toml"


def _library_config(frames_root: Path) -> AppConfiguration:
    """Build a configuration whose frames folder is under ``frames_root``.

    Parameters
    ----------
    frames_root : `pathlib.Path`
        The folder to hold the library.

    Returns
    -------
    config : `AppConfiguration`
        The configuration.
    """
    parser = _TomlSectionedConfig()
    parser.read_string(_EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    parser.set("Image Library", "path", str(frames_root))
    config = AppConfiguration()
    config.app_config = parser
    return config


def _write_frame(path: Path, frame_type: str) -> str:
    """Write a tiny FITS file with a frame type in its header.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it.
    frame_type : `str`
        The ``FRAME`` header value, such as ``"Light"`` or ``"Dark"``.

    Returns
    -------
    path : `str`
        The file path.
    """
    header = fits.Header()
    header["FRAME"] = frame_type
    header["INSTRUME"] = "ZWO CCD ASI533MM Pro"
    header["GAIN"] = 100.0
    header["EXPTIME"] = 30.0
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16), header=header).writeto(path)
    return str(path)


def test_target_add_frames_moves_lights_under_the_target(tmp_path: Path) -> None:
    """A light file named on its own is moved and its new path returned."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    light = _write_frame(incoming / "light.fits", "Light")
    storage = MagicMock()
    storage.get.return_value = [Target(id="M31")]
    catalog = TargetCatalog(_library_config(tmp_path / "library"), storage)

    added = catalog.add_frames([light], "M31", telescope="Scope")

    assert len(added) == 1
    assert Path(added[0]).exists()
    assert not Path(light).exists()
    assert "lights" in Path(added[0]).parts
    assert "M31" in Path(added[0]).parts


def test_calibration_add_frames_leaves_lights_and_moves_darks(tmp_path: Path) -> None:
    """A folder with a light and a dark moves only the dark."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    light = _write_frame(incoming / "light.fits", "Light")
    _write_frame(incoming / "dark.fits", "Dark")
    catalog = CalibrationCatalog(_library_config(tmp_path / "library"), MagicMock())

    added = catalog.add_frames([str(incoming)], telescope="Scope")

    assert len(added) == 1
    assert "darks" in Path(added[0]).parts
    assert Path(light).exists()


def test_sessions_groups_by_night_and_can_pick_one() -> None:
    """Frames on two nights give two sessions, and `night_date` picks one."""
    first_night = 1_760_000_000.0
    second_night = first_night + 2 * 86_400.0
    target = Target(
        id="M13",
        frames=[
            FrameRecord(path="/a.fits", timestamp=first_night),
            FrameRecord(path="/b.fits", timestamp=first_night + 60.0),
            FrameRecord(path="/c.fits", timestamp=second_night),
            FrameRecord(path="/d.fits"),
        ],
    )
    storage = MagicMock()
    storage.get.return_value = [target]
    catalog = TargetCatalog(AppConfiguration(), storage)

    sessions = catalog.sessions("M13")

    assert [len(session.frame_paths) for session in sessions] == [2, 1]
    assert sessions[0].night_date == compute_session_night(first_night)
    only_second = catalog.sessions("M13", night_date=compute_session_night(second_night))
    assert [session.id for session in only_second] == [sessions[1].id]
    assert catalog.sessions("M13", night_date=date(2000, 1, 1)) == []


def test_get_aperture_record_reads_the_file_or_gives_none(tmp_path: Path) -> None:
    """A good file gives a record model; a bad or missing one gives None."""
    from astrometricslib.pipelines.stacking.post_processing.odd_even_apertures import APERTURE_RECORD_VERSION

    good = tmp_path / "record.json"
    good.write_text(json.dumps({"version": APERTURE_RECORD_VERSION, "apertures": []}), encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"version": -1}), encoding="utf-8")
    records = QualityRecords(AppConfiguration(), MagicMock())

    record = records.get_aperture_record(str(good))
    assert record is not None
    assert record.version == APERTURE_RECORD_VERSION
    assert record.apertures == []
    assert records.get_aperture_record(str(other)) is None
    assert records.get_aperture_record(str(tmp_path / "missing.json")) is None
