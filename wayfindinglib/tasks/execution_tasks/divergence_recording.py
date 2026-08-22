"""Purpose: Divergence Record Construction.

Description: Builds one `DivergenceRecord` per comparison between a
computed action and the action the delegated system actually took, per
`Wayfinding_Library_Architecture.md` §2.4.4. A record is written
whether or not the comparison agreed, since a capability-promotion gate
is an agreement *rate*, which cannot be computed from disagreements
alone (the "Evidence Is Symmetric" invariant). Pairing is by
`comparison_input_id` -- the identifier of the shared measurement both
systems responded to -- rather than by timestamp proximity, which would
silently pair a computed correction against an unrelated one under
load.

`MOUNT_CONTROL`, `CAPTURE_ORCHESTRATION`, and `OBSERVATORY_SAFETY`
produce no divergence records: the first two actuate explicit intent
with no independently computed counterpart, the third has no shadowed
state at all (`Wayfinding_Library_Architecture.md` §2.1.2, "Safety Is
Never Shadowed").
"""

from wayfindinglib.models.policy.delegation import ObservatoryCapability
from wayfindinglib.models.session.divergence import DivergenceRecord

UNCOMPARABLE_CAPABILITIES = (
    ObservatoryCapability.MOUNT_CONTROL,
    ObservatoryCapability.CAPTURE_ORCHESTRATION,
    ObservatoryCapability.OBSERVATORY_SAFETY,
)


def record_divergence(
    record_id: str,
    observation_session_id: str,
    queued_observation_package_id: str | None,
    capability: ObservatoryCapability,
    comparison_input_id: str,
    intended_value: float,
    observed_value: float,
    tolerance: float,
    divergence_unit: str,
    converged: bool | None = None,
    detail: str = "",
) -> DivergenceRecord:
    """Build one `DivergenceRecord` comparing a computed and observed value.

    Parameters
    ----------
    record_id : `str`
        Identifier for the resulting record.
    observation_session_id : `str`
        The session this comparison occurred during.
    queued_observation_package_id : `str` or `None`
        The queue entry in progress when this comparison occurred, if
        any.
    capability : `ObservatoryCapability`
        Which capability this comparison evaluates. Must not be
        `MOUNT_CONTROL`, `CAPTURE_ORCHESTRATION`, or
        `OBSERVATORY_SAFETY` -- none of the three has an independently
        computed counterpart to compare against.
    comparison_input_id : `str`
        Identifier of the shared measurement both the computed and
        observed values derive from.
    intended_value : `float`
        The value this system computed.
    observed_value : `float`
        The value the delegated system actually produced.
    tolerance : `float`
        The maximum absolute divergence still considered agreement.
        Stored on the record so a later aggregate report can recompute
        tolerance-relative statistics without re-deriving this value.
    divergence_unit : `str`
        Unit of `intended_value`/`observed_value` (e.g. ``"arcsec"``,
        ``"ms"``, ``"steps"``).
    converged : `bool` or `None`, optional
        For `PLATE_SOLVE_ALIGNMENT` comparisons: whether the underlying
        `PointingCorrection` converged within its iteration limit.
        Leave `None` for every other capability.
    detail : `str`, optional
        Free-text context, e.g. an angular equivalent for a
        millisecond pulse comparison.

    Returns
    -------
    record : `DivergenceRecord`
        Written whether or not the comparison agreed.

    Raises
    ------
    ValueError
        If `capability` has no independently computed counterpart to
        compare against.
    """
    if capability in UNCOMPARABLE_CAPABILITIES:
        raise ValueError(f"{capability} has no shadowed counterpart and produces no divergence records")

    divergence_magnitude = intended_value - observed_value
    return DivergenceRecord(
        id=record_id,
        observation_session_id=observation_session_id,
        queued_observation_package_id=queued_observation_package_id,
        capability=capability,
        comparison_input_id=comparison_input_id,
        intended_value=intended_value,
        observed_value=observed_value,
        divergence_magnitude=divergence_magnitude,
        divergence_unit=divergence_unit,
        tolerance=tolerance,
        within_tolerance=abs(divergence_magnitude) <= tolerance,
        converged=converged,
        detail=detail,
    )
