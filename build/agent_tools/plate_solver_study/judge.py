#!/usr/bin/env python3
"""Purpose: Judge every saved solution against Gaia DR3 and compare solvers.

Description: For each frame the script detects stars with the project's
``SourceDetector``, refines each center with a 2-D Gaussian fit (photutils
``centroid_2dg``, as in register entry AST-01) and measures the star width
(FWHM, full width at half maximum) with the project's
``measure_fwhm_from_data``. It reads the Gaia DR3 stars brighter than G = 16
of the field from a copy of the app's Gaia tile cache, moved to the frame's
``DATE-OBS`` with the project's ``propagate_gaia_positions``. A color
(Bayer) frame is binned 2 by 2 first and a three-plane stack is averaged.

For each saved solution (solver and mode) it records:

- the AST-01 check: stars matched to Gaia within 2 arcsec, the median RA
  and Dec offset (the constant shift), the median scatter about it and the
  RMS;
- the project's own refit, ``refine_wcs_with_gaia``: ``rms_before`` (the
  solver's solution against Gaia), ``rms_after`` (the refit), the shift and
  the star count, and the residual as a fraction of the star width against
  the AST-04 limit (``MAXIMUM_RESIDUAL_FRACTION_OF_FWHM``).

Then it records Siril minus solve-field differences of center, scale,
rotation, parity and the largest sky difference over a 5 by 5 grid of
pixels. Results are appended to ``<study>/out/judge.jsonl`` (one line per
solution) and ``<study>/out/compare.jsonl``.

The Gaia cache is only read: ``--gaia-cache`` names the app's cache
database, which is copied once to ``<study>/work/catalog_cache_copy.db``,
and the project's catalog store is pointed at the copy.

Usage::

    judge.py --study <folder> [--gaia-cache <app catalog cache .db>] [ids...]
"""

import argparse
import json
import sqlite3
import warnings
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from study_common import StudyFolder, add_study_argument, open_study, use_repository_code

use_repository_code()
warnings.simplefilter("ignore", FITSFixedWarning)

# Half the side of the box a 2-D Gaussian is fitted in, in pixels.
CENTROID_HALF_BOX = 7
# The brightest detections kept for the check.
CENTROID_LIMIT = 2500
# The AST-01 match radius, in arcseconds.
MATCH_RADIUS_ARCSEC = 2.0
# The faintest Gaia G magnitude read from the cache.
GAIA_MAGNITUDE_LIMIT = 16.0
# The fewest refit stars the AST-04 gate needs.
GATE_MINIMUM_STARS = 20


def copy_gaia_cache(study: StudyFolder, source: str | None) -> Path:
    """Copy the app's Gaia cache into the study, or reuse an earlier copy.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    source : `str` or `None`
        The app's catalog cache database, opened read-only.

    Returns
    -------
    path : `pathlib.Path`
        The copy the judge reads.

    Raises
    ------
    SystemExit
        When the source is the copy itself, or there is no copy yet.
    """
    copy = study.work / "catalog_cache_copy.db"
    if source is not None:
        source_path = Path(source).expanduser().resolve()
        if source_path == copy.resolve():
            raise SystemExit("The Gaia cache source is the study's own copy; give the app's cache.")
        with (
            sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as reader,
            sqlite3.connect(copy) as writer,
        ):
            reader.backup(writer)
    if not copy.exists():
        raise SystemExit("No copy of the Gaia cache yet; pass --gaia-cache once.")
    return copy


def load_frame(study: StudyFolder, ident: str, row: dict) -> tuple[np.ndarray, fits.Header, int]:
    """Load a frame's pixels as one 2-D image.

    Parameters
    ----------
    study : `StudyFolder`
        The study folder.
    ident : `str`
        The frame's study id.
    row : `dict`
        The frame's manifest row.

    Returns
    -------
    data : `numpy.ndarray`
        The image: a three-plane stack averaged, a color frame binned 2 by 2.
    header : `astropy.io.fits.Header`
        The frame's header.
    factor : `int`
        The binning factor back to full-frame pixels.
    """
    with fits.open(study.frames / f"{ident}.fits", memmap=False) as hdul:
        data = np.asarray(hdul[0].data, dtype=np.float32)
        header = hdul[0].header.copy()
    if data.ndim == 3:
        return data.mean(axis=0), header, 1
    if row.get("BAYERPAT"):
        height, width = data.shape
        trimmed = data[: height // 2 * 2, : width // 2 * 2]
        return trimmed.reshape(height // 2, 2, width // 2, 2).mean(axis=(1, 3)), header, 2
    return data, header, 1


def centroids(data: np.ndarray, limit: int = CENTROID_LIMIT) -> np.ndarray:
    """Detect stars and refine their centers with 2-D Gaussian fits.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image.
    limit : `int`
        The brightest detections kept.

    Returns
    -------
    positions : `numpy.ndarray`
        Refined (x, y) centers, 0-based, one row per star.
    """
    from photutils.centroids import centroid_2dg

    from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector

    stars = SourceDetector().detect(data, with_radius=False)
    stars = sorted(stars, key=lambda star: -float(star.get("flux", 0)))[:limit]
    side = 2 * CENTROID_HALF_BOX + 1
    positions = []
    for star in stars:
        x_guess = float(star.get("x_centroid", star.get("xcentroid")))
        y_guess = float(star.get("y_centroid", star.get("ycentroid")))
        x, y = round(x_guess), round(y_guess)
        box = data[
            y - CENTROID_HALF_BOX : y + CENTROID_HALF_BOX + 1,
            x - CENTROID_HALF_BOX : x + CENTROID_HALF_BOX + 1,
        ]
        if box.shape != (side, side):
            continue
        try:
            dx, dy = centroid_2dg(box - np.median(box))
        except ValueError, RuntimeError:
            continue
        if 0 <= dx <= side - 1 and 0 <= dy <= side - 1:
            positions.append((x - CENTROID_HALF_BOX + dx, y - CENTROID_HALF_BOX + dy))
    return np.array(positions)


def read_wcs(path: Path) -> WCS:
    """Read a saved plate solution.

    Parameters
    ----------
    path : `pathlib.Path`
        A ``.hdr`` file written by ``run_solvers.py``.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The two-axis sky map.
    """
    return WCS(fits.Header.fromtextfile(str(path)), naxis=2)


def geometry(wcs: WCS, width: int, height: int) -> tuple[SkyCoord, float, float, str]:
    """Return a solution's center, scale, rotation and parity.

    Scale, rotation and parity come from the full mapping (SIP included) by
    finite differences of 50 pixels each side of the image center, so
    solutions with different reference pixels and SIP forms compare fairly.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The solution.
    width, height : `int`
        The image size in pixels.

    Returns
    -------
    center : `astropy.coordinates.SkyCoord`
        The sky position of the image center.
    scale : `float`
        The local pixel scale in arcseconds per pixel.
    rotation : `float`
        The angle of the +y axis from north toward east, in degrees.
    parity : `str`
        The sign of the mapping's determinant.
    """
    x_center, y_center = width / 2 - 0.5, height / 2 - 0.5
    center = wcs.pixel_to_world(x_center, y_center)
    frame = center.skyoffset_frame()

    def tangent(x: float, y: float) -> np.ndarray:
        """Return a pixel's offset from the center on the sky.

        Parameters
        ----------
        x, y : `float`
            The pixel position.

        Returns
        -------
        offset : `numpy.ndarray`
            East and north offsets in arcseconds.
        """
        offset = wcs.pixel_to_world(x, y).transform_to(frame)
        return np.array([offset.lon.arcsec, offset.lat.arcsec])

    along_x = (tangent(x_center + 50, y_center) - tangent(x_center - 50, y_center)) / 100
    along_y = (tangent(x_center, y_center + 50) - tangent(x_center, y_center - 50)) / 100
    determinant = np.linalg.det(np.column_stack([along_x, along_y]))
    parity = "east-left(det<0)" if determinant < 0 else "east-right(det>0)"
    rotation = np.degrees(np.arctan2(along_y[0], along_y[1]))
    return center, float(np.sqrt(abs(determinant))), float(rotation), parity


def ast01_check(wcs: WCS, positions: np.ndarray, gaia: SkyCoord, scale: float) -> dict:
    """Measure the constant shift and scatter of the stars matched to Gaia.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The solution.
    positions : `numpy.ndarray`
        Star centers in full-frame pixels.
    gaia : `astropy.coordinates.SkyCoord`
        Gaia positions at the frame's date.
    scale : `float`
        The pixel scale in arcseconds per pixel.

    Returns
    -------
    result : `dict`
        The match count and, with at least 5 matches, the shift, scatter and
        RMS.
    """
    sky = wcs.pixel_to_world(positions[:, 0], positions[:, 1])
    index, separation, _ = sky.match_to_catalog_sky(gaia)
    close = separation.arcsec < MATCH_RADIUS_ARCSEC
    if close.sum() < 5:
        return {"ast01_n": int(close.sum())}
    matched = gaia[index[close]]
    d_ra = (sky[close].ra - matched.ra).wrap_at(180 * u.deg).arcsec * np.cos(matched.dec.radian)
    d_dec = (sky[close].dec - matched.dec).arcsec
    shift_ra, shift_dec = np.median(d_ra), np.median(d_dec)
    return {
        "ast01_n": int(close.sum()),
        "shift_ra_arcsec": round(float(shift_ra), 3),
        "shift_dec_arcsec": round(float(shift_dec), 3),
        "shift_px": round(float(np.hypot(shift_ra, shift_dec) / scale), 3),
        "scatter_median_arcsec": round(float(np.median(np.hypot(d_ra - shift_ra, d_dec - shift_dec))), 3),
        "rms_arcsec": round(float(np.sqrt(np.mean(d_ra**2 + d_dec**2))), 3),
    }


def gaia_for_field(reference: WCS, width: int, height: int, header: fits.Header) -> tuple[SkyCoord, int]:
    """Read the field's Gaia stars from the cache copy, at the frame's date.

    Parameters
    ----------
    reference : `astropy.wcs.WCS`
        A solution that gives the field's footprint.
    width, height : `int`
        The image size in pixels.
    header : `astropy.io.fits.Header`
        The frame's header, for ``DATE-OBS``.

    Returns
    -------
    gaia : `astropy.coordinates.SkyCoord`
        Gaia positions at the frame's date.
    incomplete : `int`
        The number of cache tiles not complete to G = 16.
    """
    from astrometricslib._internal.drivers import catalog_store
    from astrometricslib._internal.drivers.sky_tiles import tiles_for_footprint
    from astrometricslib.foundation.parameters import resolve
    from astrometricslib.pipelines.astrometry.processing.gaia_field_matching import (
        propagate_gaia_positions,
        read_observation_time,
    )
    from astrometricslib.pipelines.shared.header_wcs import image_corners_on_sky

    level = int(resolve(catalog_store.GAIA_TILE_LEVEL).value)
    tiles = tiles_for_footprint(image_corners_on_sky(reference, width, height), 0.01, level)
    missing = catalog_store.missing_tiles(None, tiles, GAIA_MAGNITUDE_LIMIT)
    rows = catalog_store.stars_in_tiles(None, tiles, GAIA_MAGNITUDE_LIMIT, include_proper_motion=True)
    ra = np.array([row[1] for row in rows], float)
    dec = np.array([row[2] for row in rows], float)
    pm_ra = np.array([np.nan if row[5] is None else row[5] for row in rows], float)
    pm_dec = np.array([np.nan if row[6] is None else row[6] for row in rows], float)
    ra_now, dec_now = propagate_gaia_positions(ra, dec, pm_ra, pm_dec, read_observation_time(header))
    return SkyCoord(np.asarray(ra_now) * u.deg, np.asarray(dec_now) * u.deg), len(missing)


def judge_solution(
    wcs: WCS, positions: np.ndarray, gaia: SkyCoord, shape: tuple[int, int], fwhm: float | None
) -> dict:
    """Run the AST-01 check and the project's refit on one solution.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The solution.
    positions : `numpy.ndarray`
        Star centers in full-frame pixels.
    gaia : `astropy.coordinates.SkyCoord`
        Gaia positions at the frame's date.
    shape : `tuple` [`int`, `int`]
        The image height and width.
    fwhm : `float` or `None`
        The star width in full-frame pixels.

    Returns
    -------
    record : `dict`
        Geometry, the AST-01 numbers, the refit numbers and the gate
        fractions.
    """
    from astrometricslib.foundation.parameters import resolve
    from astrometricslib.pipelines.astrometry.post_processing.record_quality import (
        MAXIMUM_RESIDUAL_FRACTION_OF_FWHM,
    )

    # The app lost this module; see the README (commit 62906cee).
    from astrometricslib.pipelines.astrometry.processing.wcs_refinement import (  # pyrefly: ignore[missing-import]
        refine_wcs_with_gaia,
    )

    height, width = shape
    center, scale, rotation, parity = geometry(wcs, width, height)
    record = {
        "center_ra": round(center.ra.deg, 6),
        "center_dec": round(center.dec.deg, 6),
        "scale_arcsec_px": round(scale, 5),
        "rotation_deg": round(rotation, 4),
        "parity": parity,
        "sip_order": int(wcs.sip.a_order) if wcs.sip is not None else 0,
    }
    record.update(ast01_check(wcs, positions, gaia, scale))
    refit = refine_wcs_with_gaia(wcs, positions, gaia, (height, width))
    record.update({
        "refit_applied": refit.applied,
        "refit_reason": refit.reason,
        "refit_n": refit.star_count,
        "refit_rms_before_arcsec": refit.rms_before_arcsec and round(refit.rms_before_arcsec, 3),
        "refit_rms_after_arcsec": refit.rms_after_arcsec and round(refit.rms_after_arcsec, 3),
        "refit_shift_arcsec": refit.shift_arcsec and round(refit.shift_arcsec, 3),
    })
    if fwhm and refit.rms_before_arcsec is not None:
        star_width = fwhm * scale
        after = refit.rms_after_arcsec if refit.rms_after_arcsec is not None else refit.rms_before_arcsec
        record["before_fraction_of_fwhm"] = round(refit.rms_before_arcsec / star_width, 3)
        record["gate_fraction_of_fwhm"] = round(after / star_width, 3)
        limit = float(resolve(MAXIMUM_RESIDUAL_FRACTION_OF_FWHM).value)
        record["gate_pass"] = bool(
            record["gate_fraction_of_fwhm"] < limit and refit.star_count >= GATE_MINIMUM_STARS
        )
    return record


def compare(siril: tuple, solve_field: tuple, width: int, height: int) -> dict:
    """Return the Siril minus solve-field differences of two solutions.

    Parameters
    ----------
    siril, solve_field : `tuple`
        Center, scale, rotation, parity and WCS of each solution.
    width, height : `int`
        The image size in pixels.

    Returns
    -------
    differences : `dict`
        Center separation, scale and rotation differences, whether the
        parity matches, and the largest sky difference over a 5 by 5 grid.
    """
    grid_x, grid_y = np.meshgrid(np.linspace(0, width - 1, 5), np.linspace(0, height - 1, 5))
    sky_siril = siril[4].pixel_to_world(grid_x.ravel(), grid_y.ravel())
    sky_solve_field = solve_field[4].pixel_to_world(grid_x.ravel(), grid_y.ravel())
    largest = float(sky_siril.separation(sky_solve_field).arcsec.max())
    return {
        "center_sep_arcsec": round(float(siril[0].separation(solve_field[0]).arcsec), 3),
        "scale_diff_percent": round(float((siril[1] - solve_field[1]) / solve_field[1] * 100), 4),
        "rotation_diff_deg": round(float((siril[2] - solve_field[2] + 180) % 360 - 180), 4),
        "parity_same": siril[3] == solve_field[3],
        "grid_max_diff_arcsec": round(largest, 3),
        "grid_max_diff_px": round(largest / solve_field[1], 3),
    }


def main() -> None:
    """Judge all saved solutions of the frames given (all by default)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_study_argument(parser)
    parser.add_argument("--gaia-cache", help="The app's catalog cache database; copied once, never written.")
    parser.add_argument("ids", nargs="*")
    arguments = parser.parse_args()
    study = open_study(arguments.study)
    cache_copy = copy_gaia_cache(study, arguments.gaia_cache)

    from astrometricslib._internal.drivers import catalog_store

    # Read the copy, never the app's cache.
    catalog_store.get_catalog_cache_path = lambda config: cache_copy
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data

    manifest = study.read_manifest()
    with (
        open(study.out / "judge.jsonl", "a") as judge_log,
        open(study.out / "compare.jsonl", "a") as compare_log,
    ):
        for ident in arguments.ids or list(manifest):
            row = manifest[ident]
            solutions = {}
            for solver in ["solve-field", "siril"]:
                for path in sorted((study.out / "wcs" / solver).glob(f"{ident}__*.hdr")):
                    solutions[solver, path.stem.split("__")[1]] = read_wcs(path)
            if not solutions:
                print(ident, "no solutions")
                continue
            data, header, factor = load_frame(study, ident, row)
            fwhm_binned = measure_fwhm_from_data(data)
            fwhm = fwhm_binned * factor if fwhm_binned else None
            positions = centroids(data) * factor + (0.5 if factor == 2 else 0.0)
            height, width = row["NAXIS2"], row["NAXIS1"]
            reference = (
                solutions.get(("solve-field", "hint"))
                or solutions.get(("siril", "hint"))
                or next(iter(solutions.values()))
            )
            gaia, incomplete = gaia_for_field(reference, width, height, header)
            print(f"{ident}: {len(positions)} centroids, FWHM {fwhm} px, {len(gaia)} Gaia stars", flush=True)
            geometries = {}
            for (solver, mode), wcs in solutions.items():
                record = {"id": ident, "solver": solver, "mode": mode}
                record.update(judge_solution(wcs, positions, gaia, (height, width), fwhm))
                record.update({
                    "fwhm_px": fwhm and round(float(fwhm), 3),
                    "centroids": len(positions),
                    "gaia_stars": len(gaia),
                    "gaia_tiles_incomplete": incomplete,
                })
                geometries[solver, mode] = (*geometry(wcs, width, height), wcs)
                judge_log.write(json.dumps(record, default=str) + "\n")
                judge_log.flush()
            for mode in ["hint", "blind", "wrong2", "wrong5"]:
                # Siril has no blind solve; its hinted solution stands in.
                siril = geometries.get(("siril", "hint" if mode == "blind" else mode)) or geometries.get((
                    "siril",
                    "hint",
                ))
                solve_field = geometries.get(("solve-field", mode))
                if not siril or not solve_field:
                    continue
                differences = {"id": ident, "mode": mode, **compare(siril, solve_field, width, height)}
                compare_log.write(json.dumps(differences) + "\n")
                compare_log.flush()
                print("   compare", differences, flush=True)


if __name__ == "__main__":
    main()
