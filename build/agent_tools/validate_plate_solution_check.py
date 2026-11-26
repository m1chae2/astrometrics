#!/usr/bin/env python3
r"""Purpose: Try the plate-solution check on real frames with shifted maps.

Description: The plate-solution check (`plate_solution_problem`, register
entry AST-D19) says a solution is wrong when too few of the stars' nearest
catalog entries lie within the match radius R, compared with what chance
neighbors give at the field's catalog density. This script measures how it
behaves on real data. It takes plate-solved light frames from a copy of the
library, and for each frame moves the solution's reference sky position
(``CRVAL1``, ``CRVAL2``) by 0, 0.5, 1, 2 and 5 match radii. It names the
stars with `identify_session_stars` (``trust_image_wcs=True``) inside the
frame's parameter context, and prints one row per frame and shift: camera,
plate scale, catalog density, n (stars with a catalog entry within the
search circle W), the share of those within R, the chance share s0, the
limit and the verdict. An unshifted solution should pass, and a shift of
two match radii or more should fail.

It only reads the library. The shifts are made on in-memory copies of the
frames' sky maps, no sky map is written back (``write_back=False``), and the
plate solver is replaced by one that never solves, so a failed check does
not start a fresh solve. Naming the stars may fill the copy's Gaia and SIMBAD
caches, as any run does.

Usage: copy the script into ``$ASTROMETRICS_SCRATCH`` and run the copy
through ``realrun.sh``, which points it at the copy config (see the agent
tools README)::

    cp validate_plate_solution_check.py "$ASTROMETRICS_SCRATCH"/
    realrun.sh <checkout> \
        "$ASTROMETRICS_SCRATCH"/validate_plate_solution_check.py
        [--target "M 67"] [--frames-per-setup 2] [--max-frames 12]
        [--shifts 0,0.5,1,2,5]
"""

import argparse
import math
import os
import sys
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

# The match radius R and search circle W are printed in arcseconds; one
# degree holds this many.
ARCSEC_PER_DEGREE = 3600.0


class NeverSolves:
    """A plate solver that never solves, so a failed check keeps its stars.

    `identify_session_stars` re-solves a frame whose reused map fails the
    check. Here the reused map is the shifted one under test, so the fresh
    solve must not replace it.
    """

    def solve(self, **_arguments: Any) -> None:
        """Give no solution.

        Returns
        -------
        header : `None`
            Always `None`, which the caller reads as a failed solve.
        """
        return None


def refuse_unless_copy() -> None:
    """Stop unless the run points at a copy config inside the scratch folder.

    The script exits with a message when ``ASTROMETRICS_CONFIG_PATH`` is not
    set or not inside ``ASTROMETRICS_SCRATCH`` (``realrun.sh`` sets both).
    """
    scratch = os.environ.get("ASTROMETRICS_SCRATCH")
    config = os.environ.get("ASTROMETRICS_CONFIG_PATH")
    if not scratch or not config:
        sys.exit("run this through build/agent_tools/realrun.sh, which points it at the copy config")
    if not os.path.realpath(config).startswith(os.path.realpath(scratch) + os.sep):
        sys.exit(f"the config {config} is not inside {scratch}; refusing to read anything else")


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    """Read the command line.

    Parameters
    ----------
    arguments : `Sequence` [`str`]
        The arguments after the script name.

    Returns
    -------
    options : `argparse.Namespace`
        ``target`` (names, empty for every target), ``frames_per_setup``,
        ``max_frames`` and ``shifts`` (in match radii).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", action="append", default=[], help="a target id or name (repeatable)")
    parser.add_argument("--frames-per-setup", type=int, default=2, help="frames per target and camera")
    parser.add_argument("--max-frames", type=int, default=12, help="frames in all")
    parser.add_argument("--shifts", default="0,0.5,1,2,5", help="shifts in match radii, comma separated")
    options = parser.parse_args(list(arguments))
    options.shifts = [float(value) for value in options.shifts.split(",") if value.strip()]
    return options


def choose_frames(astro: Any, targets: Sequence[str], per_setup: int, limit: int) -> list[Any]:
    """Pick plate-solved light frames, a few per target and camera.

    Parameters
    ----------
    astro : `Astrometrics`
        The library (the copy).
    targets : `Sequence` [`str`]
        Target ids or names; empty for every target.
    per_setup : `int`
        Most frames per target, camera and focal length.
    limit : `int`
        Most frames in all.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames, each with a plate-solved header.
    """
    from astropy.io import fits

    from astrometricslib.drivers import header_is_plate_solved

    chosen_targets = [astro.targets.get(name) for name in targets] if targets else astro.targets.list()
    frames: list[Any] = []
    for target in chosen_targets:
        if target is None:
            continue
        per_group: dict[tuple[str, Any], int] = defaultdict(int)
        for frame in target.frames:
            if len(frames) >= limit:
                return frames
            key = (str(frame.camera), frame.focal_length_mm)
            if str(frame.role).upper() != "LIGHT" or per_group[key] >= per_setup:
                continue
            if "SPEC" in str(getattr(frame.filter, "name", frame.filter)).upper():
                continue
            try:
                header = fits.getheader(frame.path, memmap=False)
            except OSError:
                continue
            if header_is_plate_solved(header):
                frames.append(frame)
                per_group[key] += 1
    return frames


def shifted_copy(wcs: Any, shift_arcsec: float) -> Any:
    """Copy a sky map with its reference sky position moved.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The frame's plate solution; it is not changed.
    shift_arcsec : `float`
        How far to move it on the sky, half in right ascension and half in
        declination (a diagonal shift).

    Returns
    -------
    shifted : `astropy.wcs.WCS`
        The moved copy.
    """
    shifted = wcs.deepcopy()
    step_deg = shift_arcsec / math.sqrt(2.0) / ARCSEC_PER_DEGREE
    declination = float(shifted.wcs.crval[1])
    shifted.wcs.crval = [
        float(shifted.wcs.crval[0]) + step_deg / max(math.cos(math.radians(declination)), 1e-6),
        min(89.999, declination + step_deg),
    ]
    shifted.wcs.set()
    return shifted


def check_frame(frame: Any, shifts: Sequence[float]) -> list[str]:
    """Run the check on one frame at every shift and word the rows.

    Parameters
    ----------
    frame : `FrameRecord`
        A plate-solved light frame.
    shifts : `Sequence` [`float`]
        The shifts, in match radii.

    Returns
    -------
    rows : `list` [`str`]
        One tab-separated row per shift.
    """
    from astropy.wcs.utils import proj_plane_pixel_scales

    from astrometricslib._internal.drivers.image import AstrometricsImage
    from astrometricslib.foundation.parameters import resolve
    from astrometricslib.parameters import frames_parameter_context
    from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
    from astrometricslib.pipelines.astrometry.processing.wcs_refinement import record_plate_scale
    from astrometricslib.pipelines.shared.catalog_star_identity import CATALOG_MATCH_RADIUS_ARCSEC
    from astrometricslib.pipelines.shared.session_identification import (
        SOLUTION_CHECK_RADIUS_FACTOR,
        chance_concentration,
        identify_session_stars,
        match_concentration_limit,
        plate_solution_problem,
    )

    rows: list[str] = []
    with frames_parameter_context([frame]):
        original = AstrometricsImage(frame.path).wcs
        if original is None or not original.has_celestial:
            return [f"{os.path.basename(frame.path)}\tno sky map"]
        plate_scale = float(proj_plane_pixel_scales(original.celestial).mean()) * ARCSEC_PER_DEGREE
        record_plate_scale(original)
        match_radius = float(resolve(CATALOG_MATCH_RADIUS_ARCSEC).value)
        search_radius = float(resolve(SOLUTION_CHECK_RADIUS_FACTOR).value) * match_radius
        identifier = StarIdentifier()
        identifier.solver = NeverSolves()
        for shift in shifts:
            image = AstrometricsImage(frame.path)
            image.wcs = shifted_copy(original, shift * match_radius)
            result = identify_session_stars(image, identifier, write_back=False, trust_image_wcs=True)
            distances = result.nearest_catalog_separations_arcsec
            in_circle = [distance for distance in distances if distance <= search_radius]
            n = len(in_circle)
            share = sum(1 for distance in in_circle if distance <= match_radius) / n if n else math.nan
            density = result.catalog_density_per_square_degree
            chance = chance_concentration(match_radius, search_radius, density)
            limit = match_concentration_limit(chance, n) if n else math.nan
            problem = plate_solution_problem(result.stellar_objects, distances, density)
            rows.append(
                "\t".join([
                    os.path.basename(frame.path),
                    str(frame.camera),
                    f"{plate_scale:.3f}",
                    f"{shift:g}",
                    "unknown" if density is None else f"{density:.0f}",
                    str(n),
                    f"{share:.3f}",
                    f"{chance:.3f}",
                    f"{limit:.3f}",
                    "pass" if problem is None else f"FAIL: {problem}",
                ])
            )
    return rows


def main(arguments: Sequence[str]) -> int:
    """Print the check's numbers for each chosen frame and shift.

    Parameters
    ----------
    arguments : `Sequence` [`str`]
        The command-line arguments after the script name.

    Returns
    -------
    exit_code : `int`
        0 when at least one frame was checked, 1 when none was found.
    """
    refuse_unless_copy()
    options = parse_arguments(arguments)
    from astrometricslib import Astrometrics

    astro = Astrometrics()
    frames = choose_frames(astro, options.target, options.frames_per_setup, options.max_frames)
    if not frames:
        print("No plate-solved light frames found.")
        return 1
    print(
        "frame\tcamera\tarcsec/px\tshift (R)\tdensity (/deg2)\tn within W\tshare within R\ts0\tlimit\tverdict"
    )
    for frame in frames:
        for row in check_frame(frame, options.shifts):
            print(row, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
