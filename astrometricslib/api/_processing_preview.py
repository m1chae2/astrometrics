"""The preview methods of the processing sub-API (`ProcessingPipelines`).

These methods make a stack's preview picture again and choose the bright
object its second stretch protects. They are part of `ProcessingPipelines`
(in `processing.py`), which binds them as its methods.
"""

from datetime import UTC, datetime
from typing import Any, Literal

# `ProcessingPipelines` binds the functions below as its methods, so this
# module and `processing.py` import each other. An annotation is read
# only when asked for, after both modules have loaded.
from astrometricslib.api import processing as _processing
from astrometricslib.foundation.arguments import check_choice
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.jobs.runner import background_job, current_job_id, registered_job
from astrometricslib.models.processing_results import (
    BrightObjectChoices,
    PreviewRemakeResult,
)
from astrometricslib.models.target import PreviewBrightObject, Target
from astrometricslib.pipelines.shared.api_arguments import STACK_KINDS, StackKind


@background_job("preview", grace_period_seconds=20.0)
def remake_preview(
    self: _processing.ProcessingPipelines,
    target: str | Target,
    kind: StackKind = "imaging",
    denoise: bool | None = None,
    denoise_strength: float | None = None,
    star_toning: bool | None = None,
    starnet: bool = False,
    star_strength: float | None = None,
    star_stretch: float | None = None,
    keep_previous: bool = True,
    register_job: bool = True,
    stack_path: str | None = None,
) -> PreviewRemakeResult:
    """Make a target's preview picture again from its existing stack.

    Runs only the preview step (GraXpert, Siril stretch, Cosmic Clarity,
    star toning), so a changed post-processing setting shows up without a
    restack. The stack file is not changed. The overrides apply to this
    run only and are never written to the configuration. By default the
    old pictures are copied into a ``_previous_preview`` folder beside
    the stack first, so the two can be compared; a failed run puts them
    back. The target is saved when the viewer shows the new picture. A
    missing target or stack raises `NotFoundError`, and a run that makes
    no preview raises `ProcessingError`. A monochrome stack whose target
    also has a color stack from another setup gets its color picture
    made again too (see `color_from_other_stack`).

    Parameters
    ----------
    target : `str` or `Target`
        The target whose stack to make a picture of.
    kind : `str`, optional
        ``"imaging"`` (default) or ``"spectral"`` stack.
    denoise : `bool`, optional
        `False` skips Cosmic Clarity for this run. `True` runs it even if
        the settings have it switched off. Left out, the settings apply.
    denoise_strength : `float`, optional
        Cosmic Clarity's denoise strength for this run, from 0 to 1.
    star_toning : `bool`, optional
        Turn the star toning on or off for this run.
    starnet : `bool`, optional
        Stretch the stars and the rest of the picture on their own,
        using StarNet through Siril. It adds about 35 to 55 seconds to
        the remake and is off by default. The star toning is not used
        with it, and a failed StarNet run gives the normal picture.
    star_strength : `float`, optional
        With ``starnet``, how much of the stars layer is blended, from 0
        to 1. Left out, 0.8.
    star_stretch : `float`, optional
        With ``starnet``, the arcsinh strength of the stars' own stretch,
        from 1 to 100000. Larger values lift fainter stars. Left out,
        1000.
    keep_previous : `bool`, optional
        Copy the current pictures aside first. On by default.
    register_job : `bool`, optional
        Record the run in the job list. Defaults to `True`.
    stack_path : `str`, optional
        Make the picture of this stack: the main stack or one of the
        target's stacks by camera and optic
        (``stacking.stacks_by_configuration``). Left out, the main
        stack. Another configuration's stack gets its own pictures, and
        the target's main stack and processed-image pointer stay as they
        were.

    Returns
    -------
    result : `PreviewRemakeResult`
        Where the picture went, the steps that ran, where the old
        pictures were kept, and the color picture (``color_preview_path``)
        with a sentence on it (``color_preview_note``).

    Raises
    ------
    InvalidArgumentError
        If ``kind``, ``denoise_strength``, ``star_strength`` or
        ``star_stretch`` is out of range, or ``stack_path`` is not one of
        the target's stacks of that kind.
    """
    from astrometricslib.pipelines.stacking.post_processing.preview_remake import remake_stack_preview
    from astrometricslib.pipelines.stacking.post_processing.preview_settings import PreviewSettings

    check_choice("kind", kind, STACK_KINDS)
    if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
        raise InvalidArgumentError("denoise_strength must be between 0 and 1.")
    if star_strength is not None and not 0.0 <= star_strength <= 1.0:
        raise InvalidArgumentError("star_strength must be between 0 and 1.")
    if star_stretch is not None and not 1.0 <= star_stretch <= 100000.0:
        raise InvalidArgumentError("star_stretch must be between 1 and 100000.")
    resolved = self._resolve(target)
    settings = PreviewSettings(
        denoise=denoise,
        denoise_strength=denoise_strength,
        star_toning=star_toning,
        starnet=starnet,
        star_strength=star_strength,
        star_stretch=star_stretch,
    )
    # The job is recorded first, so a remake waiting for a slot shows in
    # the job list and returns its job id.
    with (
        registered_job(enabled=register_job, job_type="preview", target_id=resolved.id) as job,
        job.holding_slot(self.acquire_job_slot(), working_message="Making the preview"),
    ):
        result = remake_stack_preview(
            resolved,
            kind == "spectral",
            settings,
            keep_previous,
            job_id=current_job_id(),
            measured_at=datetime.now(UTC),
            configuration=self._config,
            stack_path=stack_path,
        )
        # Not a final status: `registered_job` completes the job on the
        # way out and keeps this message, so the list stops saying
        # "Making the preview" for a job that is over.
        job.mark("running", 100, message="Preview made")
    if result.shown_in_viewer:
        self._targets.save()
    return result


def list_preview_bright_objects(
    self: _processing.ProcessingPipelines, target: str | Target, *, refresh: bool = False
) -> BrightObjectChoices:
    """List the nebulae and galaxies in a target's stack.

    The preview's masked stretch protects one small bright smooth object
    (see ``set_preview_bright_object``). This asks SIMBAD for the objects
    of those kinds inside the footprint of the target's imaging stack,
    using the stack's plate solution, and returns them nearest the center
    first with their position and size. SIMBAD's answer is stored in
    the library, so a second call is quick; ``refresh`` asks SIMBAD
    again. A stack with no plate solution gives no objects and the
    message "Run Astrometry first to list objects in the frame". SIMBAD
    being out of reach, with nothing stored, also gives no objects and a
    message, not an error. Nothing is written except the stored answer.

    Parameters
    ----------
    target : `str` or `Target`
        The target whose imaging stack to list.
    refresh : `bool`, optional
        Ask SIMBAD again even when a list is saved for this stack file.

    Returns
    -------
    choices : `BrightObjectChoices`
        The objects, or no objects and the reason, with ``current``, the
        choice saved on the target.
    """
    from astrometricslib.pipelines.stacking.post_processing.bright_object_choices import (
        list_bright_object_choices,
    )

    resolved = self._resolve(target)
    choices = list_bright_object_choices(
        resolved.id,
        resolved.stacking.stacked_image,
        self._drivers.simbad_or_default(),
        use_cache=not refresh,
    )
    return choices.model_copy(update={"current": resolved.preview_bright_object})


def set_preview_bright_object(
    self: _processing.ProcessingPipelines,
    target: str | Target,
    mode: Literal["auto", "off", "object"] = "auto",
    simbad_id: str | None = None,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    major_axis_arcmin: float | None = None,
    name: str | None = None,
) -> PreviewBrightObject:
    """Choose which object the preview's masked stretch protects.

    The choice is saved on the target and read the next time its preview
    is made, by a stack or by ``remake_preview``. Saving it makes no
    preview. ``"auto"`` is the default rule: the target itself, when it
    is a nebula or a galaxy. ``"off"`` never uses the masked stretch.
    ``"object"`` protects the object given, whatever the target's kind;
    the checks on the region's smoothness and size still apply.

    Parameters
    ----------
    target : `str` or `Target`
        The target to save the choice on.
    mode : `str`, optional
        ``"auto"`` (default), ``"off"`` or ``"object"``.
    simbad_id : `str`, optional
        With ``"object"``, SIMBAD's name of the object (an entry of
        ``list_preview_bright_objects``).
    ra_deg : `float`, optional
        With ``"object"``, its right ascension in degrees.
    dec_deg : `float`, optional
        With ``"object"``, its declination in degrees.
    major_axis_arcmin : `float`, optional
        With ``"object"``, its major axis in arcminutes, if known.
    name : `str`, optional
        With ``"object"``, instead of the name and position: any spelling
        of the name of an object ``list_preview_bright_objects`` lists
        (``"ngc7635"`` names ``NGC 7635``). Its SIMBAD name, position and
        size are taken from that list.

    Returns
    -------
    choice : `PreviewBrightObject`
        The choice as saved.

    Raises
    ------
    InvalidArgumentError
        If ``mode`` is unknown, ``"object"`` lacks a name or a position
        inside the sky, or ``name`` is not in the stack's list (the
        message lists the objects that are).
    """
    check_choice("mode", mode, ("auto", "off", "object"))
    if mode == "object" and name and ra_deg is None and dec_deg is None:
        listed = self._listed_bright_object(target, name)
        simbad_id = listed.simbad_id
        ra_deg, dec_deg = listed.ra_deg, listed.dec_deg
        major_axis_arcmin = listed.major_axis_arcmin
    if mode == "object":
        if not simbad_id or ra_deg is None or dec_deg is None:
            raise InvalidArgumentError("Mode 'object' needs simbad_id, ra_deg and dec_deg.")
        if not (0.0 <= ra_deg < 360.0 and -90.0 <= dec_deg <= 90.0):
            raise InvalidArgumentError("The position of the object is outside the sky.")
        if major_axis_arcmin is not None and not major_axis_arcmin > 0.0:
            raise InvalidArgumentError("major_axis_arcmin must be positive.")
        choice = PreviewBrightObject(
            mode="object",
            simbad_id=simbad_id,
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            major_axis_arcmin=major_axis_arcmin,
        )
    else:
        choice = PreviewBrightObject(mode=mode)
    resolved = self._resolve(target)
    resolved.preview_bright_object = choice
    self._targets.add(resolved)
    return choice


def _listed_bright_object(self: _processing.ProcessingPipelines, target: str | Target, name: str) -> Any:
    """Find an object of the stack's bright-object list by its name.

    Parameters
    ----------
    target : `str` or `Target`
        The target whose stack's list to read.
    name : `str`
        The object's name, in any spelling (case, spaces, SIMBAD's
        ``NAME`` prefix and underscores do not matter).

    Returns
    -------
    choice : `BrightObjectChoice`
        The listed object.

    Raises
    ------
    InvalidArgumentError
        If the list has no object of that name. The message lists the
        objects, or says why there are none.
    """
    from astrometricslib.pipelines.shared.simbad_object_types import normalize_name

    listing = self.list_preview_bright_objects(target)
    wanted = "".join(normalize_name(name).split())
    for item in listing.objects:
        if "".join(normalize_name(item.simbad_id).split()) == wanted:
            return item
    names = ", ".join(item.simbad_id for item in listing.objects) or "none"
    raise InvalidArgumentError(
        f"{name!r} is not an object in the frame of {listing.target_id!r}. Objects in the frame: {names}."
        + (f" {listing.message}" if listing.message else ""),
        details={"target": listing.target_id, "name": name},
    )
