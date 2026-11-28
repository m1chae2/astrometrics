#!/usr/bin/env python3
"""Purpose: Compare each solver's star positions at the center and the edge.

Description: For each frame given, the script matches the detected stars to
their nearest Gaia star (G < 14, from the study's copy of the Gaia cache)
under each solver's hinted solution. It prints the median separation in
four zones of distance from the image center (0 to 0.3, 0.3 to 0.6, 0.6 to
0.8 and 0.8 to 1 of the half-diagonal). A distortion model that does not fit
a wide field shows as a separation that grows toward the edge. It writes no
file.

Usage::

    corner_check.py --study <folder> <ids...>
"""

import argparse

import judge
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from study_common import StudyFolder, add_study_argument, open_study

ZONES = [(0, 0.3), (0.3, 0.6), (0.6, 0.8), (0.8, 1.01)]
# Matches farther than this, in arcseconds, are left out of the medians.
LARGEST_SEPARATION_ARCSEC = 60
GAIA_MAGNITUDE_LIMIT = 14.0


def check_frame(study: StudyFolder, ident: str) -> None:
    """Print the median nearest-Gaia separation by zone for one frame.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    ident : `str`
        The frame's study id.
    """
    from astrometricslib._internal.drivers import catalog_store
    from astrometricslib._internal.drivers.sky_tiles import tiles_for_footprint
    from astrometricslib.pipelines.astrometry.processing.gaia_field_matching import (
        propagate_gaia_positions,
        read_observation_time,
    )
    from astrometricslib.pipelines.shared.header_wcs import image_corners_on_sky

    row = study.read_manifest()[ident]
    data, header, factor = judge.load_frame(study, ident, row)
    positions = judge.centroids(data) * factor + (0.5 if factor == 2 else 0)
    height, width = row["NAXIS2"], row["NAXIS1"]
    reference = judge.read_wcs(study.out / "wcs" / "siril" / f"{ident}__hint.hdr")
    tiles = tiles_for_footprint(image_corners_on_sky(reference, width, height), 0.05, 7)
    rows = catalog_store.stars_in_tiles(None, tiles, GAIA_MAGNITUDE_LIMIT, include_proper_motion=True)
    ra, dec = propagate_gaia_positions(
        np.array([r[1] for r in rows]),
        np.array([r[2] for r in rows]),
        np.array([np.nan if r[5] is None else r[5] for r in rows]),
        np.array([np.nan if r[6] is None else r[6] for r in rows]),
        read_observation_time(header),
    )
    gaia = SkyCoord(np.asarray(ra) * u.deg, np.asarray(dec) * u.deg)
    radius = np.hypot(
        (positions[:, 0] - width / 2) / (width / 2), (positions[:, 1] - height / 2) / (height / 2)
    )
    radius /= np.sqrt(2)
    for solver in ["solve-field", "siril"]:
        wcs = judge.read_wcs(study.out / "wcs" / solver / f"{ident}__hint.hdr")
        _, separation, _ = wcs.pixel_to_world(positions[:, 0], positions[:, 1]).match_to_catalog_sky(gaia)
        parts = []
        for low, high in ZONES:
            chosen = (radius >= low) & (radius < high) & (separation.arcsec < LARGEST_SEPARATION_ARCSEC)
            if chosen.sum():
                median = np.median(separation.arcsec[chosen])
                parts.append(f"r{low}-{high}: n={chosen.sum()} median={median:.2f} arcsec")
            else:
                parts.append(f"r{low}-{high}: none")
        print(ident, solver, " | ".join(parts))


def main() -> None:
    """Check each frame given on the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_study_argument(parser)
    parser.add_argument("ids", nargs="+")
    arguments = parser.parse_args()
    study = open_study(arguments.study)
    cache_copy = judge.copy_gaia_cache(study, None)

    from astrometricslib._internal.drivers import catalog_store

    # Read the study's copy, never the app's cache.
    catalog_store.get_catalog_cache_path = lambda config: cache_copy
    for ident in arguments.ids:
        check_frame(study, ident)


if __name__ == "__main__":
    main()
