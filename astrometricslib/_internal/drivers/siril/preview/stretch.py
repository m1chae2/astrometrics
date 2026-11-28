"""Purpose: The Siril scripts that stretch the stack preview and save it.

Description: The stack preview (the stacking pipeline's ``stack_preview``)
uses Siril for the stretch, the green removal and the saving of the JPEG and
the stretched FITS. This module writes those scripts:

* `build_preview_script`: stretch one stack and save it as FITS and JPEG.
* `build_stretch_script`: stretch and save as FITS only (before Cosmic
  Clarity denoises it).
* `build_picture_script`: save an already stretched image as FITS and JPEG.
* `build_bright_object_script`: clip and stretch a bright object (the Moon,
  a planet) and save it.

The stretch itself is a `SkyStretch`: either Siril's ``autostretch``, or the
same curve written as an ``mtf`` command from a sky the pipeline measured. The
pipeline chooses the numbers; this module only writes the commands. A script
is run with `core.script_run.run_siril_script`.
"""

from dataclasses import dataclass

from astrometricslib.utilities.stack_preview_path import PREVIEW_JPEG_QUALITY

# The file name, without extension, under which each final script also saves
# the stretched picture as FITS. The preview step moves it next to the stack.
PROCESSED_STEM = "processed"


# Siril's `savejpg` writes the picture upside down compared with the FITS file
# it was made from (checked on NGC 6888, M 57, M 101 and NGC 7000: the FITS
# array, which matches the camera's own view, correlates 1.0 with the JPEG only
# after a vertical flip). `mirrorx` flips the loaded image vertically just
# before the JPEG is saved, after the FITS copy, so both pictures show the
# same way up.
_JPEG_ORIENTATION_COMMAND = "mirrorx"


# The oldest Siril that has the `autostretch` and `savejpg` commands used here.
MINIMUM_SIRIL_VERSION = "1.2.0"


# Siril's Remove Green Noise command. With no options it uses the "average
# neutral" method, which limits each pixel's green to the average of its red
# and blue, and it keeps the pixel's lightness.
_REMOVE_GREEN_COMMAND = "rmgreen"


@dataclass(frozen=True)
class SkyStretch:
    """The stretch that sets a preview's sky to a chosen brightness.

    Attributes
    ----------
    sky_level : `float`
        The brightness, between 0 and 1, the sky should land on.
    shadows_clip_sigma : `float`
        How far below the median Siril's ``autostretch`` puts the black
        point, in noise widths (a negative number).
    linked : `bool`
        Whether all colour channels share one stretch. By default Siril
        stretches each channel on its own. Default `False`.
    black_point : `float` or `None`
        The black point measured from the empty sky, between 0 and 1. With
        `midtones`, the stretch is written as an ``mtf`` command instead of
        ``autostretch``. Default `None`.
    midtones : `float` or `None`
        The midtones balance that puts the measured sky on `sky_level`.
        Default `None`.
    """

    sky_level: float
    shadows_clip_sigma: float
    linked: bool = False
    black_point: float | None = None
    midtones: float | None = None

    def command(self) -> str:
        """Write the Siril command for this stretch.

        Returns
        -------
        command : `str`
            ``mtf <black> <midtones> 1`` when the sky was measured, otherwise
            ``autostretch [-linked] <clip> <sky level>``.
        """
        if self.black_point is not None and self.midtones is not None:
            return f"mtf {self.black_point:.9f} {self.midtones:.9f} 1"
        flag = "-linked " if self.linked else ""
        return f"autostretch {flag}{self.shadows_clip_sigma} {self.sky_level:.3f}"


def build_preview_script(
    stacked_file_name: str, preview_stem: str, stretch: SkyStretch, remove_green: bool = False
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
    stretch : `SkyStretch`
        The stretch to apply.
    remove_green : `bool`, optional
        Whether to remove excess green before the stretch. Only for a
        colour image. Default `False`.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return [
        f"requires {MINIMUM_SIRIL_VERSION}",
        f'load "{stacked_file_name}"',
        *([_REMOVE_GREEN_COMMAND] if remove_green else []),
        stretch.command(),
        f'save "{PROCESSED_STEM}"',
        _JPEG_ORIENTATION_COMMAND,
        f'savejpg "{preview_stem}" {PREVIEW_JPEG_QUALITY}',
        "close",
    ]


def build_bright_object_script(
    source_file_name: str,
    preview_stem: str,
    black_point: float,
    midtones: float,
    white_point: float,
    scale: float = 1.0,
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
    black_point : `float`
        The black point, between 0 and 1.
    midtones : `float`
        The midtones balance.
    white_point : `float`
        The white point, between 0 and 1.
    scale : `float`, optional
        The factor the image is multiplied by first. Only a factor below 1
        adds the command. Default 1.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    commands = [f"requires {MINIMUM_SIRIL_VERSION}", f'load "{source_file_name}"']
    if scale < 1.0:
        commands.append(f"fmul {scale:.9f}")
    commands += [
        f"mtf {black_point:.6f} {midtones:.6f} {white_point:.6f}",
        f'save "{PROCESSED_STEM}"',
        _JPEG_ORIENTATION_COMMAND,
        f'savejpg "{preview_stem}" {PREVIEW_JPEG_QUALITY}',
        "close",
    ]
    return commands


def build_stretch_script(
    source_file_name: str, stretched_stem: str, stretch: SkyStretch, remove_green: bool = False
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
    stretch : `SkyStretch`
        The stretch to apply.
    remove_green : `bool`, optional
        Whether to remove excess green before the stretch. Only for a
        colour image. Default `False`.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return [
        f"requires {MINIMUM_SIRIL_VERSION}",
        f'load "{source_file_name}"',
        *([_REMOVE_GREEN_COMMAND] if remove_green else []),
        stretch.command(),
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
        f"requires {MINIMUM_SIRIL_VERSION}",
        f'load "{source_file_name}"',
        f'save "{PROCESSED_STEM}"',
        _JPEG_ORIENTATION_COMMAND,
        f'savejpg "{preview_stem}" {PREVIEW_JPEG_QUALITY}',
        "close",
    ]
