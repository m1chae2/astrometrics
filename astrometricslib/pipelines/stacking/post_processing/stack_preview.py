"""Writes a viewable picture next to a finished stack.

A stacked FITS file holds linear data: pixel values proportional to the
light collected. Most of the sky is far too dark in that form to see
anything. Up to eight steps turn it into a picture.

1. If GraXpert is configured, the sky's brightness gradient is removed in
   one of two ways (see `sky_plane`). A stack whose sky tilts by less than
   the limit gets a fitted plane subtracted: a plane cannot make a ring
   around a large nebula. A stack with a stronger gradient goes to GraXpert,
   whose AI model learns what the smooth background looks like and subtracts
   it, so uneven sky glow or vignetting does not wash out the faint parts.
   The record beside the picture says which ran, with the measured gradient
   and the limit.
2. Colour calibration balances the colours of a colour stack, on the linear
   data: Siril's photometric colour calibration, or, when that is not asked
   for or fails, the Gaia stars like the Sun (see `sun_like_balance`). With
   no Gaia colours the colours are left uncalibrated, and the record says
   so. Just before it, the star widths of the colour channels are matched
   (see `star_width_match`):
   a channel whose stars are clearly wider than green's, such as red behind
   the 300 mm camera lens, is sharpened to green's width, so the stars get no
   colored ring and the calibration measures stars of one shape. It is
   skipped, with the reason in the record, when the widths already agree;
   the setting ``preview_star_width_match_enabled`` turns it off. The setting
   ``preview_colour_calibration_enabled`` turns it off. A white-balance check
   follows Siril's calibration (see `sun_like_balance`): stars that look
   like the Sun, found from their Gaia colours, should come out white, and
   the red and blue channels are scaled about the sky level if they do not.
   It is skipped, with the reason in the record, when the stack has no
   plate solution, the Gaia colours cannot be had, too few stars qualify, or
   the change it asks for is too large. If Siril says its calibration is
   imprecise, a notice is stored.
3. Siril's Remove Green Noise (``rmgreen``) lowers the green in a colour
   stack, such as one from the Nikon DSLR. A colour sensor has twice as many
   green pixels as red or blue, and the sky's glow is often green, so these
   stacks look green. It runs on the linear data, just before the stretch,
   and only when step 2 did not calibrate the colours (see
   `decide_remove_green`). Calibration already makes the sky equal in the
   three channels, and the strong stretch turns the small shift that
   ``rmgreen`` then makes into a magenta sky: green fell to 0.88 to 0.89 of
   the mean of red and blue on two Nikon stacks, against 1.01 to 1.03 without
   the step. A single-channel stack, such as one from the ZWO camera, has no
   colour to balance and is left alone. The setting
   ``preview_remove_green_noise_enabled`` is the master switch and turns the
   step off. The record beside the picture says whether it ran and why.
4. The stretch brightens the faint parts. It sets the black point (the value
   drawn as pure black) just below the sky, then bends the brightness curve
   so the sky lands on a chosen grey level. The level comes from the stack
   itself (see `sky_level`): faint targets get a lighter sky so their faint
   structure stays visible, and bright targets get a darker one. The sky
   level and its noise are measured on the empty sky (see `stretch_sky`),
   for a single-channel stack and for a colour stack with calibrated colours
   (on the mean of its channels, which one shared stretch treats alike). A
   colour stack whose calibration failed or is off, and any stack whose sky
   cannot be measured, is stretched by Siril's own Autostretch, which takes
   the sky from every pixel. In a crowded star field that spread is several
   times the real pixel noise, so the black point falls far below the sky and
   the picture is flat.
5. Cosmic Clarity, if it is configured, removes noise with an AI model. It
   runs after the stretch, on the stretched picture. A color stack gets the
   program's ``full`` mode, which also smooths the color speckle (strength
   ``preview_color_denoise_strength``); a single-channel stack keeps the
   brightness-only mode. Run before the stretch,
   it lowers the noise level that Autostretch measures, so the stretch turns
   the contrast back up and the remaining noise looks as strong as before,
   with extra blotchy structure. Measured on the M 13 stack, the visible
   grain fell by only 22 percent that way, against 73 percent when the
   denoise ran after the stretch.
6. Sky levelling (see `sky_levelling`) makes the sky of a color picture
   neutral. The stretch multiplies the small differences between the
   channels' sky levels that the linear steps leave (a green-cyan sky on the
   NGC 6992 stack: red 27, green 30, blue 29 of 255). The step measures each
   channel's empty sky on the stretched picture, adds an offset to red and
   blue so their sky equals green's, and clips to the range 0 to 1. It runs
   after Cosmic Clarity, which can also shift the colors, and before the star
   toning, which leaves the sky alone. It is skipped, with a notice, when the
   sky cannot be measured or an offset would exceed
   `SKY_LEVELLING_MAXIMUM_OFFSET`. The offsets are in the record beside the
   picture. A single-channel stack is not touched.
7. Star toning (see `star_tone`) dims the stars in proportion to their
   brightness and compresses the brightest values below pure white. The
   stretch makes nearly every star a small white dot of about the same
   brightness. The toning brings back some of their real range. It acts on
   the stretched picture and leaves the background and nebulae alone. The
   setting ``preview_star_tone_enabled`` turns it off.

8. The masked stretch (see `masked_stretch`) protects a small bright smooth
   object, such as the ring of M 57. The normal stretch pushes such an object
   to flat white. For a nebula or galaxy target with a smooth bright region,
   a second stretch, set by the region's own brightness, replaces the picture
   inside the region and fades out around it. The rest of the picture is left
   as the earlier steps made it.

A stack of a bright extended object, such as the Moon, takes a different
route. The normal stretch would push the whole object to white (see
`bright_object`), so one Siril script applies a stretch scaled to the
object's own brightness, and GraXpert and Cosmic Clarity are skipped.

The picture is also cropped, to the area covered by at least 85% of the
frames, before the first step (see `stack_crop`). The crop's place on the
stack is written in the picture's header (``CROPX0``, ``CROPY0``, ``CROPW``
and ``CROPH``), and its plate solution is moved with it.

All steps run on a scratch copy of the stack and the result is saved as a
JPEG beside the stack. The JPEG is only a picture for people to look at. The
stack itself is never changed, so photometry, spectroscopy and plate solving
keep reading the linear data. A failed or skipped step never fails the stack:
if GraXpert, Cosmic Clarity or the star toning fails, the picture is made from
the result of the step before it, and if Siril fails, there is no picture.

This module runs the steps in order and saves the result. The steps live in
their own modules: `preview_siril` (the Siril scripts), `preview_graxpert`
(the gradient removal), `preview_cosmic_clarity` (the denoise),
`starnet_stretch` (the StarNet stretch) and `preview_color` (the color
steps). `preview_steps` runs one optional step, and `preview_settings` holds
the per-run choices and the settings record.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.paths import siril_work_directory
from astrometricslib.foundation.run_abandonment import RunAbandonedError
from astrometricslib.pipelines.shared.registration_record import read_registration_record
from astrometricslib.pipelines.shared.stack_preview_path import (
    background_model_path_for,
    is_preview_path,
    is_processed_fits_path,
    preview_path_for,
    preview_settings_path_for,
    processed_fits_path_for,
)
from astrometricslib.pipelines.stacking.post_processing.background_gradient import (
    BackgroundGradientOutcome,
    not_measured_outcome,
    write_binned_model,
)
from astrometricslib.pipelines.stacking.post_processing.bright_object import (
    choose_bright_object_stretch_for_file,
)
from astrometricslib.pipelines.stacking.post_processing.cluster_nebula import (
    CategoryVerdict,
    refine_object_category,
)
from astrometricslib.pipelines.stacking.post_processing.masked_stretch import (
    MaskedStretchPlan,
    TargetAnchor,
    apply_masked_stretch_in_file,
    plan_masked_stretch_for_file,
)
from astrometricslib.pipelines.stacking.post_processing.preview_color import (
    build_pcc_script,
    calibrate_from_sun_like_stars,
    check_white_balance,
    decide_remove_green,
    find_local_gaia_catalog,
    has_plate_solution,
    is_colour_image,
    level_stretched_sky,
    match_star_widths,
    online_catalog_reachable,
)
from astrometricslib.pipelines.stacking.post_processing.preview_cosmic_clarity import (
    denoise_with_cosmic_clarity,
)
from astrometricslib.pipelines.stacking.post_processing.preview_graxpert import remove_background
from astrometricslib.pipelines.stacking.post_processing.preview_settings import (
    PreviewSettings,
    write_preview_settings,
)
from astrometricslib.pipelines.stacking.post_processing.preview_siril import (
    PROCESSED_STEM,
    build_bright_object_script,
    build_picture_script,
    build_preview_script,
    build_stretch_script,
    last_siril_output,
    run_preview_script,
    sky_background_for_file,
)
from astrometricslib.pipelines.stacking.post_processing.preview_steps import SkippedPreviewStep, next_source
from astrometricslib.pipelines.stacking.post_processing.quarter_turn import read_quarter_turn
from astrometricslib.pipelines.stacking.post_processing.sky_level import choose_sky_level_for_file
from astrometricslib.pipelines.stacking.post_processing.stack_crop import CropBox, crop_file, turn_box
from astrometricslib.pipelines.stacking.post_processing.star_tone import tone_stars_in_file
from astrometricslib.pipelines.stacking.post_processing.starnet_stretch import stretch_with_starnet

logger = logging.getLogger(__name__)


# The sentence Siril logs when its photometric color calibration fits the stars
# poorly. The text follows Siril 1.4.
_SIRIL_IMPRECISE_PCC_TEXT = "imprecise solution"

# The scratch folder lives on the local disk (~/.cache/astrometrics/siril), not
# system temp folder: that is often held in RAM, and a Flatpak Siril is
# sandboxed and cannot see /tmp. This is the same place the Siril driver does
# its own work (see `ImageProcessing.workdir`).
_SCRATCH_ROOT = siril_work_directory()


# A scratch folder older than this many seconds belongs to a run that was
# killed, for example by a reboot, and is removed. A preview takes about a
# minute, so an hour never touches a run that is still going.
_STALE_SCRATCH_SECONDS = 3600


def _remove_stale_scratch_folders() -> None:
    """Delete scratch folders left behind by runs that were killed."""
    if not os.path.isdir(_SCRATCH_ROOT):
        return
    now = time.time()
    for name in os.listdir(_SCRATCH_ROOT):
        folder = os.path.join(_SCRATCH_ROOT, name)
        if name.startswith("stack_preview_") and os.path.isdir(folder):
            if now - os.path.getmtime(folder) > _STALE_SCRATCH_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)


def _picture_script(
    scratch: str,
    siril_executable: str,
    settings: PreviewSettings | None = None,
    steps_log: list[str] | None = None,
    settings_used: dict[str, Any] | None = None,
    skipped_steps: list[SkippedPreviewStep] | None = None,
    background_outcomes: list[BackgroundGradientOutcome] | None = None,
    object_category: str | None = None,
    target_anchor: TargetAnchor | None = None,
) -> list[str] | None:
    """Run the cleanup steps on the scratch copy and choose the final script.

    The steps run in this order: the background removal (a fitted plane for a
    flat sky, otherwise GraXpert) on the linear copy, then Siril's
    green removal (colour stacks only) and stretch, then Cosmic Clarity and
    the star toning on the stretched copy.
    Each of those runs only if configured, and each starts from the result of
    the step before it. With neither Cosmic Clarity nor the star toning, one
    Siril script stretches and saves the picture.

    A stack of a bright extended object skips all of this and gets one script
    with its own stretch (see `bright_object`).

    Parameters
    ----------
    scratch : `str`
        The scratch folder, holding a copy of the stack named ``stack.fits``.
    siril_executable : `str`
        The command that starts Siril.
    settings : `PreviewSettings`, optional
        Choices for this run that replace the saved settings.
    steps_log : `list` [`str`], optional
        If given, the name of each step and whether it ran is added to it,
        so the caller can report what was done.
    settings_used : `dict`, optional
        If given, the choices this run really used are put into it: the
        configured value where the run did not override it, and `False`
        where a step was skipped (a bright object skips the denoise and the
        star toning). The keys are ``denoise``, ``denoise_strength``
        (`None` when the denoise did not run), ``star_toning``, ``starnet``
        and ``bright_object``, and ``masked_stretch``: whether the masked
        stretch was used, the number and area of its regions, its white
        point and, when it was not used, the reason. ``object_category`` says
        which kind the masked stretch used and, for a cluster inside a nebula,
        the nebula (see `cluster_nebula`). ``background`` says
        which background removal ran (``plane``, ``graxpert`` or ``none``),
        the sky tilt that was measured, the limit it was compared with, the
        number of sky bins and the reason, and for the plane whether the
        radial (vignetting) part was used, its fall at the corners and why.
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        If given, each cleanup step that failed and was skipped is added to
        it.
    background_outcomes : `list` [`BackgroundGradientOutcome`], optional
        If given, the measurement of GraXpert's background model is added to
        it: the numbers, or the reason there are none (GraXpert not
        configured, skipped for a bright object, failed, or no model).
        GraXpert's full-size model file is deleted either way.
    object_category : `str` or `None`, optional
        The target's kind (`Target.object_category`). Only a nebula or a
        galaxy can get the masked stretch (see `masked_stretch`). `None`,
        the default, means unknown and never gets it.
    target_anchor : `TargetAnchor` or `None`, optional
        The target's catalog position and size, so the masked stretch is
        kept to the region at the target (see `masked_stretch`).

    Returns
    -------
    commands : `list` [`str`] or `None`
        The Siril script that saves the picture, or `None` if the stretch
        step needed before Cosmic Clarity did not work.
    """
    configuration = get_configuration()
    settings = settings or PreviewSettings()
    name = "stack.fits"
    bright_object = choose_bright_object_stretch_for_file(os.path.join(scratch, name))
    if bright_object is not None:
        logger.info(
            "Bright object: the normal stretch would turn %.1f%% of the picture white; "
            "using a stretch scaled to the object, without GraXpert or the denoise.",
            100 * bright_object.white_fraction,
        )
        if background_outcomes is not None:
            background_outcomes.append(
                not_measured_outcome("the preview of a bright object skips GraXpert, so there is no model")
            )
        if steps_log is not None:
            steps_log.append("Bright object: its own stretch, without GraXpert or the denoise")
        if settings_used is not None:
            settings_used.update(
                denoise=False,
                denoise_strength=None,
                star_toning=False,
                starnet=False,
                bright_object=True,
                background={
                    "method": "none",
                    "reason": "the preview of a bright object skips the background step",
                },
                masked_stretch=MaskedStretchPlan(
                    used=False, reason="the bright-object stretch was used", category=object_category
                ).record(),
            )
        return build_bright_object_script(name, "preview", bright_object)
    steps: list[str] = []
    graxpert_executable = configuration.get_graxpert_executable()
    if graxpert_executable:
        name, background_record = remove_background(
            scratch, name, graxpert_executable, steps, skipped_steps, background_outcomes
        )
    else:
        background_record = {"method": "none", "reason": "GraXpert is not configured"}
        steps.append("GraXpert not configured")
        if background_outcomes is not None:
            background_outcomes.append(
                not_measured_outcome("GraXpert is not configured, so there is no model")
            )
    colour = is_colour_image(os.path.join(scratch, name))
    if colour and configuration.get_preview_star_width_match_enabled():
        name = match_star_widths(scratch, name, steps, settings_used, skipped_steps)
    elif colour:
        steps.append("Star width matching off")
        if settings_used is not None:
            settings_used["star_width_match"] = {"applied": False, "reason": "the setting is off"}
    if colour and configuration.get_preview_colour_calibration_enabled():
        calibrated_name, method = name, ""
        pcc_imprecise: list[bool] = []
        if configuration.get_preview_colour_calibration_method() == "pcc":
            solve = not has_plate_solution(os.path.join(scratch, name))

            def calibrate_with_pcc(input_path: str, output_path: str) -> bool:
                """Run Siril PCC, first solving the field if it is unsolved.

                Returns
                -------
                succeeded : `bool`
                    Whether Siril exited without an error.

                Raises
                ------
                OSError
                    If Siril would have to ask a server that does not answer.
                """
                local_catalog = find_local_gaia_catalog()
                if local_catalog is None and not online_catalog_reachable():
                    # Reported as a skipped step, with the reason; the stars
                    # of the stack give the colours instead.
                    raise OSError(
                        "no local Gaia catalog is installed and the online Gaia server does not answer"
                    )
                commands = build_pcc_script(
                    os.path.basename(input_path),
                    os.path.splitext(os.path.basename(output_path))[0],
                    solve,
                    local_catalog,
                )
                succeeded = run_preview_script(scratch, commands, siril_executable)
                if succeeded and _SIRIL_IMPRECISE_PCC_TEXT in last_siril_output():
                    pcc_imprecise.append(True)
                return succeeded

            calibrated_name = next_source(
                "Siril PCC",
                name,
                "colour.fits",
                scratch,
                calibrate_with_pcc,
                check_result=True,
                skipped_steps=skipped_steps,
            )
            method = "Siril PCC"
            if calibrated_name != name and pcc_imprecise:
                warning = "Siril reports that its photometric color calibration found an imprecise solution"
                logger.warning("%s.", warning)
                if skipped_steps is not None:
                    skipped_steps.append(SkippedPreviewStep("Siril PCC", warning, still_used=True))
                if settings_used is not None:
                    settings_used["colour_calibration_warning"] = warning
        checked = calibrated_name != name
        if calibrated_name == name:
            # PCC was not asked for, or failed (no network, no catalog, no
            # solution): the Gaia stars like the Sun set the white point.
            calibrated_name = calibrate_from_sun_like_stars(scratch, name, settings_used, skipped_steps)
            method = "from the stars like the Sun"
        steps.append(
            f"Colour calibration ({method}) done"
            if calibrated_name != name
            else "Colour calibration not applied: the colours are left uncalibrated"
        )
        # A balanced picture needs a stretch shared by all channels: stretched
        # one by one, the channels would be set back to equal sky and scale.
        linked = calibrated_name != name
        name = calibrated_name
        if checked:
            name = check_white_balance(scratch, name, steps, settings_used, skipped_steps)
    else:
        linked = False
        steps.append("Colour calibration not used")
    green_switch_on = bool(configuration.get_preview_remove_green_noise_enabled())
    remove_green, green_reason = decide_remove_green(colour, green_switch_on, linked)
    steps.append(
        f"Remove Green Noise in the Siril stretch ({green_reason})"
        if remove_green
        else f"Remove Green Noise not used ({green_reason})"
    )
    if settings_used is not None:
        settings_used["remove_green_noise"] = {"ran": remove_green, "reason": green_reason}
    stretched_by_starnet = False
    linear_name = name
    if settings.starnet:
        starnet_name = stretch_with_starnet(
            scratch, name, siril_executable, remove_green, linked, settings, skipped_steps
        )
        if starnet_name is not None:
            name, stretched_by_starnet = starnet_name, True
            steps.append("StarNet done: starless and stars stretched on their own")
        else:
            steps.append("StarNet failed: the normal stretch is used")
    stretch_record: dict[str, Any] = {}
    choice = choose_sky_level_for_file(os.path.join(scratch, name))
    logger.info("Preview sky level %.2f: %s.", choice.sky_level, choice.reason)
    if stretched_by_starnet:
        category_verdict = CategoryVerdict(
            object_category, object_category, None, "StarNet stretched the picture, so the kind is not used"
        )
        masked = MaskedStretchPlan(
            used=False, reason="StarNet stretched the picture", category=object_category
        )
    else:
        # A cluster that lies inside a nebula is treated as a nebula (see
        # `cluster_nebula`); any other kind is kept.
        category_verdict = refine_object_category(
            object_category, target_anchor, os.path.join(scratch, linear_name)
        )
        masked = plan_masked_stretch_for_file(
            os.path.join(scratch, linear_name), category_verdict.category, target_anchor
        )
    logger.info("Masked stretch: %s.", masked.step_sentence())
    denoise_executable = configuration.get_cosmic_clarity_denoise_executable()
    denoise_note = "Cosmic Clarity not configured"
    if not denoise_executable and configuration.get_cosmic_clarity_denoise_path():
        denoise_note = "Cosmic Clarity off in the settings"
    if settings.denoise is True and not denoise_executable:
        # Asked for on this run: use the program even if the settings have
        # the denoise switched off. It still cannot run without its path.
        denoise_executable = configuration.get_cosmic_clarity_denoise_path()
        if not denoise_executable:
            denoise_note = "Cosmic Clarity not configured (no program path set)"
    if settings.denoise is False and denoise_executable:
        denoise_executable, denoise_note = None, "Cosmic Clarity off for this run"
    tone_stars = configuration.get_preview_star_tone_enabled()
    if settings.star_toning is not None:
        tone_stars = settings.star_toning
    if stretched_by_starnet:
        # The stars were stretched apart already, so the formula that dims
        # stars would act twice.
        tone_stars = False
    # A color stack's sky is leveled after the stretch and the denoise (see
    # `sky_levelling`); the stretch is then saved as FITS first.
    level_sky = bool(colour)
    if settings_used is not None:
        strength_used = None
        if denoise_executable:
            strength_used = (
                settings.denoise_strength
                if settings.denoise_strength is not None
                else configuration.get_cosmic_clarity_denoise_strength()
            )
        settings_used.update(
            denoise=bool(denoise_executable),
            denoise_strength=strength_used,
            star_toning=bool(tone_stars),
            starnet=stretched_by_starnet,
            bright_object=False,
            background=background_record,
            object_category=category_verdict.record(),
            masked_stretch=masked.record(),
            stretch_sky=stretch_record,
        )
    if (
        not stretched_by_starnet
        and not denoise_executable
        and not tone_stars
        and not masked.used
        and not level_sky
    ):
        steps.extend(["Siril stretch done in the final script", denoise_note, "Star toning off"])
        logger.info("Preview steps: %s.", "; ".join(steps))
        if steps_log is not None:
            steps_log.extend(steps)
        return build_preview_script(
            name,
            "preview",
            choice.sky_level,
            remove_green,
            linked,
            sky_background_for_file(os.path.join(scratch, name), remove_green, linked, stretch_record),
        )
    if not stretched_by_starnet:
        stretched = run_preview_script(
            scratch,
            build_stretch_script(
                name,
                "stretched",
                choice.sky_level,
                remove_green,
                linked,
                sky_background_for_file(os.path.join(scratch, name), remove_green, linked, stretch_record),
            ),
            siril_executable,
        )
        if not stretched or not os.path.isfile(os.path.join(scratch, "stretched.fits")):
            steps.append("Siril stretch failed")
            logger.info("Preview steps: %s.", "; ".join(steps))
            if steps_log is not None:
                steps_log.extend(steps)
            return None
        name = "stretched.fits"
        steps.append("Siril stretch done")
    if denoise_executable:
        strength = (
            settings.denoise_strength
            if settings.denoise_strength is not None
            else configuration.get_cosmic_clarity_denoise_strength()
        )

        # Only a color stack has color speckle to remove; a single-channel
        # stack keeps the brightness-only mode.
        color_strength = configuration.get_preview_color_denoise_strength() if colour else None

        def denoise(input_path: str, output_path: str) -> bool:
            """Run Cosmic Clarity's denoise program.

            Returns
            -------
            succeeded : `bool`
                Whether Cosmic Clarity succeeded.
            """
            return denoise_with_cosmic_clarity(
                denoise_executable, input_path, output_path, strength, color_strength
            )

        denoised_name = next_source(
            "Cosmic Clarity", name, "denoised.fits", scratch, denoise, skipped_steps=skipped_steps
        )
        steps.append("Cosmic Clarity " + ("done" if denoised_name != name else "skipped after a failure"))
        name = denoised_name
    else:
        steps.append(denoise_note)
    if level_sky:
        name = level_stretched_sky(scratch, name, steps, settings_used, skipped_steps)
    if tone_stars:
        toned_name = next_source(
            "Star toning", name, "toned.fits", scratch, tone_stars_in_file, skipped_steps=skipped_steps
        )
        steps.append("Star toning " + ("done" if toned_name != name else "skipped after a failure"))
        name = toned_name
    else:
        steps.append("Star toning off")
    if masked.used:
        linear_path = os.path.join(scratch, linear_name)

        def blend_masked_stretch(input_path: str, output_path: str) -> bool:
            """Blend the second stretch into the picture.

            Returns
            -------
            succeeded : `bool`
                Whether the blended picture was written.
            """
            return apply_masked_stretch_in_file(linear_path, input_path, output_path, masked)

        masked_name = next_source(
            "Masked stretch", name, "masked.fits", scratch, blend_masked_stretch, skipped_steps=skipped_steps
        )
        steps.append(
            masked.step_sentence() if masked_name != name else "Masked stretch skipped after a failure"
        )
        name = masked_name
    logger.info("Preview steps: %s.", "; ".join(steps))
    if steps_log is not None:
        steps_log.extend(steps)
    return build_picture_script(name, "preview")


# Header keywords that describe the file's own layout. They are never copied
# from the stack to its stretched FITS: the stretched file keeps its own.
_LAYOUT_KEYWORDS = frozenset({
    "SIMPLE",
    "BITPIX",
    "NAXIS",
    "NAXIS1",
    "NAXIS2",
    "NAXIS3",
    "EXTEND",
    "BZERO",
    "BSCALE",
    "ROWORDER",
    "COMMENT",
    "HISTORY",
})


def picture_crop_box(stacked_path: str) -> CropBox | None:
    """Find the part of a stack its processed picture keeps.

    The stacking run saves the box in the stack's registration record, on the
    stack as registered. The saved stack may have been turned by a quarter
    turn since (see `quarter_turn`), so the box is turned the same way.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stack, as saved.

    Returns
    -------
    box : `CropBox` or `None`
        The box on the saved stack's pixels, or `None` when the picture keeps
        the whole stack: no record, no box, a spectral stack, or a box that
        does not fit the file.
    """
    record = read_registration_record(stacked_path)
    if record is None or record.processed_crop is None or record.is_spectral:
        return None
    try:
        header = read_header(stacked_path)
        width, height = int(header["NAXIS1"]), int(header["NAXIS2"])
    except OSError, KeyError, ValueError:
        return None
    degrees = read_quarter_turn(header) or 0
    registered_width, registered_height = (height, width) if degrees % 180 else (width, height)
    x0, x1, y0, y1 = record.processed_crop
    box = turn_box(CropBox(x0, x1, y0, y1), degrees, registered_width, registered_height)
    if not (0 <= box.x0 < box.x1 <= width and 0 <= box.y0 < box.y1 <= height):
        logger.warning(
            "The saved picture crop of '%s' does not fit the stack; the picture keeps it whole.", stacked_path
        )
        return None
    return box


def _copy_observation_header(stacked_path: str, processed_path: str) -> None:
    """Copy the observation keywords from a stack to its stretched FITS.

    The cleanup programs can drop the stack's header, which holds the total
    exposure time, the telescope, the object and similar facts. The viewer
    reads some of them, so they are copied back. Keywords the stretched file
    already has, and the keywords that describe the file's layout, are left
    as they are. A note in the header says the pixels are a stretched picture
    and not linear data. A failure is logged and does not fail the preview.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stack, or of the copy of it the picture was made from
        (which holds the crop's reference pixel and cards).
    processed_path : `str`
        Path of the stretched FITS, which is rewritten.
    """
    try:
        stack_header = read_header(stacked_path)
        data = read_data(processed_path)
        header = read_header(processed_path)
        for keyword in stack_header:
            if keyword in _LAYOUT_KEYWORDS or keyword in header:
                continue
            header[keyword] = (stack_header[keyword], stack_header.comments[keyword])
        header["HISTORY"] = "Stretched picture for display; not linear data."
        write_image(processed_path, data, header)
    except (OSError, ValueError) as error:
        logger.warning("Could not copy the stack's header into '%s': %s.", processed_path, error)


def write_stack_preview(
    stacked_path: str,
    settings: PreviewSettings | None = None,
    steps_log: list[str] | None = None,
    skipped_steps: list[SkippedPreviewStep] | None = None,
    background_outcomes: list[BackgroundGradientOutcome] | None = None,
    object_category: str | None = None,
    target_anchor: TargetAnchor | None = None,
) -> str | None:
    """Save a cleaned-up, stretched JPEG and FITS of a stack beside it.

    The FITS (see `processed_fits_path_for`) holds the same picture as the
    JPEG as 32-bit numbers between 0 and 1. Any earlier pictures of the same
    stack are removed first, so a failed run never leaves a picture of an
    older stack behind.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stacked FITS file.
    settings : `PreviewSettings`, optional
        Choices for this run that replace the saved settings. Nothing is
        written to the configuration.
    steps_log : `list` [`str`], optional
        If given, the steps that ran are added to it.
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        If given, each cleanup step that failed and was skipped is added to
        it, so the caller can record that the picture differs from its
        settings (see `preview_notices`).
    background_outcomes : `list` [`BackgroundGradientOutcome`], optional
        If given, GraXpert's background model is measured and the result is
        added to it (see `background_gradient`), and the 64 by 64 map is saved
        beside the picture (`background_model_path_for`). Left out, the model
        is deleted unmeasured. A preview that was not made adds nothing.
    object_category : `str` or `None`, optional
        The target's kind (`Target.object_category`), for the masked stretch
        (see `masked_stretch`). Left out, the stack is of an unknown kind and
        the step is not used. Pass `None` for a spectral stack.
    target_anchor : `TargetAnchor` or `None`, optional
        The target's catalog position and size (see `anchor_for_target`).

    Returns
    -------
    preview_path : `str` or `None`
        Path of the new JPEG, or `None` if the stack file is missing, Siril
        is not configured, or Siril failed. The reason is logged as a warning.

    Raises
    ------
    RunAbandonedError
        If the job was cancelled. The program that was running is stopped,
        the scratch folder is removed and no picture is left beside the
        stack.
    """
    preview_path = preview_path_for(stacked_path)
    processed_path = processed_fits_path_for(stacked_path)
    background_model_path = background_model_path_for(stacked_path)
    for old_picture in (
        preview_path,
        processed_path,
        preview_settings_path_for(stacked_path),
        background_model_path,
    ):
        if os.path.exists(old_picture):
            os.remove(old_picture)
    if not os.path.isfile(stacked_path):
        logger.warning("No preview made: stack '%s' does not exist.", stacked_path)
        return None
    siril_executable = get_configuration().get_siril_executable()
    if not siril_executable:
        logger.warning("No preview made for '%s': no Siril executable is configured.", stacked_path)
        return None
    scratch = None
    try:
        os.makedirs(_SCRATCH_ROOT, exist_ok=True)
        _remove_stale_scratch_folders()
        scratch = tempfile.mkdtemp(prefix="stack_preview_", dir=_SCRATCH_ROOT)
        scratch_stack = os.path.join(scratch, "stack.fits")
        shutil.copyfile(stacked_path, scratch_stack)
        # Only the picture is cropped, and before GraXpert and Cosmic Clarity
        # run, so their models never see the noisy edge. The stack is not.
        crop_box = picture_crop_box(stacked_path)
        if crop_box is not None:
            try:
                crop_file(scratch_stack, crop_box, note_crop=True)
            except (OSError, ValueError) as error:
                logger.warning(
                    "Could not crop the picture of '%s': %s. It keeps the whole stack.", stacked_path, error
                )
        settings_used: dict[str, Any] = {}
        steps_taken: list[str] = []
        commands = _picture_script(
            scratch,
            siril_executable,
            settings,
            steps_taken,
            settings_used,
            skipped_steps,
            background_outcomes,
            object_category,
            target_anchor,
        )
        if steps_log is not None:
            steps_log.extend(steps_taken)
        succeeded = commands is not None and run_preview_script(scratch, commands, siril_executable)
        picture = os.path.join(scratch, "preview.jpg")
        if not succeeded or not os.path.isfile(picture):
            logger.warning("No preview made for '%s': Siril did not save the picture.", stacked_path)
            return None
        shutil.move(picture, preview_path)
        # The FITS copy is a bonus for the viewer. Without it the JPEG alone
        # is still a good preview, so a missing file is not a failure.
        stretched_fits = os.path.join(scratch, f"{PROCESSED_STEM}.fits")
        if os.path.isfile(stretched_fits):
            shutil.move(stretched_fits, processed_path)
            # The scratch copy holds the stack's header with the crop applied
            # (the shifted reference pixel and the crop cards).
            _copy_observation_header(scratch_stack, processed_path)
        write_preview_settings(stacked_path, settings or PreviewSettings(), settings_used, steps_taken)
        for background_outcome in background_outcomes or []:
            write_binned_model(background_outcome, background_model_path)
    except RunAbandonedError:
        # The job was cancelled. A picture only partly made is not left
        # beside the stack; the caller of a remake puts the old one back.
        for partial_picture in (
            preview_path,
            processed_path,
            preview_settings_path_for(stacked_path),
            background_model_path,
        ):
            if os.path.exists(partial_picture):
                os.remove(partial_picture)
        raise
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("No preview made for '%s': %s.", stacked_path, error)
        return None
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    return preview_path


def record_preview_as_processed_image(
    target: object,
    is_spectral: bool,
    stacked_path: str,
    preview_path: str,
    keep_attached: bool = False,
) -> bool:
    """Show a stack's preview picture as its target's processed image.

    The image viewer shows a target's processed image first, so recording the
    preview there makes the picture appear. The stack's stretched FITS is
    recorded when it exists, and the JPEG otherwise. A processed image that a
    person attached is replaced too: only the target's pointer changes, and
    the attached file stays where it is. One case is left alone, because the
    picture there is not a preview of this stack:

    - The stack is not the one the target shows. A target with stacks from
      several telescope setups shows only one of them.

    `keep_attached` keeps an attached picture instead.

    Parameters
    ----------
    target : `Target`
        The target that owns the stack. The caller saves it afterwards.
    is_spectral : `bool`
        Whether the stack is the target's spectroscopy stack.
    stacked_path : `str`
        Path of the stacked FITS file.
    preview_path : `str`
        Path of the preview picture made from it.
    keep_attached : `bool`, optional
        If `True`, a picture a person attached is kept. The default replaces
        it with the new picture.

    Returns
    -------
    recorded : `bool`
        `True` if the target's processed image now names the stack's
        stretched FITS or `preview_path`.
    """
    stacking = target.spectral_stacking if is_spectral else target.stacking
    if stacking.stacked_image != stacked_path:
        return False
    current = stacking.processed_image
    is_automatic = is_preview_path(current or "") or is_processed_fits_path(current or "")
    if current and current != preview_path and keep_attached and not is_automatic:
        return False
    # The stretched FITS is shown when there is one, because the viewer can
    # zoom into it. The JPEG is the fallback.
    processed_path = processed_fits_path_for(stacked_path)
    stacking.processed_image = processed_path if os.path.isfile(processed_path) else preview_path
    return True
