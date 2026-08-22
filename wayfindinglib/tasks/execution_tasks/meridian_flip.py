"""Purpose: Bounded Meridian Flip Sequencing.

Description: `execute_meridian_flip` runs the ordered, bounded
interrupt-flip-reacquire-resume sequence of
`Wayfinding_Library_Architecture.md` §2.4.5 -- the single most
failure-prone moment in an unattended night -- producing a
`MeridianFlipOutcome`. Every step is attempted in order; the first
failure stops the sequence immediately with `resumed=False` and a
`failure_detail` rather than proceeding, per
`Wayfinding_Library_Architecture.md` §2.4.9's "Flip Failure Is Not
Skippable": an unrecovered flip escalates rather than advancing to the
next queue entry.

Every hardware-facing step is injected (`MeridianFlipSteps`), so this
module carries no hardware import. The bounded steps -- realigning by
plate solve and reacquiring a guide star -- loop internally up to
`CorrectionConfig.alignment_iteration_limit` and
`guide_reacquire_attempts` respectively, recording how many attempts
were actually used.
"""

from collections.abc import Callable
from dataclasses import dataclass

from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome


@dataclass
class MeridianFlipSteps:
    """The hardware-facing operations `execute_meridian_flip` sequences.

    `realign_iteration` and `reacquire_guide_star` represent *one*
    attempt each; the bounded loop around them lives in
    `execute_meridian_flip`, not here, so each callable stays a single
    unit of work with no bound-tracking of its own.
    """

    complete_or_abandon_exposure: Callable[[], bool]
    stop_guiding: Callable[[], bool]
    slew_through_flip: Callable[[], bool]
    realign_iteration: Callable[[], tuple[bool, float]]
    reacquire_guide_star: Callable[[], bool]
    resume_exposure: Callable[[], bool]


def execute_meridian_flip(
    outcome_id: str,
    queued_observation_package_id: str,
    hour_angle_at_trigger_deg: float,
    steps: MeridianFlipSteps,
    config: CorrectionConfig,
) -> MeridianFlipOutcome:
    """Run the six-step meridian-flip sequence and record the outcome.

    Parameters
    ----------
    outcome_id : `str`
        Identifier for the resulting `MeridianFlipOutcome`.
    queued_observation_package_id : `str`
        The queue entry being flipped mid-exposure.
    hour_angle_at_trigger_deg : `float`
        The target's hour angle at the moment the flip was triggered.
    steps : `MeridianFlipSteps`
        The injected hardware-facing operations to sequence.
    config : `CorrectionConfig`
        Supplies `alignment_iteration_limit` and
        `guide_reacquire_attempts`.

    Returns
    -------
    outcome : `MeridianFlipOutcome`
        `resumed=True` and `flip_completed=True` only if every step,
        including both bounded loops, succeeded; otherwise the first
        failing step's detail is recorded and the sequence stops
        there.
    """
    outcome = MeridianFlipOutcome(
        id=outcome_id,
        queued_observation_package_id=queued_observation_package_id,
        hour_angle_at_trigger_deg=hour_angle_at_trigger_deg,
    )

    # A meridian flip is a critical safety maneuver for German
    # Equatorial Mounts. As the target crosses the meridian (its
    # highest point in the sky), the mount must flip 180 degrees to
    # prevent the telescope equipment from crashing into the pier. We
    # must orchestrate stopping the exposure, flipping, re-aligning,
    # and resuming.

    if not steps.complete_or_abandon_exposure():
        outcome.failure_detail = "Failed to complete or abandon the in-flight exposure"
        return outcome

    if not steps.stop_guiding():
        outcome.failure_detail = "Failed to stop guiding"
        return outcome

    if not steps.slew_through_flip():
        outcome.failure_detail = "Failed to slew through the meridian flip"
        return outcome

    converged = False
    residual_arcsec: float | None = None
    for attempt in range(1, config.alignment_iteration_limit + 1):
        converged, residual_arcsec = steps.realign_iteration()
        outcome.realign_attempts = attempt
        if converged:
            break
    outcome.residual_pointing_error_arcsec = residual_arcsec
    if not converged:
        outcome.failure_detail = (
            f"Failed to re-establish pointing within {config.alignment_iteration_limit} iterations"
        )
        return outcome

    reacquired = False
    for attempt in range(1, config.guide_reacquire_attempts + 1):
        reacquired = steps.reacquire_guide_star()
        outcome.guide_reacquire_attempts = attempt
        if reacquired:
            break
    if not reacquired:
        outcome.failure_detail = (
            f"Failed to reacquire a guide star within {config.guide_reacquire_attempts} attempts"
        )
        return outcome

    if not steps.resume_exposure():
        outcome.failure_detail = "Failed to resume the exposure sequence"
        return outcome

    outcome.flip_completed = True
    outcome.resumed = True
    return outcome
