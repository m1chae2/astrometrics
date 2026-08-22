"""Purpose: Unit tests for SafetyMonitor.

Description: Verifies UNKNOWN for a stale, absent, and unconfigured
rule set, that `permits_observing()` is false for each, that the worst
verdict across rules wins and names its rule, and that reopening does
not occur until the settling period has elapsed after the last
non-safe reading -- the cases
`Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

from datetime import UTC, datetime, timedelta

from wayfindinglib.models.policy.safety import SafetyRule, SafetyRuleSet, SafetyVerdict
from wayfindinglib.tasks.control_tasks.safety_monitor import SafetyMonitor

_NOW = datetime(2026, 8, 5, 4, 0, 0, tzinfo=UTC)


def _rule_set(**overrides) -> SafetyRuleSet:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "id": "rs-1",
        "rules": [
            SafetyRule(
                id="cloud",
                measurement="cloud_cover_pct",
                comparison="greater_than",
                unsafe_threshold=80.0,
                marginal_threshold=50.0,
                staleness_bound_sec=120,
            ),
            SafetyRule(
                id="wind",
                measurement="wind_speed_kph",
                comparison="greater_than",
                unsafe_threshold=40.0,
                staleness_bound_sec=120,
            ),
        ],
        "settling_period_sec": 900,
    }
    defaults.update(overrides)
    return SafetyRuleSet(**defaults)


def test_absent_reading_yields_unknown_and_blocks_observing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a measurement with no reading yields UNKNOWN."""
    monitor = SafetyMonitor()
    assessment = monitor.evaluate(_rule_set(), {}, _NOW)
    assert assessment.verdict == SafetyVerdict.UNKNOWN
    assert assessment.permits_observing() is False


def test_stale_reading_yields_unknown_and_blocks_observing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a reading older than its staleness bound yields UNKNOWN."""
    monitor = SafetyMonitor()
    readings = {
        "cloud_cover_pct": (10.0, _NOW - timedelta(seconds=200)),
        "wind_speed_kph": (5.0, _NOW),
    }
    assessment = monitor.evaluate(_rule_set(), readings, _NOW)
    assert assessment.verdict == SafetyVerdict.UNKNOWN
    assert assessment.triggering_rule_id == "cloud"
    assert assessment.permits_observing() is False


def test_unconfigured_rule_set_yields_unknown_and_blocks_observing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a None rule set yields UNKNOWN, not a permissive default."""
    monitor = SafetyMonitor()
    assessment = monitor.evaluate(None, {"wind_speed_kph": (5.0, _NOW)}, _NOW)
    assert assessment.verdict == SafetyVerdict.UNKNOWN
    assert assessment.permits_observing() is False


def test_worst_verdict_across_rules_wins_and_names_its_rule():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the most severe among fully-read rules determines the verdict."""
    monitor = SafetyMonitor()
    readings = {
        "cloud_cover_pct": (60.0, _NOW),  # MARGINAL (>50, not >80)
        "wind_speed_kph": (50.0, _NOW),  # UNSAFE (>40)
    }
    assessment = monitor.evaluate(_rule_set(), readings, _NOW)
    assert assessment.verdict == SafetyVerdict.UNSAFE
    assert assessment.triggering_rule_id == "wind"
    assert assessment.permits_observing() is False


def test_safe_readings_yield_safe_and_permit_observing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify all-safe readings yield SAFE and permit observing."""
    monitor = SafetyMonitor()
    readings = {
        "cloud_cover_pct": (10.0, _NOW),
        "wind_speed_kph": (5.0, _NOW),
    }
    assessment = monitor.evaluate(_rule_set(), readings, _NOW)
    assert assessment.verdict == SafetyVerdict.SAFE
    assert assessment.permits_observing() is True


def test_unsafe_verdict_takes_effect_immediately():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a single unsafe reading immediately produces UNSAFE, no delay."""
    monitor = SafetyMonitor()
    safe_readings = {"cloud_cover_pct": (10.0, _NOW), "wind_speed_kph": (5.0, _NOW)}
    unsafe_readings = {"cloud_cover_pct": (10.0, _NOW), "wind_speed_kph": (50.0, _NOW)}

    assert monitor.evaluate(_rule_set(), safe_readings, _NOW).verdict == SafetyVerdict.SAFE
    assessment = monitor.evaluate(_rule_set(), unsafe_readings, _NOW)
    assert assessment.verdict == SafetyVerdict.UNSAFE


def test_reopening_withheld_until_settling_period_elapses():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a safe reading right after an unsafe one is not immediate."""
    rule_set = _rule_set(settling_period_sec=900)
    monitor = SafetyMonitor()
    unsafe_readings = {"cloud_cover_pct": (10.0, _NOW), "wind_speed_kph": (50.0, _NOW)}

    monitor.evaluate(rule_set, unsafe_readings, _NOW)

    just_after = _NOW + timedelta(seconds=1)
    still_settling = monitor.evaluate(
        rule_set,
        {"cloud_cover_pct": (10.0, just_after), "wind_speed_kph": (5.0, just_after)},
        just_after,
    )
    assert still_settling.verdict == SafetyVerdict.UNSAFE
    assert still_settling.permits_observing() is False

    almost_settled = _NOW + timedelta(seconds=899)
    still_not_clear = monitor.evaluate(
        rule_set,
        {"cloud_cover_pct": (10.0, almost_settled), "wind_speed_kph": (5.0, almost_settled)},
        almost_settled,
    )
    assert still_not_clear.verdict == SafetyVerdict.UNSAFE

    settled = _NOW + timedelta(seconds=900)
    cleared = monitor.evaluate(
        rule_set,
        {"cloud_cover_pct": (10.0, settled), "wind_speed_kph": (5.0, settled)},
        settled,
    )
    assert cleared.verdict == SafetyVerdict.SAFE
    assert cleared.permits_observing() is True


def test_settling_period_resets_on_a_new_non_safe_reading():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a fresh non-safe reading during settling restarts the clock."""
    rule_set = _rule_set(settling_period_sec=900)
    monitor = SafetyMonitor()
    unsafe_readings = {"cloud_cover_pct": (10.0, _NOW), "wind_speed_kph": (50.0, _NOW)}

    monitor.evaluate(rule_set, unsafe_readings, _NOW)

    mid_settling = _NOW + timedelta(seconds=500)
    monitor.evaluate(
        rule_set,
        {"cloud_cover_pct": (10.0, mid_settling), "wind_speed_kph": (50.0, mid_settling)},
        mid_settling,
    )

    # 900s after the *original* unsafe reading, but only 400s after the
    # renewed one -- must still be withheld.
    original_plus_900 = _NOW + timedelta(seconds=900)
    still_withheld = monitor.evaluate(
        rule_set,
        {"cloud_cover_pct": (10.0, original_plus_900), "wind_speed_kph": (5.0, original_plus_900)},
        original_plus_900,
    )
    assert still_withheld.verdict == SafetyVerdict.UNSAFE
