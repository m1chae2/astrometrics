"""Conformance test: every declared quality metric is well formed and findable.

A metric that names a function that does not exist, a unit nobody knows, or a
register entry that is not there cannot be audited. These static checks keep
new declarations honest. They read the source with ``ast`` and the registry
itself, and do not depend on other tests. They cover the astrometricslib side
of the plan: the checks on rules (`requires`, action classes, purpose held by
rules) belong in ``wayfindinglib/test/`` once the rules exist.

The last tests run the checkers on deliberately broken input, so each checker
is shown to go red.
"""

import ast
import importlib
import re
from pathlib import Path

import pytest

from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.pipelines.shared.quality.metric_registry import (
    KNOWN_UNITS,
    PIPELINES_WITH_METRICS,
    get_metric_registry,
)

LIBRARY_ROOT = Path(__file__).resolve().parents[2]
PIPELINES_ROOT = LIBRARY_ROOT / "pipelines"
REGISTER_DIR = LIBRARY_ROOT.parent / "documentation" / "library_design" / "validation"
NAME_FORM = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z0-9_]*$")
REGISTER_ENTRY_HEADING = re.compile(r"^### ([A-Z]{3}-\d{2,3})\s*$", re.MULTILINE)
# Pipeline folders that hold no pipeline of their own.
NON_PIPELINE_FOLDERS = frozenset({"shared", "test", "__pycache__"})


def _resolve_callable(dotted_path: str) -> object:
    """Import a path relative to ``astrometricslib``; return its object.

    Parameters
    ----------
    dotted_path : `str`
        Module path followed by the attribute name.

    Returns
    -------
    target : `object`
        The named attribute.
    """
    module_path, _, attribute = dotted_path.rpartition(".")
    module = importlib.import_module(f"astrometricslib.{module_path}")
    return getattr(module, attribute)


def _register_entry_ids() -> set[str]:
    """Collect every entry id in the validation register.

    Returns
    -------
    entry_ids : `set` of `str`
        The ids, such as ``"STK-24"``.
    """
    entry_ids: set[str] = set()
    for path in REGISTER_DIR.glob("*.md"):
        entry_ids.update(REGISTER_ENTRY_HEADING.findall(path.read_text(encoding="utf-8")))
    return entry_ids


def find_missing_callables(specs: tuple[MetricSpec, ...]) -> list[str]:
    """Name each spec whose ``computed_by`` is not a real callable.

    Parameters
    ----------
    specs : `tuple` of `MetricSpec`
        The declarations to check.

    Returns
    -------
    problems : `list` of `str`
        One line per bad declaration.
    """
    problems = []
    for spec in specs:
        try:
            target = _resolve_callable(spec.computed_by)
        except ImportError, AttributeError, ValueError:
            problems.append(f"{spec.name}: cannot import {spec.computed_by}")
            continue
        if not callable(target):
            problems.append(f"{spec.name}: {spec.computed_by} is not callable")
    return problems


def find_bad_names_and_units(specs: tuple[MetricSpec, ...]) -> list[str]:
    """Name each spec with a bad name, bad prefix or unknown unit.

    Parameters
    ----------
    specs : `tuple` of `MetricSpec`
        The declarations to check.

    Returns
    -------
    problems : `list` of `str`
        One line per problem.
    """
    problems = []
    for spec in specs:
        if not NAME_FORM.match(spec.name):
            problems.append(f"{spec.name}: not <checkpoint>.<snake_case>")
        elif spec.name.split(".")[0] != spec.checkpoint.value:
            problems.append(f"{spec.name}: prefix does not match checkpoint {spec.checkpoint.value}")
        if spec.unit not in KNOWN_UNITS:
            problems.append(f"{spec.name}: unknown unit {spec.unit!r}")
    return problems


def find_unknown_register_entries(specs: tuple[MetricSpec, ...], known_entries: set[str]) -> list[str]:
    """Name each spec that cites a missing register entry.

    Parameters
    ----------
    specs : `tuple` of `MetricSpec`
        The declarations to check.
    known_entries : `set` of `str`
        The entry ids in the register.

    Returns
    -------
    problems : `list` of `str`
        One line per bad citation.
    """
    return [
        f"{spec.name}: register entry {spec.register_entry} not found"
        for spec in specs
        if spec.register_entry is not None and spec.register_entry not in known_entries
    ]


def find_literal_metric_names(source: str) -> list[int]:
    """Find ``QualityMeasurement`` calls whose metric is a string literal.

    Parameters
    ----------
    source : `str`
        Python source text.

    Returns
    -------
    line_numbers : `list` of `int`
        The line of each offending call.
    """
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if func_name != "QualityMeasurement":
            continue
        for keyword in node.keywords:
            if keyword.arg == "metric" and isinstance(keyword.value, ast.Constant):
                lines.append(node.lineno)
    return lines


def test_every_computed_by_resolves_to_a_callable() -> None:
    """Each declaration names a real function."""
    assert find_missing_callables(get_metric_registry().all()) == []


def test_names_follow_the_form_and_units_are_known() -> None:
    """Names are <checkpoint>.<snake_case> and units are known."""
    assert find_bad_names_and_units(get_metric_registry().all()) == []


def test_no_duplicate_names_in_the_declarations() -> None:
    """The declarations, before registration, hold no repeated name."""
    from astrometricslib.pipelines.shared.quality.metric_registry import load_pipeline_metrics
    from astrometricslib.pipelines.shared.quality.metrics import FRAME_METRICS

    declared = list(FRAME_METRICS)
    for pipeline in PIPELINES_WITH_METRICS:
        declared.extend(load_pipeline_metrics(pipeline))
    names = [spec.name for spec in declared]
    assert len(names) == len(set(names))


def test_every_pipeline_folder_is_listed_and_has_a_metrics_file() -> None:
    """The pipeline list matches the folders on disk; each has metrics.py."""
    on_disk = {
        path.name
        for path in PIPELINES_ROOT.iterdir()
        if path.is_dir() and path.name not in NON_PIPELINE_FOLDERS and (path / "__init__.py").exists()
    }
    assert on_disk == set(PIPELINES_WITH_METRICS)
    for pipeline in PIPELINES_WITH_METRICS:
        assert (PIPELINES_ROOT / pipeline / "metrics.py").is_file(), f"{pipeline} has no metrics.py"


def test_register_entries_cited_by_declarations_exist() -> None:
    """Every cited validation register entry exists."""
    assert find_unknown_register_entries(get_metric_registry().all(), _register_entry_ids()) == []


def test_validated_dataset_limits_name_a_register_entry_that_exists() -> None:
    """A validated-dataset `QualityLimit` must cite a register entry."""
    limit = QualityLimit(
        value=0.15,
        unit="fraction",
        tier=LimitTier.VALIDATED_DATASET,
        derivation="test",
        registerEntry="STK-24",
    )
    assert limit.register_entry in _register_entry_ids()
    with pytest.raises(ValueError, match="register_entry"):
        QualityLimit(value=1.0, unit="px", tier=LimitTier.VALIDATED_DATASET, derivation="test")


def test_emitters_use_name_constants_not_string_literals() -> None:
    """No library source builds a measurement from a string literal."""
    offenders = []
    for path in LIBRARY_ROOT.rglob("*.py"):
        if "test" in path.parts or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "QualityMeasurement" not in source:
            continue
        offenders.extend(f"{path}:{line}" for line in find_literal_metric_names(source))
    assert offenders == []


def test_not_measured_carries_a_reason_and_no_value() -> None:
    """A not_measured record needs a reason and no value."""
    subject = QualitySubject(level=SubjectLevel.FRAME, subjectId="t/frame.fits")
    common = {
        "metric": "frame.detected_region_count",
        "definitionVersion": 1,
        "subject": subject,
        "status": MeasurementStatus.NOT_MEASURED,
        "origin": MeasurementOrigin.MEASURED,
        "runId": "run-1",
        "measuredAt": "2026-10-21T00:00:00+00:00",
    }
    with pytest.raises(ValueError, match="reason"):
        QualityMeasurement(**common)
    with pytest.raises(ValueError, match="value None"):
        QualityMeasurement(**common, value=3.0, reason="no stars")
    assert QualityMeasurement(**common, reason="no stars").value is None


def test_checkers_go_red_on_broken_input() -> None:
    """Each checker reports a deliberately broken declaration or source."""
    good = get_metric_registry().all()[0]
    bad_path = good.model_copy(update={"computed_by": "pipelines.shared.quality.no_such_module.nothing"})
    assert find_missing_callables((bad_path,))
    bad_unit = good.model_copy(update={"unit": "furlongs"})
    assert find_bad_names_and_units((bad_unit,))
    bad_name = good.model_copy(update={"name": "Frame-Width"})
    assert find_bad_names_and_units((bad_name,))
    bad_entry = good.model_copy(update={"register_entry": "ZZZ-99"})
    assert find_unknown_register_entries((bad_entry,), _register_entry_ids())
    assert find_literal_metric_names('QualityMeasurement(metric="frame.detected_region_count")') == [1]
    assert find_literal_metric_names("QualityMeasurement(metric=FRAME_DETECTED_REGION_COUNT)") == []
