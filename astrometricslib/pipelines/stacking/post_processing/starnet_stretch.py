"""Stretches the stars and the rest of a preview on their own, using StarNet.

The normal preview stretches the whole picture at once, so the stars and the
faint nebula or galaxy share one curve. A stretch strong enough to show the
faint light makes every star a hard white dot, and one gentle enough for the
stars leaves the faint light dark. The star toning (see `star_tone`) softens
this with a formula. This module takes the usual route of astro-photographers
instead:

1. StarNet, a neural network trained on stars, splits the linear picture into
   a starless image and a stars-only image. Siril runs it (its ``starnet``
   command), so StarNet must be set up in Siril.
2. The starless image is stretched the usual way, with the sky level that
   `sky_level` chooses for it.
3. The stars-only image is stretched on its own with an arcsinh curve, which
   lifts faint stars much more than bright ones. Its strength sets how many
   faint stars show and how soft they look.
4. The two are blended with a screen blend and the stars layer is scaled by a
   strength, so stars can be turned down without touching the nebula.

StarNet is slow (1 to 2.5 minutes for a 6000 by 4000 colour stack on a GPU)
and can leave artifacts, so this is an option and not the default. A failed
run changes nothing: the preview is made the normal way instead.

All of this works on the preview's scratch copy. The stack is never changed.
"""

import glob
import logging
import os
import shutil

import numpy as np

from astrometricslib.drivers.fits_access import read_data, read_header, write_image

logger = logging.getLogger(__name__)

# How strongly the stars layer is scaled before it is blended, from 0 (no
# stars) to 1 (as stretched). Chosen by eye on M 81, M 51 and NGC 6992
# stacks, where 0.6 left the stars looking dim and 1.0 crowded the nebula.
# Not validated beyond those three.
DEFAULT_STAR_STRENGTH = 0.8

# The arcsinh strength of the stars' own stretch. Larger values lift fainter
# stars: on M 51, 300 showed the bright stars and few others, 3000 showed
# every star but made them soft, and 1000 sat between. Chosen by eye on the
# same three stacks.
DEFAULT_STAR_STRETCH = 1000.0

# The stars' brightness is divided by this percentile of the brightest value
# at each pixel before the curve, so a few saturated cores set the scale and
# not the single brightest pixel. The 99.99th percentile follows the cores of
# the brightest stars on every stack tried.
_STAR_SCALE_PERCENTILE = 99.99

# The names the StarNet step and the blend give the files in the scratch
# folder. Siril names its two outputs after the file it loaded.
STARLESS_NAME = "starless.fits"
STARS_NAME = "stars.fits"


def build_starnet_script(source_file_name: str) -> list[str]:
    """Write the Siril commands that split an image into starless and stars.

    Siril saves the two parts beside the source as ``starless_<name>.fit``
    and ``starmask_<name>.fit`` (the stars alone, as the original minus the
    starless image). ``-stretch`` makes StarNet see a stretched copy, as it
    expects, and returns the results to linear.

    Parameters
    ----------
    source_file_name : `str`
        File name of the linear image, relative to the folder Siril is
        started in.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return ["requires 1.2.0", f'load "{source_file_name}"', "starnet -stretch", "close"]


def collect_starnet_outputs(folder: str) -> bool:
    """Give Siril's two StarNet outputs fixed names.

    Parameters
    ----------
    folder : `str`
        The folder Siril ran in.

    Returns
    -------
    found : `bool`
        `True` if both outputs exist and were copied to `STARLESS_NAME` and
        `STARS_NAME` in the folder.
    """
    starless = sorted(glob.glob(os.path.join(folder, "starless_*")))
    stars = sorted(glob.glob(os.path.join(folder, "starmask_*")))
    if not starless or not stars:
        return False
    shutil.copy(starless[0], os.path.join(folder, STARLESS_NAME))
    shutil.copy(stars[0], os.path.join(folder, STARS_NAME))
    return True


def stretch_stars(stars: np.ndarray, stretch: float) -> np.ndarray:
    """Stretch a linear stars-only image with an arcsinh curve.

    Parameters
    ----------
    stars : `numpy.ndarray`
        The stars-only image, linear, 2-D or channels first. Negative values
        are treated as zero.
    stretch : `float`
        The arcsinh strength, above 0. Larger values lift fainter stars more.

    Returns
    -------
    layer : `numpy.ndarray`
        The stretched stars, values between 0 and 1, the same shape. A star
        as bright as the scale (see `_STAR_SCALE_PERCENTILE`) or brighter
        comes out at 1.
    """
    clipped = np.clip(np.asarray(stars, dtype=np.float64), 0.0, None)
    brightest = clipped.max(axis=0) if clipped.ndim == 3 else clipped
    scale = float(np.percentile(brightest, _STAR_SCALE_PERCENTILE))
    if scale <= 0.0:
        return np.zeros_like(clipped)
    return np.arcsinh(stretch * np.clip(clipped / scale, 0.0, 1.0)) / np.arcsinh(stretch)


def blend_stars(
    stretched_starless: np.ndarray, stars: np.ndarray, stretch: float, strength: float
) -> np.ndarray:
    """Add independently stretched stars to a stretched starless image.

    The blend is a screen: ``1 - (1 - a) * (1 - b)``. It never darkens, never
    exceeds 1, and leaves the starless image as it was where there are no
    stars.

    Parameters
    ----------
    stretched_starless : `numpy.ndarray`
        The stretched starless image, values between 0 and 1.
    stars : `numpy.ndarray`
        The linear stars-only image, the same shape.
    stretch : `float`
        The arcsinh strength of the stars' stretch.
    strength : `float`
        How much of the stars layer is blended, from 0 to 1.

    Returns
    -------
    picture : `numpy.ndarray`
        The blended picture as 32-bit floats between 0 and 1.
    """
    base = np.clip(np.asarray(stretched_starless, dtype=np.float64), 0.0, 1.0)
    layer = strength * stretch_stars(stars, stretch)
    return (1.0 - (1.0 - base) * (1.0 - layer)).astype(np.float32)


def blend_files(
    starless_path: str, stars_path: str, output_path: str, stretch: float, strength: float
) -> bool:
    """Blend a stretched starless FITS and a linear stars FITS into a picture.

    Parameters
    ----------
    starless_path : `str`
        The stretched starless image, values between 0 and 1.
    stars_path : `str`
        The linear stars-only image.
    output_path : `str`
        Path to write. The starless image's header is kept.
    stretch : `float`
        The arcsinh strength of the stars' stretch.
    strength : `float`
        How much of the stars layer is blended, from 0 to 1.

    Returns
    -------
    succeeded : `bool`
        `True` if the blend was written. `False` if a file could not be read
        or the two images differ in shape. The reason is logged.
    """
    try:
        starless = read_data(starless_path)
        stars = read_data(stars_path)
        header = read_header(starless_path)
    except (OSError, ValueError, KeyError) as error:
        logger.warning("Could not read the StarNet images to blend them: %s.", error)
        return False
    if starless is None or stars is None or starless.shape != stars.shape:
        logger.warning("The StarNet starless and stars images do not match in shape.")
        return False
    write_image(output_path, blend_stars(starless, stars, stretch, strength), header)
    return True
