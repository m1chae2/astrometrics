"""Purpose: Line up group stacks with Siril's plate-solve registration.

Description: The stacking pipeline stacks each exposure group of a target on
its own, and the group stacks must then lie on one pixel grid before they are
combined. This module asks Siril to do that:

1. ``seqplatesolve`` plate-solves every group stack against the Gaia catalog
   (the sky map of the stack, with a fit of the lens distortion) and records
   it in the sequence.
2. ``seqapplyreg`` moves every solved stack onto the grid of the reference
   stack, so a star falls on the same pixel in all of them.

`build_group_registration_commands` writes the script and
`register_group_stacks` runs it and reads back what each group got.

A group whose stars Siril cannot match to the catalog is not solved, and the
other groups still are. This holds because ``seqplatesolve`` reports a group
that did not solve and goes on to the next one (Siril says the sequence
processing "partially succeeded"), and ``seqapplyreg`` then works only on the
solved groups. The one case that stops the apply is a sequence in which fewer
than two groups solved, since one solved group has nothing to line up with.
`register_group_stacks` reads the log in every case, so each group's outcome
and its reason are known even then.

The run sets ``setcpu 1`` so Siril solves one group after the other. With
more threads the lines of the groups mix, and the matched star count and fit
spread could not be given to the right group.

The reference stack comes out of ``seqapplyreg`` too: it is moved onto an
undistorted grid like the others, so all outputs share one grid and one sky
map (a plain tangent projection).
"""

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass

import numpy as np
from astropy.io import fits

from astrometricslib._internal.drivers.file_staging import stage_file
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_data, read_header
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import (
    LOCAL_GAIA_CATALOG,
    PLATE_SOLVE_MINIMUM_SIRIL_VERSION,
)
from astrometricslib._internal.drivers.siril.core.output_parsing import (
    SequenceSolveRecord,
    parse_sequence_solve,
)
from astrometricslib._internal.drivers.siril.core.program import find_local_gaia_catalog
from astrometricslib._internal.drivers.siril.core.script_run import last_siril_output, run_siril_script
from astrometricslib.foundation.paths import scratch_directory

logger = logging.getLogger(__name__)

# The base name of the staged group stacks. Siril names them ``s_00001.fit``,
# ``s_00002.fit`` and so on, and the sequence ``s``.
_SEQUENCE_NAME = "s"

# The order of the distortion fit (SIP) Siril solves each group stack with.
# Order 3 is the order the study of group alignment settled on: orders 3, 4
# and 5 left the same offset between stars, and order 2 was clearly worse on
# the Nikon 300 mm lens (a leftover of about 3 pixels instead of 0.8).
SIP_ORDER = 3

# The seconds a run may take for each group, and a base for Siril's start and
# the catalog read. The study solved and applied 10 groups in 13 s on one
# thread; a run that takes 20 times longer than that has hung.
_BASE_TIMEOUT_SECONDS = 120
_TIMEOUT_SECONDS_PER_GROUP = 60


@dataclass
class RegisteredGroup:
    """What Siril made of one group stack.

    Attributes
    ----------
    solved : `bool`
        Whether Siril solved the stack and lined it up with the reference.
    failure_reason : `str` or `None`
        Why not, in a sentence that carries Siril's own numbers. `None` when
        `solved` is `True`.
    stars_found : `int` or `None`
        How many stars Siril found in the stack, or `None` if it printed none.
    matched_star_count : `int` or `None`
        How many stars Siril matched to the Gaia catalog.
    fit_residual_rms_arcsec : `float` or `None`
        The root mean square (RMS) distance, in arcseconds, between a matched
        star and its catalog position.
    fit_order : `int` or `None`
        The order of the distortion fit Siril kept.
    solved_header : `astropy.io.fits.Header` or `None`
        The header Siril wrote for the stack's own grid. It holds the sky map
        with the distortion terms.
    aligned_image : `numpy.ndarray` or `None`
        The stack on the common grid (``float32``, zero where the move
        brought in nothing), the same shape as the input.
    aligned_header : `astropy.io.fits.Header` or `None`
        The header of the aligned image. Its sky map (a plain tangent
        projection) is the same for every group of the run.
    """

    solved: bool
    failure_reason: str | None = None
    stars_found: int | None = None
    matched_star_count: int | None = None
    fit_residual_rms_arcsec: float | None = None
    fit_order: int | None = None
    solved_header: fits.Header | None = None
    aligned_image: np.ndarray | None = None
    aligned_header: fits.Header | None = None


def build_group_registration_commands(reference_number: int, catalog_path: str, count: int) -> list[str]:
    """Write the Siril script that solves and lines up the group stacks.

    Parameters
    ----------
    reference_number : `int`
        The reference stack's number in the sequence, counted from 1.
    catalog_path : `str`
        Siril's Gaia catalog file on the disk.
    count : `int`
        How many stacks the sequence holds. It is only checked to be at
        least 2, since one stack has nothing to line up with.

    Returns
    -------
    commands : `list` [`str`]
        The script. Siril writes the lined-up stacks as
        ``r_s_<number>.fits``.

    Raises
    ------
    ValueError
        If there are fewer than two stacks, or the reference number is not
        one of them.
    """
    if count < 2:
        raise ValueError("Lining up needs at least two group stacks.")
    if not 1 <= reference_number <= count:
        raise ValueError(f"The reference number {reference_number} is not among the {count} group stacks.")
    return [
        f"requires {PLATE_SOLVE_MINIMUM_SIRIL_VERSION}",
        f"set core.catalogue_gaia_astro={catalog_path}",
        "setext fits",
        "set32bits",
        f"link {_SEQUENCE_NAME}",
        "setcpu 1",
        f"setref {_SEQUENCE_NAME} {reference_number}",
        f"seqplatesolve {_SEQUENCE_NAME} -force -order={SIP_ORDER} -catalog={LOCAL_GAIA_CATALOG} -noflip",
        f"seqapplyreg {_SEQUENCE_NAME} -framing=current -interp=la -noclamp",
        "close",
    ]


def _failure_reason(record: SequenceSolveRecord | None) -> str:
    """Say in a sentence why Siril did not solve a group stack.

    Parameters
    ----------
    record : `SequenceSolveRecord` or `None`
        What Siril printed about the stack, or `None` if it printed nothing.

    Returns
    -------
    reason : `str`
        The sentence, with the number of stars Siril found when it gave one.
    """
    if record is None:
        return "Siril reported nothing about this stack"
    if record.stars_found is None:
        return "Siril could not match the stack's stars to the Gaia catalog"
    return (
        f"Siril found {record.stars_found} star(s) in the stack and could not match them to the Gaia catalog"
    )


def _all_unsolved(count: int, reason: str) -> list[RegisteredGroup]:
    """Make the answer for a run that gave no group a solution.

    Parameters
    ----------
    count : `int`
        The number of group stacks.
    reason : `str`
        Why the run failed.

    Returns
    -------
    groups : `list` [`RegisteredGroup`]
        One unsolved entry for each stack, all with the same reason.
    """
    return [RegisteredGroup(False, reason) for _ in range(count)]


def register_group_stacks(
    stack_paths: list[str],
    reference_index: int,
    siril_executable: str,
    catalog_path: str | None = None,
) -> list[RegisteredGroup]:
    """Solve the group stacks with Siril and line them up with the reference.

    The stacks are linked into a scratch folder, so the files are only read.
    The scratch folder is removed when the run ends.

    Parameters
    ----------
    stack_paths : `list` [`str`]
        The group stacks' FITS files. Each header must hold the start
        position, focal length and pixel size Siril reads to start its
        search.
    reference_index : `int`
        The place, counted from 0, of the stack the others are lined up with.
    siril_executable : `str`
        The command that starts Siril.
    catalog_path : `str`, optional
        Siril's Gaia catalog file on the disk. By default
        `find_local_gaia_catalog` looks for it.

    Returns
    -------
    groups : `list` [`RegisteredGroup`]
        One entry for each stack, in order. A run that fails as a whole
        (Siril could not start, no catalog) gives every stack the same
        failure reason.
    """
    count = len(stack_paths)
    if count < 2:
        return _all_unsolved(count, "there is only one group stack, so nothing to line up")
    if catalog_path is None:
        catalog_path = find_local_gaia_catalog()
    if catalog_path is None:
        logger.warning("Siril's Gaia astrometry catalog is not installed; groups cannot be lined up.")
        return _all_unsolved(count, "Siril's Gaia astrometry catalog is not installed")
    work_folder = tempfile.mkdtemp(prefix="group_registration_", dir=scratch_directory())
    try:
        for number, path in enumerate(stack_paths, start=1):
            stage_file(
                os.path.abspath(os.path.expanduser(path)),
                os.path.join(work_folder, f"{_SEQUENCE_NAME}_{number:05d}.fit"),
            )
        commands = build_group_registration_commands(reference_index + 1, catalog_path, count)
        try:
            run_siril_script(
                work_folder,
                commands,
                siril_executable,
                timeout_seconds=_BASE_TIMEOUT_SECONDS + _TIMEOUT_SECONDS_PER_GROUP * count,
                offline=True,
                script_name="group_registration",
            )
        except OSError as start_error:
            logger.warning("Siril could not be started to line up the groups: %s", start_error)
            return _all_unsolved(count, f"Siril could not be started: {start_error}")
        records = parse_sequence_solve(last_siril_output(include_errors=True))
        return [_read_group(work_folder, number, records.get(number)) for number in range(1, count + 1)]
    finally:
        shutil.rmtree(work_folder, ignore_errors=True)


def _read_group(work_folder: str, number: int, record: SequenceSolveRecord | None) -> RegisteredGroup:
    """Collect what Siril wrote for one group stack.

    A group counts as solved only when Siril reported it solved and wrote
    the lined-up image, so a run that stopped before the apply gives no
    group a result.

    Parameters
    ----------
    work_folder : `str`
        The folder Siril worked in.
    number : `int`
        The stack's number in the sequence, counted from 1.
    record : `SequenceSolveRecord` or `None`
        What Siril printed about the stack.

    Returns
    -------
    group : `RegisteredGroup`
        The group's outcome.
    """
    if record is None or not record.solved:
        return RegisteredGroup(False, _failure_reason(record), None if record is None else record.stars_found)
    solved_path = os.path.join(work_folder, f"{_SEQUENCE_NAME}_{number:05d}.fits")
    aligned_path = os.path.join(work_folder, f"r_{_SEQUENCE_NAME}_{number:05d}.fits")
    if not os.path.isfile(aligned_path):
        return RegisteredGroup(
            False,
            "Siril solved the stack but wrote no lined-up image, so too few stacks could be solved "
            "to line any of them up",
            record.stars_found,
            record.matched_star_count,
            record.fit_residual_rms_arcsec,
            record.fit_order,
        )
    try:
        solved_header = read_header(solved_path, first_hdu_only=True)
        aligned_header = read_header(aligned_path, first_hdu_only=True)
        aligned = read_data(aligned_path, first_hdu_only=True)
    except FITS_READ_ERRORS as read_error:
        logger.warning("Could not read Siril's output for group stack %d: %s", number, read_error)
        return RegisteredGroup(False, f"Siril's output could not be read: {read_error}", record.stars_found)
    if aligned is None:
        return RegisteredGroup(False, "Siril's lined-up image holds no pixels", record.stars_found)
    return RegisteredGroup(
        True,
        None,
        record.stars_found,
        record.matched_star_count,
        record.fit_residual_rms_arcsec,
        record.fit_order,
        solved_header,
        np.asarray(aligned, dtype=np.float32),
        aligned_header,
    )
