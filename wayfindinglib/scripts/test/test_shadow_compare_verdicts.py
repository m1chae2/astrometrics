"""Tests for the shadow comparison script, on made-up summaries.

No library, database or real data is used: the comparison functions take plain
objects, and `main` is checked to write only the report file.
"""

from pathlib import Path
from types import SimpleNamespace

from wayfindinglib.scripts import shadow_compare_verdicts as script


def _rule(name: str, *metrics: str) -> SimpleNamespace:
    """Make a rule stand-in.

    Parameters
    ----------
    name : `str`
        The rule name.
    *metrics : `str`
        The metrics it requires.

    Returns
    -------
    rule : `SimpleNamespace`
        An object with ``name`` and ``requires``.
    """
    return SimpleNamespace(name=name, requires=[SimpleNamespace(name=metric) for metric in metrics])


def _statement(rule: str, kind: str = "warning") -> SimpleNamespace:
    """Make a statement stand-in.

    Parameters
    ----------
    rule : `str`
        The rule name.
    kind : `str`, optional
        The statement kind.

    Returns
    -------
    statement : `SimpleNamespace`
        An object with ``rule`` and ``kind``.
    """
    return SimpleNamespace(rule=rule, kind=kind)


def _summary(flagged: bool, reasons: list[str], gates: dict[str, str]) -> SimpleNamespace:
    """Make a stored-summary stand-in.

    Parameters
    ----------
    flagged : `bool`
        The summary's flag.
    reasons : `list` [`str`]
        The flag reasons.
    gates : `dict` [`str`, `str`]
        Gate name to status.

    Returns
    -------
    summary : `SimpleNamespace`
        An object shaped like a stored summary.
    """
    return SimpleNamespace(
        flagged=flagged,
        flag_reasons=reasons,
        gates=[
            SimpleNamespace(name=name, status=status, detail=f"{name} detail")
            for name, status in gates.items()
        ],
    )


INDEX = script.build_rule_index([
    _rule("dark_light_leak", "stacking.gate_dark_light_leak"),
    _rule("sharp", "stacking.gate_stack_sharpness_not_read"),
    _rule("frames_only", "frame.star_width"),
    _rule("free_rule"),
])


def test_failed_gate_with_quiet_rule_and_gate_without_rule() -> None:
    """A failed gate is reported whether its rule is quiet or missing."""
    states = {
        "stacking": script.old_state_from_summary(
            _summary(True, ["leak", "sharp"], {"dark_light_leak": "failed", "stack_sharpness": "failed"})
        )
    }
    found = script.compare_target_pipelines("T", states, [], INDEX)
    kinds = {(item.kind, item.item) for item in found}
    assert (script.GATE_FAILED_RULE_QUIET, "dark_light_leak") in kinds
    assert (script.GATE_FAILED_NO_RULE, "stack_sharpness") in kinds
    assert (script.FLAGGED_NO_RULE_FIRED, "flagged") in kinds


def test_rule_fired_while_gate_passed_and_unattributed_rule() -> None:
    """A firing rule on a passed gate and a rule with no pipeline show."""
    states = {"stacking": script.old_state_from_summary(_summary(False, [], {"dark_light_leak": "passed"}))}
    found = script.compare_target_pipelines(
        "T", states, [_statement("dark_light_leak"), _statement("free_rule")], INDEX
    )
    kinds = {(item.pipeline, item.kind, item.item) for item in found}
    assert ("stacking", script.RULE_FIRED_GATE_OK, "dark_light_leak") in kinds
    assert (script.UNATTRIBUTED, script.RULE_NO_FLAG_COUNTERPART, "free_rule") in kinds


def test_agreement_gives_no_difference_and_insufficient_data_is_noted() -> None:
    """Agreement is silent; missing data against a decided gate shows."""
    states = {
        "stacking": script.old_state_from_summary(
            _summary(True, ["dark_light_leak detail"], {"dark_light_leak": "failed"})
        )
    }
    assert script.compare_target_pipelines("T", states, [_statement("dark_light_leak")], INDEX) == []
    found = script.compare_target_pipelines(
        "T", states, [_statement("dark_light_leak", "insufficient_data")], INDEX
    )
    assert script.RULE_UNSURE_GATE_DECIDED in {item.kind for item in found}


def test_star_flags() -> None:
    """Star flags match rules by name; subtype ambiguity has no rule."""
    spectrum = SimpleNamespace(is_poor_match=True, is_class_ambiguous=False, is_ambiguous=True)
    found = script.compare_star("T", "S", spectrum, [_statement("spectral_class_ambiguous")])
    kinds = {(item.kind, item.item) for item in found}
    assert (script.STAR_FLAG_RULE_QUIET, "is_poor_match") in kinds
    assert (script.STAR_RULE_FIRED_FLAG_CLEAR, "spectral_class_ambiguous") in kinds
    assert (script.STAR_FLAG_NO_RULE, "is_ambiguous") in kinds


def test_gates_without_rule_and_report(tmp_path: Path) -> None:
    """The unruled gates are listed and the report counts differences."""
    assert script.gates_without_rule(["stacking.gate_dark_light_leak", "stacking.gate_x"], INDEX) == [
        "stacking.gate_x"
    ]
    difference = script.Difference("T", "stacking", script.GATE_FAILED_NO_RULE, "x", "d")
    text = script.build_report([difference], 1, 0, ["stacking.gate_x"], {"stacking.gate_x": {"failed": 2}})
    assert "Differences: 1" in text
    assert "| stacking.gate_x | 0 | 2 | 0 |" in text
    target = tmp_path / "report.md"
    target.write_text(text, encoding="utf-8")
    assert target.read_text(encoding="utf-8").startswith("# Shadow comparison")
