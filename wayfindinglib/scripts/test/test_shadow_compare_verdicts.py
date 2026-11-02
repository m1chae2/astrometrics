"""Tests for the shadow comparison script, on made-up summaries.

No library, database or real data is used: the comparison functions take plain
objects, and `main` is checked to write only the report file.
"""

import json
import sqlite3
from datetime import UTC, datetime
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


def _summary(gates: dict[str, str]) -> SimpleNamespace:
    """Make a stored-summary stand-in. The models no longer hold the flag.

    Parameters
    ----------
    gates : `dict` [`str`, `str`]
        Gate name to status.

    Returns
    -------
    summary : `SimpleNamespace`
        An object shaped like a stored summary.
    """
    return SimpleNamespace(
        gates=[
            SimpleNamespace(name=name, status=status, detail=f"{name} detail")
            for name, status in gates.items()
        ],
    )


def _stored(flagged: bool, reasons: list[str]) -> dict[str, object]:
    """Make the raw stored JSON of an older summary's flag fields.

    Parameters
    ----------
    flagged : `bool`
        The stored flag.
    reasons : `list` [`str`]
        The stored flag reasons.

    Returns
    -------
    stored : `dict`
        The raw keys an older summary holds.
    """
    return {"flagged": flagged, "flagReasons": reasons}


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
            _summary({"dark_light_leak": "failed", "stack_sharpness": "failed"}),
            _stored(True, ["leak", "sharp"]),
        )
    }
    found = script.compare_target_pipelines("T", states, [], INDEX)
    kinds = {(item.kind, item.item) for item in found}
    assert (script.GATE_FAILED_RULE_QUIET, "dark_light_leak") in kinds
    assert (script.GATE_FAILED_NO_RULE, "stack_sharpness") in kinds
    assert (script.FLAGGED_NO_RULE_FIRED, "flagged") in kinds


def test_rule_fired_while_gate_passed_and_unattributed_rule() -> None:
    """A firing rule on a passed gate and a rule with no pipeline show."""
    states = {
        "stacking": script.old_state_from_summary(_summary({"dark_light_leak": "passed"}), _stored(False, []))
    }
    found = script.compare_target_pipelines(
        "T", states, [_statement("dark_light_leak"), _statement("free_rule")], INDEX
    )
    kinds = {(item.pipeline, item.kind, item.item) for item in found}
    assert ("stacking", script.RULE_FIRED_GATE_OK, "dark_light_leak") in kinds
    assert (script.UNATTRIBUTED, script.RULE_NO_FLAG_COUNTERPART, "free_rule") in kinds


def test_a_rule_reading_several_gates_is_not_faulted_for_its_passed_gates() -> None:
    """A rule that fired on a failed gate is not faulted for its others."""
    index = script.build_rule_index([
        _rule("calibration_set", "stacking.gate_dark_level", "stacking.gate_dark_noise")
    ])
    states = {
        "stacking": script.old_state_from_summary(
            _summary({"dark_level": "failed", "dark_noise": "passed"}), _stored(True, ["level"])
        )
    }
    found = script.compare_target_pipelines("T", states, [_statement("calibration_set")], index)
    assert script.RULE_FIRED_GATE_OK not in {item.kind for item in found}


def test_agreement_gives_no_difference_and_insufficient_data_is_noted() -> None:
    """Agreement is silent; missing data against a decided gate shows."""
    states = {
        "stacking": script.old_state_from_summary(
            _summary({"dark_light_leak": "failed"}),
            _stored(True, ["dark_light_leak detail"]),
        )
    }
    assert script.compare_target_pipelines("T", states, [_statement("dark_light_leak")], INDEX) == []
    found = script.compare_target_pipelines(
        "T", states, [_statement("dark_light_leak", "insufficient_data")], INDEX
    )
    assert script.RULE_UNSURE_GATE_DECIDED in {item.kind for item in found}


def test_star_flags() -> None:
    """Flags worked out from stored numbers match rules by name."""
    spectrum = SimpleNamespace(
        self_determined_spectral_type_rms=0.2, rms_gap_to_next_class=0.5, rms_gap_to_second_best=0.01
    )
    found = script.compare_star("T", "S", spectrum, [_statement("spectral_class_ambiguous")])
    kinds = {(item.kind, item.item) for item in found}
    assert (script.STAR_FLAG_RULE_QUIET, "is_poor_match") in kinds
    assert (script.STAR_RULE_FIRED_FLAG_CLEAR, "spectral_class_ambiguous") in kinds
    assert (script.STAR_FLAG_RULE_QUIET, "is_ambiguous") in kinds


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


def _row(
    metric: str, reason: str, outcome: str, when: datetime, job_id: str | None = "job-1"
) -> SimpleNamespace:
    """Make a stored-row stand-in.

    Returns
    -------
    row : `SimpleNamespace`
        An object with the fields the warnings check reads.
    """
    return SimpleNamespace(
        metric=metric, reason=reason, check_outcome=outcome, measured_at=when, job_id=job_id
    )


def test_flag_reasons_are_matched_to_failed_rows_and_old_data_is_told_apart() -> None:
    """Match a reason to a failed row; tell old data from a gap by row date."""
    cutoff = datetime(2026, 11, 2, tzinfo=UTC)
    old = datetime(2026, 10, 31, tzinfo=UTC)
    new = datetime(2026, 11, 3, tzinfo=UTC)
    states = {
        "stacking": script.OldPipelineState(
            flagged=True, flag_reasons=["flat noise high", "single-frame stack"], gates={}
        ),
        "photometry": script.OldPipelineState(flagged=True, flag_reasons=["camera has no profile"], gates={}),
    }
    rows = [
        _row("stacking.gate_flat_noise", "flat noise high", "failed", new),
        _row("stacking.gate_flat_level", "", "passed", new),
        _row("photometry.gate_x", "", "passed", old),
    ]
    differences, counts = script.compare_flag_reasons_to_warnings(
        "M 31", states, script.warning_rows_from_measurements(rows), cutoff
    )
    assert counts.matched == 1
    assert counts.gaps == 1
    assert counts.old_data == 1
    kinds = {(difference.pipeline, difference.kind) for difference in differences}
    assert kinds == {
        ("stacking", script.FLAG_REASON_NO_WARNING_ROW),
        ("photometry", script.FLAG_REASON_OLD_DATA),
    }


def test_a_matched_reason_counts_failed_rows_without_a_job() -> None:
    """Count failed rows of the pipeline that have no job id."""
    when = datetime(2026, 11, 3, tzinfo=UTC)
    states = {"stacking": script.OldPipelineState(flagged=True, flag_reasons=["a"], gates={})}
    rows = [_row("stacking.gate_a", "a", "failed", when, job_id=None)]
    _, counts = script.compare_flag_reasons_to_warnings(
        "M 31", states, script.warning_rows_from_measurements(rows), script.NEW_ROWS_SINCE
    )
    assert counts.matched == 1
    assert counts.matched_without_job == 1


def test_the_report_has_a_warnings_section() -> None:
    """The report states old data apart from gaps."""
    counts = script.WarningCheckCounts(matched=3, old_data=2, gaps=1)
    text = script.build_report([], 1, 0, [], {}, counts)
    assert "Flag reasons against job warnings" in text
    assert "old data" in text


def test_a_backfilled_row_matches_by_the_start_of_its_reason() -> None:
    """A backfilled row adds a note after the sentence and still matches."""
    when = datetime(2026, 10, 12, tzinfo=UTC)
    states = {
        "photometry": script.OldPipelineState(flagged=True, flag_reasons=["the score is low"], gates={})
    }
    rows = [_row("photometry.gate_x", "the score is low Backfilled from the stored summary.", "failed", when)]
    differences, counts = script.compare_flag_reasons_to_warnings(
        "M 31", states, script.warning_rows_from_measurements(rows), script.NEW_ROWS_SINCE
    )
    assert counts.matched == 1
    assert differences == []


def test_a_summary_with_no_stored_flag_takes_its_flag_from_its_gates() -> None:
    """A summary with no stored flag is flagged when a gate failed."""
    failed = script.old_state_from_summary(_summary({"a": "failed", "b": "passed"}), {"gates": []})
    clean = script.old_state_from_summary(_summary({"a": "passed"}))
    assert failed.flagged is True
    assert failed.flag_reasons == []
    assert clean.flagged is False


def test_stored_summaries_are_read_from_the_raw_document_json(tmp_path: Path) -> None:
    """The removed flag fields are read from the raw stored JSON."""
    document = {
        "stacking": {"qualitySummary": {"flagged": True, "flagReasons": ["x"], "gates": []}},
        "quality": {"photometry": {"flagged": False, "flagReasons": []}},
    }
    database = tmp_path / "astrometrics.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE targets (id TEXT, data_json TEXT)")
    connection.execute("INSERT INTO targets VALUES (?, ?)", ("M 81", json.dumps(document)))
    connection.execute("INSERT INTO targets VALUES (?, ?)", ("bad", "not json"))
    connection.commit()
    connection.close()
    summaries = script.read_stored_summaries(str(database))
    assert set(summaries) == {"M 81"}
    assert summaries["M 81"]["stacking"]["flagReasons"] == ["x"]
    assert summaries["M 81"]["photometry"]["flagged"] is False
    assert "spectroscopy" not in summaries["M 81"]
