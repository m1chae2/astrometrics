"""Purpose: `StackingPipeline`, the processing sub-API's stacking pipeline.

Description: `StackingPipeline` is ``Astrometrics.processing.stacking``. It
stacks a target's frames (`run`), says what a stack would do without
stacking (`predict`), predicts the stacker's frame weights
(`predict_weights`), summarizes a stack (`get_summary`) and finds a target's
stack of one observatory configuration (`get_stack`). Each method is a thin
wrapper of one owner function, which it names. A stacking run makes only the
stack; the enhancement pipeline (`processing.enhancement.run`) makes its
picture.

The module also keeps `_library_stack_path` and `_subset_context`, which the
script call `stack_subset` uses through `ProcessingPipelines`.
"""

import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from astrometricslib._internal.renames import MovedNames
from astrometricslib.api._target_frames import _check_target
from astrometricslib.foundation.arguments import check_choice
from astrometricslib.foundation.jobs.runner import current_job_id
from astrometricslib.models.observatory_configuration import ObservatoryConfiguration
from astrometricslib.models.processing_results import StackPrediction, StackResult, StackWeightPrediction
from astrometricslib.models.quality.quality_reports import StackSummary
from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
from astrometricslib.models.target import StackConfigurationResult, Target
from astrometricslib.pipelines.shared.api_arguments import STACK_KINDS, StackKind
from astrometricslib.pipelines.shared.frame_choice import FrameChoice
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext

if TYPE_CHECKING:
    from astrometricslib.api.processing import ProcessingPipelines

__all__ = ["StackingPipeline"]

logger = logging.getLogger(__name__)


class StackingPipeline(MovedNames):
    """Stack a target's frames, and predict and summarize its stacks.

    Parameters
    ----------
    pipelines : `ProcessingPipelines`
        The processing sub-API this pipeline belongs to.
    """

    def __init__(self, pipelines: ProcessingPipelines) -> None:
        self._pipelines = pipelines

    def run(
        self,
        target: Target,
        frames: FrameChoice | None = None,
        *,
        force: bool = False,
        rejection_sigma: tuple[float, float] | None = None,
        filter_wfwhm: str | None = None,
        filter_round: str | None = None,
        stack_weight: str | None = None,
        generate_rejmap: bool | None = None,
    ) -> StackResult:
        """Stack a target's light frames, as the app's Stack button does.

        The stacking stage sets aside bad frames, stacks, trims the noisy
        edges, records the quality summary and saves the target. It makes no
        preview picture: run `processing.enhancement.run` afterwards, as the
        app's Stack button does. An unchanged stack is kept unless ``force``
        is set. Imaging and spectroscopy frames are never mixed, and frames
        from two observatory configurations (two cameras, or two optics) are
        never stacked together.

        Owner: astrometricslib.pipelines.stacking.stack_request.stack_target

        Parameters
        ----------
        target : `Target`
            The target to stack.
        frames : `FrameChoice`, optional
            The frames to stack: the `Page` that ``targets.frames.query``
            returns, a list of frame records or a list of the target's frame
            paths. A page cut short is refused. To stack spectroscopy frames,
            choose them with ``filter_name="SPEC"``. Left out, every imaging
            light frame of the target.
        force : `bool`, optional
            Rebuild even if nothing changed since the stack on disk.
        rejection_sigma : `tuple` [`float`, `float`], optional
            Low and high sigma-clipping bounds for pixel rejection. Left
            out, the configured value.
        filter_wfwhm : `str`, optional
            Siril's weighted-FWHM frame filter, such as ``"90%"``.
        filter_round : `str`, optional
            Siril's star-roundness frame filter.
        stack_weight : `str`, optional
            How Siril weights each frame.
        generate_rejmap : `bool`, optional
            Also write the rejection maps.

        Returns
        -------
        result : `StackResult`
            The frames chosen, the stack's path and its quality summary
            (``quality_summary``).

        Notes
        -----
        Raises `InvalidArgumentError` if the frames come from more than one
        camera or optic, or mix imaging and spectral frames; the message
        names each group and its frame count. Raises `NotFoundError` if the
        target has no frame to stack, and `ProcessingError` if the stage
        finishes without making a stack.
        """
        from astrometricslib.pipelines.stacking.stack_request import SirilOptions, stack_target

        pipelines = self._pipelines
        return stack_target(
            _check_target(target),
            frames,
            force=force,
            siril=SirilOptions(
                rejection_sigma=rejection_sigma,
                filter_wfwhm=filter_wfwhm,
                filter_round=filter_round,
                stack_weight=stack_weight,
                generate_rejmap=generate_rejmap,
            ),
            catalog=pipelines._targets,
            calibration_library=pipelines._calibration._calibration_library,
            drivers=pipelines._drivers,
            measured_at=datetime.now(UTC),
        )

    def predict(self, target: Target, frames: FrameChoice | None = None) -> StackPrediction:
        """Say what a stack would do with its frames, without stacking.

        The frames are chosen as in `run`. Then the frame checks the stack
        runs decide which frames it would set aside: clouds, trailed stars,
        a frame off the field, a minority gain setting, a different sky
        background, and frames an earlier run already set aside. A frame
        with an uneven sky but normal stars is kept and listed as flagged.
        For each exposure group of the frames that remain, the calibration
        choice the stack makes names its dark, bias and flat frames. Nothing
        is stacked, moved, stored or saved. The checks read every frame, so
        a large target takes a while. To check only the frame choice, use
        `astrometricslib.arguments.check_frames`.

        Owner:
            astrometricslib.pipelines.stacking.stack_prediction.predict_target_stack

        Parameters
        ----------
        target : `Target`
            The target to check.
        frames : `FrameChoice`, optional
            The frames, in any form `run` takes. Left out, every imaging
            light frame of the target.

        Returns
        -------
        prediction : `StackPrediction`
            The counts, the first and last chosen file, the frames set aside
            and flagged with their reasons, each group's calibration choice,
            and the checks' warnings.

        Notes
        -----
        The errors are those of `run`.
        """
        from astrometricslib.pipelines.stacking.stack_prediction import predict_target_stack

        return predict_target_stack(
            _check_target(target), frames, calibration=self._pipelines._calibration._calibration_library
        )

    @staticmethod
    def predict_weights(
        exposures_s: Sequence[float | None],
        configured_weight: str | None = None,
        *,
        kind: StackKind = "imaging",
        star_width_px: Sequence[float | None] | None = None,
        rate_variance: Sequence[float | None] | None = None,
        group_frame_noise_adu: Mapping[float, float | None] | None = None,
    ) -> StackWeightPrediction:
        """Predict how the stacker would weight a set of frames.

        This runs the stacker's own rules on the frame facts given, so a
        forecast made before stacking agrees with the stack made later.
        Nothing is read from disk or saved. The call reads no stored data,
        so code without an `Astrometrics` can call it on the class,
        ``StackingPipeline.predict_weights(...)``.

        Owner:
            astrometricslib.pipelines.stacking.stack_weight_prediction.predict_stack_weights

        Parameters
        ----------
        exposures_s : `Sequence` [`float` or `None`]
            The exposure length of each frame, in seconds. `None` marks a
            frame whose length cannot be read.
        configured_weight : `str`, optional
            The ``Processing.Siril.stack_weight`` setting. A configured mode
            wins over the stacker's own choice.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"``. A spectral stack keeps
            its configured weight.
        star_width_px : `Sequence` [`float` or `None`], optional
            The weighted star width of each frame, in pixels. Needed for the
            ``wfwhm`` weights and the weights inside each exposure group.
        rate_variance : `Sequence` [`float` or `None`], optional
            The variance of each frame in rate units. Needed for the
            ``noise`` weights.
        group_frame_noise_adu : `dict` [`float`, `float` or `None`], optional
            The noise of one raw frame of each exposure group, in counts,
            keyed by the group's length (``folded_exposure_lengths`` of the
            result). Needed for the weights of an exposure-group stack.

        Returns
        -------
        prediction : `StackWeightPrediction`
            The weighting mode, the exposure group of each frame, and the
            weight of each frame (or the reasons they are unknown).
        """
        from astrometricslib.pipelines.stacking.stack_weight_prediction import predict_stack_weights

        check_choice("kind", kind, STACK_KINDS)
        return predict_stack_weights(
            exposures_s,
            configured_weight,
            is_spectral=kind == "spectral",
            star_width_px=star_width_px,
            rate_variance=rate_variance,
            group_frame_noise_adu=group_frame_noise_adu,
        )

    def get_summary(self, target: Target, kind: StackKind = "imaging") -> StackSummary:
        """Summarize a target's current stack in one short answer.

        Reads the numbers the stacking stage saved with the stack, so
        nothing is measured again: how many frames went in and how many were
        set aside, the share of pixels rejected, the star width against what
        the input frames predict, and what happened to each exposure group.

        Owner:
            astrometricslib.pipelines.stacking.post_processing.stack_quality_report.summarize_stack

        Parameters
        ----------
        target : `Target`
            The target to summarize.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stack.

        Returns
        -------
        summary : `StackSummary`
            The summary.
        """
        from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import summarize_stack

        check_choice("kind", kind, STACK_KINDS)
        return summarize_stack(_check_target(target), kind)

    def get_stack(
        self, target: Target, configuration: ObservatoryConfiguration | None = None
    ) -> StackConfigurationResult | None:
        """Find a target's imaging stack of one observatory configuration.

        A target stacked with several configurations keeps one stack for
        each (``stacking.stacks_by_configuration``). The result is what a
        pipeline's ``stack=`` argument takes.

        Owner:
            astrometricslib.pipelines.shared.analysis_configuration.find_configuration_stack

        Parameters
        ----------
        target : `Target`
            The target.
        configuration : `ObservatoryConfiguration`, optional
            The equipment (from ``astrometrics.observatories.configurations``).
            Left out, the target's preferred stack, or its only one.

        Returns
        -------
        stack : `StackConfigurationResult` or `None`
            The stack's record: ``configuration_key``, ``configuration_id``,
            ``camera``, ``focal_length_mm`` and ``stacked_image``. `None`
            when no stack matches, or more than one does.
        """
        from astrometricslib.pipelines.shared.analysis_configuration import find_configuration_stack

        return find_configuration_stack(_check_target(target), configuration, self._pipelines._config)


# -- The subset-stack noise measurements, bound by `ProcessingPipelines` --


def _library_stack_path(self: Any, target: Target, kind: StackKind, *, must_exist: bool = True) -> str:
    """Find the path of a target's library stack for the subset jobs.

    Parameters
    ----------
    target : `Target`
        The target.
    kind : {"imaging", "spectral"}
        Which stack.
    must_exist : `bool`, optional
        Raise when the target has no stack. When `False`, a target
        without a stack gets the path its stack would have, so a
        subset can still be stacked beside it.

    Returns
    -------
    path : `str`
        The library stack's path.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import stack_path_of

    if must_exist:
        return stack_path_of(target, kind)
    stacking = target.spectral_stacking if kind == "spectral" else target.stacking
    if stacking.stacked_image:
        return str(stacking.stacked_image)
    folder = os.path.join(str(self._config.get_stacks_path()), "lights", target.id)
    tag = "_SPEC" if kind == "spectral" else ""
    return os.path.join(folder, f"{target.id.replace(' ', '_')}{tag}_Stacked.fits")


def _subset_context(target: Target, job_id: str | None) -> PipelineRunContext | None:
    """Make the shared facts of one subset-stack run.

    Parameters
    ----------
    target : `Target`
        The target.
    job_id : `str` or `None`
        The run's job id, if it recorded a job.

    Returns
    -------
    context : `PipelineRunContext`
        The context, stamped with the time now (the API layer reads the
        clock, the pipelines do not).
    """
    return PipelineRunContext.from_options(
        target.id,
        "stacking",
        STACKING_PIPELINE_VERSION,
        {"job_id": job_id or current_job_id(), "measured_at": datetime.now(UTC)},
    )
