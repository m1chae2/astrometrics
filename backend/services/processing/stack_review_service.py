"""Purpose: Start the slow stack review jobs for the Image Processing Display.

Description: Some checks on a finished stack take longer than a request should
wait. Making the preview picture again can take a minute. Comparing a stack
with the one before it measures both stacks, and a plate solve that fails can
take about ten minutes. Each is a call of the science library that records its
own job.

This service starts such a call in the background (with the library's
`run_as_background_job`). A call that finishes within a short wait gives its
answer at once. A slower one gives back its job id, and the app follows the
job with ``processing:get_job``. When the job ends, its result is stored in the
job's ``outputMetrics.result``. A call that fails inside the short wait raises
its error to the caller. A later failure shows up as a failed job with a
message.
"""

import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from astrometricslib.jobs import run_as_background_job

logger = logging.getLogger(__name__)

# How long a request waits for a job to finish before it answers with the job
# id instead, in seconds. It is long enough for a quick failure (a missing
# stack, a value out of range) to come back as an error, and short enough that
# the app is never left waiting for a slow job.
DEFAULT_WAIT_SECONDS = 2.0


class StackReviewStart(BaseModel):
    """The answer to starting a slow stack review.

    Attributes
    ----------
    status : `str`
        ``"completed"`` when the work finished within the short wait, or
        ``"running"`` when it is still going.
    job_id : `str` or `None`
        The job to follow with ``processing:get_job``. `None` when the work
        has not recorded a job yet, for example because it waits for another
        job to finish.
    result : `dict` or `None`
        The library's result as plain data, when the status is
        ``"completed"``.
    """

    status: Literal["completed", "running"]
    job_id: str | None = None
    result: dict[str, Any] | None = None


def _plain(result: Any) -> Any:
    """Turn a library result into plain data the app can read.

    Parameters
    ----------
    result : `Any`
        A library result, usually a Pydantic model.

    Returns
    -------
    plain : `Any`
        The model as a dictionary of plain values, or the value as it was.
    """
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else result


class StackReviewService:
    """Start the slow stack reviews as background jobs.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library, which does the work.
    wait_seconds : `float`, optional
        How long a request waits before it answers with the job id.
    """

    def __init__(self, astrometrics: Any, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> None:
        self._astrometrics = astrometrics
        self._wait_seconds = wait_seconds

    def _start(self, work: Callable[[], Any]) -> StackReviewStart:
        """Run a library call that records its own job.

        Parameters
        ----------
        work : `Callable` [[], `Any`]
            The library call, with no arguments.

        Returns
        -------
        reply : `StackReviewStart`
            The finished result, or the job to follow.
        """
        outcome = run_as_background_job(work, grace_period_seconds=self._wait_seconds)
        if "result" in outcome:
            return StackReviewStart(
                status="completed", job_id=outcome["jobId"], result=_plain(outcome["result"])
            )
        return StackReviewStart(status="running", job_id=outcome.get("jobId"))

    def start_preview_remake(
        self,
        target_id: str,
        kind: str = "imaging",
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
    ) -> StackReviewStart:
        """Make a target's preview picture again, as a background job.

        The settings apply to this one run only. The old pictures are kept
        aside the way the library does by default; this service never asks
        for that to change.

        Parameters
        ----------
        target_id : `str`
            The target whose stack to make a picture of.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"``.
        denoise : `bool`, optional
            `False` skips Cosmic Clarity for this run. `True` runs it even if
            the settings have it off. Left out, the settings apply.
        denoise_strength : `float`, optional
            Cosmic Clarity's strength for this run, from 0 to 1.
        star_toning : `bool`, optional
            Turn the star toning on or off for this run.

        Returns
        -------
        reply : `StackReviewStart`
            The result, when finished, is a `PreviewRemakeResult` as a dict.
        """
        processing = self._astrometrics.processing
        return self._start(
            lambda: processing.remake_preview(
                target_id,
                kind=kind,
                denoise=denoise,
                denoise_strength=denoise_strength,
                star_toning=star_toning,
                register_job=True,
            )
        )

    def start_stack_comparison(self, target_id: str, kind: str = "imaging") -> StackReviewStart:
        """Compare a target's stack with the previous one, as a job.

        The library measures both stacks the same way. A failed plate solve
        can take about ten minutes, so this always runs as a job.

        Parameters
        ----------
        target_id : `str`
            The target whose current stack to measure.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"``.

        Returns
        -------
        reply : `StackReviewStart`
            The result, when finished, is a `StackQualityReport` as a dict. A
            target with no previous stack gives a report with no comparison and
            a note that says so.
        """
        quality = self._astrometrics.quality
        return self._start(
            lambda: quality.measure_stack(
                target_id, kind=kind, include=[], compare_to="previous", register_job=True
            )
        )
