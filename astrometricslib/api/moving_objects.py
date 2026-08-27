"""Main interface for finding asteroids and other moving objects in images.

This module provides `MovingObjectRecovery`, which is the primary tool for
searching through a series of images to find things that move (like asteroids)
against the fixed background stars.
"""

from astrometricslib.models.moving_object import AsteroidRecoveryCandidate
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.models.target import Target
from astrometricslib.pipelines.asteroid_recovery.pipeline import (
    AsteroidRecoveryPipeline,
)

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
        self._pipeline = AsteroidRecoveryPipeline(config=config)

    def recover_asteroids(self, target: Target) -> list[AsteroidRecoveryCandidate]:
        """Run the full search for asteroids on a specific target.

        This runs several algorithms to find dots of light that move in a
        consistent, straight line across multiple images. The images must
        already be plate-solved (stars matched to a database) so we can
        accurately measure true movement in the sky.

        Parameters
        ----------
        target : `astrometricslib.models.target.Target`
            The target to process. Must already have a `stacked_image`.

        Returns
        -------
        candidates : `list` [`AsteroidRecoveryCandidate`]
            Every candidate the discrimination cascade produced,
            including rejected ones.

        Raises
        ------
        ValueError
            If the target has no `stacked_image`.
        """  # ruff: ignore[docstring-extraneous-exception] -- genuinely propagated from self._pipeline.process
        return self._pipeline.process(target)

    @property
    def last_run_metrics(self) -> dict[str, int]:
        """Per-stage candidate counts from the most recent recovery run."""
        return self._pipeline.last_run_metrics
