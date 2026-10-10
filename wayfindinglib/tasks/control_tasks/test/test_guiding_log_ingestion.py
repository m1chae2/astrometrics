"""Purpose: Unit tests for the guiding-log ingestion pipeline (M7a).

Description: Verifies the fetch -> parse -> persist -> refit chain in
`guiding_log_ingestion.py`, using fake `ObservatoryControl`/
`ControlRecordStore`/`RemoteTransferDriver` stand-ins rather than a real
database or SSH-reachable host.
"""

from pathlib import Path
from typing import Any

from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis
from wayfindinglib.tasks.control_tasks import guiding_log_ingestion


class _FakeRecordStore:
    """Records `replace_guiding_samples` calls and serves a fixed history."""

    def __init__(self, guiding_samples: list[dict[str, Any]] | None = None) -> None:
        """Initialize with a fixed cumulative-history response."""
        self.recorded_batches: list[list[dict[str, Any]]] = []
        self._guiding_samples = guiding_samples if guiding_samples is not None else []

    def replace_guiding_samples(self, samples: list[dict[str, Any]]) -> int:
        """Record a batch of samples for later assertion.

        Returns
        -------
        replaced_count : `int`
            Always 0; this fake never holds earlier samples.
        """
        self.recorded_batches.append(samples)
        return 0

    def get_guiding_samples(
        self,
        session_id: str | None = None,
        limit: int = 2000,
        sources: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the fixed cumulative guiding-log history.

        Filters by `sources` the way the real `ControlRecordStore` does, so
        a test sees what the real refit would see.

        Returns
        -------
        logs : `list` [`dict`]
            The fixed history passed at construction, restricted to
            `sources` when given.
        """
        if sources is None:
            return self._guiding_samples
        return [log for log in self._guiding_samples if log.get("source") in sources]


class _FakeObservatory:
    """A stand-in `ObservatoryControl` recording persisted analyses."""

    def __init__(self, remote_transfer_driver: Any = None) -> None:
        """Initialize with no persisted analysis yet."""
        self.remote_transfer_driver = remote_transfer_driver
        self.saved: GuidingSpectrumAnalysis | None = None

    def save_guiding_spectrum_analysis(self, analysis: GuidingSpectrumAnalysis) -> None:
        """Record the persisted analysis for later assertion."""
        self.saved = analysis


_SAMPLE_LOG_LINES = [
    "KStars version 3.8.3. PHD2 log version 2.5. Log enabled at 2026-09-24 22:00:00",
    "",
    "Guiding Begins at 2026-09-24 22:00:00",
    "Pixel scale = 6.39 arc-sec/px, Binning = 1, Focal length = 121.05 mm",
    "Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,"
    "RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode",
    '1,1.0,"Mount",0.5,0.3,0.4,0.2,0.4,0.2,120,W,80,N,,,5000,10.0,0',
    '2,2.0,"Mount",0.6,0.2,0.5,0.1,0.5,0.1,130,E,90,S,,,5100,11.0,0',
    "Guiding Ends at 2026-09-24 22:05:00",
]


def _write_guide_log(tmp_path: Path) -> str:
    """Write a minimal, parseable PHD2 guide log file.

    Returns
    -------
    file_path : `str`
        Path to the written log file.
    """
    log_path = tmp_path / "PHD2_GuideLog_2026-09-24.txt"
    log_path.write_text("\n".join(_SAMPLE_LOG_LINES) + "\n")
    return str(log_path)


def test_ingest_guide_log_file_returns_none_for_unparseable_file(tmp_path: Path) -> None:
    """Verify an empty/missing log file ingests and persists nothing."""
    observatory = _FakeObservatory()
    records = _FakeRecordStore()

    result = guiding_log_ingestion.ingest_guide_log_file(observatory, records, str(tmp_path / "missing.txt"))

    assert result is None
    assert records.recorded_batches == []
    assert observatory.saved is None


def test_ingest_guide_log_file_persists_samples_and_refits_spectrum(tmp_path: Path) -> None:
    """Verify a real log file's samples record, then the spectrum refits."""
    file_path = _write_guide_log(tmp_path)
    # The refit reads back through get_guiding_samples, not the just-recorded
    # batch directly -- simulate that with a fixed cumulative-history stub
    # shaped like the samples ingest_guide_log_file would have recorded.
    records = _FakeRecordStore(
        guiding_samples=[
            {"time": 1.0, "dra": 0.4, "ddec": 0.2, "pulse_dec": 80, "source": "phd2_guide_log"},
            {"time": 2.0, "dra": 0.5, "ddec": 0.1, "pulse_dec": 90, "source": "phd2_guide_log"},
        ]
    )
    observatory = _FakeObservatory()

    result = guiding_log_ingestion.ingest_guide_log_file(observatory, records, file_path)

    assert isinstance(result, GuidingSpectrumAnalysis)
    assert len(records.recorded_batches) == 1
    assert len(records.recorded_batches[0]) == 2
    assert observatory.saved is result


def test_fetch_and_ingest_new_guide_logs_returns_none_without_driver_support() -> None:
    """Verify a remote-transfer driver without guide-log support is a no-op."""

    class _DriverWithoutGuideLogs:
        """A `RemoteTransferDriver` stand-in lacking guide-log methods."""

    observatory = _FakeObservatory(remote_transfer_driver=_DriverWithoutGuideLogs())
    records = _FakeRecordStore()

    result = guiding_log_ingestion.fetch_and_ingest_new_guide_logs(observatory, records, "/tmp/guiding")

    assert result is None
    assert records.recorded_batches == []


def test_fetch_and_ingest_new_guide_logs_returns_none_when_nothing_downloaded() -> None:
    """Verify an empty remote listing ingests nothing."""

    class _DriverWithNoLogs:
        """A stand-in reporting no remote guide log files."""

        def download_guide_logs(self, destination_dir: str) -> list[str]:
            """Report no downloaded files.

            Returns
            -------
            downloaded : `list`
                Always empty.
            """
            return []

    observatory = _FakeObservatory(remote_transfer_driver=_DriverWithNoLogs())
    records = _FakeRecordStore()

    result = guiding_log_ingestion.fetch_and_ingest_new_guide_logs(observatory, records, "/tmp/guiding")

    assert result is None


def test_fetch_and_ingest_new_guide_logs_downloads_parses_and_refits(tmp_path: Path) -> None:
    """Verify downloaded logs are parsed, recorded, and the spectrum refit."""
    file_path = _write_guide_log(tmp_path)

    class _DriverWithOneLog:
        """A stand-in reporting one already-downloaded guide log file."""

        def download_guide_logs(self, destination_dir: str) -> list[str]:
            """Report the one fixed log file.

            Returns
            -------
            downloaded : `list` [`str`]
                A single-element list containing `file_path`.
            """
            return [file_path]

    records = _FakeRecordStore(guiding_samples=[{"time": 1.0, "dra": 0.4, "ddec": 0.2, "pulse_dec": 80}])
    observatory = _FakeObservatory(remote_transfer_driver=_DriverWithOneLog())

    result = guiding_log_ingestion.fetch_and_ingest_new_guide_logs(observatory, records, str(tmp_path))

    assert isinstance(result, GuidingSpectrumAnalysis)
    assert len(records.recorded_batches) == 1
    assert observatory.saved is result


def test_refit_and_persist_guiding_spectrum_persists_through_observatory() -> None:
    """Verify the standalone refit helper persists via the observatory."""
    records = _FakeRecordStore(guiding_samples=[{"time": 1.0, "dra": 0.1, "ddec": 0.1, "pulse_dec": 50}])
    observatory = _FakeObservatory()

    result = guiding_log_ingestion.refit_and_persist_guiding_spectrum(observatory, records)

    assert isinstance(result, GuidingSpectrumAnalysis)
    assert observatory.saved is result


def test_refit_ignores_samples_that_were_not_measured_from_a_real_star() -> None:
    """Verify estimated and unverified samples never reach the fit.

    A sample reconstructed from mount pulses has a drift value that is a
    model's output, so a periodic-error fit on it would only recover the
    model. Only real measurements may be fitted.
    """
    measured = [
        {"time": float(second), "dra": 0.3, "ddec": 0.1, "pulse_dec": 50, "source": "ekos_analyze_log"}
        for second in range(3)
    ]
    estimated = [
        {"time": 100.0 + second, "dra": 9.0, "ddec": 9.0, "pulse_dec": 50, "source": "indi_pulse_estimate"}
        for second in range(5)
    ]
    unverified = [{"time": 200.0, "dra": 9.0, "ddec": 9.0, "pulse_dec": 50, "source": "unverified"}]
    records = _FakeRecordStore(guiding_samples=measured + estimated + unverified)
    observatory = _FakeObservatory()

    analysis = guiding_log_ingestion.refit_and_persist_guiding_spectrum(observatory, records)

    assert analysis.sample_count == len(measured)
