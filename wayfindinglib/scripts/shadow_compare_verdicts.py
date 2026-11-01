"""Shadow run: wayfindinglib quality rules against astrometricslib flags.

Issue #97 moves judgments out of `astrometricslib` into `wayfindinglib`. Before
any reader switches, this script runs both sides on every target and lists
each place where they disagree (`QUALITY_METRICS_PLAN.md`, safeguard 4, and
`VERDICT_REMOVAL_PLAN.md`, phase 1). A person then marks each difference as a
bug fixed or a change approved.

The old side is what the stored pipeline summaries say: `flagged`,
`flag_reasons` and each gate's outcome, for stacking, spectral stacking,
astrometry, photometry, spectroscopy and asteroid detection, plus a star's
`is_poor_match` and `is_class_ambiguous`. The new side is the statements of
`planning.get_advisory`, kind ``"quality"`` for a target and ``"result"`` for a
star. A rule is tied to a gate when the rule reads the gate's metric
(``<pipeline>.gate_<name>``). A gate that no rule reads is listed as having no
rule. Detection labels (period, transit, spectral feature verdicts) stay in
`astrometricslib` and are not compared.

The script is read-only. It writes one file, the report given with
``--report``, and nothing to any database. Run it on the data copy only (see
`build/agent_tools/README.md`).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Where each pipeline's summary lives on a `Target`, and the metric prefix its
# gates use. A spectral stack's gates use the stacking prefix.
PIPELINE_METRIC_PREFIX = {
    "stacking": "stacking",
    "spectral_stacking": "stacking",
    "astrometry": "astrometry",
    "photometry": "photometry",
    "spectroscopy": "spectroscopy",
    "asteroid_detection": "asteroid_detection",
}

STAR_PIPELINE = "star_spectrum"
"""The label for the per-star spectral comparison."""

UNATTRIBUTED = "(no pipeline)"
"""The label for rules that read no pipeline metric, or only frame metrics."""

FIRING_KINDS = frozenset({"warning", "recommendation"})
"""Statement kinds that count as the rule saying "something is wrong"."""

# Star rules the old star flags map to.
STAR_FLAG_RULES = {
    "is_poor_match": "spectral_type_poor_match",
    "is_class_ambiguous": "spectral_class_ambiguous",
}

# Difference kinds, in the order the report lists them.
GATE_FAILED_RULE_QUIET = "gate failed, rule did not fire"
GATE_FAILED_NO_RULE = "gate failed, no rule reads it"
RULE_FIRED_GATE_OK = "rule fired, gate did not fail"
RULE_UNSURE_GATE_DECIDED = "rule has insufficient data, gate decided"
FLAGGED_NO_RULE_FIRED = "pipeline flagged, no rule of the pipeline fired"
FLAG_REASON_NOT_A_GATE = "flag reason with no failed gate"
RULE_FIRED_NOT_FLAGGED = "pipeline rule fired, pipeline not flagged"
RULE_NO_FLAG_COUNTERPART = "rule fired, no pipeline flag to compare"
STAR_FLAG_RULE_QUIET = "star flag set, rule did not fire"
STAR_RULE_FIRED_FLAG_CLEAR = "star rule fired, flag not set"
STAR_FLAG_NO_RULE = "star flag with no rule"
COULD_NOT_JUDGE = "could not be judged"

DIFFERENCE_KIND_ORDER = (
    GATE_FAILED_RULE_QUIET,
    GATE_FAILED_NO_RULE,
    RULE_FIRED_GATE_OK,
    RULE_UNSURE_GATE_DECIDED,
    FLAGGED_NO_RULE_FIRED,
    FLAG_REASON_NOT_A_GATE,
    RULE_FIRED_NOT_FLAGGED,
    STAR_FLAG_RULE_QUIET,
    STAR_RULE_FIRED_FLAG_CLEAR,
    STAR_FLAG_NO_RULE,
    RULE_NO_FLAG_COUNTERPART,
    COULD_NOT_JUDGE,
)


@dataclass(frozen=True)
class Difference:
    """One place where the rules and the old flags disagree.

    Parameters
    ----------
    target : `str`
        The target id.
    pipeline : `str`
        The pipeline, or `STAR_PIPELINE`, or `UNATTRIBUTED`.
    kind : `str`
        One of the difference kind constants.
    item : `str`
        The gate name, rule name or star flag the difference is about.
    detail : `str`
        A short sentence with the old and new outcome.
    """

    target: str
    pipeline: str
    kind: str
    item: str
    detail: str


@dataclass
class OldPipelineState:
    """What a stored pipeline summary says.

    Parameters
    ----------
    flagged : `bool`
        The summary's `flagged`.
    flag_reasons : `list` [`str`]
        The summary's `flag_reasons`.
    gates : `dict` [`str`, `tuple` [`str`, `str`]]
        Gate name to (status, detail). Status is ``passed``, ``failed`` or
        ``not_checked``.
    """

    flagged: bool = False
    flag_reasons: list[str] = field(default_factory=list)
    gates: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass
class RuleIndex:
    """Which rules read which metrics, and which pipelines a rule belongs to.

    Parameters
    ----------
    rules_by_metric : `dict` [`str`, `set` [`str`]]
        Metric name to the names of the rules that require it.
    pipelines_by_rule : `dict` [`str`, `set` [`str`]]
        Rule name to the metric prefixes (``stacking``, ``frame`` ...) of the
        metrics it requires. Empty for a rule that names no metric.
    """

    rules_by_metric: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    pipelines_by_rule: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


def build_rule_index(rules: Iterable[Any]) -> RuleIndex:
    """Index rules by the metrics they read.

    Parameters
    ----------
    rules : iterable of `QualityRule`
        Rules with a ``name`` and a ``requires`` list of metric requirements.

    Returns
    -------
    index : `RuleIndex`
        The two lookups.
    """
    index = RuleIndex()
    for rule in rules:
        index.pipelines_by_rule[rule.name]
        for requirement in rule.requires:
            index.rules_by_metric[requirement.name].add(rule.name)
            index.pipelines_by_rule[rule.name].add(requirement.name.split(".", 1)[0])
    return index


def _status_text(status: Any) -> str:
    """Return a status or kind as plain text.

    Parameters
    ----------
    status : `Any`
        An enum member or a string.

    Returns
    -------
    text : `str`
        The enum's value, or the string itself.
    """
    return str(getattr(status, "value", status))


def old_state_from_summary(summary: Any) -> OldPipelineState | None:
    """Read the flag and gates of a stored pipeline summary.

    Parameters
    ----------
    summary : `PipelineQualitySummaryBase` or `None`
        A stored summary with ``flagged``, ``flag_reasons`` and ``gates``.

    Returns
    -------
    state : `OldPipelineState` or `None`
        What the summary says, or `None` when the pipeline never ran.
    """
    if summary is None:
        return None
    gates = {gate.name: (_status_text(gate.status), gate.detail or "") for gate in summary.gates}
    return OldPipelineState(
        flagged=bool(summary.flagged), flag_reasons=list(summary.flag_reasons), gates=gates
    )


def old_states_from_target(target: Any) -> dict[str, OldPipelineState]:
    """Collect the stored summaries of one target, by pipeline.

    Parameters
    ----------
    target : `Target`
        The target record.

    Returns
    -------
    states : `dict` [`str`, `OldPipelineState`]
        One entry per pipeline that has a stored summary.
    """
    summaries = {
        "stacking": target.stacking.quality_summary,
        "spectral_stacking": target.spectral_stacking.quality_summary,
        "asteroid_detection": target.asteroid_detection.quality_summary,
        "astrometry": target.quality.astrometry,
        "photometry": target.quality.photometry,
        "spectroscopy": target.quality.spectroscopy,
    }
    states = {}
    for pipeline, summary in summaries.items():
        state = old_state_from_summary(summary)
        if state is not None:
            states[pipeline] = state
    return states


def fired_and_unsure_rules(statements: Iterable[Any]) -> tuple[set[str], set[str]]:
    """Split statements into rules that fired and rules that lacked data.

    Parameters
    ----------
    statements : iterable of `TargetStatement`
        The statements of one report.

    Returns
    -------
    fired, unsure : `set` [`str`], `set` [`str`]
        Rule names with a warning or recommendation, and rule names with an
        ``insufficient_data`` statement.
    """
    fired: set[str] = set()
    unsure: set[str] = set()
    for statement in statements:
        kind = _status_text(statement.kind)
        if kind in FIRING_KINDS:
            fired.add(statement.rule)
        elif kind == "insufficient_data":
            unsure.add(statement.rule)
    return fired, unsure


def compare_target_pipelines(
    target_id: str,
    old_states: Mapping[str, OldPipelineState],
    statements: Iterable[Any],
    index: RuleIndex,
) -> list[Difference]:
    """List the differences between one target's rules and its stored flags.

    Parameters
    ----------
    target_id : `str`
        The target id.
    old_states : `dict` [`str`, `OldPipelineState`]
        The stored summaries, by pipeline.
    statements : iterable of `TargetStatement`
        The target report's statements.
    index : `RuleIndex`
        The rules by the metrics they read.

    Returns
    -------
    differences : `list` [`Difference`]
        Every disagreement, in pipeline order.
    """
    fired, unsure = fired_and_unsure_rules(statements)
    differences: list[Difference] = []
    accounted: set[str] = set()
    for pipeline, state in old_states.items():
        prefix = PIPELINE_METRIC_PREFIX[pipeline]
        pipeline_rules = {name for name, prefixes in index.pipelines_by_rule.items() if prefix in prefixes}
        accounted |= pipeline_rules
        failed_gate_details = set()
        for gate_name, (status, detail) in sorted(state.gates.items()):
            readers = index.rules_by_metric.get(f"{prefix}.gate_{gate_name}", set())
            if status == "failed":
                failed_gate_details.add(detail)
                if not readers:
                    differences.append(
                        Difference(target_id, pipeline, GATE_FAILED_NO_RULE, gate_name, detail)
                    )
                elif not readers & fired:
                    differences.append(
                        Difference(
                            target_id,
                            pipeline,
                            GATE_FAILED_RULE_QUIET,
                            gate_name,
                            f"rules {sorted(readers)} did not fire. {detail}".strip(),
                        )
                    )
            else:
                for rule_name in sorted(readers & fired):
                    differences.append(
                        Difference(
                            target_id,
                            pipeline,
                            RULE_FIRED_GATE_OK,
                            gate_name,
                            f"rule {rule_name} fired; gate {status}",
                        )
                    )
            if status in ("passed", "failed"):
                for rule_name in sorted(readers & unsure):
                    differences.append(
                        Difference(
                            target_id,
                            pipeline,
                            RULE_UNSURE_GATE_DECIDED,
                            gate_name,
                            f"rule {rule_name} lacked data; gate {status}",
                        )
                    )
        for reason in state.flag_reasons:
            if reason not in failed_gate_details:
                differences.append(
                    Difference(target_id, pipeline, FLAG_REASON_NOT_A_GATE, "flag_reasons", reason)
                )
        gate_rules = {
            rule for name in state.gates for rule in index.rules_by_metric.get(f"{prefix}.gate_{name}", set())
        }
        if state.flagged and not (pipeline_rules & fired):
            differences.append(
                Difference(
                    target_id,
                    pipeline,
                    FLAGGED_NO_RULE_FIRED,
                    "flagged",
                    "; ".join(state.flag_reasons) or "no reason stored",
                )
            )
        if not state.flagged:
            for rule_name in sorted((pipeline_rules - gate_rules) & fired):
                differences.append(
                    Difference(
                        target_id,
                        pipeline,
                        RULE_FIRED_NOT_FLAGGED,
                        rule_name,
                        "rule fired on a pipeline the stored summary did not flag",
                    )
                )
    for rule_name in sorted(fired - accounted):
        prefixes = sorted(index.pipelines_by_rule.get(rule_name, set())) or ["no metric"]
        differences.append(
            Difference(
                target_id,
                UNATTRIBUTED,
                RULE_NO_FLAG_COUNTERPART,
                rule_name,
                f"reads {', '.join(prefixes)}; astrometricslib stores no flag for it",
            )
        )
    return differences


def compare_star(
    target_id: str, star_id: str, spectroscopy: Any, statements: Iterable[Any]
) -> list[Difference]:
    """List the differences between a star's spectral flags and the star rules.

    Parameters
    ----------
    target_id : `str`
        The target id.
    star_id : `str`
        The star's id.
    spectroscopy : `SpectroscopyResult`
        The star's spectrum result, with ``is_poor_match``,
        ``is_class_ambiguous`` and ``is_ambiguous``.
    statements : iterable of `TargetStatement`
        The star's statements from the ``"result"`` advisory.

    Returns
    -------
    differences : `list` [`Difference`]
        Every disagreement for this star.
    """
    fired, _ = fired_and_unsure_rules(statements)
    differences = []
    for flag_name, rule_name in STAR_FLAG_RULES.items():
        flag = bool(getattr(spectroscopy, flag_name, False))
        rule_fired = rule_name in fired
        if flag and not rule_fired:
            differences.append(
                Difference(target_id, STAR_PIPELINE, STAR_FLAG_RULE_QUIET, flag_name, f"star {star_id}")
            )
        elif rule_fired and not flag:
            differences.append(
                Difference(target_id, STAR_PIPELINE, STAR_RULE_FIRED_FLAG_CLEAR, rule_name, f"star {star_id}")
            )
    if getattr(spectroscopy, "is_ambiguous", None):
        differences.append(
            Difference(
                target_id,
                STAR_PIPELINE,
                STAR_FLAG_NO_RULE,
                "is_ambiguous",
                f"star {star_id}: subtype ambiguity; the rules judge only class ambiguity",
            )
        )
    return differences


def gates_without_rule(gate_metric_names: Iterable[str], index: RuleIndex) -> list[str]:
    """List the declared gate metrics that no rule reads.

    Parameters
    ----------
    gate_metric_names : iterable of `str`
        Registered metric names that hold a ``.gate_``.
    index : `RuleIndex`
        The rules by the metrics they read.

    Returns
    -------
    names : `list` [`str`]
        The gate metric names no rule requires, sorted.
    """
    return sorted(name for name in set(gate_metric_names) if name not in index.rules_by_metric)


def build_report(
    differences: list[Difference],
    targets_checked: int,
    stars_checked: int,
    unruled_gates: list[str],
    gate_counts: Mapping[str, Mapping[str, int]],
) -> str:
    """Write the Markdown report.

    Parameters
    ----------
    differences : `list` [`Difference`]
        Every difference found.
    targets_checked, stars_checked : `int`
        How many targets and stars were compared.
    unruled_gates : `list` [`str`]
        Registered gate metrics that no rule reads.
    gate_counts : `dict` [`str`, `dict` [`str`, `int`]]
        For each gate metric seen in stored summaries, how many times it was
        ``passed``, ``failed`` and ``not_checked``.

    Returns
    -------
    text : `str`
        The report.
    """
    lines = [
        "# Shadow comparison: wayfindinglib rules against astrometricslib flags",
        "",
        f"Targets compared: {targets_checked}. Stars compared (spectra): {stars_checked}. "
        f"Differences: {len(differences)}.",
        "",
        "Every difference must be marked as a bug fixed or a change approved before a reader switches.",
        "",
        "## Differences by pipeline and kind",
        "",
        "| Pipeline | Kind | Differences | Targets |",
        "| :--- | :--- | ---: | ---: |",
    ]
    grouped: dict[tuple[str, str], list[Difference]] = defaultdict(list)
    for difference in differences:
        grouped[difference.pipeline, difference.kind].append(difference)
    for (pipeline, kind), items in sorted(
        grouped.items(), key=lambda pair: (pair[0][0], DIFFERENCE_KIND_ORDER.index(pair[0][1]))
    ):
        lines.append(f"| {pipeline} | {kind} | {len(items)} | {len({item.target for item in items})} |")
    lines += [
        "",
        "## Largest kinds of difference, by item",
        "",
        "| Pipeline | Kind | Item | Count |",
        "| :--- | :--- | :--- | ---: |",
    ]
    by_item: dict[tuple[str, str, str], int] = defaultdict(int)
    for difference in differences:
        by_item[difference.pipeline, difference.kind, difference.item] += 1
    for (pipeline, kind, item), count in sorted(by_item.items(), key=lambda pair: (-pair[1], pair[0])):
        lines.append(f"| {pipeline} | {kind} | {item} | {count} |")
    lines += ["", "## Gates with no matching rule", ""]
    if unruled_gates:
        lines += [
            "| Gate metric | Passed | Failed | Not checked |",
            "| :--- | ---: | ---: | ---: |",
        ]
        for name in unruled_gates:
            counts = gate_counts.get(name, {})
            lines.append(
                f"| {name} | {counts.get('passed', 0)} | {counts.get('failed', 0)} | "
                f"{counts.get('not_checked', 0)} |"
            )
    else:
        lines.append("Every registered gate is read by at least one rule.")
    lines += ["", "## Every difference, by target", ""]
    for target in sorted({difference.target for difference in differences}):
        lines.append(f"### {target}")
        lines.append("")
        for difference in sorted(
            (item for item in differences if item.target == target),
            key=lambda item: (item.pipeline, DIFFERENCE_KIND_ORDER.index(item.kind), item.item),
        ):
            lines.append(
                f"- {difference.pipeline} / {difference.kind} / {difference.item}: {difference.detail}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def run_comparison(wayfinder: Any, only_targets: list[str] | None = None) -> tuple[str, list[Difference]]:
    """Compare every target and return the report text.

    Reads the library through the public calls and stores nothing.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The library handle.
    only_targets : `list` [`str`], optional
        Limit the run to these target ids.

    Returns
    -------
    text, differences : `str`, `list` [`Difference`]
        The report and the differences it lists.
    """
    from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES

    index = build_rule_index(ALL_RULES)
    astrometrics = wayfinder.astrometrics
    registered = [spec.name for spec in astrometrics.quality.metrics() if ".gate_" in spec.name]
    unruled = gates_without_rule(registered, index)
    differences: list[Difference] = []
    gate_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    targets_checked = 0
    stars_checked = 0
    for target in astrometrics.targets.list():
        if only_targets and target.id not in only_targets:
            continue
        targets_checked += 1
        old_states = old_states_from_target(target)
        for pipeline, state in old_states.items():
            for gate_name, (status, _detail) in state.gates.items():
                gate_counts[f"{PIPELINE_METRIC_PREFIX[pipeline]}.gate_{gate_name}"][status] += 1
        try:
            report = wayfinder.review.get_advisory(kind="quality", target=target)
            differences += compare_target_pipelines(target.id, old_states, report.statements, index)
        except Exception as error:  # ruff: ignore[blind-except] - one bad target must not stop the run
            differences.append(
                Difference(target.id, UNATTRIBUTED, COULD_NOT_JUDGE, repr(error)[:120], "quality report")
            )
        stars_checked += _compare_target_stars(wayfinder, target, differences)
    return build_report(differences, targets_checked, stars_checked, unruled, gate_counts), differences


def _compare_target_stars(wayfinder: Any, target: Any, differences: list[Difference]) -> int:
    """Compare the spectra of one target's stars and add the differences.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The library handle.
    target : `Target`
        The target.
    differences : `list` [`Difference`]
        Appended to.

    Returns
    -------
    count : `int`
        How many stars with a spectrum were compared.
    """
    answer = wayfinder.astrometrics.stars.query(target_id=target.id, detail="objects", limit=None)
    count = 0
    for star in answer.objects or []:
        spectroscopy = star.spectroscopy
        if spectroscopy is None or not spectroscopy.intensities:
            continue
        count += 1
        try:
            statements = wayfinder.review.get_advisory(kind="result", target=target, object_id=star.id)
            differences += compare_star(target.id, star.id, spectroscopy, statements)
        except Exception as error:  # ruff: ignore[blind-except] - one bad star must not stop the run
            differences.append(
                Difference(target.id, STAR_PIPELINE, COULD_NOT_JUDGE, repr(error)[:120], f"star {star.id}")
            )
    return count


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser with ``--report`` and ``--target``.
    """
    parser = argparse.ArgumentParser(
        prog="shadow_compare_verdicts",
        description="Compare wayfindinglib rules with astrometricslib flags. Writes only the report.",
    )
    parser.add_argument("--report", required=True, type=Path, help="Markdown report file to write.")
    parser.add_argument("--target", action="append", help="Limit to this target id (repeatable).")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the comparison and write the report.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Arguments; defaults to the command line.

    Returns
    -------
    status : `int`
        0 on success.
    """
    arguments = _build_argument_parser().parse_args(argv)
    from wayfindinglib import Wayfinder

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    text, differences = run_comparison(Wayfinder(), arguments.target)
    arguments.report.write_text(text, encoding="utf-8")
    print(f"Wrote {arguments.report}: {len(differences)} differences.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
