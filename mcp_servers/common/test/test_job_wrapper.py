"""Purpose: Tests for the MCP servers' background job wrapper.

Description: A slow library call runs in a thread. A quick one hands back
its result and the id of the job it recorded; a slow one hands back the job
id and keeps running. The job rows go to a throwaway logs database.
"""

import time
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.jobs import registered_job
from mcp_servers.common.job_wrapper import run_as_background_job

FINISHES_IN_TIME_S = 60.0
"""A grace period long enough for quick work to end inside it on a slow
Windows runner. Waiting returns as soon as the work ends, so it costs
nothing."""


@pytest.fixture
def isolated_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Send the job database and job log files to a throwaway folder.

    Yields
    ------
    logs_database_path : `str`
        Path to the throwaway job database.
    """
    library_path = tmp_path / "library"
    library_path.mkdir(parents=True, exist_ok=True)
    logs_path = tmp_path / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)

    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(configuration, "get_logs_path", lambda: logs_path)
    monkeypatch.setattr(config_loader, "get_configuration", lambda: configuration)

    yield configuration.get_logs_db_path()


def _stack_vega(result: dict, delay_seconds: float = 0.0) -> Callable[[], dict]:
    """Build work that records its own stacking job, as a library method does.

    Returns
    -------
    work : `Callable`
        Runs a "stacking" job for Vega and returns ``result``.
    """

    def work() -> dict:
        """Record the job, wait, and return the result.

        Returns
        -------
        result : `dict`
            The prepared result.
        """
        with registered_job(job_type="stacking", target_id="Vega"):
            time.sleep(delay_seconds)
            return result

    return work


class TestRunAsBackgroundJob:
    """Tests for `mcp_servers.common.job_wrapper.run_as_background_job`."""

    def test_fast_work_returns_its_real_result_and_its_own_job_id(self, isolated_logs: str) -> None:
        """A quick call gets its result and the id of the job it recorded."""
        from astrometricslib.foundation.jobs.store import JobStore

        outcome = run_as_background_job(
            _stack_vega({"stackedImage": "Vega_Stacked.fits"}), grace_period_seconds=FINISHES_IN_TIME_S
        )

        assert outcome["result"] == {"stackedImage": "Vega_Stacked.fits"}
        stored = JobStore(isolated_logs).get_job(outcome["jobId"])
        assert stored.target_id == "Vega"
        assert stored.output_metrics["result"] == {"stackedImage": "Vega_Stacked.fits"}

    def test_slow_work_returns_a_job_id_and_keeps_running(self, isolated_logs: str) -> None:
        """A call that outlasts the grace period hands back a job id.

        The work itself must keep running in the background rather than
        being abandoned: a caller giving up on waiting must not kill it.
        """
        from astrometricslib.foundation.jobs.store import JobStore

        outcome = run_as_background_job(
            _stack_vega({"stackedImage": "Vega_Stacked.fits"}, delay_seconds=0.3), grace_period_seconds=0.1
        )

        assert outcome["status"] == "running"
        assert outcome["jobId"] is not None
        assert outcome["logFilePath"] is not None

        job_store = JobStore(isolated_logs)
        deadline = time.monotonic() + 2.0
        stored = job_store.get_job(outcome["jobId"])
        while stored is not None and not stored.output_metrics and time.monotonic() < deadline:
            time.sleep(0.02)
            stored = job_store.get_job(outcome["jobId"])

        assert stored is not None and stored.status == "completed", "background work should keep running"
        assert stored.output_metrics["result"] == {"stackedImage": "Vega_Stacked.fits"}

    def test_job_recorded_after_the_grace_period_still_gets_its_id(self, isolated_logs: str) -> None:
        """A job that is recorded late (slow disk) is still handed back.

        The work takes longer than the grace period just to start its job,
        as a slow job-row write does on Windows. The caller must still get
        the job id to poll, not "no job".
        """

        def slow_to_record() -> dict:
            """Wait past the grace period, then record a job and finish.

            Returns
            -------
            result : `dict`
                A small result.
            """
            time.sleep(0.4)
            with registered_job(job_type="stacking", target_id="Vega"):
                time.sleep(0.2)
                return {"ok": True}

        outcome = run_as_background_job(slow_to_record, grace_period_seconds=0.1)

        assert outcome["status"] == "running"
        assert outcome["jobId"] is not None
        assert outcome["logFilePath"] is not None

    def test_failing_work_reports_the_error(self, isolated_logs: str) -> None:
        """A quick failure is raised to the caller, not swallowed."""

        def broken_work() -> None:
            """Fail inside a recorded job.

            Raises
            ------
            RuntimeError
                Always.
            """
            with registered_job(job_type="stacking", target_id="Vega"):
                raise RuntimeError("stacking blew up")

        with pytest.raises(RuntimeError, match="stacking blew up"):
            run_as_background_job(broken_work, grace_period_seconds=FINISHES_IN_TIME_S)

    def test_work_that_records_no_job_still_returns_its_result(self, isolated_logs: str) -> None:
        """Without a job of its own, the result comes back with no job id."""
        outcome = run_as_background_job(lambda: {"success": True}, grace_period_seconds=FINISHES_IN_TIME_S)

        assert outcome == {"jobId": None, "result": {"success": True}}


def test_background_work_with_a_job_type_records_one_job(isolated_logs: str) -> None:
    """The MCP wrapper records the call as a job; the work inside joins it."""
    from astrometricslib.foundation.jobs.store import JobStore

    def work() -> dict:
        """Run a pipeline that records its own job.

        Returns
        -------
        result : `dict`
            A fixed answer.
        """
        with registered_job(job_type="stacking", target_id="Vega"):
            return {"done": True}

    outcome = run_as_background_job(
        work, grace_period_seconds=FINISHES_IN_TIME_S, job_type="stacking", target_id="Vega"
    )
    assert outcome["result"] == {"done": True} and outcome["jobId"]
    assert [job.id for job in JobStore(isolated_logs).get_jobs_by_target("Vega")] == [outcome["jobId"]]
