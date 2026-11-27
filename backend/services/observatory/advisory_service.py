"""Purpose: Serve the quality advice and night history reads for the app.

Description: The app's quality screens read three groups of facts from the
wayfinding library, and this service gives each read its own method so each
RPC reply has one fixed type.

* Review advice, from `Wayfinder.review.targets` (`get_report`, the target
  dataset report; `get_result`, the statements about one star's result;
  `get_star_shapes`, how round a target's stars are) and
  ``Wayfinder.planning.sky.get_tonight_advice`` (the ranking of targets for
  tonight). The app sends ids, which `SubjectResolver` turns into the
  library's objects.
* The depth goal of a target, from ``Wayfinder.planning.packages``
  (`get_depth_goal` and `set_depth_goal`): the plain level a person chose
  (Quick look, Good or Deep), how deep the image is, and the time to reach
  the level.
* Night history, from `Wayfinder.review.nights`: the list of nights, one
  night's quality review, and the slews that changed target. The app asks
  for the newest few; the library gives every one, oldest first, and this
  service keeps the newest. These reads
  never register a background job.
* Two live facts: the status of the running session (read from the logs
  already on disk, without copying new ones) and the safety status limited
  to the delegation policy, which says who is in control of the equipment.

This service reads, except for `set_depth_goal`, which keeps a level a
person chose. It does no ranking, grouping or sorting; the library's reply
is passed up as it is.
"""

from datetime import datetime
from typing import Any

from astrometricslib.arguments import check_limit
from backend.services.subject_resolver import SubjectResolver

MAXIMUM_LIMIT = 50
"""Most nights, target changes or section items one history reply holds."""


class AdvisoryService:
    """Read quality advice and night history from the wayfinding library."""

    def __init__(self, wayfinder: Any) -> None:
        """Keep the Wayfinder that does the work.

        Parameters
        ----------
        wayfinder : `wayfindinglib.Wayfinder`
            The shared Wayfinder.
        """
        self.wayfinder = wayfinder

    @property
    def _resolver(self) -> SubjectResolver:
        """Give the lookup of targets and stars by id.

        Returns
        -------
        resolver : `SubjectResolver`
            Over the Wayfinder's science library.
        """
        return SubjectResolver(self.wayfinder.astrometrics)

    def get_target_report(self, target: str) -> Any:
        """Read the dataset report of one target.

        Parameters
        ----------
        target : `str`
            The target's id or name.

        Returns
        -------
        report : `TargetDatasetReport`
            The report for the target, with its warnings and recommendations.
        """
        return self.wayfinder.review.targets.get_report(self._resolver.target(target))

    def get_star_shape_review(
        self, target: str, night: str | None = None, frame_paths: list[str] | None = None
    ) -> Any:
        """Read the plain verdicts on how round a target's stars are.

        Parameters
        ----------
        target : `str`
            The target's id or name.
        night : `str`, optional
            Judge only this observing night, such as ``"2026-09-24"``.
        frame_paths : `list` [`str`], optional
            Judge only these frames.

        Returns
        -------
        review : `StarShapeReview`
            The verdicts (roundness, direction, standout frames, corners),
            each with a sentence and its numbers, and the per-frame series
            for a chart.
        """
        return self.wayfinder.review.targets.get_star_shapes(
            self._resolver.target(target), frame_paths, night=night
        )

    def get_tonight_ranking(self, at: str | datetime | None = None) -> Any:
        """Read the ranking of targets for one moment.

        Parameters
        ----------
        at : `str` or `datetime`, optional
            The moment to rank for. Now when left out.

        Returns
        -------
        ranking : `TonightRanking`
            The targets ranked for each use.
        """
        return self.wayfinder.planning.sky.get_tonight_advice(time=at)

    def get_result_statements(self, target: str, object_id: str) -> Any:
        """Read the statements about one star's science result.

        Parameters
        ----------
        target : `str`
            The target's id or name.
        object_id : `str`
            The id of the star whose result is judged.

        Returns
        -------
        statements : `list` [`TargetStatement`]
            The statements, in the order the library gives them.
        """
        resolver = self._resolver
        return self.wayfinder.review.targets.get_result(
            resolver.star(object_id), target=resolver.target(target)
        )

    def get_depth_goal(self, target: str) -> Any:
        """Read the depth goal status of one target.

        Parameters
        ----------
        target : `str`
            The target's id or name.

        Returns
        -------
        status : `DepthGoalStatus`
            The chosen level, the image's depth and level, the time to the
            chosen level, and a sentence for the user.
        """
        target_object = self._resolver.target(target)
        return self.wayfinder.planning.packages.get_depth_goal(target_object)

    def set_depth_goal(self, target: str, level: str) -> Any:
        """Choose the depth level of one target.

        Parameters
        ----------
        target : `str`
            The target's id or name.
        level : `str`
            ``"quick_look"``, ``"good"`` or ``"deep"``.

        Returns
        -------
        status : `DepthGoalStatus`
            The status for the new level.
        """
        target_object = self._resolver.target(target)
        return self.wayfinder.planning.packages.set_depth_goal(target_object, level)

    def list_nights(self, limit: int = 10) -> Any:
        """List the recorded observing nights.

        Parameters
        ----------
        limit : `int`, optional
            The most nights to return.

        Returns
        -------
        nights : `list` [`NightListEntry`]
            One entry per night, in the order the library gives them.
        """
        check_limit(limit, 1, MAXIMUM_LIMIT, allow_none=False)
        return list(self.wayfinder.review.nights.query())[-limit:]

    def get_night_quality(self, session_id: str) -> Any:
        """Read the quality review of one night.

        Parameters
        ----------
        session_id : `str`
            The night, named for the local date on which it began.

        Returns
        -------
        review : `NightQuality`
            The night's targets, warnings and guiding summary.
        """
        return self.wayfinder.review.nights.get(session_id, required=True)

    def list_target_changes(self, session_id: str | None = None, limit: int = 10) -> Any:
        """List the slews that moved the mount to a new target.

        Parameters
        ----------
        session_id : `str`, optional
            The night to list. The library chooses when left out.
        limit : `int`, optional
            The most changes to return.

        Returns
        -------
        changes : `list` [`TargetChange`]
            The target changes, in the order the library gives them.
        """
        check_limit(limit, 1, MAXIMUM_LIMIT, allow_none=False)
        return self.wayfinder.review.nights.get_target_changes(session_id)[-limit:]

    def get_live_session_status(
        self, window_minutes: float = 10.0, include: list[str] | None = None, limit: int = 10
    ) -> Any:
        """Read how the current session is going from the logs on disk.

        The logs are not copied first; a separate sync does that.

        Parameters
        ----------
        window_minutes : `float`, optional
            Length of the recent-guiding window, in minutes.
        include : `list` [`str`], optional
            Detail sections to add.
        limit : `int`, optional
            The most items per section. The library keeps 10 at most, so
            only a smaller value changes the reply.

        Returns
        -------
        status : `LiveSessionStatus`
            The session status.
        """
        check_limit(limit, 1, MAXIMUM_LIMIT, allow_none=False)
        status = self.wayfinder.review.nights.get_live_status(window_min=window_minutes, include=include)
        for section in status.details.values():
            items = section.get("items") if isinstance(section, dict) else None
            if items is not None and len(items) > limit:
                # Keep `limit` items spread over the night, with the ends.
                step = (len(items) - 1) / max(limit - 1, 1)
                section["items"] = [items[round(index * step)] for index in range(limit)]
                section["shown"] = limit
        return status

    def get_safety_status(self) -> Any:
        """Read who is in control of the equipment.

        Returns
        -------
        status : `SafetyStatus`
            The safety status with only the delegation policy filled in.
        """
        return self.wayfinder.control.safety.status(include=["delegation_policy"])
