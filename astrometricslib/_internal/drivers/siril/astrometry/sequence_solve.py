"""Purpose: Siril's plate solve of every frame of a session, each on its own.

Description: Photometry puts each star's measuring circle where the frame's own
plate solution says the star is. A night holds hundreds of frames, and a Siril
run for each one would spend most of its time starting. Siril's
``seqplatesolve`` command solves a whole sequence in one run: the study
(photometry register entry PHO-23) solved 39 frames of 3,008 by 3,008 pixels
in 3.6 s, against 0.5 to 1.6 s for each frame alone (AST-23).

`solve_frames` is the operation the app's plate-solve driver
(`astrometry.solve_driver`) calls for a session:

1. It solves the first frame, the session's reference frame, with
   `plate_solve.solve_image`, which retries with a shorter focal length. The
   solved header gives the focal length that works. Without that step a lens
   whose header names its longest focal length would fail on every frame.
2. It links the other frames into a sequence in the app's scratch folder, in
   groups of `FRAMES_PER_GROUP`. The frames themselves are only linked.
   Siril writes its converted copies, with the solutions, into the scratch
   folder, and the copies are removed after their headers are read.
3. It runs ``seqplatesolve`` with that focal length, the pixel size and the
   start position of the first frame, with the private settings file, in
   offline mode, and without waiting for the stack's Siril slot. Every frame
   is solved on its own and a frame Siril cannot solve does not stop the
   others.
4. It reads each solution from the file Siril wrote and returns it, or the
   reason the frame has none.

A frame that Siril cannot solve gets no solution and is not tried again: a
second run with the same start position and focal length would give the same
answer.
"""

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from typing import Any

from astrometricslib._internal.drivers.file_staging import stage_file
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib._internal.drivers.interfaces.plate_solve_driver import FrameSolve, PlateSolveHeader
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import (
    LOCAL_GAIA_CATALOG,
    PLATE_SOLVE_MINIMUM_SIRIL_VERSION,
    SOLVE_TIMEOUT_SECONDS,
    SolveInputs,
    _positive_number,
    read_inputs,
    seqplatesolve_command,
    solve_image,
)
from astrometricslib._internal.drivers.siril.core.program import find_local_gaia_catalog
from astrometricslib._internal.drivers.siril.core.script_run import last_siril_output, run_siril_script
from astrometricslib.foundation.paths import scratch_directory

logger = logging.getLogger(__name__)

# How many frames one Siril run solves. Siril writes a converted copy of every
# frame it solves (about 18 MB for a 9 megapixel frame) into the scratch
# folder, so the group size bounds the disk space a run uses: 50 frames of that
# size use about 1 GB. The run's start-up (about 1 s) is spread over the group.
FRAMES_PER_GROUP = 50

# The time allowed for one group, in seconds: a base for Siril's start and then
# a share for each frame. The study solved 39 frames in 3.6 s; 2 s a frame
# leaves room for a frame that makes the solver search to its own limit.
SEQUENCE_BASE_TIMEOUT_SECONDS = 30
SEQUENCE_TIMEOUT_SECONDS_PER_FRAME = 2

# What the names of the linked frames and of the sequence are.
_FRAME_NAME_PATTERN = "frame_{index:05d}.fits"
_SEQUENCE_NAME = "frames"

# Siril's line for a solved image, and its conversion list (the source file and
# the converted file of each frame).
_SOLVED_IMAGE_PATTERN = re.compile(r"Image (\S+) platesolved and updated")
_CONVERSION_PATTERN = re.compile(r"'([^']*)' -> '([^']*)'")


def _solve_one_group(
    image_paths: Sequence[str],
    inputs: SolveInputs,
    siril_executable: str,
    catalog_path: str,
    timeout_seconds: float,
) -> dict[str, FrameSolve]:
    """Solve one group of frames with a single Siril run.

    Parameters
    ----------
    image_paths : `Sequence` [`str`]
        The frames of the group. They are only read.
    inputs : `SolveInputs`
        The start position, the focal length and the pixel size.
    siril_executable : `str`
        The command that starts Siril.
    catalog_path : `str`
        Siril's Gaia catalog file on the disk.
    timeout_seconds : `float`
        The longest the run may take.

    Returns
    -------
    solves : `dict` [`str`, `FrameSolve`]
        One entry for each frame of the group.
    """
    work_folder = tempfile.mkdtemp(prefix="sequence_solve_", dir=scratch_directory())
    try:
        staged_names: dict[str, str] = {}
        for index, path in enumerate(image_paths, start=1):
            staged_name = _FRAME_NAME_PATTERN.format(index=index)
            stage_file(os.path.abspath(os.path.expanduser(path)), os.path.join(work_folder, staged_name))
            staged_names[staged_name] = path
        commands = [
            f"requires {PLATE_SOLVE_MINIMUM_SIRIL_VERSION}",
            f"set core.catalogue_gaia_astro={catalog_path}",
            f"link {_SEQUENCE_NAME}",
            seqplatesolve_command(
                _SEQUENCE_NAME,
                LOCAL_GAIA_CATALOG,
                ra_deg=inputs.ra_deg,
                dec_deg=inputs.dec_deg,
                focal_length_mm=inputs.focal_length_mm,
                pixel_size_um=inputs.pixel_size_um,
            ),
            "close",
        ]
        try:
            run_siril_script(
                work_folder,
                commands,
                siril_executable,
                timeout_seconds=timeout_seconds,
                wait_for_slot=False,
                offline=True,
                script_name="sequence_solve",
            )
        except OSError as start_error:
            logger.warning("Siril could not be started to solve a group of frames: %s", start_error)
            return {path: FrameSolve(None, "Siril could not be started") for path in image_paths}
        output = last_siril_output(include_errors=True)
        solved_files = set(_SOLVED_IMAGE_PATTERN.findall(output))
        converted_name_of: dict[str, str] = {}
        conversion_path = os.path.join(work_folder, f"{_SEQUENCE_NAME}_conversion.txt")
        if os.path.isfile(conversion_path):
            with open(conversion_path, encoding="utf-8") as conversion_file:
                for source, converted in _CONVERSION_PATTERN.findall(conversion_file.read()):
                    converted_name_of[os.path.basename(source)] = converted
        solves: dict[str, FrameSolve] = {}
        for staged_name, path in staged_names.items():
            converted_name = converted_name_of.get(staged_name)
            if converted_name is None or converted_name not in solved_files:
                solves[path] = FrameSolve(None, "Siril found no solution from the start position")
                continue
            try:
                header = read_header(os.path.join(work_folder, converted_name), first_hdu_only=True)
            except FITS_READ_ERRORS as read_error:
                logger.warning("Could not read Siril's solved frame %s: %s", converted_name, read_error)
                solves[path] = FrameSolve(None, "Siril's solved file could not be read")
                continue
            solves[path] = FrameSolve(PlateSolveHeader.from_header(header))
        return solves
    finally:
        shutil.rmtree(work_folder, ignore_errors=True)


def solve_frames(
    image_paths: Sequence[str],
    siril_executable: str,
    hints: dict[str, Any],
    catalog_path: str | None = None,
    timeout_seconds: float = SOLVE_TIMEOUT_SECONDS,
) -> dict[str, FrameSolve]:
    """Plate-solve every frame of a session with Siril, each on its own.

    Parameters
    ----------
    image_paths : `Sequence` [`str`]
        The frames of one session. The first is the reference frame. They are
        only read.
    siril_executable : `str`
        The command that starts Siril (the ``siril_executable`` setting).
    hints : `dict`
        The hints of the plate-solve interface (see `read_inputs`).
    catalog_path : `str`, optional
        Siril's Gaia catalog file on the disk. By default
        `find_local_gaia_catalog` looks for it.
    timeout_seconds : `float`, optional
        The longest the solve of the first frame may take. Default
        `SOLVE_TIMEOUT_SECONDS`.

    Returns
    -------
    solves : `dict` [`str`, `FrameSolve`]
        One entry for each frame, keyed by its path. A frame Siril could not
        solve has no header and says why. When the first frame cannot be
        solved, no frame is: every other frame is mapped through it.
    """
    paths = list(image_paths)
    if not paths:
        return {}
    try:
        first_header = read_header(paths[0], first_hdu_only=True)
    except FITS_READ_ERRORS as read_error:
        logger.warning("Could not read %s for a Siril plate solve: %s", paths[0], read_error)
        return {path: FrameSolve(None, "the reference frame could not be read") for path in paths}
    inputs = read_inputs(first_header, hints)
    if inputs is None:
        reason = "the reference frame has no start position, focal length or pixel size in its header"
        return {path: FrameSolve(None, reason) for path in paths}
    if catalog_path is None:
        catalog_path = find_local_gaia_catalog()
    if catalog_path is None:
        logger.warning("Siril's Gaia astrometry catalog is not installed; no local plate solve.")
        return {path: FrameSolve(None, "Siril's Gaia catalog is not installed") for path in paths}

    first_solved = solve_image(paths[0], siril_executable, hints, catalog_path, timeout_seconds)
    if first_solved is None:
        reason = "Siril could not solve the reference frame, so no frame has a position"
        return {path: FrameSolve(None, reason) for path in paths}
    solves = {paths[0]: FrameSolve(first_solved)}
    # Siril writes the focal length its solution fits into the header. It is
    # the one that solved the first frame, which the header's may not be.
    working_focal_length = _positive_number(first_solved.get("FOCALLEN")) or inputs.focal_length_mm
    group_inputs = SolveInputs(inputs.ra_deg, inputs.dec_deg, working_focal_length, inputs.pixel_size_um)

    remaining = paths[1:]
    for start in range(0, len(remaining), FRAMES_PER_GROUP):
        group = remaining[start : start + FRAMES_PER_GROUP]
        group_timeout = SEQUENCE_BASE_TIMEOUT_SECONDS + SEQUENCE_TIMEOUT_SECONDS_PER_FRAME * len(group)
        solves.update(_solve_one_group(group, group_inputs, siril_executable, catalog_path, group_timeout))
    return solves
