"""Removes noise from the stretched stack preview with Cosmic Clarity.

Cosmic Clarity is an outside program with an AI denoise model. The preview
(see `stack_preview`) runs it after the stretch. `denoise_with_cosmic_clarity`
stages the picture in the program's input folder, runs it, and copies the
result back. It deletes only the files it staged itself.
"""

import logging
import os
import re
import shutil

from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.run_abandonment import run_registered

logger = logging.getLogger(__name__)


# The longest Cosmic Clarity may run, in seconds. A 1024 x 1024 crop took 7.7
# seconds on the GPU including the model load. The whole preview of the full
# M 13 stack (3008 x 3008 pixels), with GraXpert and Siril as well, took 54
# seconds, so Cosmic Clarity took under that. Five minutes leaves room for a
# larger stack or a slower card, and still stops a hung process.
COSMIC_CLARITY_TIMEOUT_SECONDS = 300


# The names this module gives the files it stages in Cosmic Clarity's folders.
# Only files with these names are ever deleted from those folders.
_OWN_STAGED_FILE = re.compile(r"^stack_preview_\d+(_denoised)?\.fits$")


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
    cosmic_clarity_executable: str,
    input_path: str,
    output_path: str,
    strength: float,
    color_strength: float | None = None,
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
        How strongly to remove noise from the brightness, from 0 to 1.
    color_strength : `float`, optional
        How strongly to remove noise from the colors, from 0 to 1. Give it
        for a color image: the program then runs in its ``full`` mode and
        smooths the color speckle as well as the brightness noise. Leave it
        out for a single-channel image, which has no color; the program then
        runs in its ``luminance`` mode.

    Returns
    -------
    succeeded : `bool`
        `True` if the program exited without an error code and the denoised
        image is at `output_path`.
    """
    from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

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
            mode_options = (
                ["--denoise_mode=luminance", f"--denoise_strength={strength}"]
                if color_strength is None
                else [
                    "--denoise_mode=full",
                    f"--denoise_strength={strength}",
                    f"--color_denoise_strength={color_strength}",
                ]
            )
            completed = run_registered(
                [cosmic_clarity_executable, *mode_options], COSMIC_CLARITY_TIMEOUT_SECONDS
            )
            if completed.returncode != 0 or not os.path.isfile(produced_output):
                return False
            shutil.move(produced_output, output_path)
        finally:
            for leftover in (staged_input, produced_output):
                if os.path.exists(leftover):
                    os.remove(leftover)
    return True
