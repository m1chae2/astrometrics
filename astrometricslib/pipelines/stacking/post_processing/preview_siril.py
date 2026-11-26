"""Builds and runs the Siril scripts that stretch and save the stack preview.

The stack preview (see `stack_preview`) uses Siril for the stretch, the
green removal and the saving of the JPEG and the stretched FITS. This module
writes those scripts (`build_preview_script`, `build_stretch_script`,
`build_bright_object_script`, `build_picture_script`) and runs one
(`run_preview_script`) while holding the shared Siril slot. It also keeps the
text Siril printed in the last run (`last_siril_output`), so a step can read a
warning that Siril logs while still exiting with success.
"""

import contextvars
import logging
import os
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib._internal.drivers.siril_interface import siril_process_lock
from astrometricslib.foundation.errors import NotFoundError, StorageError
from astrometricslib.foundation.run_abandonment import run_registered
from astrometricslib.foundation.system import split_program_command
from astrometricslib.pipelines.shared.image_scaling import (
    AUTOSTRETCH_SHADOWS_CLIP_SIGMA,
    midtones_balance_for,
)
from astrometricslib.pipelines.shared.stack_preview_path import (
    PREVIEW_JPEG_QUALITY,
)
from astrometricslib.pipelines.stacking.post_processing.bright_object import (
    BrightObjectStretch,
)
from astrometricslib.pipelines.stacking.post_processing.stretch_sky import measure_stretch_sky

logger = logging.getLogger(__name__)


# The longest a single Siril script may run, in seconds. The M 13 script took
# 0.7 seconds once Siril had started, so two minutes leaves room for Siril's
# start-up and for a much larger stack, and still stops a hung process. The
# wait for a free Siril slot is not counted: the lock is taken before the
# clock starts.
PREVIEW_TIMEOUT_SECONDS = 120


# The text Siril printed in the last script this thread ran, kept so a step can
# read a warning that Siril logs and still exits with success (see
# `last_siril_output`).
_last_siril_output: contextvars.ContextVar[str] = contextvars.ContextVar("last_siril_output", default="")


# The file name, without extension, under which each final script also saves
# the stretched picture as FITS. `write_stack_preview` moves it next to the
# stack.
PROCESSED_STEM = "processed"


# Siril's `savejpg` writes the picture upside down compared with the FITS file
# it was made from (checked on NGC 6888, M 57, M 101 and NGC 7000: the FITS
# array, which matches the camera's own view, correlates 1.0 with the JPEG only
# after a vertical flip). `mirrorx` flips the loaded image vertically just
# before the JPEG is saved, after the FITS copy, so both pictures show the
# same way up.
_JPEG_ORIENTATION_COMMAND = "mirrorx"


# The oldest Siril that has the `autostretch` and `savejpg` commands used here.
_MINIMUM_SIRIL_VERSION = "1.2.0"


def _autostretch_command(
    sky_level: float, linked: bool = False, sky_background: tuple[float, float] | None = None
) -> str:
    """Write the Siril command that stretches the loaded image.

    Siril's own ``autostretch`` takes the median and spread of every pixel as
    the sky. When a large galaxy covers much of the frame, those are galaxy
    light and the sky lands darker than asked. When the empty sky has been
    measured (`sky_background`), this writes the same stretch as an ``mtf``
    command (black point and midtones balance) set from that measurement.

    Parameters
    ----------
    sky_level : `float`
        The brightness, between 0 and 1, the sky should land on.
    linked : `bool`, optional
        Whether all colour channels share one stretch. By default Siril
        stretches each channel on its own, which sets every channel's sky and
        brightness scale to the same values. That undoes any colour balance
        made on the linear data, so a picture whose colours were calibrated
        needs the shared stretch. Default `False`.
    sky_background : `tuple` [`float`, `float`], optional
        The empty sky's level and noise in the image's own units, from
        `measure_sky_background`. Only for a single-channel image. If `None`
        (the default), Siril's ``autostretch`` measures the sky itself.

    Returns
    -------
    command : `str`
        Siril's ``autostretch`` command with the black-point clip and the
        sky level written out, or the ``mtf`` command that gives the same
        stretch from the measured sky.
    """
    if sky_background is not None:
        level, noise = sky_background
        black_point = max(level + AUTOSTRETCH_SHADOWS_CLIP_SIGMA * noise, 0.0)
        normalized_sky = (level - black_point) / (1.0 - black_point)
        if 0.0 < normalized_sky < 1.0 and 0.0 < sky_level < 1.0:
            midtones = midtones_balance_for(normalized_sky, sky_level)
            return f"mtf {black_point:.9f} {midtones:.9f} 1"
    flag = "-linked " if linked else ""
    return f"autostretch {flag}{AUTOSTRETCH_SHADOWS_CLIP_SIGMA} {sky_level:.3f}"


def sky_background_for_file(
    path: str,
    data_edited_first: bool = False,
    linked: bool = False,
    record: dict[str, Any] | None = None,
) -> tuple[float, float] | None:
    """Measure the empty sky of a stack's linear image for the stretch.

    The measurement is `stretch_sky.measure_stretch_sky`: the library's sky
    level and noise, with a guard that lowers the black point when the
    stretch would clip more than a small share of the picture to black (a
    nebula that fills the frame lifts the measured level above the real sky).

    Parameters
    ----------
    path : `str`
        The FITS image the stretch will be applied to.
    data_edited_first : `bool`, optional
        Whether a Siril step that changes the pixel values (Remove Green
        Noise) runs before the stretch, so a measurement made here would not
        match what the stretch sees. Default `False`.
    linked : `bool`, optional
        Whether a colour image gets one stretch shared by its channels. Only
        then can one sky measurement (of the channels' mean) stand for all of
        them. Default `False`.
    record : `dict`, optional
        If given, it is filled with the black point and the clipped share
        (see `StretchSky.record`), for the preview settings record.

    Returns
    -------
    sky_background : `tuple` [`float`, `float`] or `None`
        The sky level and noise, or `None` when Siril should measure the sky
        itself: a colour image whose channels are stretched one by one, a
        step that edits the data first, a file that cannot be read, or a sky
        that cannot be measured.
    """
    if data_edited_first:
        return None
    try:
        data = np.asarray(AstrometricsImage(path).data)
    except (OSError, ValueError, KeyError, NotFoundError, StorageError) as error:
        logger.debug("Could not read '%s' to measure its sky: %s", path, error)
        return None
    if np.squeeze(data).ndim == 3 and not linked:
        return None
    sky = measure_stretch_sky(data)
    if sky is None:
        return None
    if record is not None:
        record.update(sky.record())
    return sky.as_background()


# Siril's Remove Green Noise command. With no options it uses the "average
# neutral" method, which limits each pixel's green to the average of its red
# and blue, and it keeps the pixel's lightness.
_REMOVE_GREEN_COMMAND = "rmgreen"


def build_preview_script(
    stacked_file_name: str,
    preview_stem: str,
    sky_level: float,
    remove_green: bool = False,
    linked: bool = False,
    sky_background: tuple[float, float] | None = None,
) -> list[str]:
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
    remove_green : `bool`, optional
        Whether to remove excess green before the stretch. Only for a
        colour image. Default `False`.
    linked : `bool`, optional
        Whether the colour channels share one stretch (see
        `_autostretch_command`). Default `False`.

    sky_background : `tuple` [`float`, `float`], optional
        The empty sky's level and noise (see `_autostretch_command`).
        Default `None`.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return [
        f"requires {_MINIMUM_SIRIL_VERSION}",
        f'load "{stacked_file_name}"',
        *([_REMOVE_GREEN_COMMAND] if remove_green else []),
        _autostretch_command(sky_level, linked, sky_background),
        f'save "{PROCESSED_STEM}"',
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
        f'save "{PROCESSED_STEM}"',
        _JPEG_ORIENTATION_COMMAND,
        f'savejpg "{preview_stem}" {PREVIEW_JPEG_QUALITY}',
        "close",
    ]
    return commands


def build_stretch_script(
    source_file_name: str,
    stretched_stem: str,
    sky_level: float,
    remove_green: bool = False,
    linked: bool = False,
    sky_background: tuple[float, float] | None = None,
) -> list[str]:
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
    remove_green : `bool`, optional
        Whether to remove excess green before the stretch. Only for a
        colour image. Default `False`.
    linked : `bool`, optional
        Whether the colour channels share one stretch (see
        `_autostretch_command`). Default `False`.

    sky_background : `tuple` [`float`, `float`], optional
        The empty sky's level and noise (see `_autostretch_command`).
        Default `None`.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return [
        f"requires {_MINIMUM_SIRIL_VERSION}",
        f'load "{source_file_name}"',
        *([_REMOVE_GREEN_COMMAND] if remove_green else []),
        _autostretch_command(sky_level, linked, sky_background),
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
        f'save "{PROCESSED_STEM}"',
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
    parts = split_program_command(siril_executable)
    if "flatpak" in parts[0] and "run" in parts:
        parts.insert(parts.index("run") + 1, "--filesystem=host")
    try:
        with siril_process_lock():
            # Stoppable: giving up on the stack stops this Siril too.
            completed = run_registered([*parts, "-d", directory, "-s", script_path], PREVIEW_TIMEOUT_SECONDS)
    finally:
        os.remove(script_path)
    _last_siril_output.set(getattr(completed, "stdout", None) or "")
    return completed.returncode == 0


def last_siril_output() -> str:
    """Give the text Siril printed in the last script this thread ran.

    Returns
    -------
    output : `str`
        The standard output of the last `run_preview_script` call in this
        thread, or an empty string if there was none.
    """
    return _last_siril_output.get()
