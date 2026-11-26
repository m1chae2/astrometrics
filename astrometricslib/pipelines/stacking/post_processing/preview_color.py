"""The color steps of the stack preview.

These steps of the preview (see `stack_preview`) work on a color stack:

1. `match_star_widths` sharpens a channel whose stars are wider than
   green's (see `star_width_match`).
2. `build_pcc_script` writes Siril's photometric color calibration, which
   needs a plate solution (`has_plate_solution`) and a Gaia catalog, local
   (`find_local_gaia_catalog`) or online (`online_catalog_reachable`).
3. `check_white_balance` checks Siril's result on stars like the Sun, and
   `calibrate_from_sun_like_stars` calibrates from those stars when Siril's
   calibration was not asked for or failed (see `sun_like_balance`).
4. `decide_remove_green` says whether Siril's green removal runs.
5. `level_stretched_sky` makes the stretched sky neutral (see
   `sky_levelling`).
"""

import logging
import os
from typing import Any

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.fits_access import read_header
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.run_abandonment import raise_if_abandoned
from astrometricslib.pipelines.stacking.post_processing.preview_steps import SkippedPreviewStep
from astrometricslib.pipelines.stacking.post_processing.sky_levelling import level_sky_in_file
from astrometricslib.pipelines.stacking.post_processing.star_width_match import (
    StarWidthMatch,
    match_star_widths_in_file,
)
from astrometricslib.pipelines.stacking.post_processing.sun_like_balance import (
    SunLikeBalance,
    balance_sun_like_stars_in_file,
)

logger = logging.getLogger(__name__)


def is_colour_image(path: str) -> bool:
    """Say whether a FITS file holds a three-channel colour image.

    Only the header is read.

    Parameters
    ----------
    path : `str`
        Path of the FITS file.

    Returns
    -------
    is_colour : `bool`
        `True` if the image has three colour planes. `False` for a
        single-channel image or a file that cannot be read.
    """
    try:
        header = read_header(path)
    except OSError, ValueError:
        return False
    return header.get("NAXIS", 0) == 3 and header.get("NAXIS3", 0) == 3


# Siril's photometric colour calibration needs the 1.4 release for the
# ``-catalog=gaia`` option used here.
_PCC_MINIMUM_SIRIL_VERSION = "1.4.0"


def has_plate_solution(path: str) -> bool:
    """Say whether a FITS file already carries a plate solution.

    Only the header is read.

    Parameters
    ----------
    path : `str`
        Path of the FITS file.

    Returns
    -------
    solved : `bool`
        `True` if the header holds world-coordinate keywords (a projection
        type and a reference sky position). `False` otherwise, or if the file
        cannot be read.
    """
    try:
        header = read_header(path)
    except OSError, ValueError:
        return False
    return "CTYPE1" in header and "CRVAL1" in header


# Siril's local Gaia catalog file, as its Catalog_Installer script names it.
_LOCAL_GAIA_CATALOG_NAME = "siril_cat_healpix8_astro.dat"


def find_local_gaia_catalog(search_folders: list[str] | None = None) -> str | None:
    """Find the Gaia star catalog that Siril can read from the disk.

    Siril's own Catalog_Installer script puts it in Siril's data folder. A
    Flatpak Siril keeps that folder under ``~/.var/app``, a normal install
    under ``~/.local/share``. Reading the catalog from the disk takes about
    10 seconds for a whole PCC run and needs no network.

    Parameters
    ----------
    search_folders : `list` [`str`], optional
        Folders to look in, in order. By default, the two places above.

    Returns
    -------
    path : `str` or `None`
        Path of the catalog file, or `None` if it is not installed.
    """
    if search_folders is None:
        search_folders = [
            os.path.expanduser("~/.local/share/siril"),
            os.path.expanduser("~/.var/app/org.siril.Siril/data/siril"),
        ]
    for folder in search_folders:
        candidate = os.path.join(folder, _LOCAL_GAIA_CATALOG_NAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def decide_remove_green(is_colour: bool, switch_on: bool, colours_calibrated: bool) -> tuple[bool, str]:
    """Decide whether Siril's Remove Green Noise runs on a stack.

    Colour calibration already makes the sky equal in all three channels.
    ``rmgreen`` then lowers green wherever green is above the mean of red and
    blue, which on a balanced sky is a shift of about 0.1 percent of the
    linear sky. The strong stretch multiplies that shift, and the sky comes
    out magenta: on the NGC 7023 and NGC 6992 stacks the stretched sky had
    green at 0.88 to 0.89 of the mean of red and blue with the step, and 1.01
    to 1.03 without it. So the step runs only when the colours were not
    calibrated (the setting is off, or the calibration failed). The setting
    ``preview_remove_green_noise_enabled`` stays the master switch: with it
    off the step never runs.

    Parameters
    ----------
    is_colour : `bool`
        Whether the stack has three colour channels.
    switch_on : `bool`
        The setting ``preview_remove_green_noise_enabled``.
    colours_calibrated : `bool`
        Whether colour calibration (Siril PCC or the star-based fallback)
        succeeded on this stack.

    Returns
    -------
    run_step : `bool`
        Whether Remove Green Noise runs.
    reason : `str`
        A short phrase that says why, for the preview's record.
    """
    if not is_colour:
        return False, "the stack has a single channel"
    if not switch_on:
        return False, "the setting is off"
    if colours_calibrated:
        return False, "the colours were calibrated, which already balances the sky"
    return True, "the colours were not calibrated"


def online_catalog_reachable() -> bool:
    """Say whether the server that Siril asks for Gaia stars answers.

    The check goes through the outside-services gateway, so a timeout pauses
    the server for every process: the next preview gets its answer at once,
    with no waiting. Without this check, a server that does not answer keeps
    Siril waiting for about two minutes, and the other stacks wait behind it
    for the one Siril slot.

    Returns
    -------
    reachable : `bool`
        `True` if a connection to the server was made in time. `False` if it
        failed, timed out, is paused after earlier trouble, or this launch
        sends nothing off the computer.
    """
    try:
        outside_services.check_siril_gaia_catalog_server()
    except (ExternalServiceError, OSError) as error:
        logger.info("The Gaia catalog server for Siril cannot be used now: %s", error)
        return False
    return True


def build_pcc_script(
    source_file_name: str, output_stem: str, solve: bool, local_catalog: str | None = None
) -> list[str]:
    """Write the Siril commands that calibrate an image's colours with PCC.

    Siril's photometric colour calibration matches the stars of a plate-solved
    image to the Gaia catalog and scales the channels, after removing each
    channel's background.

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
    catalog_name = "localgaia" if local_catalog else "gaia"
    return [
        f"requires {_PCC_MINIMUM_SIRIL_VERSION}",
        *([f"set core.catalogue_gaia_astro={local_catalog}"] if local_catalog else []),
        f'load "{source_file_name}"',
        *([f"platesolve -catalog={catalog_name}"] if solve else []),
        f"pcc -catalog={catalog_name}",
        f'save "{output_stem}"',
        "close",
    ]


def check_white_balance(
    scratch: str,
    name: str,
    steps: list[str],
    settings_used: dict[str, Any] | None,
    skipped_steps: list[SkippedPreviewStep] | None,
) -> str:
    """Check the white balance of a calibrated stack against sun-like stars.

    The check runs after Siril's colour calibration, on the linear data (see
    `sun_like_balance`). It never fails the preview: when it cannot run or a
    guard stops it, the calibrated image is kept, the reason goes into the
    record and a notice is stored.

    Parameters
    ----------
    scratch : `str`
        The scratch folder.
    name : `str`
        The calibrated image's file name in the scratch folder.
    steps : `list` [`str`]
        The sentences of the steps; the check's sentence is added.
    settings_used : `dict`, optional
        The record of the choices the run used; ``white_balance_check`` is
        added.
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        A check that changed nothing is added to it.

    Returns
    -------
    file_name : `str`
        The corrected image's file name when the correction was applied,
        otherwise `name`.
    """
    raise_if_abandoned()
    balanced_name = "balanced.fits"
    try:
        balance = balance_sun_like_stars_in_file(
            os.path.join(scratch, name), os.path.join(scratch, balanced_name)
        )
    except (OSError, ValueError) as error:
        balance = SunLikeBalance(applied=False, reason=f"the check could not run ({error})")
    applied = balance.applied and os.path.isfile(os.path.join(scratch, balanced_name))
    steps.append(balance.step_sentence())
    if settings_used is not None:
        settings_used["white_balance_check"] = balance.record()
    if applied:
        logger.info(
            "White balance check: %d sun-like stars read red %.3f and blue %.3f of green before, "
            "%.3f and %.3f after (gains red x%.3f, blue x%.3f).",
            balance.star_count,
            balance.red_to_green_before,
            balance.blue_to_green_before,
            balance.red_to_green_after,
            balance.blue_to_green_after,
            balance.red_gain,
            balance.blue_gain,
        )
        return balanced_name
    logger.warning("White balance check not applied: %s.", balance.reason)
    if skipped_steps is not None:
        skipped_steps.append(SkippedPreviewStep("White balance check", balance.reason))
    return name


def calibrate_from_sun_like_stars(
    scratch: str,
    name: str,
    settings_used: dict[str, Any] | None,
    skipped_steps: list[SkippedPreviewStep] | None,
) -> str:
    """Calibrate the colours from the Gaia stars like the Sun.

    This runs when Siril's calibration was not asked for or failed. The white
    point is the library's one (see `sun_like_balance`), with no limit on the
    size of the change. With no Gaia colours there is no white point: the
    colours are left uncalibrated. That is a status in the record and a
    notice, not an error.

    Parameters
    ----------
    scratch : `str`
        The scratch folder.
    name : `str`
        The image's file name in the scratch folder.
    settings_used : `dict`, optional
        The record of the choices the run used; ``colour_calibration_status``
        is added.
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        A calibration that changed nothing is added to it.

    Returns
    -------
    file_name : `str`
        The calibrated image's file name, or `name` when the colours are left
        uncalibrated.
    """
    raise_if_abandoned()
    calibrated_name = "colour.fits"
    try:
        balance = balance_sun_like_stars_in_file(
            os.path.join(scratch, name), os.path.join(scratch, calibrated_name), as_calibration=True
        )
    except (OSError, ValueError) as error:
        balance = SunLikeBalance(applied=False, reason=f"the calibration could not run ({error})")
    applied = balance.applied and os.path.isfile(os.path.join(scratch, calibrated_name))
    if settings_used is not None:
        settings_used["colour_calibration_status"] = {
            "status": "calibrated" if applied else "uncalibrated",
            **balance.record(),
        }
    if applied:
        logger.info("Colour calibration: %s.", balance.step_sentence())
        return calibrated_name
    logger.info("Colours left uncalibrated: %s.", balance.reason)
    if skipped_steps is not None:
        skipped_steps.append(
            SkippedPreviewStep(
                "Colour calibration from the stars like the Sun",
                f"the colours are left uncalibrated because {balance.reason}",
            )
        )
    return name


def match_star_widths(
    scratch: str,
    name: str,
    steps: list[str],
    settings_used: dict[str, Any] | None,
    skipped_steps: list[SkippedPreviewStep] | None,
) -> str:
    """Match the star widths of a colour stack's channels.

    See `star_width_match`.

    The step runs on the linear data, before the colour calibration. It never
    fails the preview: when it cannot run, the image is kept, the reason goes
    into the record and a notice is stored. When the widths already agree it
    changes nothing and only the record says so.

    Parameters
    ----------
    scratch : `str`
        The scratch folder.
    name : `str`
        The linear colour image's file name in the scratch folder.
    steps : `list` [`str`]
        The sentences of the steps; the step's sentence is added.
    settings_used : `dict`, optional
        The record of the choices the run used; ``star_width_match`` is
        added.
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        A step that could not run is added to it.

    Returns
    -------
    file_name : `str`
        The matched image's file name when a channel was sharpened,
        otherwise `name`.
    """
    raise_if_abandoned()
    matched_name = "widths.fits"
    try:
        result = match_star_widths_in_file(os.path.join(scratch, name), os.path.join(scratch, matched_name))
    except (OSError, ValueError, MemoryError) as error:
        result = StarWidthMatch(applied=False, reason=f"the step could not run ({error})")
        if skipped_steps is not None:
            skipped_steps.append(SkippedPreviewStep("Star width matching", result.reason))
    steps.append(result.step_sentence())
    if settings_used is not None:
        settings_used["star_width_match"] = result.record()
    if result.applied and os.path.isfile(os.path.join(scratch, matched_name)):
        return matched_name
    return name


def level_stretched_sky(
    scratch: str,
    name: str,
    steps: list[str],
    settings_used: dict[str, Any] | None,
    skipped_steps: list[SkippedPreviewStep] | None,
) -> str:
    """Level the sky of a stretched color picture (see `sky_levelling`).

    The step never fails the preview: when the sky cannot be measured or a
    needed offset is too large, the picture is kept as it is, the reason goes
    into the record and a notice is stored.

    Parameters
    ----------
    scratch : `str`
        The scratch folder.
    name : `str`
        The stretched picture's file name in the scratch folder.
    steps : `list` [`str`]
        The sentences of the steps; this step's sentence is added.
    settings_used : `dict`, optional
        The record of the choices the run used; ``sky_levelling`` is added.
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        A step that changed nothing is added to it.

    Returns
    -------
    file_name : `str`
        The leveled picture's file name when the offsets were added,
        otherwise `name`.
    """
    raise_if_abandoned()
    leveled_name = "levelled.fits"
    result = level_sky_in_file(os.path.join(scratch, name), os.path.join(scratch, leveled_name))
    applied = result.applied and os.path.isfile(os.path.join(scratch, leveled_name))
    steps.append(result.step_sentence())
    if settings_used is not None:
        settings_used["sky_levelling"] = result.record()
    if applied:
        return leveled_name
    if skipped_steps is not None:
        skipped_steps.append(SkippedPreviewStep("Sky levelling", result.reason))
    return name
