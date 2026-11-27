"""Purpose: Record the suggested packages, checklist items and completions.

Description: `record_suggestions` is the owner behind
``wayfinder.planning.packages.record_suggestions``. One run does four things:

* **Rule suggestions.** While a target's noise is above the library's default
  noise goal and its quality report asks for frames, the target gets one
  suggested package of exposure rows. Missing data never deletes a
  suggestion.
* **Calibration suggestions.** A rule whose action is a checklist item adds
  it to tonight's session checklist.
* **Ranking suggestions.** The top targets of each use in tonight's ranking
  become suggested packages with no rows, tied to tonight. The ranking is
  read only when every target is refreshed.
* **Completion.** A planned package whose light rows are all captured and
  judged becomes complete; a complete one that is no longer met becomes
  planned again.

Every write checks the revision it read (a lost race is read again, up to
three times), and every change is written to the suggestion log. The steps
themselves are in `suggestion_tasks`.
"""

import logging
import sqlite3
from collections.abc import Callable, Sequence
from datetime import date
from functools import partial
from typing import Any

from astrometricslib import Target
from astrometricslib.arguments import observing_night_id
from astrometricslib.exceptions import AstrometricsError
from astrometricslib.parameters import resolve_parameter
from wayfindinglib.analytics.frame_counting import TargetFrameReader
from wayfindinglib.models.planning.observation_package import ObservationPackage
from wayfindinglib.models.planning.suggestion_log import PackageChange
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.observation_session import ObservationSession, PlanRevisionConflictError
from wayfindinglib.tasks.planning_tasks import planning_context
from wayfindinglib.tasks.planning_tasks.planning_context import (
    PlanningContext,
    equipment_names,
    session_for_night,
)

__all__ = ["record_suggestions"]

logger = logging.getLogger(__name__)


def _refresh_rule_package_of(
    tasks: Any,
    target_id: str,
    reading: Any,
    noise: Any,
    depth_met: bool | None,
    held_keys: Callable[[], set[str]],
    writer: Any,
    packages: list[ObservationPackage],
) -> None:
    """Refresh one target's suggested package of rule rows.

    Parameters
    ----------
    tasks : `module`
        The suggestion tasks module.
    target_id : `str`
        The target.
    reading : `RuleReading`
        What the target's statements ask for.
    noise : `TargetNoise` or `None`
        The target's noise.
    depth_met : `bool` or `None`
        Whether the target reached its chosen depth level, or `None` when it
        has no depth goal.
    held_keys : `Callable`
        Gives the source keys no suggestion may take now.
    writer : `PackageWriter`
        Writes with revision checks and logs.
    packages : `list` [`ObservationPackage`]
        Every package, freshly read.
    """
    from wayfindinglib.analytics.package_suggestions import DEFAULT_NOISE_GOAL

    tasks.refresh_rule_package(
        writer,
        packages,
        target_id,
        reading,
        noise,
        resolve_parameter(DEFAULT_NOISE_GOAL).value,
        held_keys(),
        depth_goal_met=depth_met,
    )


def _add_calibration_items(
    context: PlanningContext,
    session: ObservationSession | None,
    statements: list[TargetStatement],
    change: PackageChange,
) -> None:
    """Add the checklist items that calibration statements ask for.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session : `ObservationSession` or `None`
        Tonight's session. With `None`, nothing is added (a note says so).
    statements : `list` [`TargetStatement`]
        Statements whose rule makes a checklist item.
    change : `PackageChange`
        The refresh result. The new item ids and any note are added.
    """
    from wayfindinglib.tasks.planning_tasks.session_records import edit_session
    from wayfindinglib.tasks.planning_tasks.suggestion_tasks import checklist_items_from

    items = checklist_items_from(statements)
    if not items:
        return
    if session is None:
        change.notes.append("Tonight has no session, so calibration items were not added to a checklist.")
        return
    for _attempt in range(3):
        stored = context.butler.get("observation_session", {"session_id": session.id})
        if stored is None:
            break
        try:
            result = edit_session(context, stored, checklist=[*stored.checklist, *items])
        except PlanRevisionConflictError:
            continue
        change.checklist_item_ids.extend(
            item.entry_id for item in result.changes if item.kind == "checklist_added" and item.entry_id
        )
        return
    change.notes.append("The checklist kept changing, so calibration items were left for the next refresh.")


def record_suggestions(context: PlanningContext, targets: Sequence[Target] | None = None) -> PackageChange:
    """Update the suggested packages, the checklist and finished packages.

    The run is recorded as a ``planning`` job (or joins the job it runs in).

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    targets : `Sequence` [`Target`], optional
        The targets to refresh. `None` refreshes every library target and
        tonight's ranking.

    Returns
    -------
    change : `PackageChange`
        What changed.
    """
    from astrometricslib.jobs import registered_job

    with registered_job(job_type="planning", target_id="package_suggestions"):
        return _record_suggestions(context, targets)


def _record_suggestions(context: PlanningContext, targets: Sequence[Target] | None) -> PackageChange:
    """Run the suggestion refresh. See `record_suggestions`.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    targets : `Sequence` [`Target`], optional
        The targets to refresh (after new frames were measured). `None`
        refreshes every library target and tonight's ranking.

    Returns
    -------
    change : `PackageChange`
        The ids of the packages added, updated, deleted, completed and
        reverted, the checklist items added, and notes on anything skipped.
    """
    from wayfindinglib.analytics.package_suggestions import RANKING_SUGGESTIONS_PER_USE, read_target_noise
    from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES
    from wayfindinglib.tasks.planning_tasks import advisory_tasks
    from wayfindinglib.tasks.planning_tasks import suggestion_tasks as tasks
    from wayfindinglib.tasks.planning_tasks.depth_goal_tasks import depth_goal_met
    from wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks import rank_tonight

    astrometrics = context.astrometrics
    butler = context.butler
    now = planning_context.utc_now()
    change = PackageChange(as_of=now)
    night_id = observing_night_id(now.timestamp())
    tonight_session = session_for_night(context, date.fromisoformat(night_id))
    rules = {rule.name: rule for rule in ALL_RULES}
    try:
        from wayfindinglib.analytics.result_quality.exposure_request import exposure_request_from_statement
    except ImportError:
        exposure_request_from_statement = None
        change.notes.append("Rule suggestions are not available: no function turns a statement into a row.")
    target_ids = (
        list(dict.fromkeys(target.id for target in targets))
        if targets is not None
        else [target.id for target in astrometrics.targets.query(include_empty=True).items]
    )
    reader = TargetFrameReader(astrometrics)
    noise_by_target: dict[str, Any] = {}

    def noise_for(target_id: str) -> Any:
        """Read a target's noise once per refresh.

        Parameters
        ----------
        target_id : `str`
            The target.

        Returns
        -------
        noise : `TargetNoise` or `None`
            The target's noise, or `None` when unknown.
        """
        if target_id not in noise_by_target:
            noise_by_target[target_id] = read_target_noise(astrometrics, target_id)
        return noise_by_target[target_id]

    def held_keys() -> set[str]:
        """Collect the source keys no suggestion may take now.

        Returns
        -------
        keys : `set` [`str`]
            The held keys, read fresh.
        """
        return tasks.held_source_keys(
            butler.get_all("observation_package"),
            butler.get_all("observation_session"),
            night_id,
            lambda target_id: getattr(noise_for(target_id), "measured_at", None),
        )

    def retrying(work: Any, label: str) -> None:
        """Run one write step, reading again when it loses a race.

        Parameters
        ----------
        work : `Callable`
            Takes a `PackageWriter` and the freshly read packages.
        label : `str`
            What the step is, for the note when it gives up.
        """
        for _attempt in range(3):
            writer = tasks.PackageWriter(butler, now, change)
            try:
                work(writer, butler.get_all("observation_package"))
                return
            except PlanRevisionConflictError:
                continue
        change.notes.append(f"{label}: a package kept changing, so it was left for the next refresh.")

    checklist_statements: list[TargetStatement] = []
    for target_id in target_ids:
        if exposure_request_from_statement is None:
            break
        try:
            report = advisory_tasks.build_advisory(
                "quality", target_id, astrometrics=astrometrics, butler=butler, config=context.config
            )
        except (AstrometricsError, OSError, sqlite3.Error, ValueError) as error:
            logger.warning("Suggestions: no report for %s: %s", target_id, error)
            change.notes.append(f"{target_id}: no report ({error}). Its suggestions were left as they are.")
            continue
        reading = tasks.read_rule_statements(
            target_id, report.statements, rules, exposure_request_from_statement
        )
        checklist_statements.extend(reading.checklist)
        depth_met = depth_goal_met(astrometrics, butler, target_id)
        retrying(
            partial(
                _refresh_rule_package_of,
                tasks,
                target_id,
                reading,
                noise_for(target_id),
                depth_met,
                held_keys,
            ),
            target_id,
        )
    _add_calibration_items(context, tonight_session, checklist_statements, change)
    ranking = None
    if targets is None:
        try:
            ranking = rank_tonight(now, astrometrics=astrometrics, butler=butler, config=context.config)
        except (AstrometricsError, OSError, sqlite3.Error, ValueError) as error:
            logger.warning("Suggestions: no ranking: %s", error)
            change.notes.append(
                f"No ranking for tonight ({error}). Ranking suggestions were left as they are."
            )
    queued_targets = {entry.target_id for entry in tonight_session.queue} if tonight_session else set()
    ranking_suggestions_per_use = resolve_parameter(RANKING_SUGGESTIONS_PER_USE).value
    retrying(
        lambda writer, packages: tasks.refresh_ranking_packages(
            writer, packages, ranking, night_id, ranking_suggestions_per_use, held_keys(), queued_targets
        ),
        "ranking",
    )
    retrying(
        lambda writer, packages: tasks.refresh_completion(
            writer,
            packages,
            butler.get_all("observation_session"),
            reader,
            partial(equipment_names, context),
            None if targets is None else set(target_ids),
        ),
        "completion",
    )
    for name in ("added", "updated", "deleted", "completed", "reverted"):
        setattr(change, name, list(dict.fromkeys(getattr(change, name))))
    return change
