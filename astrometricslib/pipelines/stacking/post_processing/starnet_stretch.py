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

StarNet adds about 35 to 55 seconds to a preview on this machine's GPU
(measured on M 81, M 51 and NGC 6992), and a trained model can leave
artifacts or fail on a dense field such as the core of M 13, so this is an
option and not the default. A failed
run changes nothing: the preview is made the normal way instead.

All of this works on the preview's scratch copy. The stack is never changed.
"""

import glob
import logging
import os
import shutil
import subprocess

import numpy as np

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_abandonment import raise_if_abandoned
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.post_processing.preview_settings import PreviewSettings
from astrometricslib.pipelines.stacking.post_processing.preview_siril import (
    build_stretch_script,
    run_preview_script,
    sky_background_for_file,
)
from astrometricslib.pipelines.stacking.post_processing.preview_steps import SkippedPreviewStep
from astrometricslib.pipelines.stacking.post_processing.sky_level import choose_sky_level_for_file

logger = logging.getLogger(__name__)

# How strongly the stars layer is scaled before it is blended, from 0 (no
# stars) to 1 (as stretched). Chosen by eye on M 81, M 51 and NGC 6992
# stacks, where 0.6 left the stars looking dim and 1.0 crowded the nebula.
# Not validated beyond those three.
DEFAULT_STAR_STRENGTH = TunedParameter(
    name="stacking.default_star_strength",
    default=0.8,
    unit="fraction",
    description="How strongly the stars layer is scaled before it is blended.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-31",
)

# The arcsinh strength of the stars' own stretch. Larger values lift fainter
# stars: on M 51, 300 showed the bright stars and few others, 3000 showed
# every star but made them soft, and 1000 sat between. Chosen by eye on the
# same three stacks.
DEFAULT_STAR_STRETCH = TunedParameter(
    name="stacking.default_star_stretch",
    default=1000.0,
    unit="none",
    description="Arcsinh strength of the stars' own stretch.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-31",
)

# The stars' brightness is divided by this percentile of the brightest value
# at each pixel before the curve, so a few saturated cores set the scale and
# not the single brightest pixel. The 99.99th percentile follows the cores of
# the brightest stars on every stack tried.
_STAR_SCALE_PERCENTILE = TunedParameter(
    name="stacking.star_scale_percentile",
    default=99.99,
    unit="percentile",
    description="Percentile of the brightest value at each pixel by which star brightness is divided.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D56",
)

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
    scale = float(np.percentile(brightest, resolve(_STAR_SCALE_PERCENTILE).value))
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


def stretch_with_starnet(
    scratch: str,
    name: str,
    siril_executable: str,
    remove_green: bool,
    linked: bool,
    settings: PreviewSettings,
    skipped_steps: list[SkippedPreviewStep] | None = None,
) -> str | None:
    """Stretch the stars and the rest of the image on their own with StarNet.

    StarNet splits the linear image into a starless image and a stars-only
    image. The starless image gets the usual stretch, the stars get an
    arcsinh stretch of their own, and the two are blended (see
    `starnet_stretch`). The result is saved as ``stretched.fits``.

    Parameters
    ----------
    scratch : `str`
        The scratch folder.
    name : `str`
        The linear image in the scratch folder to split.
    siril_executable : `str`
        The command that starts Siril.
    remove_green : `bool`
        Whether the stretch of the starless image removes excess green first.
    linked : `bool`
        Whether the colour channels share one stretch.
    settings : `PreviewSettings`
        Supplies the strength and the stretch of the stars layer.
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        If given, a failure adds a `SkippedPreviewStep` to it.

    Returns
    -------
    file_name : `str` or `None`
        ``"stretched.fits"`` if StarNet and the blend worked, otherwise
        `None`. The reason is logged.
    """
    strength = (
        resolve(DEFAULT_STAR_STRENGTH).value if settings.star_strength is None else settings.star_strength
    )
    stretch = resolve(DEFAULT_STAR_STRETCH).value if settings.star_stretch is None else settings.star_stretch

    def fall_back(reason: str) -> None:
        """Log that StarNet failed and remember it for the caller."""
        logger.warning("%s; the preview uses the normal stretch.", reason)
        if skipped_steps is not None:
            skipped_steps.append(SkippedPreviewStep("StarNet", reason))

    raise_if_abandoned()
    try:
        ran = run_preview_script(scratch, build_starnet_script(name), siril_executable)
    except (OSError, subprocess.SubprocessError) as error:
        fall_back(f"StarNet could not run ({error})")
        return None
    if not ran or not collect_starnet_outputs(scratch):
        fall_back("StarNet did not give a starless and a stars image")
        return None
    level = choose_sky_level_for_file(os.path.join(scratch, STARLESS_NAME)).sky_level
    logger.info("StarNet: the starless image is stretched to sky level %.2f.", level)
    try:
        stretched = run_preview_script(
            scratch,
            build_stretch_script(
                STARLESS_NAME,
                "starless_stretched",
                level,
                remove_green,
                linked,
                sky_background_for_file(os.path.join(scratch, STARLESS_NAME), remove_green, linked),
            ),
            siril_executable,
        )
    except (OSError, subprocess.SubprocessError) as error:
        fall_back(f"The starless stretch could not run ({error})")
        return None
    if not stretched or not blend_files(
        os.path.join(scratch, "starless_stretched.fits"),
        os.path.join(scratch, STARS_NAME),
        os.path.join(scratch, "stretched.fits"),
        stretch,
        strength,
    ):
        fall_back("StarNet's stretched pieces could not be blended")
        return None
    return "stretched.fits"
