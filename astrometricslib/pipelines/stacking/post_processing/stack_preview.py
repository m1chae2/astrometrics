"""Writes a viewable picture next to a finished stack.

A stacked FITS file holds linear data: pixel values proportional to the
light collected. Most of the sky is far too dark in that form to see
anything. Up to four steps turn it into a picture.

1. GraXpert, if it is configured, removes the sky's brightness gradient.
   Its AI model learns what the smooth background looks like and subtracts
   it, so uneven sky glow or vignetting does not wash out the faint parts.
2. Siril's Autostretch command brightens the faint parts the way its own
   display does. It sets the black point from the image's own background
   noise, then bends the brightness curve so the typical background lands on
   a chosen grey level. The level comes from the stack itself (see
   `sky_level`): faint targets get a lighter sky so their faint structure
   stays visible, and bright targets get a darker one.
3. Cosmic Clarity, if it is configured, removes noise with an AI model. It
   runs after the stretch, on the stretched picture. Run before the stretch,
   it lowers the noise level that Autostretch measures, so the stretch turns
   the contrast back up and the remaining noise looks as strong as before,
   with extra blotchy structure. Measured on the M 13 stack, the visible
   grain fell by only 22 percent that way, against 73 percent when the
   denoise ran after the stretch.
4. Star toning (see `star_tone`) dims the stars in proportion to their
   brightness and compresses the brightest values below pure white. The
   stretch makes nearly every star a small white dot of about the same
   brightness. The toning brings back some of their real range. It acts on
   the stretched picture and leaves the background and nebulae alone. The
   setting ``preview_star_tone_enabled`` turns it off.

A stack of a bright extended object, such as the Moon, takes a different
route. The normal stretch would push the whole object to white (see
`bright_object`), so one Siril script applies a stretch scaled to the
object's own brightness, and GraXpert and Cosmic Clarity are skipped.

All steps run on a scratch copy of the stack and the result is saved as a
JPEG beside the stack. The JPEG is only a picture for people to look at. The
stack itself is never changed, so photometry, spectroscopy and plate solving
keep reading the linear data. A failed or skipped step never fails the stack:
if GraXpert, Cosmic Clarity or the star toning fails, the picture is made from
the result of the step before it, and if Siril fails, there is no picture.
"""

import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable

import numpy as np

from astrometricslib.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.drivers.siril_interface import siril_process_lock
from astrometricslib.pipelines.shared.image_scaling import AUTOSTRETCH_SHADOWS_CLIP_SIGMA
from astrometricslib.pipelines.shared.stack_preview_path import (
    PREVIEW_JPEG_QUALITY,
    is_preview_path,
    is_processed_fits_path,
    preview_path_for,
    processed_fits_path_for,
)
from astrometricslib.pipelines.stacking.post_processing.bright_object import (
    BrightObjectStretch,
    choose_bright_object_stretch_for_file,
)
from astrometricslib.pipelines.stacking.post_processing.sky_level import choose_sky_level_for_file
from astrometricslib.pipelines.stacking.post_processing.star_tone import tone_stars_in_file
from astrometricslib.utilities.config_loader import get_configuration

logger = logging.getLogger(__name__)

# The longest a single Siril script may run, in seconds. The M 13 script took
# 0.7 seconds once Siril had started, so two minutes leaves room for Siril's
# start-up and for a much larger stack, and still stops a hung process. The
# wait for a free Siril slot is not counted: the lock is taken before the
# clock starts.
PREVIEW_TIMEOUT_SECONDS = 120

# The longest GraXpert may run, in seconds. On the M 13 stack (3008 x 3008
# pixels) the extraction took 7 seconds including start-up, with the model
# already on disk. A first run also downloads the AI model, which took 34
# seconds. Five minutes covers both and a much larger stack, and still stops
# a hung process. Slot waiting is not counted, as above.
GRAXPERT_TIMEOUT_SECONDS = 300

# The longest Cosmic Clarity may run, in seconds. A 1024 x 1024 crop took 7.7
# seconds on the GPU including the model load. The whole preview of the full
# M 13 stack (3008 x 3008 pixels), with GraXpert and Siril as well, took 54
# seconds, so Cosmic Clarity took under that. Five minutes leaves room for a
# larger stack or a slower card, and still stops a hung process.
COSMIC_CLARITY_TIMEOUT_SECONDS = 300

# The scratch folder lives under the home folder, not the system temp folder:
# a Flatpak Siril is sandboxed and cannot see /tmp. This is the same place
# the Siril driver does its own work (see `ImageProcessing.workdir`).
_SCRATCH_ROOT = os.path.join(os.path.expanduser("~"), "Siril", "Work")

# The share of an image's pixels that must be finite numbers for a cleanup
# step's result to be used. GraXpert can return not-a-number pixels for a
# stack with large empty regions. 0.9 allows for blank borders and still
# rejects an image that is mostly invalid. Not tuned beyond that.
_MINIMUM_FINITE_FRACTION = 0.9

# A scratch folder older than this many seconds belongs to a run that was
# killed, for example by a reboot, and is removed. A preview takes about a
# minute, so an hour never touches a run that is still going.
_STALE_SCRATCH_SECONDS = 3600

# The names this module gives the files it stages in Cosmic Clarity's folders.
# Only files with these names are ever deleted from those folders.
_OWN_STAGED_FILE = re.compile(r"^stack_preview_\d+(_denoised)?\.fits$")

# The file name, without extension, under which each final script also saves
# the stretched picture as FITS. `write_stack_preview` moves it next to the
# stack.
_PROCESSED_STEM = "processed"

# Siril's `savejpg` writes the picture upside down compared with the FITS file
# it was made from (checked on NGC 6888, M 57, M 101 and NGC 7000: the FITS
# array, which matches the camera's own view, correlates 1.0 with the JPEG only
# after a vertical flip). `mirrorx` flips the loaded image vertically just
# before the JPEG is saved, after the FITS copy, so both pictures show the
# same way up.
_JPEG_ORIENTATION_COMMAND = "mirrorx"

# The oldest Siril that has the `autostretch` and `savejpg` commands used here.
_MINIMUM_SIRIL_VERSION = "1.2.0"


def _autostretch_command(sky_level: float) -> str:
    """Write the Siril command that stretches the loaded image.

    Parameters
    ----------
    sky_level : `float`
        The brightness, between 0 and 1, the sky should land on.

    Returns
    -------
    command : `str`
        Siril's ``autostretch`` command with the black-point clip and the
        sky level written out.
    """
    return f"autostretch {AUTOSTRETCH_SHADOWS_CLIP_SIGMA} {sky_level:.3f}"


def build_preview_script(stacked_file_name: str, preview_stem: str, sky_level: float) -> list[str]:
    """Write the Siril commands that stretch one stack and save it.

    The picture is saved twice: as FITS (``processed.fits``, numbers between
    0 and 1) and as JPEG.

    Names are put in quotes so a file name with a space still works.

    Parameters
    ----------
    stacked_file_name : `str`
        File name of the stack, relative to the folder Siril is started in.
    preview_stem : `str`
        File name for the picture, relative to that folder and without the
        ``.jpg`` extension (Siril adds it).
    sky_level : `float`
        The brightness, between 0 and 1, the sky should land on (see
        `sky_level.choose_sky_level`).

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return [
        f"requires {_MINIMUM_SIRIL_VERSION}",
        f'load "{stacked_file_name}"',
        _autostretch_command(sky_level),
        f'save "{_PROCESSED_STEM}"',
        _JPEG_ORIENTATION_COMMAND,
        f'savejpg "{preview_stem}" {PREVIEW_JPEG_QUALITY}',
        "close",
    ]


def build_bright_object_script(
    source_file_name: str, preview_stem: str, stretch: BrightObjectStretch
) -> list[str]:
    """Write the Siril commands that stretch a bright object and save it.

    The picture is saved as FITS (``processed.fits``) and as JPEG.

    Siril's ``mtf`` command clips the image between a black and a white
    point (both between 0 and 1) and applies the midtones curve. A stack with
    values above 1 is multiplied down first.

    Parameters
    ----------
    source_file_name : `str`
        File name of the stack, relative to the folder Siril is started in.
    preview_stem : `str`
        File name for the picture, relative to that folder and without the
        ``.jpg`` extension (Siril adds it).
    stretch : `BrightObjectStretch`
        The black point, white point, midtones balance and scale.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    commands = [f"requires {_MINIMUM_SIRIL_VERSION}", f'load "{source_file_name}"']
    if stretch.scale < 1.0:
        commands.append(f"fmul {stretch.scale:.9f}")
    commands += [
        f"mtf {stretch.black_point:.6f} {stretch.midtones:.6f} {stretch.white_point:.6f}",
        f'save "{_PROCESSED_STEM}"',
        _JPEG_ORIENTATION_COMMAND,
        f'savejpg "{preview_stem}" {PREVIEW_JPEG_QUALITY}',
        "close",
    ]
    return commands


def build_stretch_script(source_file_name: str, stretched_stem: str, sky_level: float) -> list[str]:
    """Write the Siril commands that stretch an image and save it as FITS.

    The saved file holds the stretched picture as numbers between 0 and 1.
    Cosmic Clarity then denoises it, and `build_picture_script` turns the
    result into a JPEG.

    Parameters
    ----------
    source_file_name : `str`
        File name of the image to stretch, relative to the folder Siril is
        started in.
    stretched_stem : `str`
        File name for the stretched copy, relative to that folder and without
        the ``.fits`` extension (Siril adds it).
    sky_level : `float`
        The brightness, between 0 and 1, the sky should land on.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return [
        f"requires {_MINIMUM_SIRIL_VERSION}",
        f'load "{source_file_name}"',
        _autostretch_command(sky_level),
        f'save "{stretched_stem}"',
        "close",
    ]


def build_picture_script(source_file_name: str, preview_stem: str) -> list[str]:
    """Write the Siril commands that save an already stretched image.

    The picture is saved as FITS (``processed.fits``) and as JPEG.

    Parameters
    ----------
    source_file_name : `str`
        File name of the stretched image, relative to the folder Siril is
        started in.
    preview_stem : `str`
        File name for the picture, relative to that folder and without the
        ``.jpg`` extension (Siril adds it).

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry. It does not stretch again.
    """
    return [
        f"requires {_MINIMUM_SIRIL_VERSION}",
        f'load "{source_file_name}"',
        f'save "{_PROCESSED_STEM}"',
        _JPEG_ORIENTATION_COMMAND,
        f'savejpg "{preview_stem}" {PREVIEW_JPEG_QUALITY}',
        "close",
    ]


def run_preview_script(directory: str, commands: list[str], siril_executable: str) -> bool:
    """Run a short Siril script in a folder.

    Waits for a free Siril slot first, so a preview never competes with a
    stack for the machine's Siril limit.

    Parameters
    ----------
    directory : `str`
        Folder to start Siril in (its ``-d`` option). The script file is
        written here and removed afterwards.
    commands : `list` [`str`]
        The Siril commands to run, in order.
    siril_executable : `str`
        The command that starts Siril, as in the configuration. A Flatpak
        command is given access to the host's files, since the sandbox cannot
        see most folders by default.

    Returns
    -------
    succeeded : `bool`
        `True` if Siril exited without an error code.
    """
    script_path = os.path.join(directory, f"stack_preview{os.getpid()}.ssf")
    with open(script_path, "w", encoding="utf-8") as script_file:
        script_file.write("\n".join(commands) + "\n")
    parts = shlex.split(siril_executable)
    if "flatpak" in parts[0] and "run" in parts:
        parts.insert(parts.index("run") + 1, "--filesystem=host")
    try:
        with siril_process_lock():
            completed = subprocess.run(
                [*parts, "-d", directory, "-s", script_path],
                capture_output=True,
                text=True,
                timeout=PREVIEW_TIMEOUT_SECONDS,
                check=False,
            )
    finally:
        os.remove(script_path)
    return completed.returncode == 0


def flatten_background(graxpert_executable: str, input_path: str, output_stem: str) -> bool:
    """Remove the sky gradient from an image with GraXpert's AI model.

    Waits for a free slot first, as `run_preview_script` does. GraXpert uses
    the graphics card when it can and the processor otherwise, so a machine
    without CUDA still works.

    Parameters
    ----------
    graxpert_executable : `str`
        The command that starts GraXpert, as in the configuration.
    input_path : `str`
        The FITS image to flatten. It is not changed.
    output_stem : `str`
        Where to write the result, without an extension. GraXpert adds
        ``.fits``.

    Returns
    -------
    succeeded : `bool`
        `True` if GraXpert exited without an error code.
    """
    arguments = [
        *shlex.split(graxpert_executable),
        "-cli",
        "-cmd",
        "background-extraction",
        "-gpu",
        "true",
        "-correction",
        "Subtraction",
        "-smoothing",
        "0.0",
        "-output",
        output_stem,
        input_path,
    ]
    with siril_process_lock():
        completed = subprocess.run(
            arguments, capture_output=True, text=True, timeout=GRAXPERT_TIMEOUT_SECONDS, check=False
        )
    return completed.returncode == 0


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


def _remove_own_staged_files(folder: str) -> None:
    """Delete files this module staged in a Cosmic Clarity folder earlier.

    A run that was killed between staging a file and cleaning up leaves it
    behind. The caller holds the exclusive lock, so no run of this module is
    using such a file. Files with any other name are never touched.
    """
    for name in os.listdir(folder):
        if _OWN_STAGED_FILE.match(name):
            os.remove(os.path.join(folder, name))


def denoise_with_cosmic_clarity(
    cosmic_clarity_executable: str, input_path: str, output_path: str, strength: float
) -> bool:
    """Remove noise from an image with Cosmic Clarity's AI model.

    The program takes no folder options. It reads every file in the ``input``
    folder next to it and writes the results to the ``output`` folder next to
    it. This function therefore takes an exclusive lock first, so two runs
    never share the folders. It refuses to run if ``input`` already holds a
    file it did not put there, because the program would process that file
    too, and it never deletes files that are not its own.

    Parameters
    ----------
    cosmic_clarity_executable : `str`
        Path of ``SetiAstroCosmicClarity_denoise``.
    input_path : `str`
        The FITS image to denoise. It is not changed.
    output_path : `str`
        Where to put the denoised FITS image.
    strength : `float`
        How strongly to remove noise, from 0 to 1.

    Returns
    -------
    succeeded : `bool`
        `True` if the program exited without an error code and the denoised
        image is at `output_path`.
    """
    from datastore.process_locks import acquire_resource_slot

    program_folder = os.path.dirname(os.path.abspath(cosmic_clarity_executable))
    input_folder = os.path.join(program_folder, "input")
    output_folder = os.path.join(program_folder, "output")
    os.makedirs(input_folder, exist_ok=True)
    os.makedirs(output_folder, exist_ok=True)
    file_stem = f"stack_preview_{os.getpid()}"
    staged_input = os.path.join(input_folder, f"{file_stem}.fits")
    produced_output = os.path.join(output_folder, f"{file_stem}_denoised.fits")
    with acquire_resource_slot(get_configuration(), "cosmic_clarity", 1):
        _remove_own_staged_files(input_folder)
        _remove_own_staged_files(output_folder)
        if os.listdir(input_folder):
            logger.warning(
                "Cosmic Clarity's input folder '%s' already holds files, so it was not used.", input_folder
            )
            return False
        try:
            shutil.copyfile(input_path, staged_input)
            completed = subprocess.run(
                [
                    cosmic_clarity_executable,
                    "--denoise_mode=luminance",
                    f"--denoise_strength={strength}",
                ],
                capture_output=True,
                text=True,
                timeout=COSMIC_CLARITY_TIMEOUT_SECONDS,
                check=False,
            )
            if completed.returncode != 0 or not os.path.isfile(produced_output):
                return False
            shutil.move(produced_output, output_path)
        finally:
            for leftover in (staged_input, produced_output):
                if os.path.exists(leftover):
                    os.remove(leftover)
    return True


def _is_usable_image(path: str) -> bool:
    """Tell whether a FITS image is mostly valid numbers with some contrast.

    Parameters
    ----------
    path : `str`
        Path of the FITS image.

    Returns
    -------
    usable : `bool`
        `True` if at least `_MINIMUM_FINITE_FRACTION` of the pixels are
        finite and the finite pixels are not all one value. A file that
        cannot be read is not usable.
    """
    try:
        data = AstrometricsImage(path).data
    except OSError, ValueError, KeyError:
        return False
    finite = np.isfinite(data)
    if finite.mean() < _MINIMUM_FINITE_FRACTION:
        return False
    return bool(np.nanmax(data) > np.nanmin(data))


def _next_source(
    step_name: str,
    current_name: str,
    new_name: str,
    scratch: str,
    run_step: Callable[[str, str], bool],
    check_result: bool = False,
) -> str:
    """Run one optional cleanup step and choose the file to carry forward.

    Parameters
    ----------
    step_name : `str`
        The step's name, for the warning if it fails.
    current_name : `str`
        The file in the scratch folder that the step starts from.
    new_name : `str`
        The file the step should create in the scratch folder.
    scratch : `str`
        The scratch folder.
    run_step : `Callable` [[`str`, `str`], `bool`]
        Runs the step on a full input path and a full output path, and
        returns whether the program succeeded.
    check_result : `bool`, optional
        If `True`, the step's file is used only if `_is_usable_image` accepts
        it, so a program that exits without an error but writes invalid
        pixels is treated as a failure.

    Returns
    -------
    file_name : `str`
        `new_name` if the step worked, otherwise `current_name`.
    """
    try:
        worked = run_step(os.path.join(scratch, current_name), os.path.join(scratch, new_name))
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("%s could not run (%s); the preview skips that step.", step_name, error)
        return current_name
    result_path = os.path.join(scratch, new_name)
    if not worked or not os.path.isfile(result_path):
        logger.warning("%s did not finish; the preview skips that step.", step_name)
        return current_name
    if check_result and not _is_usable_image(result_path):
        logger.warning("%s wrote an image with invalid pixels; the preview skips that step.", step_name)
        return current_name
    logger.info("%s finished; the preview uses its result.", step_name)
    return new_name


def _picture_script(scratch: str, siril_executable: str) -> list[str] | None:
    """Run the cleanup steps on the scratch copy and choose the final script.

    The steps run in this order: GraXpert on the linear copy, then Siril's
    stretch, then Cosmic Clarity and the star toning on the stretched copy.
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

    Returns
    -------
    commands : `list` [`str`] or `None`
        The Siril script that saves the picture, or `None` if the stretch
        step needed before Cosmic Clarity did not work.
    """
    configuration = get_configuration()
    name = "stack.fits"
    bright_object = choose_bright_object_stretch_for_file(os.path.join(scratch, name))
    if bright_object is not None:
        logger.info(
            "Bright object: the normal stretch would turn %.1f%% of the picture white; "
            "using a stretch scaled to the object, without GraXpert or the denoise.",
            100 * bright_object.white_fraction,
        )
        return build_bright_object_script(name, "preview", bright_object)
    steps: list[str] = []
    graxpert_executable = configuration.get_graxpert_executable()
    if graxpert_executable:

        def flatten(input_path: str, output_path: str) -> bool:
            """Run GraXpert, which adds its own ``.fits`` to the output name.

            Returns
            -------
            succeeded : `bool`
                Whether GraXpert succeeded.
            """
            return flatten_background(graxpert_executable, input_path, os.path.splitext(output_path)[0])

        flattened_name = _next_source("GraXpert", name, "flat.fits", scratch, flatten, check_result=True)
        steps.append("GraXpert " + ("done" if flattened_name != name else "skipped after a failure"))
        name = flattened_name
    else:
        steps.append("GraXpert not configured")
    choice = choose_sky_level_for_file(os.path.join(scratch, name))
    logger.info("Preview sky level %.2f: %s.", choice.sky_level, choice.reason)
    denoise_executable = configuration.get_cosmic_clarity_denoise_executable()
    tone_stars = configuration.get_preview_star_tone_enabled()
    if not denoise_executable and not tone_stars:
        steps.extend([
            "Siril stretch done in the final script",
            "Cosmic Clarity not configured",
            "Star toning off",
        ])
        logger.info("Preview steps: %s.", "; ".join(steps))
        return build_preview_script(name, "preview", choice.sky_level)
    stretched = run_preview_script(
        scratch, build_stretch_script(name, "stretched", choice.sky_level), siril_executable
    )
    if not stretched or not os.path.isfile(os.path.join(scratch, "stretched.fits")):
        steps.append("Siril stretch failed")
        logger.info("Preview steps: %s.", "; ".join(steps))
        return None
    name = "stretched.fits"
    steps.append("Siril stretch done")
    if denoise_executable:
        strength = configuration.get_cosmic_clarity_denoise_strength()

        def denoise(input_path: str, output_path: str) -> bool:
            """Run Cosmic Clarity's denoise program.

            Returns
            -------
            succeeded : `bool`
                Whether Cosmic Clarity succeeded.
            """
            return denoise_with_cosmic_clarity(denoise_executable, input_path, output_path, strength)

        denoised_name = _next_source("Cosmic Clarity", name, "denoised.fits", scratch, denoise)
        steps.append("Cosmic Clarity " + ("done" if denoised_name != name else "skipped after a failure"))
        name = denoised_name
    else:
        steps.append("Cosmic Clarity not configured")
    if tone_stars:
        toned_name = _next_source("Star toning", name, "toned.fits", scratch, tone_stars_in_file)
        steps.append("Star toning " + ("done" if toned_name != name else "skipped after a failure"))
        name = toned_name
    else:
        steps.append("Star toning off")
    logger.info("Preview steps: %s.", "; ".join(steps))
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
        Path of the stack.
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


def write_stack_preview(stacked_path: str) -> str | None:
    """Save a cleaned-up, stretched JPEG and FITS of a stack beside it.

    The FITS (see `processed_fits_path_for`) holds the same picture as the
    JPEG as 32-bit numbers between 0 and 1. Any earlier pictures of the same
    stack are removed first, so a failed run never leaves a picture of an
    older stack behind.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stacked FITS file.

    Returns
    -------
    preview_path : `str` or `None`
        Path of the new JPEG, or `None` if the stack file is missing, Siril
        is not configured, or Siril failed. The reason is logged as a warning.
    """
    preview_path = preview_path_for(stacked_path)
    processed_path = processed_fits_path_for(stacked_path)
    for old_picture in (preview_path, processed_path):
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
        shutil.copyfile(stacked_path, os.path.join(scratch, "stack.fits"))
        commands = _picture_script(scratch, siril_executable)
        succeeded = commands is not None and run_preview_script(scratch, commands, siril_executable)
        picture = os.path.join(scratch, "preview.jpg")
        if not succeeded or not os.path.isfile(picture):
            logger.warning("No preview made for '%s': Siril did not save the picture.", stacked_path)
            return None
        shutil.move(picture, preview_path)
        # The FITS copy is a bonus for the viewer. Without it the JPEG alone
        # is still a good preview, so a missing file is not a failure.
        stretched_fits = os.path.join(scratch, f"{_PROCESSED_STEM}.fits")
        if os.path.isfile(stretched_fits):
            shutil.move(stretched_fits, processed_path)
            _copy_observation_header(stacked_path, processed_path)
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
