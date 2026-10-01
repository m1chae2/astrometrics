"""Writes a viewable picture next to a finished stack.

A stacked FITS file holds linear data: pixel values proportional to the
light collected. Most of the sky is far too dark in that form to see
anything. Up to three steps turn it into a picture.

1. GraXpert, if it is configured, removes the sky's brightness gradient.
   Its AI model learns what the smooth background looks like and subtracts
   it, so uneven sky glow or vignetting does not wash out the faint parts.
2. Cosmic Clarity, if it is configured, removes noise with an AI model. It
   runs before the stretch because the stretch makes noise much easier to
   see.
3. Siril's Autostretch command brightens the faint parts the way its own
   display does. It sets the black point from the image's own background
   noise, then bends the brightness curve so the typical background lands at
   a fixed grey level.

All steps run on a scratch copy of the stack and the result is saved as a
JPEG beside the stack. The JPEG is only a picture for people to look at. The
stack itself is never changed, so photometry, spectroscopy and plate solving
keep reading the linear data. A failed or skipped step never fails the stack:
if GraXpert or Cosmic Clarity fails, the picture is made from the result of
the step before it, and if Siril fails, there is no picture.
"""

import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable

from astrometricslib.drivers.siril_interface import siril_process_lock
from astrometricslib.pipelines.shared.stack_preview_path import PREVIEW_JPEG_QUALITY, preview_path_for
from astrometricslib.utilities.config_loader import get_configuration

logger = logging.getLogger(__name__)

# The longest a single preview may run, in seconds. The M 13 script took 0.7
# seconds once Siril had started, so two minutes leaves room for Siril's
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

# The oldest Siril that has the `autostretch` and `savejpg` commands used here.
_MINIMUM_SIRIL_VERSION = "1.2.0"


def build_preview_script(stacked_file_name: str, preview_stem: str) -> list[str]:
    """Write the Siril commands that stretch one stack and save a JPEG.

    Names are put in quotes so a file name with a space still works.

    Parameters
    ----------
    stacked_file_name : `str`
        File name of the stack, relative to the folder Siril is started in.
    preview_stem : `str`
        File name for the picture, relative to that folder and without the
        ``.jpg`` extension (Siril adds it).

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return [
        f"requires {_MINIMUM_SIRIL_VERSION}",
        f'load "{stacked_file_name}"',
        "autostretch",
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


def _next_source(
    step_name: str, current_name: str, new_name: str, scratch: str, run_step: Callable[[str, str], bool]
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
    if not worked or not os.path.isfile(os.path.join(scratch, new_name)):
        logger.warning("%s did not finish; the preview skips that step.", step_name)
        return current_name
    return new_name


def _prepared_copy_name(scratch: str) -> str:
    """Run the optional cleanup steps on the scratch copy of a stack.

    The steps run in this order: GraXpert, then Cosmic Clarity. Each runs only
    if it is configured, and each starts from the result of the one before it.

    Parameters
    ----------
    scratch : `str`
        The scratch folder, holding a copy of the stack named ``stack.fits``.

    Returns
    -------
    file_name : `str`
        The file Siril should stretch: ``stack.fits`` if no step ran,
        ``flat.fits`` after GraXpert, ``denoised.fits`` after Cosmic Clarity.
    """
    configuration = get_configuration()
    name = "stack.fits"
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

        name = _next_source("GraXpert", name, "flat.fits", scratch, flatten)
    denoise_executable = configuration.get_cosmic_clarity_denoise_executable()
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

        name = _next_source("Cosmic Clarity", name, "denoised.fits", scratch, denoise)
    return name


def write_stack_preview(stacked_path: str) -> str | None:
    """Save a cleaned-up, stretched JPEG of a stack beside it.

    Any earlier preview of the same stack is removed first, so a failed run
    never leaves a picture of an older stack behind.

    Parameters
    ----------
    stacked_path : `str`
        Path of the stacked FITS file.

    Returns
    -------
    preview_path : `str` or `None`
        Path of the new picture, or `None` if the stack file is missing, Siril
        is not configured, or Siril failed. The reason is logged as a warning.
    """
    preview_path = preview_path_for(stacked_path)
    if os.path.exists(preview_path):
        os.remove(preview_path)
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
        scratch = tempfile.mkdtemp(prefix="stack_preview_", dir=_SCRATCH_ROOT)
        shutil.copyfile(stacked_path, os.path.join(scratch, "stack.fits"))
        source_name = _prepared_copy_name(scratch)
        commands = build_preview_script(source_name, "preview")
        succeeded = run_preview_script(scratch, commands, siril_executable)
        picture = os.path.join(scratch, "preview.jpg")
        if not succeeded or not os.path.isfile(picture):
            logger.warning("No preview made for '%s': Siril did not save the picture.", stacked_path)
            return None
        shutil.move(picture, preview_path)
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("No preview made for '%s': %s.", stacked_path, error)
        return None
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    return preview_path
