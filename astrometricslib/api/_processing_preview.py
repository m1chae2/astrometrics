"""Purpose: `EnhancementPipeline`, the processing sub-API's picture pipeline.

Description: `EnhancementPipeline` is ``Astrometrics.processing.enhancement``.
It makes a stack's preview picture (GraXpert, the Siril stretch, Cosmic
Clarity, the star toning) from the stack that is there (`run`), and lists the
nebulae and galaxies in the stack's field (`get_bright_objects`), one of which
the picture's masked stretch can protect. The choice of that object is a
field of the target, set with
``targets.update(target, preview_bright_object=PreviewBrightObject(...))``.
Each method is a thin wrapper of one owner function, which it names.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from astrometricslib._internal.renames import MovedNames
from astrometricslib.api._target_frames import _check_target
from astrometricslib.models.processing_results import (
    BrightObjectChoices,
    EnhancementSettings,
    PreviewRemakeResult,
)
from astrometricslib.models.target import Target
from astrometricslib.pipelines.pipeline_runs import StackChoice

if TYPE_CHECKING:
    from astrometricslib.api.processing import ProcessingPipelines

__all__ = ["EnhancementPipeline"]


class EnhancementPipeline(MovedNames):
    """Make a stack's preview picture, and list the objects it can protect.

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
        settings: EnhancementSettings | None = None,
        stack: StackChoice | None = None,
        *,
        keep_previous: bool = True,
        force: bool = False,
    ) -> PreviewRemakeResult:
        """Make a target's preview picture from its stack.

        Runs only the picture steps (GraXpert, Siril stretch, Cosmic Clarity,
        star toning), so the stack file is not changed. The app's Stack button
        runs this after `processing.stacking.run`. ``settings`` apply to this
        run only and are never written to the configuration. By default the
        old pictures are copied into a ``_previous_preview`` folder beside the
        stack first, so the two can be compared; a failed run puts them back.
        The target is saved when the viewer shows the new picture. A
        monochrome stack whose target also has a color stack from another
        setup gets its color picture made again too. A picture that is
        already up to date (newer than the stack and made with the settings
        now in force) is kept unless ``force`` is set or ``settings`` are
        given.

        Owner:
            astrometricslib.pipelines.stacking.post_processing.preview_remake.enhance_target

        Parameters
        ----------
        target : `Target`
            The target whose stack to make a picture of.
        settings : `EnhancementSettings`, optional
            Picture choices for this run, such as
            ``EnhancementSettings(denoise=False)``. Left out, the configured
            values.
        stack : `StackConfigurationResult` or `str`, optional
            The stack: one from ``processing.stacking.get_stack``, the
            target's spectral stack (its path), or the main imaging stack
            (the default; a target with only a spectral stack uses that).
            Another configuration's stack gets its own pictures, and the
            target's main picture stays as it was.
        keep_previous : `bool`, optional
            Copy the current pictures aside first. On by default.
        force : `bool`, optional
            Make the picture even when it is up to date.

        Returns
        -------
        result : `PreviewRemakeResult`
            Where the picture went, the steps that ran, where the old
            pictures were kept, the color picture with a sentence on it, and
            ``kept_unchanged`` when an up-to-date picture was kept.

        Notes
        -----
        Raises `InvalidArgumentError` if a setting is out of range or
        ``stack`` is not one of the target's stacks; `NotFoundError` if the
        target has no stack or its file is missing; `ProcessingError` if no
        picture was made.
        """
        from astrometricslib.pipelines.stacking.post_processing.preview_remake import enhance_target

        return enhance_target(
            _check_target(target),
            settings,
            stack,
            keep_previous=keep_previous,
            force=force,
            catalog=self._pipelines._targets,
            measured_at=datetime.now(UTC),
        )

    def get_bright_objects(self, target: Target, force: bool = False) -> BrightObjectChoices:
        """List the nebulae and galaxies in a target's imaging stack.

        The picture's masked stretch protects one small bright smooth object.
        This asks SIMBAD for the objects of those kinds inside the footprint
        of the target's imaging stack, using the stack's plate solution, and
        returns them nearest the center first with their position and size.
        SIMBAD's answer is stored in the library, so a second call is quick;
        ``force`` asks SIMBAD again. A stack with no plate solution gives no
        objects and the message "Run Astrometry first to list objects in the
        frame". SIMBAD out of reach, with nothing stored, also gives no
        objects and a message, not an error. To protect one of the objects,
        save it on the target with ``targets.update(target,
        preview_bright_object=PreviewBrightObject(mode="object", ...))``.

        Owner:
            astrometricslib.pipelines.stacking.post_processing.bright_object_choices.list_target_bright_objects

        Parameters
        ----------
        target : `Target`
            The target whose imaging stack to list.
        force : `bool`, optional
            Ask SIMBAD again even when an answer is stored.

        Returns
        -------
        choices : `BrightObjectChoices`
            The objects, or no objects and the reason, with ``current``, the
            choice saved on the target.
        """
        from astrometricslib.pipelines.stacking.post_processing.bright_object_choices import (
            list_target_bright_objects,
        )

        return list_target_bright_objects(
            _check_target(target), self._pipelines._drivers.simbad_or_default(), force=force
        )
