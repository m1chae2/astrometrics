"""Purpose: Bounded Guide-Star Loss Recovery.

Description: `attempt_guide_star_recovery` runs a bounded reacquire
loop -- up to `CorrectionConfig.guide_reacquire_attempts` -- when a
guide frame under `AUTHORITATIVE` control produces no usable centroid
drift, producing a `GuideStarLossEvent`. Mirrors
`meridian_flip.py`'s bounded reacquire loop and reuses the same config
bound, since both represent "how many attempts to reacquire a guide
star" -- the same underlying capability, triggered from two different
places (a meridian flip vs. a standing loss during otherwise normal
guiding).

The reacquisition step is injected, so this module carries no hardware
import.
"""

from collections.abc import Callable

from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.guide_star_loss import GuideStarLossEvent


def attempt_guide_star_recovery(
    event_id: str,
    observation_session_id: str,
    comparison_input_id: str,
    reacquire_guide_star: Callable[[], bool],
    config: CorrectionConfig,
) -> GuideStarLossEvent:
    """Run a bounded reacquire loop and record the outcome.

    Parameters
    ----------
    event_id : `str`
        Identifier for the resulting event.
    observation_session_id : `str`
        The session the loss occurred during.
    comparison_input_id : `str`
        Identifier of the guide frame where the loss was detected.
    reacquire_guide_star : callable
        One reacquisition attempt, returning whether it succeeded;
        injected so this module carries no hardware import.
    config : `CorrectionConfig`
        Supplies `guide_reacquire_attempts`.

    Returns
    -------
    event : `GuideStarLossEvent`
        `recovered=True` and the attempt count at which it succeeded,
        or `recovered=False` with `reacquire_attempts` at the
        configured bound after every attempt is exhausted.
    """
    event = GuideStarLossEvent(
        id=event_id,
        observation_session_id=observation_session_id,
        comparison_input_id=comparison_input_id,
    )
    for attempt in range(1, config.guide_reacquire_attempts + 1):
        event.reacquire_attempts = attempt
        if reacquire_guide_star():
            event.recovered = True
            break
    return event
