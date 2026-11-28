"""Purpose: Split a picture into stars and the rest with StarNet in Siril.

Description: StarNet, a neural network trained on stars, splits a picture
into a starless image and a stars-only image. Siril runs it (its ``starnet``
command), so StarNet must be set up in Siril; the private settings file
carries the path Siril's own settings give it (``starnet_exe``).

`build_starnet_script` writes the script, and `collect_starnet_outputs` gives
Siril's two outputs fixed names (`STARLESS_NAME`, `STARS_NAME`).
"""

import glob
import os
import shutil

# The Siril version that has the ``starnet`` command.
MINIMUM_SIRIL_VERSION = "1.2.0"

# The names the two outputs get in the folder Siril ran in. Siril names its
# own outputs after the file it loaded.
STARLESS_NAME = "starless.fits"
STARS_NAME = "stars.fits"


def build_starnet_script(source_file_name: str, stretch: bool = True) -> list[str]:
    """Write the Siril commands that split an image into starless and stars.

    Siril saves the two parts beside the source as ``starless_<name>.fit``
    and ``starmask_<name>.fit`` (the stars alone, as the original minus the
    starless image).

    Parameters
    ----------
    source_file_name : `str`
        File name of the image, relative to the folder Siril is started in.
    stretch : `bool`, optional
        Whether the image is linear. Then ``-stretch`` makes StarNet see a
        stretched copy, as it expects, and returns the results to linear.
        Pass `False` for a picture that is already stretched. Default `True`.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    return [
        f"requires {MINIMUM_SIRIL_VERSION}",
        f'load "{source_file_name}"',
        "starnet -stretch" if stretch else "starnet",
        "close",
    ]


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
