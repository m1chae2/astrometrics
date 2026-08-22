"""Layer-1 domain high-level interface for the moving-object/asteroid domain.

`MovingObjectRecovery` is the single entry point external callers
should use to run asteroid recovery -- it delegates to
`astrometricslib.tasks.moving_object_tasks`.
"""

from astrometricslib.models.moving_object import AsteroidRecoveryCandidate
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.models.target import Target
from astrometricslib.tasks.moving_object_tasks.moving_object_pipeline_tasks import (
    AsteroidRecoveryPipeline,
)

__all__ = ["MovingObjectRecovery"]


class MovingObjectRecovery:
    """Recover known solar-system bodies from a target's plate-solved frames.

    This pipeline orchestrates the search for moving objects (like
    asteroids and comets) by analyzing the differences between
    successive images of the same target area. It leverages astrometry
    to convert pixel coordinates into sky coordinates, allowing us to
    differentiate between stationary background stars and true moving
    transients.

    Parameters
    ----------
    config : `MovingObjectConfig`, optional
        Pipeline configuration. If `None` (default), loaded from the
        application configuration the first time a recovery run needs it.
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
        """Run the full asteroid-recovery pipeline against one target.

        This process executes a sequence of algorithms (the
        discrimination cascade) to identify sources that move
        consistently in a straight line across multiple frames. It
        relies on the target having a stacked image and plate-solved
        frames to accurately map pixel motion to true celestial
        motion.

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
