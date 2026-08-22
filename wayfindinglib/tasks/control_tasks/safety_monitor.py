"""Purpose: Environmental Safety Assessment.

Description: Evaluates a configured `SafetyRuleSet` against sensor
readings and publishes a `SafetyAssessment`, per
`Wayfinding_Library_Architecture.md` §2.5.6. Readings are passed in
as a plain mapping rather than read from a live driver directly, so
rule evaluation is exercisable with no hardware or Execution package
present (§2.5.9, "Safety Runs Without Execution") -- gathering the
mapping from `drivers/indi/weather_controller.py` is a thin, separate
step layered on top.

Three properties are architectural rather than incidental
(`Wayfinding_Library_Architecture.md` §2.5.4):

* **Unknown is unsafe.** A reading that is missing, stale, or whose
  rule set is itself unconfigured yields `UNKNOWN`, which
  `permits_observing()` treats identically to `UNSAFE`.
* **Worst verdict wins.** Among rules that did produce a definite
  reading, the most severe verdict determines the outcome.
* **Asymmetric hysteresis.** An unsafe verdict takes effect
  immediately; returning to safe requires every rule to have read safe
  continuously for `settling_period_sec`.
"""

from datetime import datetime

from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyRule, SafetyRuleSet, SafetyVerdict

# Readings are measurement name -> (value, observed_at); a measurement
# absent from this mapping is indistinguishable from an unparseable one --
# both simply have no entry, and both yield UNKNOWN for any rule naming them.
SensorReadings = dict[str, tuple[float, datetime]]

_NON_UNKNOWN_SEVERITY = {
    SafetyVerdict.SAFE: 0,
    SafetyVerdict.MARGINAL: 1,
    SafetyVerdict.UNSAFE: 2,
}


def _evaluate_rule(rule: SafetyRule, readings: SensorReadings, now: datetime) -> SafetyVerdict:
    """Evaluate one rule against the current readings.

    Parameters
    ----------
    rule : `SafetyRule`
        The rule to evaluate.
    readings : `SensorReadings`
        The current sensor readings.
    now : `datetime`
        The current time, used to evaluate staleness.

    Returns
    -------
    verdict : `SafetyVerdict`
        `UNKNOWN` if the named measurement is absent or its reading is
        older than `rule.staleness_bound_sec`; otherwise `SAFE`,
        `MARGINAL`, or `UNSAFE` per the configured thresholds.

    Raises
    ------
    ValueError
        If `rule.comparison` names an unrecognized comparison -- a
        configuration error, not a runtime reading problem.
    """
    reading = readings.get(rule.measurement)
    if reading is None:
        return SafetyVerdict.UNKNOWN
    value, observed_at = reading
    if (now - observed_at).total_seconds() > rule.staleness_bound_sec:
        return SafetyVerdict.UNKNOWN

    if rule.comparison == "greater_than":
        is_unsafe = value > rule.unsafe_threshold
        is_marginal = rule.marginal_threshold is not None and value > rule.marginal_threshold
    elif rule.comparison == "less_than":
        is_unsafe = value < rule.unsafe_threshold
        is_marginal = rule.marginal_threshold is not None and value < rule.marginal_threshold
    else:
        raise ValueError(f"SafetyRule {rule.id!r} has unrecognized comparison {rule.comparison!r}")

    if is_unsafe:
        return SafetyVerdict.UNSAFE
    if is_marginal:
        return SafetyVerdict.MARGINAL
    return SafetyVerdict.SAFE


def _evaluate_rules(
    rule_set: SafetyRuleSet | None, readings: SensorReadings, now: datetime
) -> tuple[SafetyVerdict, str | None, datetime | None]:
    """Evaluate every rule and combine into one instantaneous verdict.

    Parameters
    ----------
    rule_set : `SafetyRuleSet` or `None`
        The safety rule set to evaluate, if any.
    readings : `SensorReadings`
        The current sensor readings.
    now : `datetime`
        The current time.

    Returns
    -------
    verdict, triggering_rule_id, oldest_reading_at : `tuple`
        The combined verdict, the id of the rule that produced it (or
        `None` for an unconfigured rule set or a `SAFE` verdict), and
        the oldest `observed_at` among readings actually consulted.
    """
    if rule_set is None or not rule_set.rules:
        return SafetyVerdict.UNKNOWN, None, None

    oldest_reading_at: datetime | None = None
    for rule in rule_set.rules:
        reading = readings.get(rule.measurement)
        if reading is not None and (oldest_reading_at is None or reading[1] < oldest_reading_at):
            oldest_reading_at = reading[1]

    for rule in rule_set.rules:
        if _evaluate_rule(rule, readings, now) == SafetyVerdict.UNKNOWN:
            return SafetyVerdict.UNKNOWN, rule.id, oldest_reading_at

    worst_verdict = SafetyVerdict.SAFE
    triggering_rule_id: str | None = None
    for rule in rule_set.rules:
        verdict = _evaluate_rule(rule, readings, now)
        if _NON_UNKNOWN_SEVERITY[verdict] > _NON_UNKNOWN_SEVERITY[worst_verdict]:
            worst_verdict = verdict
            triggering_rule_id = rule.id

    return worst_verdict, triggering_rule_id, oldest_reading_at


class SafetyMonitor:
    """Evaluates safety rules over time, applying asymmetric hysteresis.

    Holds the timestamp and verdict of the last non-safe evaluation so
    a return to `SAFE` can be withheld until `settling_period_sec` has
    elapsed since then -- state a pure per-call function cannot carry.
    """

    def __init__(self) -> None:
        """Initialize with no prior non-safe reading recorded."""
        self._last_non_safe_at: datetime | None = None
        self._last_non_safe_verdict: SafetyVerdict | None = None
        self._last_non_safe_rule_id: str | None = None

    def evaluate(
        self, rule_set: SafetyRuleSet | None, readings: SensorReadings, now: datetime
    ) -> SafetyAssessment:
        """Evaluate the current readings and apply hysteresis to the verdict.

        Parameters
        ----------
        rule_set : `SafetyRuleSet` or `None`
            The safety rule set to evaluate.
        readings : `SensorReadings`
            The current sensor readings.
        now : `datetime`
            The current evaluation time.

        Returns
        -------
        assessment : `SafetyAssessment`
            The current verdict. An unsafe or unknown result takes
            effect immediately; a safe instantaneous reading is only
            published once `settling_period_sec` has elapsed since the
            last non-safe reading.
        """
        raw_verdict, triggering_rule_id, oldest_reading_at = _evaluate_rules(rule_set, readings, now)

        if raw_verdict != SafetyVerdict.SAFE:
            self._last_non_safe_at = now
            self._last_non_safe_verdict = raw_verdict
            self._last_non_safe_rule_id = triggering_rule_id
            return SafetyAssessment(
                verdict=raw_verdict,
                triggering_rule_id=triggering_rule_id,
                assessed_at=now,
                oldest_reading_at=oldest_reading_at,
            )

        settling_period_sec = rule_set.settling_period_sec if rule_set is not None else 0
        if (
            self._last_non_safe_at is None
            or (now - self._last_non_safe_at).total_seconds() >= settling_period_sec
        ):
            return SafetyAssessment(
                verdict=SafetyVerdict.SAFE,
                triggering_rule_id=None,
                assessed_at=now,
                oldest_reading_at=oldest_reading_at,
            )

        return SafetyAssessment(
            verdict=self._last_non_safe_verdict,
            triggering_rule_id=self._last_non_safe_rule_id,
            assessed_at=now,
            oldest_reading_at=oldest_reading_at,
        )
