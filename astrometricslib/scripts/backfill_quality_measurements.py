r"""Copy the stored quality summaries into the quality measurement table.

Every target's saved document (``targets.data_json`` in ``astrometrics.db``)
holds the quality summary of the latest run of each pipeline: stacking and
spectral stacking, astrometry, photometry, spectroscopy and asteroid
detection. Before the quality measurement table existed, these summaries were
the only record of how a run went. This script reads them and writes each
number, and each self-check (gate), as a row in the table, so the history
starts with what the library already knows.

Check first, then apply::

    python -m astrometricslib.scripts.backfill_quality_measurements

    python -m astrometricslib.scripts.backfill_quality_measurements --apply

To try it on a copy of the database, add ``--database /path/to/copy.db``.

What the script does
--------------------

* It reads the raw JSON, not the data models. A summary saved in an old shape
  must not be rewritten through today's validators. One table in this file,
  per pipeline, says which stored field fills which registered metric.
* Every row has ``origin = backfilled``. The pipeline version is the one the
  summary stored, or empty if it stored none. Nothing is guessed.
* The row's time is the summary's ``createdAt``. If the summary has none, the
  target's newest frame time is used and the row's reason says so. If the
  target has neither, the summary is skipped and counted. The clock is never
  read.
* A row keeps the definition version the metric is registered with only if the
  stored value was made by that estimator. The pipeline version numbers are
  coarse, so the script keeps a short table of the estimator changes that the
  code documents (``ESTIMATOR_CHANGED_AT``). A metric in that table is
  written only when the summary's pipeline version is known and at least the
  version that introduced the estimator. Every other metric is written for
  any version.
* Each row's reason says it was backfilled, from which summary, and which
  definition version and pipeline version it carries. That is the tag that
  tells a reader which estimator made the number.
* The run id is ``backfill-<target>-<summary createdAt>``, so a second run
  writes nothing new.
* Gates become self-check rows (outcome, number and limit) like the rows the
  pipelines now write.
* Values that sit on a stack (the stack's own numbers, and astrometry's) are
  filed under the stack's subject, ``<target>/<stack file name>``, the same
  subject a new run uses. The file name comes from the target's stored stacked
  image.

What it does not do
-------------------

* It does not copy frame-level measurements. The summaries are run and stack
  level. The stored per-frame fields ``registrationRmse`` and
  ``registrationFwhmYPx`` hold Siril's normalized background level and the
  weighted star width, not what their names say, so they are never copied.
* It does not write measurements that need data the JSON never held:
  empty-aperture noise, frame weights, effective counts, night shares,
  photometry's comparison-star and centroid scatter, and a few gates whose
  steady number needs the run's own data. These are skipped and counted, per
  pipeline, in the report.
* A field that is absent from a summary is skipped and counted. It is never
  written as a "not measured" row, because for an old summary the field was
  never recorded, which is different from a run that measured nothing.

Safety
------

Nothing is written without ``--apply``. ``--apply`` first copies the database
to a timestamped ``.bak`` file (an integrity-checked snapshot) and refuses to
write if the copy fails. After writing, the script reads the rows back and
checks that every planned row is stored with the value the JSON holds. A
mismatch is reported and the exit code is 1.
"""

import argparse
import json
import logging
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from astrometricslib import configure_logging
from astrometricslib.drivers.local_database import backup_catalog_database, snapshot_sqlite_database
from astrometricslib.drivers.quality_store import QualityStore, quality_database_path
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import InvalidArgumentError, StorageError
from astrometricslib.models.quality.gate_result import GateResult, GateStatus
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
    gate_measurement,
)
from astrometricslib.pipelines.shared.quality.record_measurements import (
    check_measurement_against_registry,
    record_measurements,
)
from astrometricslib.pipelines.spectroscopy.metrics import (
    GAIA_XP_BAND_EDGES_ANGSTROM,
    STAGE_METRIC_KEYS,
    STAGE_NAMES,
    gaia_xp_band_metric_name,
    stage_metric_name,
    stage_spectrum_count_name,
)

logger = logging.getLogger(__name__)

# The prefix of every run id this script writes. A run id made here can never
# equal a job id or a generated ``run-`` id.
BACKFILL_RUN_ID_PREFIX = "backfill-"

# The estimator changes the code documents, as the first pipeline version
# whose summaries hold the metric as it is registered today. Every other
# metric has had one estimator since it was first stored, so it is written for
# any pipeline version. The astrometry version 1.4.0 note in
# `models/quality/quality_summary.py` says: the residual gate judges the plate
# solver's own fit when it reports one, and Gaia positions carry proper motion
# to the observation epoch. So the residual gate, and the four numbers that
# compare detected stars with Gaia positions, mean something different before
# 1.4.0. The pipeline versions have moved in large steps (for example
# astrometry went from 1.2.0 to 1.4.0 in one commit), so this table lists only
# what the code says. A change it does not say is not covered, and the
# validation register is where such a change would be recorded.
ESTIMATOR_CHANGED_AT: dict[str, str] = {
    "astrometry.gate_astrometric_residual": "1.4.0",
    "astrometry.gaia_fit_star_count": "1.4.0",
    "astrometry.gaia_fit_rms_arcsec": "1.4.0",
    "astrometry.gaia_fit_rms_before_arcsec": "1.4.0",
    "astrometry.wcs_refinement_shift_arcsec": "1.4.0",
}

# The text of a stored time that is read as UTC because it has no zone.
_NAIVE_TIME_NOTE = "stored without a time zone, read as UTC"


class SkipReason(StrEnum):
    """Why a value or gate was not copied into the table."""

    NOT_HELD = "not_held"
    FIELD_ABSENT = "field_absent"
    NOT_A_MEASUREMENT = "not_a_measurement"
    BAD_VALUE = "bad_value"
    BAD_GATE = "bad_gate"
    UNKNOWN_GATE = "unknown_gate"
    NEEDS_RUN_DATA = "needs_run_data"
    NO_STACK_FILE = "no_stack_file"
    NO_TIME = "no_time"
    ESTIMATOR_UNKNOWN = "estimator_unknown"
    ESTIMATOR_OLDER = "estimator_older"
    DEFAULT_ZERO = "default_zero"
    DUPLICATE_SUBJECT = "duplicate_subject"
    REFUSED = "refused"
    UNREADABLE_TARGET = "unreadable_target"


SKIP_DESCRIPTIONS: dict[SkipReason, str] = {
    SkipReason.NOT_HELD: "needs data the stored JSON never held",
    SkipReason.FIELD_ABSENT: "the stored summary has no value for the field",
    SkipReason.NOT_A_MEASUREMENT: "the stored value is the pipeline's marker for no measurement",
    SkipReason.BAD_VALUE: "the stored value is not a finite number",
    SkipReason.BAD_GATE: "the stored gate could not be read",
    SkipReason.UNKNOWN_GATE: "the stored gate has no declared metric",
    SkipReason.NEEDS_RUN_DATA: "the gate's steady number needs run data the summary does not hold",
    SkipReason.NO_STACK_FILE: "the target stores no stacked image name to file the value under",
    SkipReason.NO_TIME: "the summary has no time and neither has the target's frames",
    SkipReason.ESTIMATOR_UNKNOWN: "the pipeline version is unknown and the metric's estimator changed",
    SkipReason.ESTIMATOR_OLDER: "the summary is older than the metric's current estimator",
    SkipReason.DEFAULT_ZERO: "the star counts are all zero, as a path that does not count them stores",
    SkipReason.DUPLICATE_SUBJECT: "two stored values would share one subject and metric",
    SkipReason.REFUSED: "the metric registry refused the row",
    SkipReason.UNREADABLE_TARGET: "the target's JSON could not be read",
}


class SubjectKind(StrEnum):
    """Which subject a stored value is filed under."""

    STACK = "stack"
    RUN = "run"


@dataclass(frozen=True)
class ValueMapping:
    """One stored field and the registered metric it fills.

    Parameters
    ----------
    metric : `str`
        The registered metric name.
    paths : `tuple` [`tuple` [`str`, ...], ...]
        Where the value is in the summary, as keys from the summary's root.
        The first path with a value that is not null is used. A later path
        holds an older name of the same field.
    subject : `SubjectKind`
        Whether the value is about the stack or the run.
    divisor : `float`, optional
        The stored number divided by this is the metric's value (a stored
        percent becomes a fraction).
    sample_count_path : `tuple` [`str`, ...], optional
        Where the number of samples behind the value is stored.
    """

    metric: str
    paths: tuple[tuple[str, ...], ...]
    subject: SubjectKind
    divisor: float = 1.0
    sample_count_path: tuple[str, ...] | None = None


@dataclass(frozen=True)
class GateMapping:
    """One gate and how its limit was obtained.

    The metric that stands for the gate is ``<pipeline>.gate_<gate name>``.

    Parameters
    ----------
    gate_name : `str`
        The gate's name as stored in the summary's ``gates`` list.
    tier : `LimitTier`
        How the gate's limit was obtained.
    register_entry : `str`, optional
        The validation register entry that backs the limit.
    register_from_metric : `bool`, optional
        Take the register entry from the metric's declaration instead.
    """

    gate_name: str
    tier: LimitTier
    register_entry: str | None = None
    register_from_metric: bool = False


GateAdjuster = Callable[[GateResult, Mapping[str, Any]], GateResult | None]
ExtraBuilder = Callable[["SummaryPlanner"], None]


@dataclass(frozen=True)
class SummaryKind:
    """Where one pipeline's summary is stored and what to copy from it.

    Parameters
    ----------
    key : `str`
        A short name used in the report and in the run id lookup.
    label : `str`
        The name used in a row's reason.
    pipeline_name : `str`
        The pipeline, as it appears in a run subject and a metric name.
    location : `tuple` [`str`, ...]
        The summary's keys in the target document.
    flat_keys : `tuple` [`str`, ...]
        Names the summary had at the top of the document before the document
        was nested.
    metrics_key : `str`
        The key of the summary's own metrics object.
    stack_image_paths : `tuple` [`tuple` [`str`, ...], ...]
        Where the target stores the stack's file path, newest name first.
    values : `tuple` [`ValueMapping`, ...]
        The plain fields.
    gates : `tuple` [`GateMapping`, ...]
        The gates.
    extra_metrics : `tuple` [`str`, ...]
        Metrics the ``extras`` function fills.
    not_held : `tuple` [`str`, ...]
        Registered metrics the stored JSON never held.
    gates_on_stack : `bool`, optional
        File the gates under the stack's subject instead of the run's.
    extras : `Callable`, optional
        Fills the metrics that need a list or a table.
    gate_adjuster : `Callable`, optional
        Gives the gate as the pipeline's measurement step would store it.
    """

    key: str
    label: str
    pipeline_name: str
    location: tuple[str, ...]
    flat_keys: tuple[str, ...]
    metrics_key: str
    stack_image_paths: tuple[tuple[str, ...], ...]
    values: tuple[ValueMapping, ...]
    gates: tuple[GateMapping, ...]
    extra_metrics: tuple[str, ...] = ()
    not_held: tuple[str, ...] = ()
    gates_on_stack: bool = False
    extras: ExtraBuilder | None = None
    gate_adjuster: GateAdjuster | None = None


def dig(document: Any, *path: str) -> Any:
    """Follow a path of keys into nested dictionaries.

    Parameters
    ----------
    document : `Any`
        A dictionary, or anything else.
    *path : `str`
        The keys to follow.

    Returns
    -------
    value : `Any`
        The value found, or `None` if any step is missing or not a dictionary.
    """
    for key in path:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def first_present(document: Any, paths: Sequence[tuple[str, ...]]) -> Any:
    """Read the first path that holds a value that is not null.

    Parameters
    ----------
    document : `Any`
        The summary.
    paths : `Sequence` [`tuple` [`str`, ...]]
        The paths to try, in order.

    Returns
    -------
    value : `Any`
        The first value that is not `None`, or `None` if there is none.
    """
    for path in paths:
        value = dig(document, *path)
        if value is not None:
            return value
    return None


def as_number(value: Any) -> float | None:
    """Read a stored value as a finite number.

    Parameters
    ----------
    value : `Any`
        A value from the JSON.

    Returns
    -------
    number : `float` or `None`
        The value as a `float`, or `None` if it is not a finite number. A
        true or false value is not a number here.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_version(text: Any) -> tuple[int, ...] | None:
    """Read a dotted version such as ``"1.4.0"``.

    Parameters
    ----------
    text : `Any`
        The stored version.

    Returns
    -------
    version : `tuple` [`int`, ...] or `None`
        The numbers, or `None` if the text is not a dotted list of integers.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def estimator_problem(metric: str, pipeline_version: str | None) -> SkipReason | None:
    """Say whether a summary's number came from a different estimator.

    Parameters
    ----------
    metric : `str`
        The metric name.
    pipeline_version : `str` or `None`
        The pipeline version the summary stored.

    Returns
    -------
    reason : `SkipReason` or `None`
        `None` if the number may be written under the registered definition.
        Otherwise why not: the version is unknown, or older than the one that
        introduced the current estimator.
    """
    introduced = ESTIMATOR_CHANGED_AT.get(metric)
    if introduced is None:
        return None
    stored = parse_version(pipeline_version)
    if stored is None:
        return SkipReason.ESTIMATOR_UNKNOWN
    required = parse_version(introduced)
    if required is not None and stored < required:
        return SkipReason.ESTIMATOR_OLDER
    return None


def file_name_of(path_text: Any) -> str | None:
    """Take the file name from a stored path of either slash style.

    Parameters
    ----------
    path_text : `Any`
        A stored path.

    Returns
    -------
    name : `str` or `None`
        The last part of the path, or `None` if there is no text.
    """
    if not isinstance(path_text, str) or not path_text.strip():
        return None
    return re.split(r"[\\/]", path_text.strip())[-1] or None


def read_stored_time(text: Any) -> tuple[datetime, str] | None:
    """Read a stored ISO time.

    Parameters
    ----------
    text : `Any`
        The stored ``createdAt``.

    Returns
    -------
    result : `tuple` [`datetime`, `str`] or `None`
        The time with a time zone and a note ("" or a note that the zone was
        assumed), or `None` if the text is not a time.
    """
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return moment.replace(tzinfo=UTC), _NAIVE_TIME_NOTE
    return moment, ""


def newest_frame_time(document: Mapping[str, Any]) -> datetime | None:
    """Find the time of the target's newest stored frame.

    Parameters
    ----------
    document : `dict`
        The target document.

    Returns
    -------
    moment : `datetime` or `None`
        The newest frame ``timestamp`` (seconds since the Unix epoch) as a UTC
        time, or `None` if no frame has one.
    """
    stamps = []
    for frame in document.get("frames") or []:
        stamp = as_number(frame.get("timestamp")) if isinstance(frame, dict) else None
        if stamp is not None and stamp > 0:
            stamps.append(stamp)
    if not stamps:
        return None
    try:
        return datetime.fromtimestamp(max(stamps), tz=UTC)
    except OverflowError, OSError, ValueError:
        return None


@dataclass
class SummaryPlan:
    """The rows one stored summary will give.

    Attributes
    ----------
    target_id : `str`
        The target.
    kind : `SummaryKind`
        The kind of summary.
    run_id : `str`
        The run id of every row.
    measurements : `list` [`QualityMeasurement`]
        The rows, ready for `record_measurements`.
    skipped : `collections.Counter`
        How many values were skipped, by reason.
    """

    target_id: str
    kind: SummaryKind
    run_id: str
    measurements: list[QualityMeasurement] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)


class SummaryPlanner:
    """Turns one stored summary into measurements.

    Parameters
    ----------
    kind : `SummaryKind`
        The kind of summary.
    target_id : `str`
        The target.
    summary : `dict`
        The summary as stored.
    measured_at : `datetime`
        The row time, with a time zone.
    time_note : `str`
        A phrase saying where the time came from.
    run_id : `str`
        The run id of every row.
    stack_file_name : `str` or `None`
        The stack's file name, if the target stores it.

    Attributes
    ----------
    plan : `SummaryPlan`
        The plan being built.
    summary : `dict`
        The summary as stored.
    pipeline_version : `str` or `None`
        The pipeline version the summary stored.
    """

    def __init__(
        self,
        kind: SummaryKind,
        target_id: str,
        summary: dict[str, Any],
        measured_at: datetime,
        time_note: str,
        run_id: str,
        stack_file_name: str | None,
    ) -> None:
        """Set up the plan for one summary.

        Parameters
        ----------
        kind : `SummaryKind`
            The kind of summary.
        target_id : `str`
            The target.
        summary : `dict`
            The summary as stored.
        measured_at : `datetime`
            The row time, with a time zone.
        time_note : `str`
            A phrase saying where the time came from.
        run_id : `str`
            The run id of every row.
        stack_file_name : `str` or `None`
            The stack's file name, if the target stores it.
        """
        stored_version = summary.get("pipelineVersion")
        self.pipeline_version: str | None = stored_version if isinstance(stored_version, str) else None
        self.kind = kind
        self.summary = summary
        self.time_note = time_note
        self.stack_file_name = stack_file_name
        self.plan = SummaryPlan(target_id, kind, run_id)
        self._registry = get_metric_registry()
        self._context = PipelineRunContext(
            target_id=target_id,
            pipeline_name=kind.pipeline_name,
            pipeline_version=self.pipeline_version or "",
            run_id=run_id,
            job_id=None,
            measured_at=measured_at,
        )
        self._keys: set[tuple[str, str, str, str]] = set()

    def skip(self, reason: SkipReason, count: int = 1) -> None:
        """Count values that were not copied.

        Parameters
        ----------
        reason : `SkipReason`
            Why.
        count : `int`, optional
            How many values.
        """
        self.plan.skipped[reason] += count

    def run_subject(self) -> QualitySubject:
        """Give the subject of the whole run.

        Returns
        -------
        subject : `QualitySubject`
            Level ``run``, the same subject a new run of this pipeline uses.
        """
        return self._context.run_subject()

    def stack_subject(self, file_name: str | None = None) -> QualitySubject | None:
        """Give the subject of a stack.

        Parameters
        ----------
        file_name : `str`, optional
            The stack file's name. The target's stack is used if omitted.

        Returns
        -------
        subject : `QualitySubject` or `None`
            Level ``stack``, or `None` if the target stores no stack name.
        """
        name = file_name or self.stack_file_name
        return None if name is None else self._context.stack_subject(name)

    def subject_for(self, kind: SubjectKind) -> QualitySubject | None:
        """Give the subject of a kind.

        Parameters
        ----------
        kind : `SubjectKind`
            Stack or run.

        Returns
        -------
        subject : `QualitySubject` or `None`
            The subject, or `None` for a stack when the target stores no stack
            name.
        """
        return self.run_subject() if kind is SubjectKind.RUN else self.stack_subject()

    def _reason(self, spec: MetricSpec, existing: str) -> str:
        """Write the row's reason, which tags its estimator and source.

        Parameters
        ----------
        spec : `MetricSpec`
            The metric's declaration.
        existing : `str`
            The reason the row already has (a gate's sentence).

        Returns
        -------
        reason : `str`
            The existing sentence followed by the backfill tag.
        """
        tag = (
            f"Backfilled from the stored {self.kind.label} summary ({self.time_note}); "
            f"estimator as registered, definition version {spec.definition_version}; "
            f"pipeline version {self.pipeline_version or 'unknown'}."
        )
        return f"{existing} {tag}" if existing.strip() else tag

    def _accept(self, measurement: QualityMeasurement, spec: MetricSpec) -> None:
        """Mark a built row as backfilled and keep it if it is allowed.

        Parameters
        ----------
        measurement : `QualityMeasurement`
            The row, built with a measured origin.
        spec : `MetricSpec`
            The metric's declaration.
        """
        row = measurement.model_copy(
            update={
                "origin": MeasurementOrigin.BACKFILLED,
                "pipeline_version": self.pipeline_version,
                "reason": self._reason(spec, measurement.reason),
            }
        )
        try:
            check_measurement_against_registry(row)
        except InvalidArgumentError:
            logger.exception("The registry refused the backfilled row for %s", spec.name)
            self.skip(SkipReason.REFUSED)
            return
        key = (row.metric, row.subject.level.value, row.subject.subject_id, row.subject.channel or "")
        if key in self._keys:
            self.skip(SkipReason.DUPLICATE_SUBJECT)
            return
        self._keys.add(key)
        self.plan.measurements.append(row)

    def add_value(
        self,
        metric: str,
        value: Any,
        subject: QualitySubject | None,
        *,
        sample_count: Any = None,
    ) -> None:
        """Add the row for one stored number, or count why it was skipped.

        Parameters
        ----------
        metric : `str`
            The registered metric name.
        value : `Any`
            The number as stored (already divided if the unit differs).
        subject : `QualitySubject` or `None`
            What the number is about, or `None` when it cannot be filed.
        sample_count : `Any`, optional
            The stored number of samples.
        """
        if value is None:
            self.skip(SkipReason.FIELD_ABSENT)
            return
        number = as_number(value)
        if number is None:
            self.skip(SkipReason.BAD_VALUE)
            return
        if subject is None:
            self.skip(SkipReason.NO_STACK_FILE)
            return
        problem = estimator_problem(metric, self.pipeline_version)
        if problem is not None:
            self.skip(problem)
            return
        spec = self._registry.get(metric)
        samples = as_number(sample_count)
        try:
            measurement = build_measurement(
                spec,
                number,
                self._context,
                subject,
                sample_count=None if samples is None else int(samples),
            )
        except ValidationError, ValueError:
            self.skip(SkipReason.BAD_VALUE)
            return
        self._accept(measurement, spec)

    def _add_gates(self) -> None:
        """Add one self-check row for each stored gate."""
        kind = self.kind
        stored: dict[str, Any] = {}
        for raw_gate in self.summary.get("gates") or []:
            if isinstance(raw_gate, dict) and isinstance(raw_gate.get("name"), str):
                stored[raw_gate["name"]] = raw_gate
        known = {mapping.gate_name for mapping in kind.gates}
        self.skip(SkipReason.UNKNOWN_GATE, len(set(stored) - known))
        metrics = self.summary.get(kind.metrics_key)
        metrics = metrics if isinstance(metrics, dict) else {}
        for mapping in kind.gates:
            raw_gate = stored.get(mapping.gate_name)
            if raw_gate is None:
                self.skip(SkipReason.FIELD_ABSENT)
                continue
            self._add_gate(mapping, raw_gate, metrics)

    def _add_gate(self, mapping: GateMapping, raw_gate: dict[str, Any], metrics: Mapping[str, Any]) -> None:
        """Add the row for one stored gate, or count why it was skipped.

        Parameters
        ----------
        mapping : `GateMapping`
            The gate's declaration here.
        raw_gate : `dict`
            The gate as stored.
        metrics : `dict`
            The summary's metrics object, for gates whose steady number comes
            from it.
        """
        metric = f"{self.kind.pipeline_name}.gate_{mapping.gate_name}"
        try:
            gate = GateResult.model_validate(raw_gate)
        except ValidationError:
            self.skip(SkipReason.BAD_GATE)
            return
        if self.kind.gate_adjuster is not None:
            adjusted = self.kind.gate_adjuster(gate, metrics)
            if adjusted is None:
                self.skip(SkipReason.NEEDS_RUN_DATA)
                return
            gate = adjusted
        subject = self.stack_subject() if self.kind.gates_on_stack else self.run_subject()
        if subject is None:
            self.skip(SkipReason.NO_STACK_FILE)
            return
        problem = estimator_problem(metric, self.pipeline_version)
        if problem is not None:
            self.skip(problem)
            return
        spec = self._registry.get(metric)
        register_entry = spec.register_entry if mapping.register_from_metric else mapping.register_entry
        try:
            measurement = gate_measurement(
                gate, spec, self._context, subject, limit_tier=mapping.tier, register_entry=register_entry
            )
        except ValidationError, ValueError:
            self.skip(SkipReason.BAD_VALUE)
            return
        self._accept(measurement, spec)

    def build(self) -> SummaryPlan:
        """Plan every row this summary gives.

        Returns
        -------
        plan : `SummaryPlan`
            The rows and the counts of what was skipped.
        """
        for mapping in self.kind.values:
            value = first_present(self.summary, mapping.paths)
            number = as_number(value)
            if number is not None:
                value = number / mapping.divisor
            sample = dig(self.summary, *mapping.sample_count_path) if mapping.sample_count_path else None
            self.add_value(mapping.metric, value, self.subject_for(mapping.subject), sample_count=sample)
        if self.kind.extras is not None:
            self.kind.extras(self)
        self.skip(SkipReason.NOT_HELD, len(self.kind.not_held))
        self._add_gates()
        return self.plan


def _stacking_negative_pixels_gate(gate: GateResult, metrics: Mapping[str, Any]) -> GateResult | None:
    """Put the negative-pixel gate in the unit its metric declares.

    The check quotes whole percent; its metric is a fraction, like every other
    share.

    Parameters
    ----------
    gate : `GateResult`
        The gate as stored.
    metrics : `dict`
        The summary's metrics (not used).

    Returns
    -------
    gate : `GateResult`
        The gate, with its number and limit divided by 100 for that gate.
    """
    if gate.name != "negative_pixels":
        return gate
    return gate.model_copy(
        update={
            "measured_value": None if gate.measured_value is None else gate.measured_value / 100.0,
            "limit": None if gate.limit is None else gate.limit / 100.0,
        }
    )


def _astrometry_gate(gate: GateResult, metrics: Mapping[str, Any]) -> GateResult | None:
    """Give an astrometry gate as the measurement step stores it.

    The plate-solve gate carries 1 when the image was solved and 0 when it
    was not, with the limit 1. The lookup gate carries the failed share of its
    lookups whenever any was made.

    Parameters
    ----------
    gate : `GateResult`
        The gate as stored.
    metrics : `dict`
        The summary's ``astrometryMetrics``.

    Returns
    -------
    gate : `GateResult` or `None`
        The adjusted gate, or `None` if the steady number needs a field the
        summary does not hold.
    """
    if gate.name == "plate_solve":
        solved = metrics.get("plateSolveSucceeded")
        if not isinstance(solved, bool):
            return None
        return gate.model_copy(
            update={
                "measured_value": 1.0 if solved else 0.0,
                "limit": 1.0,
                "limit_source": "sky coordinates need a plate solution",
            }
        )
    if gate.name == "catalog_lookup" and gate.status is not GateStatus.NOT_CHECKED:
        attempted = as_number(metrics.get("remoteCatalogQueriesAttempted"))
        failed = as_number(metrics.get("remoteCatalogQueriesFailed"))
        if attempted is None or failed is None:
            return None
        if attempted > 0:
            return gate.model_copy(update={"measured_value": failed / attempted})
    return gate


def _photometry_gate(gate: GateResult, metrics: Mapping[str, Any]) -> GateResult | None:
    """Give a photometry gate as the measurement step stores it.

    Two gates quote a steady number that comes from the run's own data (the
    smallest comparison set and the largest drift). The summary does not hold
    it, so those gates are copied only when they were not checked.

    Parameters
    ----------
    gate : `GateResult`
        The gate as stored.
    metrics : `dict`
        The summary's ``photometryMetrics`` (not used).

    Returns
    -------
    gate : `GateResult` or `None`
        The gate, or `None` if its steady number needs run data.
    """
    if (
        gate.name in ("comparison_ensemble", "registration_drift")
        and gate.status is not GateStatus.NOT_CHECKED
    ):
        return None
    return gate


def _asteroid_gate(gate: GateResult, metrics: Mapping[str, Any]) -> GateResult | None:
    """Give an asteroid gate as the measurement step stores it.

    The known-asteroid gate carries the number of failed queries and the limit
    0 unless it was not checked. The straight-line gate carries no number
    unless it passed.

    Parameters
    ----------
    gate : `GateResult`
        The gate as stored.
    metrics : `dict`
        The summary's ``asteroidDetectionMetrics``.

    Returns
    -------
    gate : `GateResult` or `None`
        The adjusted gate, or `None` if the number needs a field the summary
        does not hold.
    """
    if gate.name == "ephemeris_cross_match" and gate.status is not GateStatus.NOT_CHECKED:
        failed = as_number(metrics.get("ephemerisQueriesFailed"))
        if failed is None:
            return None
        return gate.model_copy(update={"measured_value": failed, "limit": 0.0})
    if gate.name == "track_residuals" and gate.status is GateStatus.FAILED:
        return gate.model_copy(update={"measured_value": None})
    return gate


def exposure_group_subject_for(
    planner: SummaryPlanner, group: Mapping[str, Any], group_index: int
) -> QualitySubject | None:
    """Name the subject of one exposure group's values.

    This follows `exposure_group_subject` in the stacking record step: a group
    that was stacked on its own is named after its own stack file (in the
    ``groups`` folder unless it is the combined stack itself). A group with no
    file is named after the combined stack, its place in the list and its
    exposure.

    Parameters
    ----------
    planner : `SummaryPlanner`
        The planner of the stack's summary.
    group : `dict`
        The exposure group as stored.
    group_index : `int`
        The group's place in the stored list.

    Returns
    -------
    subject : `QualitySubject` or `None`
        Level ``stack``, or `None` if the group cannot be named.
    """
    stack_path = group.get("stackPath")
    if isinstance(stack_path, str) and stack_path.strip():
        parts = re.split(r"[\\/]", stack_path.strip())
        name = parts[-1]
        folder = parts[-2] if len(parts) > 1 else ""
        return planner.stack_subject(f"groups/{name}" if folder == "groups" else name)
    exposure = as_number(group.get("exposureSeconds"))
    if exposure is None or planner.stack_file_name is None:
        return None
    return planner.stack_subject(f"{planner.stack_file_name}#group-{group_index}-{exposure:g}s")


# Fields on a stored exposure group that are plain numbers.
_GROUP_FIELDS: tuple[tuple[str, str], ...] = (
    ("exposureSeconds", "stacking.group_exposure_s"),
    ("framesSubmitted", "stacking.group_frames_submitted"),
    ("framesStacked", "stacking.group_frames_stacked"),
    ("gainMidRange", "stacking.group_gain_mid_range"),
    ("gainBrightEndRatio", "stacking.group_gain_bright_end_ratio"),
    ("gainDisagreement", "stacking.group_gain_disagreement"),
)
_GROUP_SHIFT_METRIC = "stacking.group_alignment_shift_px"


def _stacking_extras(planner: SummaryPlanner) -> None:
    """Add the values of each exposure group.

    Parameters
    ----------
    planner : `SummaryPlanner`
        The planner of the stack's summary.
    """
    groups = dig(planner.summary, "stackingMetrics", "exposureGroups")
    groups = groups if isinstance(groups, list) else []
    per_group = len(_GROUP_FIELDS) + 1
    for index, group in enumerate(groups):
        subject = exposure_group_subject_for(planner, group, index) if isinstance(group, dict) else None
        if not isinstance(group, dict) or (subject is None and planner.stack_file_name is not None):
            planner.skip(SkipReason.BAD_VALUE, per_group)
            continue
        for field_name, metric in _GROUP_FIELDS:
            planner.add_value(metric, group.get(field_name), subject)
        shift = group.get("alignmentShiftPixels")
        shift_numbers = [as_number(part) for part in shift] if isinstance(shift, list) else []
        shift_value = None
        if shift_numbers and all(part is not None for part in shift_numbers):
            shift_value = math.hypot(*[part for part in shift_numbers if part is not None])
        planner.add_value(_GROUP_SHIFT_METRIC, shift_value, subject)


def _spectroscopy_extras(planner: SummaryPlanner) -> None:
    """Add the identification counts, checkpoint roll-ups and Gaia XP bands.

    Parameters
    ----------
    planner : `SummaryPlanner`
        The planner of the run's summary.
    """
    metrics = planner.summary.get("spectroscopyMetrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    run = planner.run_subject()

    identification = (
        ("catalogMatchedStarCount", "spectroscopy.catalog_matched_star_count"),
        ("positionOnlyStarCount", "spectroscopy.position_only_star_count"),
        ("unresolvedStarCount", "spectroscopy.unresolved_star_count"),
    )
    counts = [as_number(metrics.get(field_name)) for field_name, _ in identification]
    # The session batch does not count star identification, and its stored
    # zeros are defaults. All three zero cannot be told from that.
    if None not in counts and any(count != 0 for count in counts):
        for (_, metric), count in zip(identification, counts, strict=True):
            planner.add_value(metric, count, run)
    elif None in counts:
        planner.skip(SkipReason.FIELD_ABSENT, len(identification))
    else:
        planner.skip(SkipReason.DEFAULT_ZERO, len(identification))

    rollups = {
        rollup.get("stage"): rollup
        for rollup in metrics.get("stageQualitySummary") or []
        if isinstance(rollup, dict)
    }
    for stage in STAGE_NAMES:
        planner.add_value(stage_spectrum_count_name(stage), dig(rollups.get(stage), "spectrumCount"), run)
    for stage, key in STAGE_METRIC_KEYS:
        planner.add_value(stage_metric_name(stage, key), dig(rollups.get(stage), "metricMedians", key), run)

    gaia = metrics.get("gaiaXpSummary")
    gaia = gaia if isinstance(gaia, dict) else None
    bands = [band for band in (gaia or {}).get("bands") or [] if isinstance(band, dict)]
    for band_index, (band_start, _band_end) in enumerate(GAIA_XP_BAND_EDGES_ANGSTROM):
        band = next(
            (
                entry
                for entry in bands
                if (start := as_number(entry.get("startAngstrom"))) is not None and round(start) == band_start
            ),
            None,
        )
        count = None if band is None else band.get("starCount")
        planner.add_value(
            gaia_xp_band_metric_name(band_index, "median_ratio"),
            None if band is None else band.get("medianRatio"),
            run,
            sample_count=count,
        )
        planner.add_value(
            gaia_xp_band_metric_name(band_index, "scatter"),
            None if band is None else band.get("scatter"),
            run,
            sample_count=count,
        )
        planner.add_value(gaia_xp_band_metric_name(band_index, "star_count"), count, run)


def _asteroid_extras(planner: SummaryPlanner) -> None:
    """Add the worst accepted straight-line ratio.

    The pipeline gives 0.0 when no track was accepted, and the summary of a
    run that tested no chain holds a missing value. Neither is a ratio.

    Parameters
    ----------
    planner : `SummaryPlanner`
        The planner of the run's summary.
    """
    metrics = planner.summary.get("asteroidDetectionMetrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    ratio = as_number(metrics.get("residualWorstAcceptedRatio"))
    tested = as_number(metrics.get("residualChainsTested"))
    if ratio is None:
        planner.skip(SkipReason.FIELD_ABSENT)
    elif tested is None or tested == 0 or ratio <= 0.0:
        planner.skip(SkipReason.NOT_A_MEASUREMENT)
    else:
        planner.add_value("asteroid_detection.residual_worst_accepted_ratio", ratio, planner.run_subject())


def _value(
    metric: str,
    *path: str,
    subject: SubjectKind = SubjectKind.RUN,
    divisor: float = 1.0,
    older_path: tuple[str, ...] | None = None,
    sample_count_path: tuple[str, ...] | None = None,
) -> ValueMapping:
    """Build the mapping of one stored field.

    Parameters
    ----------
    metric : `str`
        The registered metric name.
    *path : `str`
        The keys from the summary's root to the field.
    subject : `SubjectKind`, optional
        What the value is about.
    divisor : `float`, optional
        The stored number is divided by this.
    older_path : `tuple` [`str`, ...], optional
        An older location of the same field.
    sample_count_path : `tuple` [`str`, ...], optional
        Where the number of samples is stored.

    Returns
    -------
    mapping : `ValueMapping`
        The mapping.
    """
    paths = (path,) if older_path is None else (path, older_path)
    return ValueMapping(metric, paths, subject, divisor, sample_count_path)


_STACK = SubjectKind.STACK

# Stacking and spectral stacking share one summary shape. Each stored field
# fills the metric the stacking record step fills from the same field
# (`_METRICS_FIELDS` and `_INPUT_FIELDS` in the stacking `record_quality.py`).
_STACKING_VALUES: tuple[ValueMapping, ...] = (
    _value("stacking.frames_submitted", "stackingMetrics", "framesSubmitted", subject=_STACK),
    _value("stacking.frames_stacked", "stackingMetrics", "framesStacked", subject=_STACK),
    _value("stacking.rejected_pixel_fraction", "stackingMetrics", "rejectedPixelFraction", subject=_STACK),
    _value("stacking.flat_frame_count", "stackingMetrics", "flatFrameCount", subject=_STACK),
    _value("stacking.flat_noise_fraction", "stackingMetrics", "flatNoiseFraction", subject=_STACK),
    _value("stacking.flat_smoothing_sigma_px", "stackingMetrics", "flatSmoothingSigmaPx", subject=_STACK),
    _value("stacking.saturated_pixel_fraction", "stackingMetrics", "saturatedPixelFraction", subject=_STACK),
    _value("stacking.zero_pixel_fraction", "stackingMetrics", "zeroPixelFraction", subject=_STACK),
    _value(
        "stacking.negative_pixel_max_fraction",
        "stackingMetrics",
        "negativePixelMaxPercent",
        subject=_STACK,
        divisor=100.0,
    ),
    _value("stacking.stacked_fwhm_px", "stackingMetrics", "stackedFwhmPx", subject=_STACK),
    _value("stacking.median_input_fwhm_px", "stackingMetrics", "medianInputFwhmPx", subject=_STACK),
    _value("stacking.expected_stack_fwhm_px", "stackingMetrics", "expectedStackFwhmPx", subject=_STACK),
    _value("stacking.duration_s", "stackingMetrics", "stackingDurationSeconds", subject=_STACK),
    _value(
        "stacking.registration_reference_star_count",
        "stackingMetrics",
        "registrationReferenceStarCount",
        subject=_STACK,
    ),
    _value("stacking.frames_accepted", "inputQuality", "framesAccepted", subject=_STACK),
    _value("stacking.frames_excluded_for_gain", "inputQuality", "framesExcludedForGain", subject=_STACK),
    _value(
        "stacking.frames_excluded_for_background",
        "inputQuality",
        "framesExcludedForBackground",
        subject=_STACK,
    ),
    _value("stacking.frames_quarantined", "inputQuality", "framesQuarantined", subject=_STACK),
)

# How each stacking gate's limit was obtained, as the stacking record step
# states it (`_TIER_BY_GATE`). The two gates whose limits the validation
# register marks as measured carry their register entry.
_STACKING_GATES: tuple[GateMapping, ...] = (
    GateMapping("gain_homogeneity", LimitTier.PHYSICAL_BUDGET),
    GateMapping("frame_quarantine", LimitTier.PHYSICAL_BUDGET),
    GateMapping("background_homogeneity", LimitTier.PHYSICAL_BUDGET),
    GateMapping("flat_level", LimitTier.PHYSICAL_BUDGET),
    GateMapping("flat_noise", LimitTier.PHYSICAL_BUDGET),
    GateMapping("calibration_metadata", LimitTier.GEOMETRY),
    GateMapping("calibration_frame_count", LimitTier.GEOMETRY),
    GateMapping("exposure_group_linearity", LimitTier.PHYSICAL_BUDGET),
    GateMapping("exposure_groups_combined", LimitTier.GEOMETRY),
    GateMapping("raw_frame_clipping", LimitTier.PHYSICAL_BUDGET),
    GateMapping("rejected_pixel_fraction", LimitTier.PHYSICAL_BUDGET),
    GateMapping("stack_sharpness", LimitTier.PHYSICAL_BUDGET),
    GateMapping("spectral_registration", LimitTier.GEOMETRY),
    GateMapping("saturated_pixel_fraction", LimitTier.PHYSICAL_BUDGET),
    GateMapping("zero_pixel_fraction", LimitTier.VALIDATED_DATASET, "STK-26"),
    GateMapping("negative_pixels", LimitTier.VALIDATED_DATASET, "STK-26"),
)

# The stacking numbers the stored summary never held: the noise measured in
# empty sky, the effective frame count and time, the frame weights and the
# night shares all come from the stack's pixels and membership, not from the
# summary.
_STACKING_NOT_HELD: tuple[str, ...] = (
    "stacking.empty_aperture_noise_adu",
    "stacking.noise_reference_flux_adu",
    "stacking.empty_aperture_noise_normalized",
    "stacking.effective_frame_count",
    "stacking.effective_time_s",
    "stacking.frame_weight",
    "stacking.night_weight_share",
    "stacking.noise_without_night",
)

_ASTROMETRY_VALUES: tuple[ValueMapping, ...] = (
    _value("astrometry.sources_detected", "astrometryMetrics", "sourcesDetected", subject=_STACK),
    _value("astrometry.simbad_matched_count", "astrometryMetrics", "simbadMatchedCount", subject=_STACK),
    _value(
        "astrometry.catalog_matched_star_count",
        "astrometryMetrics",
        "catalogMatchedStarCount",
        subject=_STACK,
    ),
    _value(
        "astrometry.position_only_star_count", "astrometryMetrics", "positionOnlyStarCount", subject=_STACK
    ),
    _value("astrometry.unresolved_star_count", "astrometryMetrics", "unresolvedStarCount", subject=_STACK),
    _value("astrometry.plate_solve_attempts", "astrometryMetrics", "plateSolveAttempts"),
    _value(
        "astrometry.plate_solve_matched_star_count",
        "astrometryMetrics",
        "plateSolveMatchedStarCount",
        subject=_STACK,
    ),
    _value("astrometry.gaia_fit_star_count", "astrometryMetrics", "gaiaFitStarCount", subject=_STACK),
    _value(
        "astrometry.remote_catalog_queries_attempted", "astrometryMetrics", "remoteCatalogQueriesAttempted"
    ),
    _value("astrometry.remote_catalog_queries_failed", "astrometryMetrics", "remoteCatalogQueriesFailed"),
    # Summaries saved before 2026-10-10 hold the catalog match separation as
    # ``astrometricResidualRmsArcsec``. It is the same number.
    _value(
        "astrometry.catalog_match_separation_rms_arcsec",
        "astrometryMetrics",
        "catalogMatchSeparationRmsArcsec",
        subject=_STACK,
        older_path=("astrometryMetrics", "astrometricResidualRmsArcsec"),
    ),
    _value(
        "astrometry.plate_solve_fit_residual_rms_arcsec",
        "astrometryMetrics",
        "plateSolveFitResidualRmsArcsec",
        subject=_STACK,
    ),
    _value("astrometry.gaia_fit_rms_arcsec", "astrometryMetrics", "gaiaFitRmsArcsec", subject=_STACK),
    _value(
        "astrometry.gaia_fit_rms_before_arcsec", "astrometryMetrics", "gaiaFitRmsBeforeArcsec", subject=_STACK
    ),
    _value(
        "astrometry.wcs_refinement_shift_arcsec",
        "astrometryMetrics",
        "wcsRefinementShiftArcsec",
        subject=_STACK,
    ),
    _value(
        "astrometry.plate_scale_arcsec_per_pixel",
        "astrometryMetrics",
        "plateScaleArcsecPerPixel",
        subject=_STACK,
    ),
    _value("astrometry.star_width_gaussian_px", "astrometryMetrics", "starFwhmPx", subject=_STACK),
)

_ASTROMETRY_GATES: tuple[GateMapping, ...] = (
    GateMapping("plate_solve", LimitTier.GEOMETRY),
    GateMapping("source_detection", LimitTier.GEOMETRY),
    GateMapping("catalog_matches", LimitTier.PHYSICAL_BUDGET),
    GateMapping("astrometric_residual", LimitTier.PHYSICAL_BUDGET),
    GateMapping("catalog_lookup", LimitTier.PHYSICAL_BUDGET),
)

_PHOTOMETRY_VALUES: tuple[ValueMapping, ...] = (
    _value("photometry.stars_processed", "photometryMetrics", "starsProcessed"),
    _value("photometry.stars_found", "photometryMetrics", "starsFound"),
    _value("photometry.frames_processed", "photometryMetrics", "framesProcessed"),
    _value("photometry.variable_candidate_count", "photometryMetrics", "variableCandidateCount"),
    _value("photometry.cross_session_match_count", "photometryMetrics", "crossSessionMatchCount"),
    _value(
        "photometry.long_term_variable_candidate_count", "photometryMetrics", "longTermVariableCandidateCount"
    ),
    _value(
        "photometry.astrometry_identified_star_count", "photometryMetrics", "astrometryIdentifiedStarCount"
    ),
    _value("photometry.catalog_matched_star_count", "photometryMetrics", "catalogMatchedStarCount"),
    _value("photometry.position_only_star_count", "photometryMetrics", "positionOnlyStarCount"),
    _value("photometry.unresolved_star_count", "photometryMetrics", "unresolvedStarCount"),
    _value("photometry.light_curve_scatter_rms_median_mag", "photometryMetrics", "lightCurveScatterRmsMag"),
)

_PHOTOMETRY_GATES: tuple[GateMapping, ...] = (
    GateMapping("ensemble_frame_rejection", LimitTier.OWN_BASELINE, register_from_metric=True),
    GateMapping("capture_timestamps", LimitTier.GEOMETRY, register_from_metric=True),
    GateMapping("session_content", LimitTier.GEOMETRY, register_from_metric=True),
    GateMapping("session_plate_solve", LimitTier.GEOMETRY, register_from_metric=True),
    GateMapping("photometry_work", LimitTier.GEOMETRY, register_from_metric=True),
    GateMapping("comparison_ensemble", LimitTier.PHYSICAL_BUDGET, register_from_metric=True),
    GateMapping("registration_drift", LimitTier.GEOMETRY, register_from_metric=True),
    GateMapping("scatter_population", LimitTier.PHYSICAL_BUDGET, register_from_metric=True),
    GateMapping("variability_discrimination", LimitTier.PHYSICAL_BUDGET, register_from_metric=True),
    GateMapping("detectable_amplitude", LimitTier.PHYSICAL_BUDGET, register_from_metric=True),
    GateMapping("flux_uncertainty", LimitTier.PHYSICAL_BUDGET, register_from_metric=True),
)

# The photometry numbers the stored summary never held: they come from the
# run's per-session comparison sets and centroid summaries, and from counts
# the summary does not keep.
_PHOTOMETRY_NOT_HELD: tuple[str, ...] = (
    "photometry.stars_with_light_curve_scatter",
    "photometry.noise_model_star_count",
    "photometry.comparison_scatter_over_predicted_worst",
    "photometry.corner_displacement_p95_worst_px",
    "photometry.centroid_fallback_fraction_worst",
)

_SPECTROSCOPY_VALUES: tuple[ValueMapping, ...] = (
    _value(
        "spectroscopy.zero_order_saturated_fraction_max",
        "spectroscopyMetrics",
        "zeroOrderSaturatedPixelFraction",
    ),
    _value("spectroscopy.dispersion_angle_first_star", "spectroscopyMetrics", "dispersionAngleDeg"),
    _value("spectroscopy.trail_width_median", "spectroscopyMetrics", "medianTrailWidthPx"),
    _value(
        "spectroscopy.poor_match_classification_count", "spectroscopyMetrics", "poorMatchClassificationCount"
    ),
    _value(
        "spectroscopy.ambiguous_classification_count", "spectroscopyMetrics", "ambiguousClassificationCount"
    ),
    _value(
        "spectroscopy.wavelength_scale_spectrum_count",
        "spectroscopyMetrics",
        "wavelengthScaleSummary",
        "spectrumCount",
    ),
    _value(
        "spectroscopy.wavelength_scale_applied_count",
        "spectroscopyMetrics",
        "wavelengthScaleSummary",
        "appliedCount",
    ),
    _value(
        "spectroscopy.wavelength_scale_rms_before_correction_angstrom",
        "spectroscopyMetrics",
        "wavelengthScaleSummary",
        "rmsBeforeCorrectionAngstrom",
    ),
    _value(
        "spectroscopy.wavelength_scale_rms_after_correction_angstrom",
        "spectroscopyMetrics",
        "wavelengthScaleSummary",
        "rmsAfterCorrectionAngstrom",
        sample_count_path=("spectroscopyMetrics", "wavelengthScaleSummary", "spectrumCount"),
    ),
    _value(
        "spectroscopy.wavelength_scale_saturation_correlation",
        "spectroscopyMetrics",
        "wavelengthScaleSummary",
        "offsetSaturationCorrelation",
    ),
    _value(
        "spectroscopy.wavelength_scale_correlation_spectrum_count",
        "spectroscopyMetrics",
        "wavelengthScaleSummary",
        "correlationSpectrumCount",
    ),
    _value(
        "spectroscopy.gaia_xp_compared_star_count",
        "spectroscopyMetrics",
        "gaiaXpSummary",
        "comparedStarCount",
    ),
    _value(
        "spectroscopy.gaia_xp_median_slope_percent_per_1000_angstrom",
        "spectroscopyMetrics",
        "gaiaXpSummary",
        "medianSlopePercentPer1000Angstrom",
    ),
    _value(
        "spectroscopy.gaia_xp_median_absolute_slope_percent_per_1000_angstrom",
        "spectroscopyMetrics",
        "gaiaXpSummary",
        "medianAbsoluteSlopePercentPer1000Angstrom",
    ),
)

_SPECTROSCOPY_GATES: tuple[GateMapping, ...] = (
    GateMapping("spectra_extracted", LimitTier.GEOMETRY),
    GateMapping("zero_order_saturation", LimitTier.PHYSICAL_BUDGET),
    GateMapping("spectral_classification", LimitTier.GEOMETRY),
    GateMapping("catalog_agreement", LimitTier.GEOMETRY),
    GateMapping("feature_significance", LimitTier.GEOMETRY),
    GateMapping("resolution_measured", LimitTier.GEOMETRY),
    GateMapping("processing_quality", LimitTier.PHYSICAL_BUDGET),
    GateMapping("wavelength_scale", LimitTier.PHYSICAL_BUDGET),
    GateMapping("gaia_xp_agreement", LimitTier.PHYSICAL_BUDGET),
)

# The spectroscopy metrics the extras function fills: the three
# star-identification counts, each checkpoint's spectrum count and medians,
# and the Gaia XP band quantities.
_SPECTROSCOPY_EXTRA_METRICS: tuple[str, ...] = (
    "spectroscopy.catalog_matched_star_count",
    "spectroscopy.position_only_star_count",
    "spectroscopy.unresolved_star_count",
    *(stage_spectrum_count_name(stage) for stage in STAGE_NAMES),
    *(stage_metric_name(stage, key) for stage, key in STAGE_METRIC_KEYS),
    *(
        gaia_xp_band_metric_name(index, quantity)
        for index in range(len(GAIA_XP_BAND_EDGES_ANGSTROM))
        for quantity in ("median_ratio", "scatter", "star_count")
    ),
)

_ASTEROID_VALUES: tuple[ValueMapping, ...] = tuple(
    _value(f"asteroid_detection.{snake_name}", "asteroidDetectionMetrics", stored_name)
    for snake_name, stored_name in (
        ("frames_with_wcs_estimate", "framesWithWcsEstimate"),
        ("frames_excluded_missing_pointing_metadata", "framesExcludedMissingPointingMetadata"),
        ("candidates_detected", "candidatesDetected"),
        ("candidates_persistence_confirmed", "candidatesPersistenceConfirmed"),
        ("candidates_rate_linearity_confirmed", "candidatesRateLinearityConfirmed"),
        ("candidates_ephemeris_matched", "candidatesEphemerisMatched"),
        ("ephemeris_queries_attempted", "ephemerisQueriesAttempted"),
        ("ephemeris_queries_failed", "ephemerisQueriesFailed"),
        ("residual_chains_tested", "residualChainsTested"),
        ("residual_chains_rejected", "residualChainsRejected"),
        ("residual_chains_non_monotonic", "residualChainsNonMonotonic"),
        ("residual_assumed_error_detections", "residualAssumedErrorDetections"),
    )
)

_ASTEROID_GATES: tuple[GateMapping, ...] = (
    GateMapping("search_frames", LimitTier.GEOMETRY),
    GateMapping("pointing_metadata", LimitTier.GEOMETRY),
    GateMapping("ephemeris_cross_match", LimitTier.GEOMETRY),
    GateMapping("unmatched_movers", LimitTier.GEOMETRY),
    GateMapping("track_residuals", LimitTier.PHYSICAL_BUDGET, register_from_metric=True),
)

_STACKING_EXTRA_METRICS: tuple[str, ...] = (*(metric for _, metric in _GROUP_FIELDS), _GROUP_SHIFT_METRIC)

_IMAGING_STACK_PATHS: tuple[tuple[str, ...], ...] = (
    ("stacking", "stackedImage"),
    ("stackedImage",),
    ("stacked_image",),
)

SUMMARY_KINDS: tuple[SummaryKind, ...] = (
    SummaryKind(
        key="stacking",
        label="stacking",
        pipeline_name="stacking",
        location=("stacking", "qualitySummary"),
        flat_keys=("stackQualitySummary", "stack_quality_summary"),
        metrics_key="stackingMetrics",
        stack_image_paths=_IMAGING_STACK_PATHS,
        values=_STACKING_VALUES,
        gates=_STACKING_GATES,
        extra_metrics=_STACKING_EXTRA_METRICS,
        not_held=_STACKING_NOT_HELD,
        gates_on_stack=True,
        extras=_stacking_extras,
        gate_adjuster=_stacking_negative_pixels_gate,
    ),
    SummaryKind(
        key="spectralStacking",
        label="spectral stacking",
        pipeline_name="stacking",
        location=("spectralStacking", "qualitySummary"),
        flat_keys=("spectralStackQualitySummary", "spectral_stack_quality_summary"),
        metrics_key="stackingMetrics",
        stack_image_paths=(
            ("spectralStacking", "stackedImage"),
            ("stackedSpectralTarget",),
            ("stacked_spectral_target",),
        ),
        values=_STACKING_VALUES,
        gates=_STACKING_GATES,
        extra_metrics=_STACKING_EXTRA_METRICS,
        not_held=_STACKING_NOT_HELD,
        gates_on_stack=True,
        extras=_stacking_extras,
        gate_adjuster=_stacking_negative_pixels_gate,
    ),
    SummaryKind(
        key="astrometry",
        label="astrometry",
        pipeline_name="astrometry",
        location=("quality", "astrometry"),
        flat_keys=("astrometryQualitySummary", "astrometry_quality_summary"),
        metrics_key="astrometryMetrics",
        stack_image_paths=_IMAGING_STACK_PATHS,
        values=_ASTROMETRY_VALUES,
        gates=_ASTROMETRY_GATES,
        gate_adjuster=_astrometry_gate,
    ),
    SummaryKind(
        key="photometry",
        label="photometry",
        pipeline_name="photometry",
        location=("quality", "photometry"),
        flat_keys=("photometryQualitySummary", "photometry_quality_summary"),
        metrics_key="photometryMetrics",
        stack_image_paths=(),
        values=_PHOTOMETRY_VALUES,
        gates=_PHOTOMETRY_GATES,
        not_held=_PHOTOMETRY_NOT_HELD,
        gate_adjuster=_photometry_gate,
    ),
    SummaryKind(
        key="spectroscopy",
        label="spectroscopy",
        pipeline_name="spectroscopy",
        location=("quality", "spectroscopy"),
        flat_keys=("spectroscopyQualitySummary", "spectroscopy_quality_summary"),
        metrics_key="spectroscopyMetrics",
        stack_image_paths=(),
        values=_SPECTROSCOPY_VALUES,
        gates=_SPECTROSCOPY_GATES,
        extra_metrics=_SPECTROSCOPY_EXTRA_METRICS,
        extras=_spectroscopy_extras,
    ),
    SummaryKind(
        key="asteroidDetection",
        label="asteroid detection",
        pipeline_name="asteroid_detection",
        location=("asteroidDetection", "qualitySummary"),
        flat_keys=("asteroidDetectionQualitySummary", "asteroid_detection_quality_summary"),
        metrics_key="asteroidDetectionMetrics",
        stack_image_paths=(),
        values=_ASTEROID_VALUES,
        gates=_ASTEROID_GATES,
        extra_metrics=("asteroid_detection.residual_worst_accepted_ratio",),
        extras=_asteroid_extras,
        gate_adjuster=_asteroid_gate,
    ),
)


def mapped_metric_names(kind: SummaryKind) -> set[str]:
    """List every metric a kind of summary can fill or knowingly leaves out.

    Parameters
    ----------
    kind : `SummaryKind`
        The kind of summary.

    Returns
    -------
    names : `set` [`str`]
        The metrics of its plain fields, its extras, its gates, and the ones
        the stored JSON never held.
    """
    names = {mapping.metric for mapping in kind.values}
    names.update(kind.extra_metrics)
    names.update(f"{kind.pipeline_name}.gate_{mapping.gate_name}" for mapping in kind.gates)
    names.update(kind.not_held)
    return names


def find_summary(document: Mapping[str, Any], kind: SummaryKind) -> dict[str, Any] | None:
    """Find a kind of summary in a target document.

    Parameters
    ----------
    document : `dict`
        The target document as stored.
    kind : `SummaryKind`
        The kind of summary.

    Returns
    -------
    summary : `dict` or `None`
        The summary, from its nested place or from the name it had before the
        document was nested, or `None` if the target has none.
    """
    summary = dig(document, *kind.location)
    if not isinstance(summary, dict):
        summary = next(
            (document[key] for key in kind.flat_keys if isinstance(document.get(key), dict)),
            None,
        )
    return summary if isinstance(summary, dict) and summary else None


def plan_summary(
    target_id: str, document: Mapping[str, Any], kind: SummaryKind, summary: dict[str, Any]
) -> SummaryPlan:
    """Plan the rows one stored summary gives.

    Parameters
    ----------
    target_id : `str`
        The target.
    document : `dict`
        The target document.
    kind : `SummaryKind`
        The kind of summary.
    summary : `dict`
        The summary as stored.

    Returns
    -------
    plan : `SummaryPlan`
        The rows. If the summary has no time of its own and the target has no
        frame time, the plan has no rows and counts the summary once as
        ``NO_TIME``.
    """
    stored_text = summary.get("createdAt")
    stored = read_stored_time(stored_text)
    if stored is not None:
        measured_at, note = stored
        time_note = f"created {stored_text}" + (f", {note}" if note else "")
        run_label = str(stored_text)
    else:
        fallback = newest_frame_time(document)
        if fallback is None:
            plan = SummaryPlan(target_id, kind, "")
            plan.skipped[SkipReason.NO_TIME] += 1
            return plan
        measured_at = fallback
        time_note = "no creation time was stored, so the target's newest frame time was used"
        run_label = fallback.isoformat()
    run_id = f"{BACKFILL_RUN_ID_PREFIX}{target_id}-{run_label}"
    stack_file_name = file_name_of(first_present(document, kind.stack_image_paths))
    return SummaryPlanner(kind, target_id, summary, measured_at, time_note, run_id, stack_file_name).build()


@dataclass
class BackfillReport:
    """Everything a run learned.

    Attributes
    ----------
    targets_read : `int`
        Targets read from the database.
    summaries_found : `collections.Counter`
        How many targets hold each kind of summary.
    rows_planned : `collections.Counter`
        How many rows each kind of summary gives.
    rows_already_stored : `collections.Counter`
        How many of those rows are in the table already, equal to the JSON.
    skipped : `collections.Counter`
        Skipped values, by kind of summary and reason.
    rows_written : `int`
        Rows that were new (with ``--apply``).
    backup_path : `str` or `None`
        Where the database was copied before writing.
    applied : `bool`
        Whether rows were written.
    mismatches : `list` [`str`]
        What the row-count check found wrong. Empty is good.
    """

    targets_read: int = 0
    summaries_found: Counter = field(default_factory=Counter)
    rows_planned: Counter = field(default_factory=Counter)
    rows_already_stored: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)
    rows_written: int = 0
    backup_path: str | None = None
    applied: bool = False
    mismatches: list[str] = field(default_factory=list)


def read_target_documents(
    database_path: str, target_id: str | None = None, limit: int | None = None
) -> list[tuple[str, dict[str, Any] | None]]:
    """Read the saved target documents without changing the database.

    Parameters
    ----------
    database_path : `str`
        The catalog database.
    target_id : `str`, optional
        Only this target.
    limit : `int`, optional
        At most this many targets, in id order.

    Returns
    -------
    documents : `list` [`tuple`]
        ``(target id, document)``. The document is `None` when the stored JSON
        does not parse as an object.

    Raises
    ------
    FileNotFoundError
        If the database file does not exist.
    """
    if not os.path.isfile(database_path):
        raise FileNotFoundError(f"No catalog database at {database_path}.")
    connection = sqlite3.connect(f"file:{quote(database_path)}?mode=ro", uri=True)
    try:
        sql = "SELECT id, data_json FROM targets"
        parameters: list[Any] = []
        if target_id is not None:
            sql += " WHERE id = ?"
            parameters.append(target_id)
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        rows = connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()
    documents: list[tuple[str, dict[str, Any] | None]] = []
    for row_id, data_json in rows:
        try:
            document = json.loads(data_json)
        except TypeError, ValueError:
            document = None
        documents.append((str(row_id), document if isinstance(document, dict) else None))
    return documents


def _row_key(measurement: QualityMeasurement) -> tuple[str, str, str, str, str]:
    """Give the key that identifies a row inside one run.

    Parameters
    ----------
    measurement : `QualityMeasurement`
        A row.

    Returns
    -------
    key : `tuple` [`str`, ...]
        Run id, metric, subject level, subject id and channel.
    """
    subject = measurement.subject
    return (
        measurement.run_id,
        measurement.metric,
        subject.level.value,
        subject.subject_id,
        subject.channel or "",
    )


def _same_fact(expected: QualityMeasurement, stored: QualityMeasurement) -> bool:
    """Say whether a stored row holds what the plan holds.

    Parameters
    ----------
    expected : `QualityMeasurement`
        The planned row.
    stored : `QualityMeasurement`
        The row read back.

    Returns
    -------
    same : `bool`
        True if the origin, definition version, status, value, sample count,
        self-check outcome and limit value are equal.
    """
    return (
        stored.origin is MeasurementOrigin.BACKFILLED
        and stored.definition_version == expected.definition_version
        and stored.status == expected.status
        and stored.value == expected.value
        and stored.sample_count == expected.sample_count
        and stored.check_outcome == expected.check_outcome
        and (stored.check_limit.value if stored.check_limit else None)
        == (expected.check_limit.value if expected.check_limit else None)
    )


def read_stored_rows(
    store: QualityStore, target_id: str
) -> dict[tuple[str, str, str, str, str], QualityMeasurement]:
    """Read back the backfilled rows of a target.

    Only run-level and stack-level rows are read; the script writes no frame
    rows.

    Parameters
    ----------
    store : `QualityStore`
        The store.
    target_id : `str`
        The target.

    Returns
    -------
    rows : `dict`
        The backfilled rows by `_row_key`.
    """
    rows: dict[tuple[str, str, str, str, str], QualityMeasurement] = {}
    for level in (SubjectLevel.RUN, SubjectLevel.STACK):
        for row in store.query(target_id=target_id, level=level, latest_only=False):
            if row.origin is MeasurementOrigin.BACKFILLED and row.run_id.startswith(BACKFILL_RUN_ID_PREFIX):
                rows[_row_key(row)] = row
    return rows


def check_rows_against_plans(store: QualityStore, plans: Sequence[SummaryPlan]) -> tuple[Counter, list[str]]:
    """Compare the stored rows with the planned rows, per target and pipeline.

    Parameters
    ----------
    store : `QualityStore`
        The store, read after writing.
    plans : `Sequence` [`SummaryPlan`]
        The plans that were written.

    Returns
    -------
    matched : `collections.Counter`
        The planned rows found stored and equal, by kind of summary.
    mismatches : `list` [`str`]
        One line for each target and pipeline whose stored count or values
        differ from the plan.
    """
    matched: Counter = Counter()
    mismatches: list[str] = []
    stored_by_target: dict[str, dict[tuple[str, str, str, str, str], QualityMeasurement]] = {}
    for plan in plans:
        if not plan.measurements:
            continue
        if plan.target_id not in stored_by_target:
            stored_by_target[plan.target_id] = read_stored_rows(store, plan.target_id)
        stored = stored_by_target[plan.target_id]
        found = 0
        different: list[str] = []
        for expected in plan.measurements:
            row = stored.get(_row_key(expected))
            if row is not None and _same_fact(expected, row):
                found += 1
            else:
                different.append(expected.metric)
        matched[plan.kind.key] += found
        if different:
            mismatches.append(
                f"{plan.target_id} / {plan.kind.key}: expected {len(plan.measurements)} rows, "
                f"{found} stored as planned (for example {different[0]})."
            )
    return matched, mismatches


def run_backfill(
    database_path: str,
    *,
    target_id: str | None = None,
    limit: int | None = None,
    apply: bool = False,
    make_backup: Callable[[], Any] | None = None,
    store: QualityStore | None = None,
    log: Callable[[str], None] = print,
) -> BackfillReport:
    """Copy the stored summaries into the quality table.

    Parameters
    ----------
    database_path : `str`
        The catalog database holding ``targets`` and, after a write, the
        quality table.
    target_id : `str`, optional
        Only this target.
    limit : `int`, optional
        At most this many targets.
    apply : `bool`, optional
        Write the rows. Without it nothing is written.
    make_backup : `Callable`, optional
        Makes the safety copy of the database and returns its path, or a false
        value if it could not. Required with `apply`.
    store : `QualityStore`, optional
        Where rows go. A store on `database_path` if omitted.
    log : `Callable`, optional
        Where progress lines go.

    Returns
    -------
    report : `BackfillReport`
        What happened. ``mismatches`` is non-empty if the row-count check
        failed.

    Raises
    ------
    ValueError
        If `apply` is set without `make_backup`.
    RuntimeError
        If the backup could not be made. Nothing is written then.
    """
    if apply and make_backup is None:
        raise ValueError("Saving needs a backup function.")
    store = store or QualityStore(database_path)
    report = BackfillReport()
    plans: list[SummaryPlan] = []
    for row_target_id, document in read_target_documents(database_path, target_id, limit):
        report.targets_read += 1
        if document is None:
            report.skipped["target", SkipReason.UNREADABLE_TARGET] += 1
            continue
        for kind in SUMMARY_KINDS:
            summary = find_summary(document, kind)
            if summary is None:
                continue
            report.summaries_found[kind.key] += 1
            plan = plan_summary(row_target_id, document, kind, summary)
            report.rows_planned[kind.key] += len(plan.measurements)
            for reason, count in plan.skipped.items():
                report.skipped[kind.key, reason] += count
            plans.append(plan)
    log(f"{report.targets_read} target(s) read, {sum(report.rows_planned.values())} row(s) planned.")

    already_stored, _ = check_rows_against_plans(store, plans)
    report.rows_already_stored.update(already_stored)

    if not apply or not any(plan.measurements for plan in plans):
        return report

    backup_path = make_backup() if make_backup is not None else None
    if not backup_path:
        raise RuntimeError("Could not create a safety backup of the catalog database.")
    report.backup_path = str(backup_path)
    log(f"Backed up the catalog database to {backup_path}.")
    for plan in plans:
        if plan.measurements:
            report.rows_written += record_measurements(store, plan.measurements)
    report.applied = True
    _, report.mismatches = check_rows_against_plans(store, plans)
    return report


def format_report(report: BackfillReport) -> str:
    """Write a run's report as text.

    Parameters
    ----------
    report : `BackfillReport`
        What happened.

    Returns
    -------
    text : `str`
        The report.
    """
    lines = [f"\n{report.targets_read} target(s) read."]
    lines.append(f"{'summary':20s}{'targets':>9s}{'rows planned':>14s}{'already stored':>16s}")
    for kind in SUMMARY_KINDS:
        lines.append(
            f"{kind.key:20s}{report.summaries_found[kind.key]:9d}{report.rows_planned[kind.key]:14d}"
            f"{report.rows_already_stored[kind.key]:16d}"
        )
    if report.skipped:
        lines.append("Skipped values, by summary and reason:")
        for (key, reason), count in sorted(report.skipped.items(), key=lambda item: (item[0][0], item[0][1])):
            lines.append(f"  {key:20s}{count:7d}  {SKIP_DESCRIPTIONS[reason]}")
    if report.applied:
        lines.append(f"Wrote {report.rows_written} new row(s).")
        if report.mismatches:
            lines.append("The row-count check FAILED:")
            lines.extend(f"  {line}" for line in report.mismatches)
        else:
            lines.append(
                "The row-count check passed: every planned row is stored with the value the JSON holds."
            )
    else:
        lines.append("Dry run: nothing was written. Re-run with --apply to save these rows.")
    return "\n".join(lines)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering whether to write and which targets to read.
    """
    parser = argparse.ArgumentParser(
        prog="backfill_quality_measurements",
        description="Copy the stored quality summaries into the quality measurement table.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the rows. Without this only a report is printed."
    )
    parser.add_argument("--target", default=None, help="Only this target. Default: every target.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Read at most this many targets. Default: all of them."
    )
    parser.add_argument(
        "--database",
        default=None,
        help="Use this catalog database file (for example a copy) instead of the configured one.",
    )
    return parser


def _backup_function(database_path: str, configured_path: str) -> Callable[[], str | None]:
    """Choose how the safety copy is made.

    Parameters
    ----------
    database_path : `str`
        The database the run reads and writes.
    configured_path : `str`
        The configured catalog database.

    Returns
    -------
    make_backup : `Callable`
        For the configured database, the library's own backup. For any other
        file, an integrity-checked snapshot beside it.
    """
    if database_path == configured_path:
        return lambda: backup_catalog_database(get_configuration())

    def snapshot_beside() -> str | None:
        """Copy the database to a timestamped ``.bak`` file beside it.

        Returns
        -------
        backup_path : `str` or `None`
            Where the copy is, or `None` if it could not be made.
        """
        backup_path = f"{database_path}.{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.bak"
        return backup_path if snapshot_sqlite_database(database_path, backup_path) else None

    return snapshot_beside


def main(argv: list[str] | None = None) -> int:
    """Run the backfill from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success. ``1`` if the database is missing, the safety backup
        could not be made, the quality table could not be written, or the
        row-count check found a difference.
    """
    arguments = _build_argument_parser().parse_args(argv)
    configure_logging("backfill_quality_measurements", level=logging.INFO, log_dir="")
    configured_path = quality_database_path(get_configuration())
    database_path = arguments.database or configured_path
    try:
        report = run_backfill(
            database_path,
            target_id=arguments.target,
            limit=arguments.limit,
            apply=arguments.apply,
            make_backup=_backup_function(database_path, configured_path),
        )
    except FileNotFoundError as error:
        print(f"\n{error}")
        return 1
    except RuntimeError as error:
        print(f"\n{error} Nothing was written.")
        return 1
    except StorageError as error:
        print(f"\nThe quality table could not be written: {error}")
        return 1
    print(format_report(report))
    return 1 if report.mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
