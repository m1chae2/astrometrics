"""Purpose: Test add_frames, sessions and get_aperture_record.

Description: `TargetCatalog.add_frames` and `calibration.frames.add`
sort new FITS files into the library and return where they went. `sessions`
groups a target's frames by night, and `QualityRecords.get_aperture_record`
reads the per-aperture noise file beside a stack.
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.calibration import CalibrationCatalog
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


def test_target_frames_add_moves_lights_under_the_target(tmp_path: Path) -> None:
    """A light file from outside the library is moved, indexed and returned."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    light = _write_frame(incoming / "light.fits", "Light")
    storage = MagicMock()
    target = Target(id="M31")
    storage.get.return_value = [target]
    del storage.merge_and_record
    catalog = TargetCatalog(_library_config(tmp_path / "library"), storage)

    added = catalog.frames.add(target, [light])

    assert len(added) == 1
    assert Path(added[0].path).exists()
    assert not Path(light).exists()
    assert "lights" in Path(added[0].path).parts
    assert "M31" in Path(added[0].path).parts
    assert [frame.path for frame in target.frames] == [added[0].path]


def test_calibration_frames_add_leaves_lights_and_moves_and_indexes_darks(tmp_path: Path) -> None:
    """A folder with a light and a dark moves only the dark, and indexes it."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    light = _write_frame(incoming / "light.fits", "Light")
    _write_frame(incoming / "dark.fits", "Dark")
    catalog = CalibrationCatalog(_library_config(tmp_path / "library"), MagicMock())

    reports = catalog.frames.add([str(incoming)], telescope="Scope")

    assert [report.kind for report in reports] == ["dark", "bias", "flat"]
    added = [path for report in reports for path in report.added_paths]
    assert len(added) == 1
    assert "darks" in Path(added[0]).parts
    assert reports[0].added_count == 1
    assert Path(light).exists()


def test_sessions_groups_by_night_and_can_pick_one() -> None:
    """Frames on two nights give two sessions, and `night` picks one."""
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

    sessions = catalog.frames.get_nights(target)

    assert [len(session.frame_paths) for session in sessions] == [2, 1]
    assert sessions[0].night_date == compute_session_night(first_night)
    only_second = catalog.frames.get_nights(target, night=compute_session_night(second_night))
    assert [session.id for session in only_second] == [sessions[1].id]
    assert len(catalog.frames.get_nights(target, night=date(2000, 1, 1))) == 0


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


def test_target_frames_add_indexes_a_sorted_frame_where_it_is(tmp_path: Path) -> None:
    """A light frame already in its camera folder is indexed, not moved."""
    folder = tmp_path / "library" / "frames" / "lights" / "M31" / "Scope" / "Camera"
    folder.mkdir(parents=True)
    light = _write_frame(folder / "light.fits", "Light")
    target = Target(id="M31")
    storage = MagicMock()
    storage.get.return_value = [target]
    del storage.merge_and_record
    config = _library_config(tmp_path / "library")
    config.get_frames_path = lambda: tmp_path / "library" / "frames"
    catalog = TargetCatalog(config, storage)

    added = catalog.frames.add(target, [light])

    assert [frame.path for frame in added] == [light]
    assert Path(light).exists()


def test_target_frames_scan_counts_folders_as_the_jobs_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library scan inside a job reports each target folder as progress."""
    from astrometricslib.pipelines.shared import target_frames, target_records

    lights = tmp_path / "frames" / "lights"
    (lights / "M31").mkdir(parents=True)
    (lights / "M33").mkdir()
    job = MagicMock()
    monkeypatch.setattr("astrometricslib.foundation.jobs.runner.get_current_job", lambda: job)
    monkeypatch.setattr(target_frames, "_scan_one", lambda catalog, target, prune: None)
    monkeypatch.setattr(target_records, "get_target", lambda api, target_id: Target(id=target_id))
    catalog = SimpleNamespace(_config=SimpleNamespace(get_frames_path=lambda: tmp_path / "frames"))

    report = target_frames.scan_frames(catalog)

    assert [change.target_id for change in report.targets] == ["M31", "M33"]
    assert [call.args[:2] for call in job.report_count.call_args_list] == [(0, 2), (1, 2)]
