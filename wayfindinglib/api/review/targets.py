"""Purpose: `ReviewTargets`, judgments of a target's data (``review.targets``).

Description: Judges data and results that already exist: a target's
dataset report, how far one star's result can be trusted, how round a
target's stars are, and the guide error during each of its frames. The
advice is worked out on demand and never stored. The advice that looks
ahead (tonight's ranking) is in `planning`. Each method is a thin wrapper
of one owner function.
"""

from __future__ import annotations

from astrometricslib import StellarObject, Target
from astrometricslib.arguments import FrameChoice
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.planning.target_report import TargetDatasetReport
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.control_results import FrameGuidingReport
from wayfindinglib.models.session.star_shape_review import StarShapeReview

__all__ = ["ReviewTargets"]


class ReviewTargets(ControlChild):
    """Judgments of targets and results, as ``wayfinder.review.targets``."""

    def get_report(self, target: Target) -> TargetDatasetReport:
        """Report on a target's dataset.

        It summarizes each capture setting, lists warnings and
        recommendations in priority order, and gives a starting point when
        the target has no frames.

        Owner:
            wayfindinglib.tasks.planning_tasks.target_report_tasks.build_target_dataset_report

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        report : `TargetDatasetReport`
            The report.
        """
        from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report

        context = self._context
        return build_target_dataset_report(
            target, astrometrics=context.astrometrics, butler=context.butler, config=context.config
        )

    def get_result(self, star: StellarObject, *, target: Target | None = None) -> list[TargetStatement]:
        """Say how far one star's science results can be trusted.

        The star's spectrum, period search and repeating-dip search are each
        judged with the result rules.

        Owner:
            wayfindinglib.tasks.planning_tasks.result_judgment_tasks.judge_star_result

        Parameters
        ----------
        star : `StellarObject`
            The star, from ``astrometrics.stars.get``.
        target : `Target`, optional
            The target the star was found in. A star recorded only for other
            targets is refused.

        Returns
        -------
        statements : `list` [`TargetStatement`]
            Warnings first, then the number behind each result that passed,
            then the notes about what cannot be judged yet. Never empty.

        Notes
        -----
        Raises `NotFoundError` if the star is not in the catalog, or is
        recorded for other targets only.
        """
        from wayfindinglib.tasks.planning_tasks.result_judgment_tasks import judge_star_result

        return judge_star_result(star, astrometrics=self._context.astrometrics, target=target)

    def get_star_shapes(
        self, target: Target, frames: FrameChoice | None = None, *, night: str | None = None
    ) -> StarShapeReview:
        """Say in plain words how round a target's stars are and why.

        Reads the star-shape measurements already stored for the target's
        light frames (and its stack, when the stack has its own). Nothing is
        measured or stored. The verdicts are suggestions: whether the stars
        are round against this equipment's own history (WAY-42), whether the
        stretch points one way (tracking, guiding, wind or flexing) or many
        ways (seeing or focus), and which frames stand out as candidates to
        set aside. The corner widths are reported but are not validated
        (AST-18), so no tilt verdict is made.

        Owner:
            wayfindinglib.tasks.control_tasks.star_shape_tasks.get_star_shape_review

        Parameters
        ----------
        target : `Target`
            The target.
        frames : `FrameChoice`, optional
            Judge only these frames, from ``astrometrics.targets.frames.query``
            or a list of paths.
        night : `str`, optional
            Judge only this observing night, such as ``"2026-09-24"``. The
            roundness limit then uses only earlier nights, so a bad night
            cannot make itself normal.

        Returns
        -------
        review : `StarShapeReview`
            The verdicts, each with a plain sentence and its numbers, the
            standout frames, and the per-frame series for a chart. A target
            with no stored star shapes gets "not measured yet" verdicts, not
            an error.
        """
        from wayfindinglib.tasks.control_tasks.star_shape_tasks import get_star_shape_review

        return get_star_shape_review(self._context, target, night=night, frames=frames)

    def get_frame_guiding(
        self, target: Target, frames: FrameChoice | None = None, *, include: list[str] | None = None
    ) -> FrameGuidingReport:
        """Report the guide error during each light frame's exposure.

        Cuts the stored guide-log samples to the window of each frame and
        gives the sample count, how much of the window the samples cover,
        and the RMS and peak error in arcseconds. Use it to tell whether
        wind, a dither or a drift spoiled a frame. Nothing is stored.

        Owner:
            wayfindinglib.tasks.control_tasks.frame_guiding.link_frames_to_guiding

        Parameters
        ----------
        target : `Target`
            The library target whose light frames to match.
        frames : `FrameChoice`, optional
            The frames, from ``astrometrics.targets.frames.query`` (for
            example by night, filter or time), or a list of paths. Every
            light frame of the target when omitted. The newest 200 of them
            are reported.
        include : `list` [`str`], optional
            ``["quality"]`` also puts each frame's image measurements on its
            row: star count, star width, roundness, longest trail, sky level,
            saturated pixels and quality flags. Slow (about a second a
            frame), so at most 60 frames are measured.

        Returns
        -------
        report : `FrameGuidingReport`
            ``frames`` (one row per frame, oldest first) and ``group`` (the
            median error and the frames well above it).

        Notes
        -----
        Raises `InvalidArgumentError` if `include` names an unknown part or
        `frames` is a page cut short, and `NotFoundError` if a path is not
        one of the target's frames.
        """
        from wayfindinglib.tasks.control_tasks.frame_guiding import link_frames_to_guiding

        return link_frames_to_guiding(self._context, target, frames=frames, include=include)
