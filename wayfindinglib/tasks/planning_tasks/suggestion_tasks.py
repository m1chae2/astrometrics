"""Purpose: The steps of the suggestion refresh.

Description: `ObservationPlanning.refresh_suggestions` keeps the library of
suggested observation packages up to date. It hands the work to the functions
here. They do five jobs:

* Read the statements of a target's quality report (`read_rule_statements`)
  and turn the ones whose rule makes a package row into suggested rows.
* Create, update or delete the one suggested package of a target
  (`refresh_rule_package`), only while the target's noise is above the
  default goal, and never because data is missing.
* Turn tonight's ranking into suggested packages with no rows, and remove
  the ones that fell out of the top few or whose night ended
  (`refresh_ranking_packages`).
* Collect the checklist items that calibration statements ask for
  (`checklist_items_from`).
* Mark a planned package complete when every light row is captured, and set
  it back when frames were set aside (`refresh_completion`).

Every write to a package checks the revision it read (and a delete checks
that the package is still a suggestion), so a user edit made at the same time
is never overwritten. A lost race raises `PlanRevisionConflictError` and the
caller reads again. Every change is also written to the suggestion lifecycle
log, with the evidence row ids and the numbers that mattered.

Skipped suggestions: a source key held by a planned or complete package or by
a queue entry of tonight or later is not suggested again. A complete package
holds its key only until the target's stack is made again after the package
was completed; a newer stack is new evidence.
"""

import logging
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from wayfindinglib.analytics.frame_counting import (
    EquipmentNames,
    TargetFrameReader,
    count_request_frames,
    package_is_complete,
)
from wayfindinglib.analytics.package_suggestions import (
    STOP_RULES,
    TargetNoise,
    evidence_fingerprint,
    package_fingerprint,
    ranking_fingerprint,
    ranking_source_key,
    row_source_key,
)
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.observation_package import (
    ExposureRequest,
    FrameType,
    ObservationPackage,
    PackageStatus,
    PackageSuggestion,
)
from wayfindinglib.models.planning.suggestion_log import PackageChange, SuggestionEvent, SuggestionLogEntry
from wayfindinglib.models.planning.target_report import ForecastGate
from wayfindinglib.models.planning.tonight_ranking import CandidateStatus, TonightRanking
from wayfindinglib.models.quality.rule import QualityRule
from wayfindinglib.models.quality.statements import Confidence, StatementKind, TargetStatement
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    ObservationSession,
    PlanRevisionConflictError,
)

logger = logging.getLogger(__name__)

_CONFIDENCE_ORDER = (Confidence.MEASURED, Confidence.PARTLY_MEASURED, Confidence.DESIGN_ESTIMATE)
"""From the best backed to the least."""


@dataclass
class RuleRow:
    """One suggested exposure row made from one statement.

    Parameters
    ----------
    key : `str`
        The row's source key (rule, target, capture setting).
    request : `ExposureRequest`
        The row, with its source key, reason and fingerprint filled in.
    fingerprint : `str`
        The row's evidence fingerprint.
    rule_name : `str`
        The rule that made it.
    confidence : `Confidence`
        How well that rule is backed.
    evidence_row_ids : `list` [`int`]
        The stored measurement rows the statement cites.
    """

    key: str
    request: ExposureRequest
    fingerprint: str
    rule_name: str
    confidence: Confidence
    evidence_row_ids: list[int] = field(default_factory=list)


@dataclass
class RuleReading:
    """What a target's statements say about suggested rows.

    Parameters
    ----------
    rows : `list` [`RuleRow`]
        The rows the package-row rules ask for.
    stopped : `bool`
        `True` when a stop rule says more frames will not help.
    missing : `bool`
        `True` when a package-row rule could not speak (missing data), so
        nothing may be deleted because of it.
    checklist : `list` [`TargetStatement`]
        Statements whose rule makes a checklist item.
    """

    rows: list[RuleRow] = field(default_factory=list)
    stopped: bool = False
    missing: bool = False
    checklist: list[TargetStatement] = field(default_factory=list)


class PackageWriter:
    """Writes packages with revision checks; keeps the log and change list.

    Parameters
    ----------
    butler : `DiskButler`
        The storage.
    now : `datetime`
        The time stamped on every write and log line.
    change : `PackageChange`
        The result the refresh builds. Ids are added as writes succeed.
    """

    def __init__(self, butler: DiskButler, now: datetime, change: PackageChange) -> None:
        self.butler = butler
        self.now = now
        self.change = change

    def log(
        self,
        event: SuggestionEvent,
        package: ObservationPackage,
        *,
        rule_name: str = "",
        source_key: str = "",
        cause: str = "",
        evidence_row_ids: Iterable[int] = (),
        values: Mapping[str, float | str | None] | None = None,
    ) -> None:
        """Add one line to the suggestion lifecycle log.

        Parameters
        ----------
        event : `SuggestionEvent`
            What happened.
        package : `ObservationPackage`
            The package it happened to.
        rule_name : `str`, optional
            The rule behind it. Taken from the package's suggestion when empty.
        source_key : `str`, optional
            The row key, when the event is about one row.
        cause : `str`, optional
            Why it happened.
        evidence_row_ids : `Iterable` [`int`], optional
            The stored measurement rows behind it.
        values : `Mapping`, optional
            The numbers that mattered.
        """
        if not rule_name and package.suggestion is not None:
            rule_name = package.suggestion.rule_name
        entry = SuggestionLogEntry(
            time=self.now,
            event=event,
            package_id=package.id,
            target_id=package.target_id,
            rule_name=rule_name,
            source_key=source_key,
            cause=cause,
            evidence_row_ids=sorted({int(row_id) for row_id in evidence_row_ids}),
            values=dict(values or {}),
        )
        self.butler.put(entry, "package_suggestion_event", {"id": entry.id})

    def create(self, package: ObservationPackage, **log_fields: Any) -> None:
        """Store a new suggested package and log it.

        Parameters
        ----------
        package : `ObservationPackage`
            The new package.
        **log_fields : `Any`
            Passed to `log`.
        """
        self.butler.put(package, "observation_package", {"id": package.id})
        self.change.added.append(package.id)
        self.log(SuggestionEvent.CREATED, package, **log_fields)

    def update(
        self,
        package: ObservationPackage,
        loaded_revision: int,
        *,
        event: SuggestionEvent = SuggestionEvent.UPDATED,
        **log_fields: Any,
    ) -> None:
        """Store a changed package if nobody changed it since it was read.

        Parameters
        ----------
        package : `ObservationPackage`
            The changed package, still carrying the revision it was read at.
        loaded_revision : `int`
            That revision.
        event : `SuggestionEvent`, optional
            The log event. Defaults to `UPDATED`.
        **log_fields : `Any`
            Passed to `log`.

        Raises
        ------
        PlanRevisionConflictError
            If the stored revision moved, or the package is gone.
        """
        package.revision = loaded_revision + 1
        if not self.butler.put_package_if_revision(package, loaded_revision):
            raise PlanRevisionConflictError(
                "The package changed while the suggestions were refreshed.",
                details={"package_id": package.id, "expected_revision": loaded_revision},
            )
        self.log(event, package, **log_fields)

    def delete(self, package: ObservationPackage, cause: str, **log_fields: Any) -> None:
        """Delete a suggested package if still suggested at that revision.

        Parameters
        ----------
        package : `ObservationPackage`
            The package as read.
        cause : `str`
            Why it is deleted, for the log.
        **log_fields : `Any`
            Passed to `log`.

        Raises
        ------
        PlanRevisionConflictError
            If the package was edited, promoted or removed since it was read.
        """
        if not self.butler.delete_package(
            package.id, expected_revision=package.revision, only_status=PackageStatus.SUGGESTED.value
        ):
            raise PlanRevisionConflictError(
                "The package changed while the suggestions were refreshed.",
                details={"package_id": package.id, "expected_revision": package.revision},
            )
        self.change.deleted.append(package.id)
        self.log(SuggestionEvent.DELETED, package, cause=cause, **log_fields)


def read_rule_statements(
    target_id: str,
    statements: Iterable[TargetStatement],
    rules: Mapping[str, QualityRule],
    make_request: Callable[[TargetStatement], ExposureRequest | None],
) -> RuleReading:
    """Sort a target's statements by what their rules make of them.

    Parameters
    ----------
    target_id : `str`
        The target.
    statements : `Iterable` [`TargetStatement`]
        The statements of the target's quality report.
    rules : `Mapping` [`str`, `QualityRule`]
        Every rule by name. A rule's `produces` says what its action becomes.
    make_request : `Callable`
        `exposure_request_from_statement`: turns a recommendation into an
        exposure row, or `None` when it cannot.

    Returns
    -------
    reading : `RuleReading`
        The rows, whether a stop rule spoke, whether data was missing and
        the checklist statements.
    """
    reading = RuleReading()
    seen: set[str] = set()
    for statement in statements:
        rule = rules.get(statement.rule)
        produces = getattr(rule, "produces", "none")
        if statement.rule in STOP_RULES and statement.kind == StatementKind.WARNING:
            reading.stopped = True
        elif produces == "checklist_item" and statement.kind in (
            StatementKind.WARNING,
            StatementKind.RECOMMENDATION,
        ):
            reading.checklist.append(statement)
        elif produces == "package_row" and statement.kind == StatementKind.INSUFFICIENT_DATA:
            reading.missing = True
        elif produces == "package_row" and statement.kind == StatementKind.RECOMMENDATION:
            request = make_request(statement)
            if request is None:
                reading.missing = True
                continue
            key = row_source_key(statement.rule, target_id, statement.capture_setting)
            if key in seen:
                continue
            seen.add(key)
            fingerprint = evidence_fingerprint(
                rule, statement.rule, statement.kind.value, statement.proposed_action
            )
            reading.rows.append(
                RuleRow(
                    key=key,
                    request=request.model_copy(
                        update={
                            "source_key": key,
                            "reason": statement.plain_message or statement.message,
                            "evidence_fingerprint": fingerprint,
                        }
                    ),
                    fingerprint=fingerprint,
                    rule_name=statement.rule,
                    confidence=statement.confidence,
                    evidence_row_ids=[
                        reference.row_id for reference in statement.evidence if reference.row_id is not None
                    ],
                )
            )
    return reading


def checklist_items_from(statements: Iterable[TargetStatement]) -> list[ChecklistItem]:
    """Make checklist items from the statements of calibration rules.

    Parameters
    ----------
    statements : `Iterable` [`TargetStatement`]
        Statements whose rule makes a checklist item.

    Returns
    -------
    items : `list` [`ChecklistItem`]
        One item for each distinct rule and capture setting.
    """
    items: list[ChecklistItem] = []
    keys: set[tuple[str, Any]] = set()
    for statement in statements:
        key = (statement.rule, statement.capture_setting)
        if key in keys:
            continue
        keys.add(key)
        items.append(
            ChecklistItem(
                rule=statement.rule,
                capture_setting=statement.capture_setting,
                label=statement.plain_message or statement.message,
            )
        )
    return items


def held_source_keys(
    packages: Iterable[ObservationPackage],
    sessions: Iterable[ObservationSession],
    tonight: str,
    noise_measured_at: Callable[[str], datetime | None],
) -> set[str]:
    """Collect the source keys the refresh must not suggest again.

    A key is held by a planned package, by a queue entry of a session whose
    night is tonight or later, and by a complete package until the target's
    stack was made again after the package completed.

    Parameters
    ----------
    packages : `Iterable` [`ObservationPackage`]
        Every package.
    sessions : `Iterable` [`ObservationSession`]
        Every session.
    tonight : `str`
        The current observing night, such as ``"2026-10-25"``.
    noise_measured_at : `Callable`
        Gives the time of a target's newest stack noise row, or `None`.

    Returns
    -------
    keys : `set` [`str`]
        The held keys. A promoted ranking suggestion holds its own key.
    """
    packages = list(packages)
    keys: set[str] = set()
    for package in packages:
        if package.status == PackageStatus.SUGGESTED:
            continue
        if package.status == PackageStatus.COMPLETE and package.completed_at is not None:
            measured = noise_measured_at(package.target_id)
            if measured is not None and measured > package.completed_at:
                continue
        keys.update(row.source_key for row in package.exposure_requests if row.source_key)
        if package.suggestion is not None and package.suggestion.source_key:
            keys.add(package.suggestion.source_key)
    complete_ids = {package.id for package in packages if package.status == PackageStatus.COMPLETE}
    for session in sessions:
        if session.night_date.isoformat() < tonight:
            continue
        for entry in session.queue:
            if entry.observation_package_id in complete_ids:
                continue  # a complete package holds its key by its own rule above
            keys.update(row.source_key for row in entry.exposure_requests if row.source_key)
    return keys


def _weakest(confidences: Iterable[Confidence]) -> Confidence:
    """Pick the least backed of some confidences.

    Parameters
    ----------
    confidences : `Iterable` [`Confidence`]
        The confidences.

    Returns
    -------
    weakest : `Confidence`
        The weakest one, or `DESIGN_ESTIMATE` when there are none.
    """
    found = list(confidences)
    if not found:
        return Confidence.DESIGN_ESTIMATE
    return max(found, key=_CONFIDENCE_ORDER.index)


def _rule_package_of(
    packages: Iterable[ObservationPackage], target_id: str
) -> tuple[ObservationPackage | None, list[ObservationPackage]]:
    """Find a target's one suggested rule package, and any extra ones.

    Parameters
    ----------
    packages : `Iterable` [`ObservationPackage`]
        Every package.
    target_id : `str`
        The target.

    Returns
    -------
    keeper, extras : `ObservationPackage` or `None`, `list`
        The newest suggested rule package of the target, and the older ones.
    """
    found = sorted(
        (
            package
            for package in packages
            if package.target_id == target_id
            and package.status == PackageStatus.SUGGESTED
            and package.suggestion is not None
            and package.suggestion.source_kind == "rule"
        ),
        key=lambda package: package.created_at,
        reverse=True,
    )
    return (found[0] if found else None), found[1:]


def refresh_rule_package(
    writer: PackageWriter,
    packages: list[ObservationPackage],
    target_id: str,
    reading: RuleReading,
    noise: TargetNoise | None,
    noise_goal: float,
    held_keys: Collection[str],
) -> None:
    """Create, update or delete a target's suggested package of rule rows.

    A suggestion for more frames stands only while the target's noise is
    above `noise_goal`. It is deleted only when the rules say frames are no
    longer needed (the noise is at or below the goal, or a stop rule spoke).
    A target with no noise reading, or whose rules lacked data, changes
    nothing: missing data never deletes a suggestion.

    Parameters
    ----------
    writer : `PackageWriter`
        Writes with revision checks and logs.
    packages : `list` [`ObservationPackage`]
        Every package, as read for this refresh.
    target_id : `str`
        The target.
    reading : `RuleReading`
        What the target's statements ask for.
    noise : `TargetNoise` or `None`
        The target's noise, or `None` when unknown.
    noise_goal : `float`
        The default noise goal.
    held_keys : `Collection` [`str`]
        Source keys held by a planned package, a complete one or a queue
        entry. They are not suggested.
    """
    keeper, extras = _rule_package_of(packages, target_id)
    values: dict[str, float | str | None] = {
        "noise": None if noise is None else noise.value,
        "noise_goal": noise_goal,
    }
    noise_rows = [] if noise is None or noise.row_id is None else [noise.row_id]
    for extra in extras:
        writer.delete(extra, "a target has one suggested package", values=values)
    goal_met = noise is not None and noise.value <= noise_goal
    if reading.stopped or goal_met:
        if keeper is not None:
            cause = "a rule says more frames will not help" if reading.stopped else "the noise goal is met"
            writer.delete(keeper, cause, evidence_row_ids=noise_rows, values=values)
        return
    if noise is None or not reading.rows:
        # No noise reading, or the rules said nothing this time (missing
        # data): leave the suggestion as it is.
        return
    desired = [row for row in reading.rows if row.key not in held_keys]
    all_fingerprint = package_fingerprint(row.fingerprint for row in desired)
    if keeper is None:
        if not desired:
            return
        _create_rule_package(writer, target_id, desired, all_fingerprint, noise_rows, values)
        return
    _update_rule_package(writer, keeper, reading, desired, held_keys, all_fingerprint, noise_rows, values)


def _create_rule_package(
    writer: PackageWriter,
    target_id: str,
    rows: list[RuleRow],
    fingerprint: str,
    noise_rows: list[int],
    values: dict[str, float | str | None],
) -> None:
    """Create a target's suggested package from its rows.

    Parameters
    ----------
    writer : `PackageWriter`
        The writer.
    target_id : `str`
        The target.
    rows : `list` [`RuleRow`]
        The rows, none of them held.
    fingerprint : `str`
        The package's evidence fingerprint.
    noise_rows : `list` [`int`]
        The noise row id, for the log.
    values : `dict`
        The numbers for the log.
    """
    suggestion = PackageSuggestion(
        source_kind="rule",
        rule_name=", ".join(sorted({row.rule_name for row in rows})),
        source_key=f"rules|{target_id}",
        reason=rows[0].request.reason,
        confidence=_weakest(row.confidence for row in rows).value,
        evidence_fingerprint=fingerprint,
        created_at=writer.now,
        updated_at=writer.now,
    )
    package = ObservationPackage(
        id=str(uuid.uuid4()),
        name=target_id,
        target_id=target_id,
        exposure_requests=[row.request for row in rows],
        status=PackageStatus.SUGGESTED,
        suggestion=suggestion,
        created_at=writer.now,
    )
    writer.create(
        package,
        source_key=suggestion.source_key,
        evidence_row_ids=[*noise_rows, *(row_id for row in rows for row_id in row.evidence_row_ids)],
        values={
            **values,
            "frame_count": float(sum(row.request.count for row in rows)),
            "rows": float(len(rows)),
        },
    )


def _update_rule_package(
    writer: PackageWriter,
    package: ObservationPackage,
    reading: RuleReading,
    desired: list[RuleRow],
    held_keys: Collection[str],
    fingerprint: str,
    noise_rows: list[int],
    values: dict[str, float | str | None],
) -> None:
    """Bring a suggested rule package in line with the rules.

    Parameters
    ----------
    writer : `PackageWriter`
        The writer.
    package : `ObservationPackage`
        The package as read.
    reading : `RuleReading`
        What the statements ask for.
    desired : `list` [`RuleRow`]
        The rows the rules ask for that no planned package holds.
    held_keys : `Collection` [`str`]
        Held source keys.
    fingerprint : `str`
        The new evidence fingerprint of the package.
    noise_rows : `list` [`int`]
        The noise row id, for the log.
    values : `dict`
        The numbers for the log.
    """
    suggestion = package.suggestion
    if suggestion is None:
        return
    loaded_revision = package.revision
    before = package.model_dump(mode="json")
    dismissed = {item.source_key: item for item in suggestion.dismissed_rows}
    rows = {row.source_key or str(index): row for index, row in enumerate(package.exposure_requests)}
    for desired_row in desired:
        held = dismissed.get(desired_row.key)
        if held is not None:
            if held.fingerprint == desired_row.fingerprint:
                continue
            del dismissed[desired_row.key]
        existing = rows.get(desired_row.key)
        if existing is None or existing.evidence_fingerprint != desired_row.fingerprint:
            rows[desired_row.key] = desired_row.request
    if reading.rows:
        # The rules spoke, so a row they no longer ask for (or that a planned
        # package now holds) goes. When they were silent, rows stay.
        wanted = {row.key for row in desired}
        rows = {key: row for key, row in rows.items() if key in wanted}
    package.exposure_requests = list(rows.values())
    suggestion.dismissed_rows = list(dismissed.values())
    if suggestion.dismissed_fingerprint and suggestion.dismissed_fingerprint != fingerprint:
        suggestion.dismissed_fingerprint = ""
    suggestion.evidence_fingerprint = fingerprint
    if package.exposure_requests:
        suggestion.reason = package.exposure_requests[0].reason
    if not package.exposure_requests and not suggestion.dismissed_rows:
        writer.delete(package, "no rows are left", evidence_row_ids=noise_rows, values=values)
        return
    suggestion.rule_name = ", ".join(sorted({row.rule_name for row in desired})) or suggestion.rule_name
    suggestion.confidence = (
        _weakest(row.confidence for row in desired).value if desired else suggestion.confidence
    )
    if package.model_dump(mode="json") == before:
        return
    suggestion.updated_at = writer.now
    writer.update(
        package,
        loaded_revision,
        source_key=suggestion.source_key,
        evidence_row_ids=[*noise_rows, *(row_id for row in desired for row_id in row.evidence_row_ids)],
        values={
            **values,
            "frame_count": float(sum(row.count for row in package.exposure_requests)),
            "rows": float(len(package.exposure_requests)),
        },
    )
    writer.change.updated.append(package.id)


def refresh_ranking_packages(
    writer: PackageWriter,
    packages: list[ObservationPackage],
    ranking: TonightRanking | None,
    night_id: str,
    per_use: int,
    held_keys: Collection[str],
    queued_target_ids: Collection[str],
) -> None:
    """Make tonight's ranking into suggested packages and remove stale ones.

    The top `per_use` ranked targets of each use get a suggested package with
    no rows, labeled as a design estimate, tied to tonight. The order is the
    ranking's: a target whose count is only exploratory (decision D11) sits
    below one of about the same gain, and its reason says so. A ranking package
    whose night has ended is deleted. When the ranking is known and a package
    of tonight fell out of the top, it is deleted. When the ranking could not
    be made (`ranking` is `None`), nothing but the expiry changes.

    Parameters
    ----------
    writer : `PackageWriter`
        The writer.
    packages : `list` [`ObservationPackage`]
        Every package, as read.
    ranking : `TonightRanking` or `None`
        Tonight's ranking, or `None` when it could not be made.
    night_id : `str`
        The current observing night.
    per_use : `int`
        How many targets of each use to suggest.
    held_keys : `Collection` [`str`]
        Source keys held by a planned or complete package.
    queued_target_ids : `Collection` [`str`]
        Targets already in tonight's queue. They are not suggested.
    """
    existing = {
        package.suggestion.source_key: package
        for package in packages
        if package.status == PackageStatus.SUGGESTED
        and package.suggestion is not None
        and package.suggestion.source_kind == "ranking"
    }
    for key, package in list(existing.items()):
        suggestion = package.suggestion
        if suggestion is not None and (suggestion.observing_night_id or night_id) < night_id:
            writer.delete(package, "the night ended")
            del existing[key]
    if ranking is None:
        return
    wanted: dict[str, tuple[str, str, str]] = {}
    for ranked_list in ranking.lists:
        picked = [
            candidate
            for candidate in sorted(ranked_list.candidates, key=lambda item: item.rank or 10**9)
            if candidate.status == CandidateStatus.RANKED and candidate.rank is not None
        ][:per_use]
        for candidate in picked:
            key = ranking_source_key(candidate.target_id, ranked_list.use.value, night_id)
            if key in held_keys or candidate.target_id in queued_target_ids:
                continue
            gain = candidate.gain_per_hour_fraction
            reason = f"Ranked number {candidate.rank} for {ranked_list.use.value} tonight." + (
                f" We expect one more hour to cut its noise by roughly {gain * 100:.0f}%."
                if gain is not None
                else ""
            )
            if candidate.forecast_gate == ForecastGate.EXPLORATORY:
                reason += " Its count of frames to take is exploratory, so it ranks below a confirmed one."
            wanted[key] = (candidate.target_id, ranked_list.use.value, reason)
            _store_ranking_package(
                writer, existing.get(key), key, candidate, ranked_list.use.value, night_id, reason
            )
    for key, package in existing.items():
        if key not in wanted and (package.suggestion.observing_night_id or night_id) == night_id:
            writer.delete(package, "no longer in the top of tonight's ranking")


def _store_ranking_package(
    writer: PackageWriter,
    package: ObservationPackage | None,
    key: str,
    candidate: Any,
    use: str,
    night_id: str,
    reason: str,
) -> None:
    """Create a ranking suggestion, or refresh the reason of an existing one.

    Parameters
    ----------
    writer : `PackageWriter`
        The writer.
    package : `ObservationPackage` or `None`
        The existing package for this key, or `None`.
    key : `str`
        The source key.
    candidate : `RankedCandidate`
        The ranked target.
    use : `str`
        The ranked use.
    night_id : `str`
        The observing night.
    reason : `str`
        The plain reason.
    """
    values = {
        "rank": float(candidate.rank),
        "gain_per_hour": candidate.gain_per_hour_fraction,
        "use": use,
    }
    evidence = [reference.row_id for reference in candidate.evidence if reference.row_id is not None]
    if package is None:
        suggestion = PackageSuggestion(
            source_kind="ranking",
            rule_name="ranking",
            source_key=key,
            reason=reason,
            confidence=Confidence.DESIGN_ESTIMATE.value,
            evidence_fingerprint=ranking_fingerprint(candidate.target_id, use, night_id),
            ranking_use=use,
            observing_night_id=night_id,
            created_at=writer.now,
            updated_at=writer.now,
        )
        writer.create(
            ObservationPackage(
                id=str(uuid.uuid4()),
                name=f"{candidate.target_id} tonight ({use})",
                target_id=candidate.target_id,
                status=PackageStatus.SUGGESTED,
                suggestion=suggestion,
                created_at=writer.now,
            ),
            rule_name="ranking",
            source_key=key,
            evidence_row_ids=evidence,
            values=values,
        )
        return
    suggestion = package.suggestion
    if suggestion is None or suggestion.reason == reason:
        return
    loaded_revision = package.revision
    suggestion.reason = reason
    suggestion.updated_at = writer.now
    writer.update(
        package,
        loaded_revision,
        rule_name="ranking",
        source_key=key,
        evidence_row_ids=evidence,
        values=values,
    )
    writer.change.updated.append(package.id)


def refresh_completion(
    writer: PackageWriter,
    packages: list[ObservationPackage],
    sessions: list[ObservationSession],
    reader: TargetFrameReader,
    equipment_for: Callable[[ObservationSession], EquipmentNames],
    target_ids: Collection[str] | None,
) -> None:
    """Mark packages complete, or set them back, from the frames captured.

    A planned package whose every light row is met (judged frames reach the
    count, counted from the row's own time) becomes complete. A complete
    package that is no longer met (a frame was set aside) becomes planned
    again. The counting uses the camera and telescope of the newest session
    that queued the package. A package with no session, no light row, or
    frames that could not be read is left alone.

    Parameters
    ----------
    writer : `PackageWriter`
        The writer.
    packages : `list` [`ObservationPackage`]
        Every package, as read.
    sessions : `list` [`ObservationSession`]
        Every session.
    reader : `TargetFrameReader`
        Reads each target's frames and judgments.
    equipment_for : `Callable`
        Gives a session's camera and telescope names.
    target_ids : `Collection` [`str`] or `None`
        Look only at these targets; `None` for every target.
    """
    for package in packages:
        if package.status not in (PackageStatus.PLANNED, PackageStatus.COMPLETE):
            continue
        if target_ids is not None and package.target_id not in target_ids:
            continue
        light_rows = [row for row in package.exposure_requests if row.frame_type == FrameType.LIGHT]
        session = newest_session_with(package.id, sessions)
        if not light_rows or session is None:
            continue
        target_frames = reader.read(package.target_id)
        if target_frames.note and not target_frames.frames:
            writer.change.notes.append(f"{package.target_id}: {target_frames.note}")
            continue
        equipment = equipment_for(session)
        counts = [
            count_request_frames(row, target_frames, equipment, package.created_at) for row in light_rows
        ]
        complete = package_is_complete(counts)
        loaded_revision = package.revision
        values = {
            "requested": float(sum(count.requested for count in counts)),
            "judged": float(sum(min(count.judged, count.requested) for count in counts)),
            "not_judged": float(sum(count.not_judged for count in counts)),
            "flagged": float(sum(count.flagged for count in counts)),
        }
        if package.status == PackageStatus.PLANNED and complete:
            package.status = PackageStatus.COMPLETE
            package.completed_at = writer.now
            writer.update(
                package,
                loaded_revision,
                event=SuggestionEvent.COMPLETED,
                cause="every light row is captured",
                values=values,
            )
            writer.change.completed.append(package.id)
        elif package.status == PackageStatus.COMPLETE and not complete:
            package.status = PackageStatus.PLANNED
            package.completed_at = None
            writer.update(
                package,
                loaded_revision,
                event=SuggestionEvent.REOPENED,
                cause="frames were set aside, so the rows are no longer met",
                values=values,
            )
            writer.change.reverted.append(package.id)


def newest_session_with(package_id: str, sessions: Iterable[ObservationSession]) -> ObservationSession | None:
    """Find the newest session that queued a package.

    Parameters
    ----------
    package_id : `str`
        The package id.
    sessions : `Iterable` [`ObservationSession`]
        Every session.

    Returns
    -------
    session : `ObservationSession` or `None`
        The session with the latest night (then the newest creation time)
        that has a queue entry for the package, or `None`.
    """
    holders = [
        session
        for session in sessions
        if any(entry.observation_package_id == package_id for entry in session.queue)
    ]
    if not holders:
        return None
    return max(holders, key=lambda session: (session.night_date, session.created_at))
