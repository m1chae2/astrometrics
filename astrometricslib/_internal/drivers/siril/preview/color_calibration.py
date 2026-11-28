"""Purpose: Siril's photometric colour calibration (PCC) of a preview.

Description: Siril's photometric colour calibration matches the stars of a
plate-solved image to the Gaia catalog and scales the colour channels, after
removing each channel's background. `build_pcc_script` writes the script; an
image with no plate solution is solved first, with the plate solve of
`astrometry.plate_solve`. `reports_imprecise_solution` reads Siril's output
for its warning that the calibration fitted the stars poorly.
"""

from astrometricslib._internal.drivers.siril.astrometry.plate_solve import (
    LOCAL_GAIA_CATALOG,
    ONLINE_GAIA_CATALOG,
    platesolve_command,
)
from astrometricslib._internal.drivers.siril.core.output_parsing import reports_imprecise_color_calibration

# Siril's photometric colour calibration needs the 1.4 release for the
# ``-catalog=gaia`` option used here.
PCC_MINIMUM_SIRIL_VERSION = "1.4.0"


def build_pcc_script(
    source_file_name: str, output_stem: str, solve: bool, local_catalog: str | None = None
) -> list[str]:
    """Write the Siril commands that calibrate an image's colours with PCC.

    Parameters
    ----------
    source_file_name : `str`
        File name of the linear colour image, relative to the folder Siril is
        started in.
    output_stem : `str`
        File name for the calibrated copy, relative to that folder and without
        the ``.fits`` extension (Siril adds it).
    solve : `bool`
        Whether Siril must first solve the field. It needs the pointing,
        focal length and pixel size in the file's header. Pass `False` for an
        image that already holds a plate solution.
    local_catalog : `str`, optional
        Path of Siril's Gaia catalog file on the disk. If given, Siril is
        told where it is and reads the stars from it. By default Siril asks
        a server over the network. The path is set in the script because the
        path saved in Siril's settings may not point to the file.

    Returns
    -------
    commands : `list` [`str`]
        The script, one command per entry.
    """
    catalog_name = LOCAL_GAIA_CATALOG if local_catalog else ONLINE_GAIA_CATALOG
    return [
        f"requires {PCC_MINIMUM_SIRIL_VERSION}",
        *([f"set core.catalogue_gaia_astro={local_catalog}"] if local_catalog else []),
        f'load "{source_file_name}"',
        *([platesolve_command(catalog_name)] if solve else []),
        f"pcc -catalog={catalog_name}",
        f'save "{output_stem}"',
        "close",
    ]


def reports_imprecise_solution(output: str) -> bool:
    """Say whether Siril warned that its colour calibration fitted poorly.

    Parameters
    ----------
    output : `str`
        What Siril printed while it ran the PCC script.

    Returns
    -------
    imprecise : `bool`
        `True` if Siril reported an imprecise solution.
    """
    return reports_imprecise_color_calibration(output)
