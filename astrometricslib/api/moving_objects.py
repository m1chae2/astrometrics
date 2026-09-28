"""Main interface for finding asteroids and other moving objects in images.

This module provides `MovingObjectRecovery`, which is the primary tool for
searching through a series of images to find things that move (like asteroids)
against the fixed background stars.
"""

from typing import Any

from astrometricslib.models.moving_object import AsteroidDetectionCandidate
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.models.target import Target

__all__ = ["MovingObjectRecovery"]


class MovingObjectRecovery:
    """Finds asteroids and comets in a sequence of images.

    This tool searches for moving objects by looking for things that change
    position across multiple images of the same area. It uses the known
    positions of background stars to figure out if an object is truly moving
    through space or if the telescope just bumped.

    Parameters
    ----------
    config : `MovingObjectConfig`, optional
        Settings for the search. If not provided, it will load the default
        settings automatically.
    """

    def __init__(self, config: MovingObjectConfig | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with an optional recovery pipeline configuration.

        Parameters
        ----------
        config : `MovingObjectConfig`, optional
            Pipeline configuration. If `None` (default), loaded from
            the application configuration the first time a recovery
            run needs it.
        """
        self._config = config
        self._last_run_metrics: dict[str, Any] = {}

    def detect_asteroids(self, target: Target) -> list[AsteroidDetectionCandidate]:
        """Run the full search for asteroids on a specific target.

        This runs several algorithms to find dots of light that move in a
        consistent, straight line across multiple images. The images must
        already be plate-solved (stars matched to a database) so the system can
        accurately measure true movement in the sky.

        Goes through `analyze_target`, the same entry point the other
        three pipelines use -- this both records a real
        `AsteroidDetectionQualitySummary` on `target` (this method used
        to bypass that entirely) and gives the run a tracked job, so it
        gets IVOA provenance recorded like every other pipeline.

        Parameters
        ----------
        target : `astrometricslib.models.target.Target`
            The target to process. Must already have a `stacked_image`.

        Returns
        -------
        candidates : `list` [`AsteroidDetectionCandidate`]
            The candidates that survived the discrimination cascade,
            already written onto `target.asteroid_detection.candidates`.
        """
        from astrometricslib.pipelines.tasks import analyze_target

        analyze_target(
            target,
            pipeline_type="asteroid_detection",
            register_job=True,
            moving_object_config=self._config,
        )
        summary = target.asteroid_detection.quality_summary
        self._last_run_metrics = (
            summary.asteroid_detection_metrics.model_dump() if summary is not None else {}
        )
        return target.asteroid_detection.candidates

    @property
    def last_run_metrics(self) -> dict[str, Any]:
        """Per-stage candidate counts from the most recent recovery run."""
        return self._last_run_metrics
