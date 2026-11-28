"""Purpose: `PlanningPackages`, the observation packages of planning.

Reached as ``wayfinder.planning.packages``.

Description: A package is a reusable imaging request for one target: its
exposure rows, dithering, altitude limit and priority. People make packages
by hand; the library suggests others (`record_suggestions`) and marks a
package complete when its light frames are in. `get`, `get_for_target` and
`query` read packages; `create`, `update`, `restore` and `delete` change them.
A target's depth goal and its mosaic panels live here too. Every change checks
the revision the package object carries. Each method is a thin wrapper of one
owner function in `wayfindinglib.tasks.planning_tasks`.
"""

from typing import Any

from astrometricslib import Target
from astrometricslib.arguments import Page
from astrometricslib.jobs import background_job
from wayfindinglib._internal.renames import MovedNames
from wayfindinglib.models.planning.depth_goal import DepthGoalStatus, DepthLevel
from wayfindinglib.models.planning.mosaic import MosaicPanel, MosaicPlan
from wayfindinglib.models.planning.observation_package import (
    DitherConfig,
    ExposureRequest,
    ObservationPackage,
    PackageStatus,
)
from wayfindinglib.models.planning.suggestion_log import PackageChange, SuggestionLogEntry
from wayfindinglib.tasks.planning_tasks.planning_context import PlanningContext

__all__ = ["PlanningPackages"]


class PlanningPackages(MovedNames):
    """The stored observation packages and the library's suggestions.

    Parameters
    ----------
    context : `PlanningContext`
        The settings and storage the planning objects share.
    """

    def __init__(self, context: PlanningContext) -> None:
        self._context = context

    def get(self, package_id: str, required: bool = False) -> ObservationPackage | None:
        """Read one stored package by its id.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.get_package

        Parameters
        ----------
        package_id : `str`
            The package id.
        required : `bool`, optional
            Raise `NotFoundError` instead of returning `None`.

        Returns
        -------
        package : `ObservationPackage` or `None`
            The package, or `None` when no package has that id.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import get_package

        return get_package(self._context, package_id, required)

    def get_for_target(self, target: Target) -> ObservationPackage | None:
        """Read the package that stands for a target.

        That is the target's newest planned package, else its newest
        suggested one that is not dismissed. A complete package is never
        picked. The target is matched ignoring case, with ``_`` read as a
        space.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.get_package_for_target

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        package : `ObservationPackage` or `None`
            The package, or `None` when the target has none to pick.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import get_package_for_target

        return get_package_for_target(self._context, target)

    def query(
        self,
        *,
        status: PackageStatus | str | None = None,
        target: Target | None = None,
        dismissed: bool | None = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[ObservationPackage]:
        """List the stored packages, newest first.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.query_packages

        Parameters
        ----------
        status : `PackageStatus` or `str`, optional
            Only packages with this status (``"PLANNED"``, ``"SUGGESTED"``
            or ``"COMPLETE"``).
        target : `Target`, optional
            Only this target's packages.
        dismissed : `bool` or `None`, optional
            `False` (the default) leaves out the suggestions the user
            dismissed, `True` lists only them, `None` lists both.
        limit : `int`, optional
            Most packages to return. `None` (the default) returns every match.
        offset : `int`, optional
            How many matches to skip first. Defaults to 0.

        Returns
        -------
        packages : `Page` [`ObservationPackage`]
            The matching packages.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import query_packages

        return query_packages(self._context, status, target, dismissed, limit, offset)

    def query_suggestion_log(
        self, *, target: Target | None = None, limit: int | None = None, offset: int = 0
    ) -> Page[SuggestionLogEntry]:
        """List the suggestion lifecycle log, newest first.

        Each line says what happened to a suggested package (created,
        updated, deleted with the cause, dismissed, promoted, completed).

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.query_suggestion_log

        Parameters
        ----------
        target : `Target`, optional
            Only this target's lines.
        limit : `int`, optional
            Most lines to return. `None` (the default) returns every match.
        offset : `int`, optional
            How many matches to skip first.

        Returns
        -------
        entries : `Page` [`SuggestionLogEntry`]
            The log lines.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import query_suggestion_log

        return query_suggestion_log(self._context, target, limit, offset)

    def create(
        self,
        target: Target,
        exposures: list[ExposureRequest] | None = None,
        *,
        dither_config: DitherConfig | None = None,
        minimum_altitude_deg: float | None = None,
        priority: int = 0,
        quality_weighting_enabled: bool = False,
        notes: str = "",
    ) -> ObservationPackage:
        """Record a reusable imaging request for a target.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.create_package

        Parameters
        ----------
        target : `Target`
            The library target. A catalog name not in the library yet is added
            with ``astrometrics.targets.create(name, from_catalog=True)``.
        exposures : `list` [`ExposureRequest`], optional
            The exposure rows. A package may start with none.
        dither_config : `DitherConfig`, optional
            How the package dithers.
        minimum_altitude_deg : `float`, optional
            The lowest altitude the package may be imaged at.
        priority : `int`, optional
            Higher is placed first. Defaults to 0.
        quality_weighting_enabled : `bool`, optional
            Let the target's dataset report raise the priority during
            automatic placement. Defaults to `False`.
        notes : `str`, optional
            Free text kept with the package.

        Returns
        -------
        package : `ObservationPackage`
            The stored package.

        Notes
        -----
        Raises `InvalidArgumentError` if a value is not valid.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import create_package

        return create_package(
            self._context,
            target,
            exposures or (),
            dither_config,
            minimum_altitude_deg,
            priority,
            quality_weighting_enabled,
            notes,
        )

    def update(self, package: ObservationPackage, **changes: Any) -> ObservationPackage:
        """Change a stored package and return the new one.

        Plain changes set ``name``, ``exposure_requests``, ``dither_config``,
        ``minimum_altitude_deg``, ``priority``, ``quality_weighting_enabled``,
        ``notes`` or ``framing``. A new or changed exposure row starts
        counting frames from now; a row that only changes its count keeps its
        counting time. ``status="PLANNED"`` reopens a complete package: for
        each light row, the frames already captured are stored so they do not
        complete it again. ``dismissed=True`` hides a library suggestion until
        its evidence changes, and ``dismissed_rows=[source_key, ...]`` hides
        those rows of it; `restore` takes a dismissal back. A dismissal cannot
        be combined with other changes.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.update_package

        Parameters
        ----------
        package : `ObservationPackage`
            The package as last read; its revision must be the stored one.
        **changes : `Any`
            Field name -> new value.

        Returns
        -------
        package : `ObservationPackage`
            The stored package.

        Notes
        -----
        Raises `InvalidArgumentError` for an unknown name or a value that is
        not valid, `ConflictError` for a reopen of a package that is not
        complete or a dismissal of one that is not a suggestion, and
        `PlanRevisionConflictError` if the package changed since it was read.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import update_package

        return update_package(self._context, package, changes)

    def restore(
        self, package: ObservationPackage, source_keys: list[str] | None = None
    ) -> ObservationPackage:
        """Bring back a dismissed suggestion, or some of its dismissed rows.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.restore_package

        Parameters
        ----------
        package : `ObservationPackage`
            The package as last read.
        source_keys : `list` [`str`], optional
            Bring back only these rows.

        Returns
        -------
        package : `ObservationPackage`
            The stored package.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import restore_package

        return restore_package(self._context, package, source_keys)

    def delete(self, package: ObservationPackage) -> None:
        """Remove a stored package.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.delete_package

        Parameters
        ----------
        package : `ObservationPackage`
            The package as last read; its revision must be the stored one.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import delete_package

        delete_package(self._context, package)

    @background_job("planning", grace_period_seconds=20.0)
    def record_suggestions(self, targets: list[Target] | None = None) -> PackageChange:
        """Update the suggested packages, the checklist and finished packages.

        A target whose quality report asks for frames gets one suggested
        package of exposure rows while its noise is above the library's
        noise goal; a calibration rule adds a checklist item to tonight's
        session; the top targets of tonight's ranking become suggested
        packages tied to tonight; and a planned package whose light rows are
        all captured and judged becomes complete. Missing data never deletes
        a suggestion, a dismissed suggestion stays hidden until its evidence
        changes, and every change is written to the suggestion log
        (`query_suggestion_log`). The run is recorded as a job.

        Owner:
            wayfindinglib.tasks.planning_tasks.suggestion_refresh.record_suggestions

        Parameters
        ----------
        targets : `list` [`Target`], optional
            The targets to refresh, after new frames were measured. `None`
            refreshes every library target and tonight's ranking.

        Returns
        -------
        change : `PackageChange`
            The ids of the packages added, updated, deleted, completed and
            reverted, the checklist items added, and notes on anything
            skipped.
        """
        from wayfindinglib.tasks.planning_tasks.suggestion_refresh import record_suggestions

        return record_suggestions(self._context, targets)

    def get_depth_goal(self, target: Target) -> DepthGoalStatus:
        """Say how close a target's image is to its chosen depth level.

        The levels are Quick look, Good (the default) and Deep, each a depth
        in magnitudes that depends on the kind of object (register row
        WAY-D94).

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.get_depth_goal

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        status : `DepthGoalStatus`
            The chosen level, the newest stack's depth, the level it reaches
            and the time to reach the chosen level, with a sentence for a
            novice in ``message``.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import get_depth_goal

        return get_depth_goal(self._context, target)

    def set_depth_goal(self, target: Target, level: DepthLevel | str) -> DepthGoalStatus:
        """Choose a target's depth level.

        The level belongs to the target, with or without a package, and is
        kept in wayfindinglib's own database.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.set_depth_goal

        Parameters
        ----------
        target : `Target`
            The target.
        level : `DepthLevel` or `str`
            ``"quick_look"``, ``"good"`` or ``"deep"``.

        Returns
        -------
        status : `DepthGoalStatus`
            The status for the new level.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import set_depth_goal

        return set_depth_goal(self._context, target, level)

    def create_mosaic(
        self,
        target: Target,
        panels: list[MosaicPanel],
        exposures: list[ExposureRequest] | None = None,
        *,
        dither_config: DitherConfig | None = None,
    ) -> MosaicPlan:
        """Add one library target per mosaic panel, and one package each.

        Each panel target is named after the parent and the panel, such as
        ``"NGC 7000_P1_2"``, and copies the parent's camera, telescope and
        field of view. With ``exposures`` one package per panel is recorded
        too, all sharing the rows; with none, only the panel targets are
        added.

        Owner:
            wayfindinglib.tasks.planning_tasks.package_records.create_mosaic_packages

        Parameters
        ----------
        target : `Target`
            The target the mosaic covers.
        panels : `list` [`MosaicPanel`]
            The panels, as ``wayfinder.planning.sky.get_panels`` gives them.
        exposures : `list` [`ExposureRequest`], optional
            The exposure rows every panel's package shares.
        dither_config : `DitherConfig`, optional
            The dithering every panel's package shares.

        Returns
        -------
        plan : `MosaicPlan`
            The panel target ids and the recorded packages.
        """
        from wayfindinglib.tasks.planning_tasks.package_records import create_mosaic_packages

        return create_mosaic_packages(self._context, target, panels, exposures or (), dither_config)
