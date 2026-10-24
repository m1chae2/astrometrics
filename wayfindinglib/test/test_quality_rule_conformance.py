"""Purpose: Check the quality rule table against the registry and the register.

Description: The rule table (`RESULT_QUALITY_RULES`) reads metrics that
astrometricslib registers and cites validation register entries. These
checks keep the two sides in step, so a rule cannot depend on a metric that
was renamed, redefined or dropped, and a rule's confidence cannot differ
from the evidence behind it (plan section 3.7 and 6.7, decisions S12 and S13):

* every requirement names a registered metric at its current definition
  version, and no requirement is an exploratory metric;
* every warning and recommendation names an action class, and a constraint
  or a note about missing data names none;
* every rule's register entry exists in the validation register, and the
  rule's confidence equals the entry's status label;
* every decision metric is required by a rule or is on a commented allow-list
  that names the batch that will use it;
* every context metric states why it exists (the check that a report section
  shows it is in ``tasks/planning_tasks/test/test_target_report_context.py``);
* every exploratory metric states its question and a review date that has not
  passed;
* every item of the decision catalog has a rule or a deferred entry with a
  reason.

Every check that applies to a rule walks `ALL_RULES`, the one list that joins
both rule tables (the data-quality table and the per-star result table), so a
new table cannot escape the checks. The module of each rule that another module
computes (``computed_by``) is also scanned as text: a metric name it reads must
not be an exploratory metric, even when no rule row requires it.

The register files are read from disk, so a rule whose entry has not been
written yet fails here until the entry exists.
"""

import re
from datetime import date
from pathlib import Path

import pytest

from astrometricslib import Astrometrics, MetricPurpose, MetricSpec
from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES
from wayfindinglib.analytics.result_quality.judge_results import PLAIN_RULE_NAMES
from wayfindinglib.analytics.result_quality.rules import DEFERRED_RULES, RESULT_QUALITY_RULES
from wayfindinglib.models.quality.statements import ActionClass, Confidence, StatementKind

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTER_DIR = REPO_ROOT / "documentation" / "library_design" / "validation"

# The modules that make the statements of the rules with ``computed_by`` set.
# Each is built by the batch named here (issue 95).
KNOWN_COMPUTING_MODULES = {
    "noise_growth": "batch 2",
    "efficiency": "batch 2",
    "focus_drift": "batch 1",
    "target_report_tasks": "batch 6a",
    "report_rules": "batch 6a",
    "recommend_for_guiding": "issue 95 wave D1",
    "recommend_for_capture": "issue 95 wave D1",
    "recommend_for_sky": "issue 95 wave D1",
}

# The items of the decision catalog (plan section 6.6) and the rule or
# deferred entry that covers each.
DECISION_CATALOG = {
    "Overexposed setting": "overexposed_setting",
    "Underexposed setting": "underexposed_setting",
    "Offset too low": "offset_too_low",
    "Guiding limits exposure": "guiding_limits_exposure",
    "Frames lost": "frames_lost",
    "More frames": "more_frames",
    "More frames will not help": "more_frames_will_not_help",
    "Flat noise limits the stack": "flat_noise_limits_stack",
    "Focus drift": "focus_drift",
    "Calibration gap": "calibration_gap",
    "Sky cost": "sky_cost",
    "Altitude cost": "altitude_cost",
    "Best window": "best_window",
    "Period alias": "period_alias",
    "Spectrum SNR": "spectrum_snr",
    "Asteroid linking": "asteroid_linking",
    "Polar alignment": "polar_alignment",
    "Tilt": "tilt",
}

# Decision metrics that no rule of the table requires, each with the batch or
# issue that will use it. A metric whose name contains ".gate_" is a self-check
# that its own pipeline judges (the check outcome is stored on the row); no
# rule of this table compares them with a limit of its own, and the view of
# those outcomes belongs to issue 97, so they are listed once by pattern (see
# `SELF_CHECK_MARKER`) and not by name.
UNUSED_DECISION_METRICS = {
    "frame.background_level_adu": (
        "issue 97: input of the stacker's cloud check (STK-04). The stacker judges it and moves the frame; "
        "the report counts the frames moved (rule frames_lost, through the quarantine rows). A rule that "
        "reads the level itself would repeat the stacker's check."
    ),
    "frame.saturated_pixel_fraction": (
        "issue 97: input of the stacker's raw-frame checks, which stack the outcome as a gate row. "
        "Showing gate outcomes of every pipeline is the pipeline-quality view of issue 97."
    ),
    "frame.star_width_gaussian_brightest_px": (
        "issue 97: input of the stack sharpness check (STK-25), judged by the stacker. The report's "
        "sharpness trade-off reads the Moffat width in arcseconds, not this width."
    ),
    "stacking.rejected_pixel_fraction": (
        "issue 97: input of the stacker's rejection check (STK-24), judged by the stacker; its gate "
        "outcome belongs to the pipeline-quality view of issue 97."
    ),
    "astrometry.gaia_fit_rms_arcsec": (
        "issue 96: input of the astrometry self-check (AST-02), judged in astrometricslib."
    ),
    "astrometry.plate_scale_arcsec_per_pixel": (
        "issue 96: input of the astrometry self-check (AST-04), judged in astrometricslib."
    ),
    "astrometry.star_width_gaussian_px": (
        "issue 96: input of the astrometry self-check (AST-04), judged in astrometricslib."
    ),
}

# The marker in the name of a metric that is the stored outcome of a pipeline's
# own self-check. These decision metrics are judged by their pipeline when it
# runs, so a rule of this table does not compare them with a limit of its own.
SELF_CHECK_MARKER = ".gate_"

# Metrics, of any purpose, that code of a later batch reads directly. They are
# listed so a reader can see who uses a metric the rule table does not require.
# The names are checked against the registry.
READ_BY_LATER_BATCHES = {
    "frame.star_width_effective_px": "batch 2: efficiency seeing factor",
    "frame.star_width_effective_arcsec": "batch 2: efficiency seeing factor and physical reference",
    "frame.sky_rate_e_per_s_per_px": "batch 2: efficiency sky factor, noise growth and the sky model",
    "frame.sky_noise_e": "batch 2: noise regime and starting point (batch 6a)",
    "frame.read_noise_e": "batch 2: noise regime and starting point (batch 6a)",
    "stacking.empty_aperture_noise_stack_unit": "batches 6a and 6b: noise growth and the forecast check",
    "stacking.empty_aperture_noise_normalized": "batches 6a and 6b: noise growth and the forecast check",
    "stacking.effective_time_s": "batches 6a and 6b: noise growth and the forecast check",
    "stacking.effective_time_read_noise_s": "batches 6a and 6b: noise growth floor fit",
    "stacking.effective_frame_count": "batch 2: noise growth",
    "stacking.frame_weight": "batch 2: noise growth weights",
}


@pytest.fixture(scope="module")
def registry() -> dict[str, MetricSpec]:
    """Read the metric registry through the public API.

    Returns
    -------
    registry : `dict` [`str`, `MetricSpec`]
        Every registered metric, by name.
    """
    return {spec.name: spec for spec in Astrometrics().quality.metrics(detail="full")}


@pytest.fixture(scope="module")
def register_statuses() -> dict[str, Confidence]:
    """Read the status of every register entry and design-estimate row.

    Returns
    -------
    statuses : `dict` [`str`, `Confidence`]
        The confidence each entry ID gives, taken from its status label. A
        design-estimate row such as ``WAY-D05`` gives ``design_estimate``.
    """
    labels = {
        "Measured": Confidence.MEASURED,
        "Partly measured": Confidence.PARTLY_MEASURED,
        "Design estimate": Confidence.DESIGN_ESTIMATE,
    }
    statuses: dict[str, Confidence] = {}
    for path in sorted(REGISTER_DIR.glob("*.md")):
        text = path.read_text()
        for match in re.finditer(r"^\| ([A-Z]{3}-D\d+) \|", text, re.MULTILINE):
            statuses[match.group(1)] = Confidence.DESIGN_ESTIMATE
        parts = re.split(r"^### ([A-Z]{3}-\d{2,3})\s*$", text, flags=re.MULTILINE)
        for identifier, body in zip(parts[1::2], parts[2::2], strict=True):
            status = re.search(r"\*\*Status:\*\* (Measured|Partly measured|Design estimate)\b", body)
            if status:
                statuses[identifier] = labels[status.group(1)]
    return statuses


def test_rule_names_are_unique() -> None:
    """No two rules, and no rule and deferred entry, share a name."""
    names = [rule.name for rule in ALL_RULES] + [deferred.name for deferred in DEFERRED_RULES]
    assert len(names) == len(set(names))


def test_every_requirement_names_a_registered_metric_at_its_current_version(
    registry: dict[str, MetricSpec],
) -> None:
    """A requirement pins the version the registry holds now (decision S13)."""
    for rule in ALL_RULES:
        for requirement in rule.requires:
            assert requirement.name in registry, f"{rule.name} reads unregistered {requirement.name}"
            current = registry[requirement.name].definition_version
            assert requirement.definition_version == current, (
                f"{rule.name} pins {requirement.name} at version {requirement.definition_version}; "
                f"the registry is at {current}. Check what changed, then move the pin."
            )


def test_no_requirement_is_an_exploratory_metric(registry: dict[str, MetricSpec]) -> None:
    """An exploratory metric never drives a rule (decision S12)."""
    for rule in ALL_RULES:
        for requirement in rule.requires:
            assert registry[requirement.name].purpose != MetricPurpose.EXPLORATORY, (
                f"{rule.name} reads the exploratory metric {requirement.name}"
            )


MISSING_DATA_ROWS = frozenset({"efficiency_reference_missing"})
"""Rows of the report table that are ``insufficient_data`` by design. The
judge makes the missing-data statement of every other rule from that rule's own
row. A setting with no efficiency reference has no rule of its own to speak
for it, so it has this row (register row WAY-D81)."""


def test_action_classes_fit_the_statement_kind() -> None:
    """Warnings and recommendations name a class; the others name none."""
    for rule in ALL_RULES:
        if rule.statement_kind in (StatementKind.WARNING, StatementKind.RECOMMENDATION):
            assert rule.action_class in set(ActionClass), rule.name
        else:
            assert rule.action_class is None, rule.name
        if rule in RESULT_QUALITY_RULES and rule.name not in MISSING_DATA_ROWS:
            assert rule.statement_kind != StatementKind.INSUFFICIENT_DATA, "missing data is made by the judge"


def test_every_rule_register_entry_exists_and_confidence_matches(
    register_statuses: dict[str, Confidence],
) -> None:
    """A rule's confidence is the status label of its register entry."""
    for rule in ALL_RULES:
        assert rule.register_entry in register_statuses, (
            f"{rule.name}: register entry {rule.register_entry} is not in the validation register"
        )
        assert rule.confidence == register_statuses[rule.register_entry], (
            f"{rule.name}: confidence {rule.confidence.value} does not match the status of "
            f"{rule.register_entry} ({register_statuses[rule.register_entry].value})"
        )


def test_every_limit_register_entry_exists(register_statuses: dict[str, Confidence]) -> None:
    """A limit that names a register entry names one that exists."""
    for rule in ALL_RULES:
        for limit in rule.limits:
            if limit.register_entry:
                assert limit.register_entry in register_statuses, f"{rule.name}: {limit.register_entry}"


def test_computed_by_names_a_known_module() -> None:
    """A rule that another module computes names that module."""
    for rule in ALL_RULES:
        if rule.computed_by is not None:
            assert rule.computed_by in KNOWN_COMPUTING_MODULES, rule.name


def _module_source(module_name: str) -> str:
    """Read the source text of one library module, found by its file name.

    Parameters
    ----------
    module_name : `str`
        The module's file name without ``.py``, such as ``"focus_drift"``.

    Returns
    -------
    source : `str`
        The text of the one non-test file of that name under ``wayfindinglib``.
    """
    paths = [
        path
        for path in (REPO_ROOT / "wayfindinglib").rglob(f"{module_name}.py")
        if "test" not in path.relative_to(REPO_ROOT).parts
    ]
    assert len(paths) == 1, f"{module_name}: expected one file, found {paths}"
    return paths[0].read_text()


def test_computing_modules_read_no_exploratory_metric(registry: dict[str, MetricSpec]) -> None:
    """A module that makes a rule statement reads no exploratory metric (S12).

    The rule row lists the metrics it requires, but a module can read another
    one in its code. This check finds every registered metric name written as
    a string in the module and fails for an exploratory one.
    """
    pattern = re.compile(r"[\"']([a-z]+\.[a-z0-9_]+)[\"']")
    for rule in ALL_RULES:
        if rule.computed_by is None:
            continue
        names_in_code = {
            match.group(1)
            for match in pattern.finditer(_module_source(rule.computed_by))
            if match.group(1) in registry
        }
        for name in sorted(names_in_code):
            assert registry[name].purpose != MetricPurpose.EXPLORATORY, (
                f"{rule.computed_by} (rule {rule.name}) reads the exploratory metric {name}"
            )


def test_plain_rules_are_the_judged_rules() -> None:
    """Exactly the rules with no ``computed_by`` are judged here."""
    plain = {rule.name for rule in RESULT_QUALITY_RULES if rule.computed_by is None}
    assert plain == PLAIN_RULE_NAMES


def test_every_decision_metric_is_required_or_allow_listed(registry: dict[str, MetricSpec]) -> None:
    """A decision metric feeds a rule or is on the allow-list."""
    required = {requirement.name for rule in RESULT_QUALITY_RULES for requirement in rule.requires}
    for name, reason in UNUSED_DECISION_METRICS.items():
        assert name in registry, f"allow-list names unregistered {name}"
        assert reason.strip()
        assert registry[name].purpose == MetricPurpose.DECISION, f"{name} is not a decision metric"
        assert SELF_CHECK_MARKER not in name
    for name, spec in registry.items():
        if spec.purpose != MetricPurpose.DECISION or name in required or SELF_CHECK_MARKER in name:
            continue
        assert name in UNUSED_DECISION_METRICS, (
            f"decision metric {name} is required by no rule and is not on the allow-list"
        )


def test_later_batch_metrics_are_registered(registry: dict[str, MetricSpec]) -> None:
    """The later-batch list names registered metrics, each with a user."""
    for name, user in READ_BY_LATER_BATCHES.items():
        assert name in registry, name
        assert user.strip()


def test_every_context_metric_states_a_reason(registry: dict[str, MetricSpec]) -> None:
    """A context metric explains why it is kept."""
    for spec in registry.values():
        if spec.purpose == MetricPurpose.CONTEXT:
            assert spec.purpose_reason.strip(), spec.name


def test_every_exploratory_metric_has_a_question_and_a_future_review_date(
    registry: dict[str, MetricSpec],
) -> None:
    """An exploratory metric is promoted or removed by its review date."""
    for spec in registry.values():
        if spec.purpose == MetricPurpose.EXPLORATORY:
            assert spec.exploratory_question.strip(), spec.name
            assert spec.review_date is not None and spec.review_date >= date.today(), (
                f"{spec.name} is past its review date {spec.review_date}: promote it or remove it"
            )


def test_every_catalog_item_has_a_rule_or_a_deferred_entry() -> None:
    """Each catalog item is a rule or is deferred with a reason."""
    rule_names = {rule.name for rule in ALL_RULES}
    deferred = {entry.name: entry for entry in DEFERRED_RULES}
    for item, name in DECISION_CATALOG.items():
        assert name in rule_names or name in deferred, f"catalog item {item!r} has no rule or deferred entry"
        if name in deferred:
            assert deferred[name].reason.strip() and deferred[name].unblocked_by.strip()
    for entry in DEFERRED_RULES:
        assert entry.name not in rule_names


def test_deferred_rules_do_not_hide_a_usable_metric(registry: dict[str, MetricSpec]) -> None:
    """The metrics that block deferred rules are still exploratory."""
    blockers = (
        "stacking.frame_registration_rotation_deg",
        "frame.star_width_center_px",
        "photometry.catalog_zero_point_gaia_g_mag",
    )
    for name in blockers:
        assert registry[name].purpose == MetricPurpose.EXPLORATORY, (
            f"{name} was promoted; write the deferred rule that waits for it"
        )
