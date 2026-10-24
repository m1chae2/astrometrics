"""Purpose: Shared helpers for the rules of the per-night analyses.

Description: The guiding, capture and sky analyses each turn measurements
into `Recommendation` statements. Every statement carries a confidence and
the limits it was compared with. Two small things are the same in all three,
so they live here:

* The confidence of a statement comes from the validation register entry
  behind the analysis that made it (`PIPELINE_REGISTER_ENTRIES`), using the
  register's status labels. A statement that rests on a proxy, or on limits
  from equipment that only partly matches the night's, is stepped down one
  label (`rule_confidence`).
* A limit that is a fixed design choice, not one derived from the equipment,
  is described as a `QualityLimit` of the design-estimate tier
  (`design_limit`), so every number a statement was compared with has the same
  shape and names its register row.
"""

from astrometricslib import LimitTier, QualityLimit
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.quality.statements import Confidence

PIPELINE_REGISTER_ENTRIES = {
    "guiding": "WAY-45",
    "capture": "WAY-46",
    "sky": "WAY-47",
}
"""The register entry that backs the rules of each per-night analysis."""

PIPELINE_CONFIDENCE = {
    "guiding": Confidence.PARTLY_MEASURED,
    "capture": Confidence.PARTLY_MEASURED,
    "sky": Confidence.DESIGN_ESTIMATE,
}
"""The status label of each analysis's register entry (WAY-45, WAY-46, WAY-47).

A test checks that these match the Status line of the entries in
`validation/wayfinding.md`, so a change of status there is followed here.
"""

_STEPPED_DOWN = {
    Confidence.MEASURED: Confidence.PARTLY_MEASURED,
    Confidence.PARTLY_MEASURED: Confidence.DESIGN_ESTIMATE,
    Confidence.DESIGN_ESTIMATE: Confidence.DESIGN_ESTIMATE,
}
"""One step toward "design estimate", which is as low as the labels go."""


def rule_confidence(pipeline: str, degraded: bool = False) -> Confidence:
    """Give the confidence of a statement made by one of the analyses.

    Parameters
    ----------
    pipeline : `str`
        ``"guiding"``, ``"capture"`` or ``"sky"``.
    degraded : `bool`, optional
        `True` when the evidence is weaker than the rule assumes: the guide
        signal was poor, the limits come from equipment that only partly
        matches the night's, or the statement rests on a proxy. The label is
        then one step lower.

    Returns
    -------
    confidence : `Confidence`
        The status label of the analysis's register entry, or one step lower.
    """
    base = PIPELINE_CONFIDENCE[pipeline]
    return _STEPPED_DOWN[base] if degraded else base


def design_limit(value: float | None, unit: str, derivation: str, register_entry: str) -> QualityLimit:
    """Describe a fixed design choice as a limit.

    Parameters
    ----------
    value : `float` or `None`
        The number.
    unit : `str`
        Its unit, such as ``"fraction"`` or ``"s"``.
    derivation : `str`
        Where the number comes from, in plain words.
    register_entry : `str`
        The register row (a ``WAY-Dnn`` design estimate) that records it.

    Returns
    -------
    limit : `QualityLimit`
        The limit, of the design-estimate tier.
    """
    return QualityLimit(
        value=value,
        unit=unit,
        tier=LimitTier.DESIGN_ESTIMATE,
        derivation=derivation,
        register_entry=register_entry,
    )


def envelope_limits(
    envelope: PerformanceEnvelope | None, names: tuple[str, ...], applies: bool = True
) -> list[QualityLimit]:
    """Collect the envelope's limits a statement was compared with.

    Parameters
    ----------
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits.
    names : `tuple` [`str`]
        The limit names, in the order to list them.
    applies : `bool`, optional
        `False` when the night's equipment does not match the envelope, in
        which case none of its limits was used and the list is empty.

    Returns
    -------
    limits : `list` [`QualityLimit`]
        Each named limit the envelope holds, even one that could not be
        worked out (its value is then `None` and its derivation says why).
    """
    if envelope is None or not applies:
        return []
    return [limit for name in names if (limit := envelope.limit(name)) is not None]
