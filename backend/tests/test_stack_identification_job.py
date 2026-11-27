"""Purpose: Unit tests for naming a stack's stars as an analysis-type job.

Description: The Astrometry button on a stack starts
`AnalysisOrchestrator.identify_stack`. The job has the analysis type, so the
Analyze Target button shows it as running; it reports progress, can be
stopped, and its result is read with `get_stack_identification`. It must not
be taken for an analysis result. The library call is a stand-in here.
"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.jobs import JobStore
from backend.services.analysis.analysis_orchestrator import AnalysisOrchestrator
from backend.services.processing.job_service import JobService


def _identification() -> SimpleNamespace:
    """Build a small identification result like the library's.

    Returns
    -------
    identification : `SimpleNamespace`
        One matched star, no notes.
    """
    star = SimpleNamespace(id="detection-0", name="Vega", kind="matched", x=1.0, y=2.0, ra_deg=279.2,
                           dec_deg=38.8, g_mag=0.1, gaia_source_id="123", name_source="simbad",
                           library_id="* alf Lyr")  # fmt: skip
    return SimpleNamespace(
        width=10, height=10, stars=[star], matched_count=1, unmatched_count=0, catalog_only_count=0,
        solution_reused=True, gaia_depth=20.0, catalog_only_limit=19.0, gaia_tiles_missing=0,
        simbad_used=True, notes=[], alias_records_updated=2,
    )  # fmt: skip


def _orchestrator(tmp_path: Path, identify: Any, frames: int = 30) -> AnalysisOrchestrator:
    """Build an orchestrator with a real job table and a stand-in library.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test folder.
    identify : `Callable`
        Stands in for ``processing.identify_stack``.
    frames : `int`, optional
        The ``STACKCNT`` the stand-in header gives.

    Returns
    -------
    orchestrator : `AnalysisOrchestrator`
        The orchestrator.
    """
    config = MagicMock()
    config.get_logs_path.return_value = tmp_path
    stellar = MagicMock()
    stellar._checked_image_path.side_effect = lambda path: path
    astrometrics = SimpleNamespace(
        processing=SimpleNamespace(
            identify_stack=identify, get_stack_identification=lambda _p: _identification()
        ),
        targets=SimpleNamespace(frames=SimpleNamespace(get_header=lambda _p: {"STACKCNT": frames})),
    )
    return AnalysisOrchestrator(
        config_service=config,
        stellar_service=stellar,
        job_service=JobService(JobStore(str(tmp_path / "jobs.db"))),
        astrometrics=astrometrics,
    )


def _wait_until(condition: Any, seconds: float = 5.0) -> None:
    """Wait for a condition, failing the test after a few seconds.

    Parameters
    ----------
    condition : `Callable`
        Returns True when done.
    seconds : `float`, optional
        How long to wait.
    """
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


def test_the_job_is_an_analysis_job_with_progress_and_a_result(tmp_path: Path) -> None:
    """The job runs as type analysis, reports progress and gives its stars."""
    release = threading.Event()

    def identify(path: str, *, progress: Any, should_stop: Any) -> SimpleNamespace:
        """Report half way, wait, then finish.

        Returns
        -------
        identification : `SimpleNamespace`
            The result.
        """
        progress(0.5, "Downloading Gaia sky tiles")
        release.wait(5)
        return _identification()

    orchestrator = _orchestrator(tmp_path, identify)
    started = orchestrator.identify_stack("M 99", "/lib/M99_Stacked.fits")
    assert started["status"] == "started"
    job_id = started["jobId"]

    _wait_until(lambda: orchestrator.get_stack_identification("M 99", "/lib/M99_Stacked.fits")["progress"])
    running = orchestrator.get_stack_identification("M 99", "/lib/M99_Stacked.fits")
    assert running["running"] and running["progress"] == pytest.approx(0.5)
    assert orchestrator._job_service.get_job(job_id).job_type == "analysis"
    again = orchestrator.identify_stack("M 99", "/lib/M99_Stacked.fits")
    assert again == {"status": "already_running", "jobId": job_id}

    release.set()
    _wait_until(lambda: orchestrator._job_service.get_job(job_id).status == "completed")
    answer = orchestrator.get_stack_identification("M 99", "/lib/M99_Stacked.fits")
    assert not answer["running"]
    assert answer["identification"]["stars"][0]["name"] == "Vega"
    assert answer["identification"]["stars"][0]["matchKind"] == "matched"
    # The overlay finds the star's library record by this id.
    assert answer["identification"]["stars"][0]["libraryId"] == "* alf Lyr"
    # The library's 1-based pixels (1, 2) reach the overlay unchanged.
    assert (answer["identification"]["stars"][0]["x"], answer["identification"]["stars"][0]["y"]) == (
        1.0,
        2.0,
    )
    # It is not taken for the target's analysis result.
    assert orchestrator.get_analysis_results("M 99") is None


def test_the_analysis_cancel_stops_the_identification(tmp_path: Path) -> None:
    """Cancel on the Analyze panel sets the stop switch the library checks."""
    stopped = threading.Event()

    def identify(path: str, *, progress: Any, should_stop: Any) -> SimpleNamespace:
        """Wait for the stop switch, then stop as the library does.

        Raises
        ------
        RuntimeError
            When stopped.
        """
        _wait_until(should_stop)
        stopped.set()
        raise RuntimeError("The star identification was stopped.")

    orchestrator = _orchestrator(tmp_path, identify)
    job_id = orchestrator.identify_stack("M 99", "/lib/M99_Stacked.fits")["jobId"]
    _wait_until(lambda: orchestrator._job_service.get_job(job_id).status in ("started", "running"))

    assert orchestrator.cancel_analysis("M 99")["cancelled"] is True
    _wait_until(stopped.is_set)
    _wait_until(lambda: orchestrator._job_service.get_job(job_id).status == "failed")


def test_a_single_frame_is_refused(tmp_path: Path) -> None:
    """A raw frame never starts a job."""
    orchestrator = _orchestrator(tmp_path, MagicMock(), frames=1)

    with pytest.raises(InvalidArgumentError, match="stacks only"):
        orchestrator.identify_stack("M 99", "/lib/frame_001.fits")
