"""Purpose: Safety Rule Set Resolution.

Description: Reads the persisted `SafetyRuleSet`. Returns `None` when
none has been configured, rather than a default rule set -- an absent
rule set has nothing to evaluate a reading against, and the safety
monitor (`Wayfinding_Library_Architecture.md` §2.5.6) must treat that
absence as producing an `UNKNOWN` verdict, the same fail-closed posture
"Unknown Is Unsafe" applies to a stale or unparseable reading
(`Wayfinding_Library_Architecture.md` §2.5.4). This module resolves
configuration only; producing the verdict from it is the safety
monitor's responsibility, not this reader's.
"""

from wayfindinglib.models.policy.safety import SafetyRuleSet

_DEFAULT_RULE_SET_ID = "default"


def get_safety_rule_set(butler) -> SafetyRuleSet | None:  # ruff: ignore[missing-type-function-argument]
    """Return the persisted `SafetyRuleSet`, or `None` if none is configured.

    Parameters
    ----------
    butler : `wayfindinglib.drivers.butler.DiskButler`
        The persistence layer to read from.

    Returns
    -------
    rule_set : `SafetyRuleSet` or `None`
        The persisted rule set, or `None` when unconfigured. Callers
        must treat `None` as grounds for an `UNKNOWN` safety verdict,
        never as permission to proceed.
    """
    return butler.get("safety_rule_set", {"id": _DEFAULT_RULE_SET_ID})


def save_safety_rule_set(butler, rule_set: SafetyRuleSet) -> None:  # ruff: ignore[missing-type-function-argument]
    """Persist `rule_set` as the active configuration.

    Persisted under `rule_set.id` -- callers who want the result found
    by `get_safety_rule_set` must set `id="default"` on the rule set
    they pass in.

    Parameters
    ----------
    butler : `wayfindinglib.drivers.butler.DiskButler`
        The persistence layer to write to.
    rule_set : `SafetyRuleSet`
        The rule set to persist.
    """
    butler.put(rule_set, "safety_rule_set", {"id": rule_set.id})
