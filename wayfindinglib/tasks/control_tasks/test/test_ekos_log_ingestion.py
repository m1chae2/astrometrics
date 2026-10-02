"""Purpose: Unit tests for Ekos session log ingestion.

Description: Verifies the download, parse and store chain for Ekos guide
and analyze logs, using a real `LoggerInterface` on a temporary database so
that repeat-safety is tested against real storage, not a stand-in. Also
verifies that each session is tied to the equipment its own data shows.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib import LoggerInterface
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.guide_log import GuidingSection
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.tasks.control_tasks import ekos_log_ingestion
from wayfindinglib.tasks.control_tasks.ekos_log_ingestion import (
    attribute_session_equipment,
    build_frame_lookup,
    fetch_and_ingest_ekos_session_logs,
    ingest_ekos_session_logs_from_directory,
)

_GUIDE_LOG = """\
KStars version 3.8.3. PHD2 log version 2.5. Log enabled at 2026-09-23 20:43:05

Guiding Begins at 2026-09-23 20:50:00
Pixel scale = 6.39 arc-sec/px, Binning = 1, Focal length = 121.05 mm
Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode
1,2.0,"Mount",0.1,0.1,0.100,0.200,0.1,0.1,10,E,10,N,,,250000,40.0,0
2,4.0,"DROP",0.000,0.000,0.000,0.000,0.000,0.000,0,,0,,,,0,0.00,7
3,6.0,"Mount",0.1,0.1,0.300,0.400,0.1,0.1,20,W,20,S,,,250000,41.0,0
Guiding Ends at 2026-09-23 20:55:00
"""

_ANALYZE_LOG = """\
#KStars version 3.8.3. Analyze log version 1.0.

AnalyzeStartTime,2026-09-23 20:31:48.442,MDT
MountCoords,2.758,19.8746,90.0000,0.0000,45.6817,0,-90.0000
CaptureComplete,1500.0,30.000,Luminance,1.262,/home/stellarmate/M_27_001.fits,238,460,0.452
"""

_NIGHT_OF_ANALYZE_LOG_START = 1790217108.442
"""2026-09-23 20:31:48.442 MDT as seconds since the epoch."""


class _FakeObservatory:
    """A stand-in `ObservatoryControl` that remembers saved session records."""

    def __init__(self, guide_plate_scale: float | None = 6.39, remote_transfer_driver: Any = None) -> None:
        """Initialize with a fixed configured guide plate scale."""
        self._guide_plate_scale = guide_plate_scale
        self.remote_transfer_driver = remote_transfer_driver
        self.saved_contexts: dict[str, EkosSessionContext] = {}
        self.saved_runs: dict[str, GuidingRunSummary] = {}

    def guider_plate_scale_arcsec_per_px(self) -> float | None:
        """Return the fixed configured guide plate scale.

        Returns
        -------
        plate_scale : `float` or `None`
            The value given at construction.
        """
        return self._guide_plate_scale

    def save_ekos_session_context(self, context: EkosSessionContext) -> None:
        """Remember a saved context, replacing any with the same id."""
        self.saved_contexts[context.id] = context

    def save_guiding_run(self, run: GuidingRunSummary) -> None:
        """Remember a saved run, replacing any with the same id."""
        self.saved_runs[run.id] = run


@pytest.fixture
def logger_interface(tmp_path: Path) -> LoggerInterface:
    """Build a real `LoggerInterface` on a temporary database.

    Returns
    -------
    logger_interface : `LoggerInterface`
        An initialised interface, isolated from the real log database.
    """
    return LoggerInterface(db_path=str(tmp_path / "log.db"))


@pytest.fixture
def logs_directory(tmp_path: Path) -> str:
    """Write one guide log and one analyze log into a directory.

    Returns
    -------
    directory : `str`
        Path to the directory holding the two files.
    """
    directory = tmp_path / "ekos_logs"
    directory.mkdir()
    (directory / "guide_log-2026-09-23T20-43-05.txt").write_text(_GUIDE_LOG, encoding="utf-8")
    (directory / "ekos-2026-09-23T20-31-48.analyze").write_text(_ANALYZE_LOG, encoding="utf-8")
    return str(directory)


def test_guide_samples_are_stored_in_arcseconds_and_labelled_as_measured(
    logger_interface: LoggerInterface, logs_directory: str
) -> None:
    """Verify samples are scaled, signed, and labelled as Ekos data."""
    ingest_ekos_session_logs_from_directory(_FakeObservatory(), logger_interface, logs_directory)

    stored = logger_interface.get_guiding_logs()
    assert len(stored) == 2
    assert stored[0]["dra"] == pytest.approx(0.100 * 6.39)
    assert stored[1]["pulse_ra"] == pytest.approx(-20.0)
    assert {row["source"] for row in stored} == {"ekos_guide_log"}


def test_lost_star_frames_are_counted_not_stored(
    logger_interface: LoggerInterface, logs_directory: str
) -> None:
    """Verify the failed frame shows in the summary but not as a sample."""
    summary = ingest_ekos_session_logs_from_directory(_FakeObservatory(), logger_interface, logs_directory)

    assert summary.guide_frames_with_error_code == 1
    assert summary.guide_samples_stored == 2


def test_ingesting_twice_leaves_the_same_data_as_ingesting_once(
    logger_interface: LoggerInterface, logs_directory: str
) -> None:
    """Verify a repeat run neither duplicates samples nor session records."""
    observatory = _FakeObservatory()

    ingest_ekos_session_logs_from_directory(observatory, logger_interface, logs_directory)
    ingest_ekos_session_logs_from_directory(observatory, logger_interface, logs_directory)

    assert len(logger_interface.get_guiding_logs()) == 2
    assert len(observatory.saved_contexts) == 1


def test_a_session_record_is_saved_for_each_analyze_file(
    logger_interface: LoggerInterface, logs_directory: str
) -> None:
    """Verify the analyze file becomes a session record named for it."""
    observatory = _FakeObservatory()

    summary = ingest_ekos_session_logs_from_directory(observatory, logger_interface, logs_directory)

    assert summary.analyze_files_read == 1
    assert summary.session_contexts_stored == 1
    assert summary.nights == ["2026-09-23"]
    context = observatory.saved_contexts["2026-09-23T20-31-48"]
    assert len(context.captures) == 1


def test_each_guiding_run_is_recorded_with_its_lost_frames(
    logger_interface: LoggerInterface, logs_directory: str
) -> None:
    """Verify lost frames survive as a run fact, since they are not samples."""
    observatory = _FakeObservatory()

    summary = ingest_ekos_session_logs_from_directory(observatory, logger_interface, logs_directory)

    (run,) = observatory.saved_runs.values()
    assert summary.guiding_runs_stored == 1
    assert run.id == "guide_log-2026-09-23T20-43-05.txt#0"
    assert run.session_id == "2026-09-23"
    assert run.frames_total == 3
    assert run.frames_lost == 1
    assert run.samples_stored == 2
    assert run.pixel_scale_arcsec_per_px == pytest.approx(6.39)
    assert run.focal_length_mm == pytest.approx(121.05)
    assert run.written_by_ekos is True
    assert run.lost_fraction == pytest.approx(1 / 3)
    assert run.duration_seconds == pytest.approx(300.0)


def test_a_log_with_only_a_calibration_records_no_run(
    logger_interface: LoggerInterface, tmp_path: Path
) -> None:
    """Verify a calibration alone is not a guiding run."""
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "guide_log-2026-03-03T04-43-50.txt").write_text(
        "KStars version 3.8.1. PHD2 log version 2.5. Log enabled at 2026-03-03 04:43:50\n", encoding="utf-8"
    )
    observatory = _FakeObservatory()

    ingest_ekos_session_logs_from_directory(observatory, logger_interface, str(directory))

    assert observatory.saved_runs == {}


def test_an_unreadable_analyze_file_is_reported_not_fatal(
    logger_interface: LoggerInterface, logs_directory: str
) -> None:
    """Verify an empty analyze file is listed as skipped."""
    (Path(logs_directory) / "ekos-2026-01-06T18-00-34.analyze").write_text("", encoding="utf-8")

    summary = ingest_ekos_session_logs_from_directory(_FakeObservatory(), logger_interface, logs_directory)

    assert summary.analyze_files_skipped == ["ekos-2026-01-06T18-00-34.analyze"]
    assert summary.session_contexts_stored == 1


def test_a_log_with_no_usable_samples_is_listed(logger_interface: LoggerInterface, tmp_path: Path) -> None:
    """Verify a calibration-only guide log is reported, not dropped."""
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "guide_log-2026-03-03T04-43-50.txt").write_text(
        "KStars version 3.8.1. PHD2 log version 2.5. Log enabled at 2026-03-03 04:43:50\n", encoding="utf-8"
    )

    summary = ingest_ekos_session_logs_from_directory(_FakeObservatory(), logger_interface, str(directory))

    assert summary.guide_log_files_with_no_samples == ["guide_log-2026-03-03T04-43-50.txt"]


def test_a_missing_directory_ingests_nothing(logger_interface: LoggerInterface, tmp_path: Path) -> None:
    """Verify a directory that does not exist gives an empty summary."""
    summary = ingest_ekos_session_logs_from_directory(
        _FakeObservatory(), logger_interface, str(tmp_path / "nowhere")
    )

    assert summary.guide_log_files_read == 0
    assert summary.analyze_files_read == 0


def test_fetch_downloads_both_kinds_of_log_then_ingests(
    logger_interface: LoggerInterface, logs_directory: str
) -> None:
    """Verify the download methods are called, then the directory is read."""
    calls: list[str] = []

    class _Driver:
        """A remote-transfer driver that records what it was asked to fetch."""

        def download_guide_logs(self, destination_dir: str) -> list[str]:
            """Record the request.

            Returns
            -------
            paths : `list` [`str`]
                Nothing, since the files are already in place.
            """
            calls.append("guide")
            return []

        def download_ekos_analyze_logs(self, destination_dir: str) -> list[str]:
            """Record the request.

            Returns
            -------
            paths : `list` [`str`]
                Nothing, since the files are already in place.
            """
            calls.append("analyze")
            return []

    summary = fetch_and_ingest_ekos_session_logs(
        _FakeObservatory(remote_transfer_driver=_Driver()), logger_interface, logs_directory
    )

    assert calls == ["guide", "analyze"]
    assert summary.guide_samples_stored == 2


def test_fetch_still_ingests_local_files_when_the_driver_cannot_download(
    logger_interface: LoggerInterface, logs_directory: str
) -> None:
    """Verify a driver with no Ekos support still allows local reads."""
    summary = fetch_and_ingest_ekos_session_logs(
        _FakeObservatory(remote_transfer_driver=object()), logger_interface, logs_directory
    )

    assert summary.guide_samples_stored == 2


def _section(started_at: float, focal_length: float, pixel_scale: float) -> GuidingSection:
    """Build a guiding section with the given optics and no samples.

    Returns
    -------
    section : `GuidingSection`
        A section that ends 10 minutes after it starts.
    """
    return GuidingSection(
        started_at=started_at,
        ended_at=started_at + 600.0,
        focal_length_mm=focal_length,
        pixel_scale_arcsec_per_px=pixel_scale,
    )


def _context() -> EkosSessionContext:
    """Build a one-hour session record.

    Returns
    -------
    context : `EkosSessionContext`
        An empty session starting at `_NIGHT_OF_ANALYZE_LOG_START`.
    """
    return EkosSessionContext(
        id="2026-09-23T20-31-48",
        session_id="2026-09-23",
        started_at=_NIGHT_OF_ANALYZE_LOG_START,
        ended_at=_NIGHT_OF_ANALYZE_LOG_START + 3600.0,
    )


def test_attribution_reads_the_guide_optics_from_the_sessions_own_log() -> None:
    """Verify the focal length and scale come from the log, not the config."""
    sections = [_section(_NIGHT_OF_ANALYZE_LOG_START + 1200.0, 121.05, 6.39)]

    attribution = attribute_session_equipment(_context(), sections, 1.91, None)

    assert attribution.guide_focal_length_mm == pytest.approx(121.05)
    assert attribution.guide_pixel_scale_arcsec_per_px == pytest.approx(6.39)
    assert attribution.guide_scale_matches_configuration is False
    assert "guide_focal_mm=121" in attribution.equipment_fingerprint


def test_attribution_flags_agreement_with_the_configuration() -> None:
    """Verify matching configured and logged scales are reported so."""
    sections = [_section(_NIGHT_OF_ANALYZE_LOG_START + 1200.0, 121.05, 6.39)]

    attribution = attribute_session_equipment(_context(), sections, 6.390, None)

    assert attribution.guide_scale_matches_configuration is True


def test_attribution_ignores_guide_sections_from_other_nights() -> None:
    """Verify a section days away does not name this session's guide scope."""
    sections = [_section(_NIGHT_OF_ANALYZE_LOG_START + 5 * 86400.0, 240.0, 3.2)]

    attribution = attribute_session_equipment(_context(), sections, None, None)

    assert attribution.guide_focal_length_mm is None
    assert attribution.guide_scale_matches_configuration is None


def test_attribution_names_the_imaging_equipment_from_frames_in_the_session() -> None:
    """Verify the most common (telescope, camera) among frames wins."""

    def frame_lookup(start: float, end: float) -> list[tuple[str | None, str | None]]:
        """Return three frames from one setup and one from another.

        Returns
        -------
        gear : `list` [`tuple`]
            Four (telescope, camera) pairs.
        """
        return [("Apertura 75Q", "ZWO ASI 533MM Pro")] * 3 + [("Nikkor 300mm", "Nikon D5300")]

    attribution = attribute_session_equipment(_context(), [], None, frame_lookup)

    assert attribution.imaging_telescope_name == "Apertura 75Q"
    assert attribution.imaging_camera_name == "ZWO ASI 533MM Pro"
    assert attribution.imaging_frames_matched == 4
    assert "camera=zwoasi533mmpro" in attribution.equipment_fingerprint


def test_a_session_with_no_evidence_gets_an_unknown_fingerprint() -> None:
    """Verify missing data yields `unknown`, never a guess."""
    attribution = attribute_session_equipment(_context(), [], None, None)

    assert attribution.equipment_fingerprint == (
        "telescope=unknown|camera=unknown|guide_focal_mm=unknown|guide_scale=unknown"
    )


def test_different_equipment_gives_a_different_fingerprint_for_the_same_night() -> None:
    """Verify swapping the guide scope changes the session's identity."""
    first = attribute_session_equipment(
        _context(), [_section(_NIGHT_OF_ANALYZE_LOG_START + 1200.0, 121.05, 6.39)], None, None
    )
    second = attribute_session_equipment(
        _context(), [_section(_NIGHT_OF_ANALYZE_LOG_START + 1200.0, 240.0, 3.22)], None, None
    )

    assert first.equipment_fingerprint != second.equipment_fingerprint


def test_frame_lookup_returns_only_light_frames_in_the_time_span() -> None:
    """Verify the lookup indexes light frames by time and skips the rest."""

    class _Frame:
        """A minimal frame record."""

        def __init__(self, timestamp: float | None, role: str, telescope: str, camera: str) -> None:
            """Store the fields the lookup reads."""
            self.timestamp = timestamp
            self.role = role
            self.telescope = telescope
            self.camera = camera

    class _Target:
        """A minimal target holding frames."""

        frames = (
            _Frame(100.0, "LIGHT", "T1", "C1"),
            _Frame(200.0, "LIGHT", "T1", "C1"),
            _Frame(150.0, "DARK", "T9", "C9"),
            _Frame(None, "LIGHT", "T8", "C8"),
            _Frame(900.0, "LIGHT", "T2", "C2"),
        )

    class _Astrometrics:
        """A minimal science-library interface."""

        class targets:  # ruff: ignore[invalid-class-name]
            """Stand-in for the target catalog."""

            @staticmethod
            def list() -> list[_Target]:
                """Return one target.

                Returns
                -------
                targets : `list`
                    A single target with five frames.
                """
                return [_Target()]

    lookup = build_frame_lookup(_Astrometrics())

    assert lookup(50.0, 250.0) == [("T1", "C1"), ("T1", "C1")]
    assert lookup(800.0, 1000.0) == [("T2", "C2")]
    assert lookup(300.0, 400.0) == []


def test_module_exposes_the_documented_constants() -> None:
    """Verify the agreement and margin constants exist with sane values."""
    assert 0.0 < ekos_log_ingestion.GUIDE_SCALE_AGREEMENT_FRACTION < 0.1
    assert ekos_log_ingestion.SESSION_TIME_MARGIN_SECONDS >= 600.0
