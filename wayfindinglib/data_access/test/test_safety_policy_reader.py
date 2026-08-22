"""Purpose: Unit tests for safety rule set resolution.

Description: Verifies get_safety_rule_set() returns None when
unconfigured (never a default-permissive rule set) and that a
persisted rule set round-trips.
"""

import pytest

from wayfindinglib.data_access.safety_policy_reader import get_safety_rule_set, save_safety_rule_set
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.policy.safety import SafetyRule, SafetyRuleSet


@pytest.fixture
def isolated_butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by a fully isolated temporary database.

    Overrides `_find_config_file` directly via `monkeypatch.setattr`
    rather than the `ASTROMETRICS_CONFIG_PATH` env var, which
    `astrometricslib/conftest.py`'s session-scoped class-level override
    makes silently ineffective when this session also collects
    `astrometricslib/`.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def test_get_safety_rule_set_returns_none_when_unconfigured(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unconfigured rule set resolves to None, not permissive.

    Callers (the safety monitor) must treat None as grounds for an
    UNKNOWN verdict.
    """
    assert get_safety_rule_set(isolated_butler) is None


def test_save_and_get_safety_rule_set_round_trips(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a persisted rule set is returned by get_safety_rule_set()."""
    rule_set = SafetyRuleSet(
        id="default",
        rules=[
            SafetyRule(
                id="wind", measurement="wind_speed_kph", comparison="greater_than", unsafe_threshold=40.0
            )
        ],
    )
    save_safety_rule_set(isolated_butler, rule_set)

    loaded = get_safety_rule_set(isolated_butler)
    assert loaded is not None
    assert len(loaded.rules) == 1
    assert loaded.rules[0].unsafe_threshold == pytest.approx(40.0)


def test_save_safety_rule_set_under_non_default_id_not_found_by_get(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_safety_rule_set() only finds a rule set under 'default'."""
    rule_set = SafetyRuleSet(id="draft", rules=[])
    save_safety_rule_set(isolated_butler, rule_set)
    assert get_safety_rule_set(isolated_butler) is None
