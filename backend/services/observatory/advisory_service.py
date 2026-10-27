"""Purpose: Serve the quality advice and night history reads for the app.

Description: The app's quality screens read three groups of facts from the
wayfinding library, and this service gives each read its own method so each
RPC reply has one fixed type.

* Planning advice, from `Wayfinder.planning.get_advisory`: the target
  dataset report, the ranking of targets for tonight, and the statements
  about one star's result. The library has one advice call with a ``kind``;
  the RPC route cannot fix an argument, so each kind has a method here.
* The depth goal of a target, from `Wayfinder.planning.get_depth_goal` and
  `set_depth_goal`: the plain level a person chose (Quick look, Good or
  Deep), how deep the image is, and the time to reach the level.
* Night history, from `Wayfinder.control.history`: the list of nights, one
  night's quality review, and the slews that changed target. These reads
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
        return self.wayfinder.planning.get_advisory(kind="quality", target=target)

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
        return self.wayfinder.planning.get_advisory(kind="tonight", at=at)

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
        return self.wayfinder.planning.get_advisory(kind="result", target=target, object_id=object_id)

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
        return self.wayfinder.planning.get_depth_goal(target)

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
        return self.wayfinder.planning.set_depth_goal(target, level)

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
        return self.wayfinder.control.history.query(kind="nights", limit=limit, register_job=False)

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
        return self.wayfinder.control.history.query(
            kind="night_quality", session_id=session_id, register_job=False
        )

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
        return self.wayfinder.control.history.query(
            kind="target_changes", session_id=session_id, limit=limit, register_job=False
        )

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
            The most items per section.

        Returns
        -------
        status : `LiveSessionStatus`
            The session status.
        """
        return self.wayfinder.control.history.get_live_session_status(
            window_minutes=window_minutes, refresh=False, include=include, limit=limit
        )

    def get_safety_status(self) -> Any:
        """Read who is in control of the equipment.

        Returns
        -------
        status : `SafetyStatus`
            The safety status with only the delegation policy filled in.
        """
        return self.wayfinder.control.safety.status(include=["delegation_policy"])
