"""Purpose: Siril's plate-solve command.

Description: Siril's ``platesolve`` command finds where an image points on the
sky. It needs a position to start from (the image header's pointing), the
focal length and the pixel size, and a star catalog: Siril's Gaia catalog on
the disk (``localgaia``) or the same catalog asked from a server (``gaia``).

Every plate solve the driver writes carries ``-noflip``. Without it, Siril
turns an image stored bottom-up the other way round after solving it, which
would change the pixels the app then measures.

`platesolve_command` is the one place this command is written. The preview's
colour calibration (`preview.color_calibration`) calls it today. The app's
own plate-solve driver, for image astrometry and mount centering, will add its
options here: a position hint, the focal length and pixel size, and ``-force``
to solve an image that already has a solution.
"""

# Siril's catalog names: Gaia from the disk, or Gaia from a server.
LOCAL_GAIA_CATALOG = "localgaia"
ONLINE_GAIA_CATALOG = "gaia"


def platesolve_command(catalog: str) -> str:
    """Write Siril's command that plate-solves the loaded image.

    Parameters
    ----------
    catalog : `str`
        The star catalog Siril matches the stars to: `LOCAL_GAIA_CATALOG` or
        `ONLINE_GAIA_CATALOG`.

    Returns
    -------
    command : `str`
        The ``platesolve`` command, always with ``-noflip``.
    """
    return f"platesolve -catalog={catalog} -noflip"
