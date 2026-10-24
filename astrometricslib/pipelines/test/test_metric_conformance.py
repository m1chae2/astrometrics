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
from collections.abc import Callable
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
# The ending a metric name must have for each unit, so a reader sees the unit
# in the name. A unitless metric (unit "") has no required ending.
UNIT_NAME_ENDING: dict[str, str] = {
    "adu": "_adu",
    "px": "_px",
    "arcsec": "_arcsec",
    "arcsec/px": "_arcsec_per_pixel",
    "fraction": "_fraction",
    "count": "_count",
    "degC": "_degc",
    "deg": "_deg",
    "s": "_s",
    "e-": "_e",
    "e-/s/px": "_e_per_s_per_px",
    "mag": "_mag",
    "angstrom": "_angstrom",
    "stack_unit": "_stack_unit",
    "stack_unit/adu": "_stack_unit_per_adu",
    "1/d": "_per_day",
}
# Names that are allowed to break the unit-ending rule, each with the reason.
# Keep this list short. A gate's metric (``<pipeline>.gate_<name>``) carries
# the gate's own name and the units of whatever number that gate quotes, so
# gates are exempt as a group (see `is_gate_metric`). The two slopes below are
# unitless in the registry because "percent per 1000 angstrom" is not a known
# unit; their names spell the unit out in full.
UNIT_ENDING_ALLOW_LIST: dict[str, str] = {
    "spectroscopy.gaia_xp_median_slope_percent_per_1000_angstrom": "unit is percent per 1000 angstrom",
    "spectroscopy.gaia_xp_median_absolute_slope_percent_per_1000_angstrom": (
        "unit is percent per 1000 angstrom"
    ),
    "spectroscopy.stage_post_processing_gaia_xp_slope_percent_per_1000_angstrom": (
        "unit is percent per 1000 angstrom"
    ),
}
# Pipeline folders that hold no pipeline of their own.
NON_PIPELINE_FOLDERS = frozenset({"shared", "test", "__pycache__"})


def _asteroid_detection_emitted_measurements() -> list[QualityMeasurement]:
    """Run the asteroid pipeline's record step on a fixed run.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for a run whose counts and gates are known.
    """
    from datetime import UTC, datetime

    from astrometricslib.pipelines.asteroid_detection.record_quality import (
        asteroid_run_gates,
        asteroid_run_measurements,
    )
    from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext

    counts = {
        "frames_with_wcs_estimate": 8,
        "frames_excluded_missing_pointing_metadata": 0,
        "candidates_detected": 5,
        "candidates_persistence_confirmed": 4,
        "candidates_rate_linearity_confirmed": 2,
        "candidates_ephemeris_matched": 2,
        "ephemeris_queries_attempted": 4,
        "ephemeris_queries_failed": 0,
        "residual_chains_tested": 3,
        "residual_chains_rejected": 1,
        "residual_chains_non_monotonic": 2,
        "residual_assumed_error_detections": 0,
        "residual_worst_accepted_ratio": 1.4,
    }
    context = PipelineRunContext(
        "M 99", "asteroid_detection", "1.1.0", "job-1", "job-1", datetime(2026, 10, 22, tzinfo=UTC)
    )
    return asteroid_run_measurements(counts, asteroid_run_gates(counts, 3, 0), context)


def _astrometry_emitted_measurements() -> list[QualityMeasurement]:
    """Run the astrometry pipeline's record step on a fixed run.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for a fully measured, solved run.
    """
    from astrometricslib.pipelines.astrometry.test.conformance_fixture import (
        astrometry_emitted_measurements,
    )

    return astrometry_emitted_measurements()


def _photometry_emitted_measurements() -> list[QualityMeasurement]:
    """Run the photometry pipeline's record step on a fixed, healthy run.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for a healthy run.
    """
    from astrometricslib.pipelines.photometry.test.conformance_fixture import (
        photometry_emitted_measurements,
    )

    return photometry_emitted_measurements()


def _spectroscopy_emitted_measurements() -> list[QualityMeasurement]:
    """Run the spectroscopy pipeline's record step on a fixed run.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for a healthy run.
    """
    from astrometricslib.pipelines.spectroscopy.test.conformance_fixture import (
        spectroscopy_emitted_measurements,
    )

    return spectroscopy_emitted_measurements()


def _stacking_emitted_measurements() -> list[QualityMeasurement]:
    """Run the stacking pipeline's record step on a fixed stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for a fully measured stack of two exposure
        groups.
    """
    from astrometricslib.pipelines.stacking.test.conformance_fixture import (
        stacking_emitted_measurements,
    )

    return stacking_emitted_measurements()


# Phase 3 (plan section 3.7): a pipeline listed here produces every metric it
# declares. Each entry maps the pipeline to a function that runs its record
# step on a fixed run and returns the measurements. A pipeline joins this
# table when its record step is built; the others are still on phase 2.
PHASE_3_RECORD_STEPS: dict[str, Callable[[], list[QualityMeasurement]]] = {
    "asteroid_detection": _asteroid_detection_emitted_measurements,
    "astrometry": _astrometry_emitted_measurements,
    "photometry": _photometry_emitted_measurements,
    "spectroscopy": _spectroscopy_emitted_measurements,
    "stacking": _stacking_emitted_measurements,
}


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


def is_gate_metric(name: str) -> bool:
    """Say whether a metric stands for a self-check gate.

    Parameters
    ----------
    name : `str`
        A registered metric name.

    Returns
    -------
    is_gate : `bool`
        `True` for ``<checkpoint>.gate_<gate name>``.
    """
    return name.split(".", 1)[-1].startswith("gate_")


def find_unit_ending_problems(specs: tuple[MetricSpec, ...]) -> list[str]:
    """Name each spec whose name does not end with its unit.

    Parameters
    ----------
    specs : `tuple` of `MetricSpec`
        The declarations to check.

    Returns
    -------
    problems : `list` of `str`
        One line per metric. A gate metric and the names in
        `UNIT_ENDING_ALLOW_LIST` are skipped. A unitless metric must not end
        with another unit's ending (it would claim a unit it does not have).
    """
    problems = []
    all_endings = tuple(UNIT_NAME_ENDING.values())
    for spec in specs:
        if is_gate_metric(spec.name) or spec.name in UNIT_ENDING_ALLOW_LIST:
            continue
        required = UNIT_NAME_ENDING.get(spec.unit)
        if required is not None and not spec.name.endswith(required):
            problems.append(f"{spec.name}: unit {spec.unit!r} needs the ending {required!r}")
        if spec.unit == "" and spec.name.endswith(all_endings):
            problems.append(f"{spec.name}: unitless, but the name ends with a unit")
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


def test_every_metric_name_ends_with_its_unit() -> None:
    """A name ends with its unit suffix, such as ``_px`` or ``_count``."""
    assert find_unit_ending_problems(get_metric_registry().all()) == []


def test_the_allow_list_names_are_registered_and_justified() -> None:
    """Each allow-listed name still exists and gives a reason."""
    registered = set(get_metric_registry().names())
    assert set(UNIT_ENDING_ALLOW_LIST) <= registered
    assert all(reason.strip() for reason in UNIT_ENDING_ALLOW_LIST.values())


def test_the_unit_ending_checker_goes_red_on_broken_input() -> None:
    """A name with the wrong ending is reported."""
    good = get_metric_registry().get("asteroid_detection.candidates_detected_count")
    assert find_unit_ending_problems((good,)) == []
    wrong_name = good.model_copy(update={"name": "asteroid_detection.candidates_detected"})
    assert find_unit_ending_problems((wrong_name,))
    wrong_unit = good.model_copy(update={"unit": "px"})
    assert find_unit_ending_problems((wrong_unit,))
    unitless_with_ending = good.model_copy(update={"unit": ""})
    assert find_unit_ending_problems((unitless_with_ending,))


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


def test_phase_3_pipelines_are_known_pipelines() -> None:
    """Only pipelines with a metrics file can be on the phase 3 list."""
    assert set(PHASE_3_RECORD_STEPS) <= set(PIPELINES_WITH_METRICS)


@pytest.mark.parametrize("pipeline", sorted(PHASE_3_RECORD_STEPS))
def test_a_phase_3_pipeline_emits_every_metric_it_declares(pipeline: str) -> None:
    """The record step emits each declared metric and nothing undeclared."""
    from astrometricslib.pipelines.shared.quality.metric_registry import load_pipeline_metrics

    declared = {spec.name for spec in load_pipeline_metrics(pipeline)}
    emitted = {measurement.metric for measurement in PHASE_3_RECORD_STEPS[pipeline]()}
    assert declared - emitted == set(), f"{pipeline} declares metrics its record step never emits"
    assert emitted - declared == set(), f"{pipeline} emits metrics it does not declare"


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
