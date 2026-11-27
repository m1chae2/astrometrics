"""Purpose: Stack parts of a target's library stack and store the noise.

Description: These are the owners behind the three noise methods of
``astrometrics.quality.stacks``. Each stacks some of the library stack's
frames into the target's ``_subsets`` folder (never over the library stack),
measures the noise in the library stack's sky apertures and stores the rows,
as a ``stacking`` job that holds one of the heavy-job slots:

* `record_noise_growth` stores the noise against the number of frames
  (`subset_noise_run.run_noise_growth`), and can run the other two in the
  same job.
* `record_night_contributions` stores what each observing night gave
  (`subset_noise_run.run_night_contributions`).
* `record_odd_even_noise` stores the noise of the difference of two halves
  (`odd_even_difference_run.run_odd_even_difference_noise`).

A target with no stack raises `NotFoundError`.
"""

import logging
from collections.abc import Collection, Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.arguments import check_include
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
from astrometricslib.models.quality.subset_stacks import (
    NightContributionResult,
    NoiseGrowthResult,
    OddEvenDifferenceNoiseResult,
)
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext

logger = logging.getLogger(__name__)

__all__ = [
    "NOISE_GROWTH_SECTIONS",
    "record_night_contributions",
    "record_noise_growth",
    "record_odd_even_noise",
]

NOISE_GROWTH_SECTIONS = ("night_contributions", "odd_even_difference")
"""The heavy parts `record_noise_growth` can add in the same job."""


@contextmanager
def _subset_run(
    target: Target, configuration: AppConfiguration, drivers: Drivers | None, measured_at: datetime
) -> Generator[dict[str, Any]]:
    """Record a ``stacking`` job and give the shared arguments of a subset run.

    Parameters
    ----------
    target : `Target`
        The target.
    configuration : `AppConfiguration`
        The settings.
    drivers : `Drivers` or `None`
        The outside services.
    measured_at : `datetime`
        When the measurement is made, with a time zone.

    Yields
    ------
    shared : `dict` [`str`, `Any`]
        ``context``, ``configuration``, ``drivers`` and ``stacking_slot``,
        the keyword arguments every subset run takes.
    """
    from astrometricslib.foundation.jobs.runner import current_job_id, registered_job
    from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

    def stacking_slot() -> Any:
        """Give one of the heavy-job slots.

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to wait for a slot, then hold it.
        """
        return acquire_resource_slot(configuration, "job", configuration.get_max_concurrent_jobs())

    with registered_job(job_type="stacking", target_id=target.id) as job:
        context = PipelineRunContext.from_options(
            target.id,
            "stacking",
            STACKING_PIPELINE_VERSION,
            {"job_id": job.job_id or current_job_id(), "measured_at": measured_at},
        )
        yield {
            "context": context,
            "configuration": configuration,
            "drivers": drivers,
            "stacking_slot": stacking_slot,
        }


def _library_stack_path(target: Target) -> str:
    """Find the path of a target's library imaging stack.

    Returns
    -------
    path : `str`
        The stack's path.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of

    return stack_path_of(target, "imaging")


def record_noise_growth(
    target: Target,
    configuration: AppConfiguration,
    measured_at: datetime,
    drivers: Drivers | None = None,
    *,
    maximum_steps: int | None = None,
    include: Collection[str] | None = None,
) -> NoiseGrowthResult:
    """Stack growing parts of the library stack and store the noise of each.

    Parameters
    ----------
    target : `Target`
        The target.
    configuration : `AppConfiguration`
        The settings.
    measured_at : `datetime`
        When the measurement is made, with a time zone. The caller reads the
        clock; a pipeline never does.
    drivers : `Drivers`, optional
        The outside services.
    maximum_steps : `int`, optional
        The most subsets to stack. The registered default when left out.
    include : `Collection` [`str`], optional
        Parts to run in the same job, under the same lock, after the series:
        ``"night_contributions"`` and ``"odd_even_difference"``.

    Returns
    -------
    result : `NoiseGrowthResult`
        The subsets, smallest first, and the parts asked for.

    Raises
    ------
    InvalidArgumentError
        If a part is unknown or ``maximum_steps`` is below 1.
    """
    from astrometricslib.pipelines.stacking.odd_even_difference_run import run_odd_even_difference_noise
    from astrometricslib.pipelines.stacking.subset_noise_run import run_night_contributions, run_noise_growth
    from astrometricslib.pipelines.stacking.target_stack_guard import subset_job_guard

    sections = check_include(include, NOISE_GROWTH_SECTIONS)
    if maximum_steps is not None and maximum_steps < 1:
        raise InvalidArgumentError("maximum_steps must be at least 1.")
    library_stack_path = _library_stack_path(target)
    with _subset_run(target, configuration, drivers, measured_at) as shared, subset_job_guard(target.id):
        result = run_noise_growth(target, library_stack_path, maximum_steps=maximum_steps, **shared)
        if "night_contributions" in sections:
            result.night_contributions = run_night_contributions(target, library_stack_path, **shared)
        if "odd_even_difference" in sections:
            try:
                result.odd_even_difference = run_odd_even_difference_noise(
                    target, library_stack_path, **shared
                )
            except Exception as error:  # the series is already stored; report, do not lose it
                logger.exception("The odd–even difference noise of %s failed", target.id)
                reason = f"{type(error).__name__}: {error}"
                result.notes.append(f"The odd–even difference noise was not measured ({reason}).")
        return result


def record_night_contributions(
    target: Target, configuration: AppConfiguration, measured_at: datetime, drivers: Drivers | None = None
) -> NightContributionResult:
    """Stack the library stack's frames without each night and store the noise.

    Parameters
    ----------
    target : `Target`
        The target.
    configuration : `AppConfiguration`
        The settings.
    measured_at : `datetime`
        When the measurement is made, with a time zone. The caller reads the
        clock; a pipeline never does.
    drivers : `Drivers`, optional
        The outside services.

    Returns
    -------
    result : `NightContributionResult`
        One entry for each night.
    """
    from astrometricslib.pipelines.stacking.subset_noise_run import run_night_contributions

    library_stack_path = _library_stack_path(target)
    with _subset_run(target, configuration, drivers, measured_at) as shared:
        return run_night_contributions(target, library_stack_path, **shared)


def record_odd_even_noise(
    target: Target,
    configuration: AppConfiguration,
    measured_at: datetime,
    drivers: Drivers | None = None,
    *,
    keep_halves: bool = False,
) -> OddEvenDifferenceNoiseResult:
    """Stack two halves of the library stack's frames and store the noise.

    Parameters
    ----------
    target : `Target`
        The target.
    configuration : `AppConfiguration`
        The settings.
    measured_at : `datetime`
        When the measurement is made, with a time zone. The caller reads the
        clock; a pipeline never does.
    drivers : `Drivers`, optional
        The outside services.
    keep_halves : `bool`, optional
        Keep the two half stacks in the ``_subsets`` folder.

    Returns
    -------
    result : `OddEvenDifferenceNoiseResult`
        The difference noise, or the reason there is none.
    """
    from astrometricslib.pipelines.stacking.odd_even_difference_run import run_odd_even_difference_noise

    library_stack_path = _library_stack_path(target)
    with _subset_run(target, configuration, drivers, measured_at) as shared:
        return run_odd_even_difference_noise(target, library_stack_path, keep_halves=keep_halves, **shared)
