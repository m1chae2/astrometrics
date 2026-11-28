"""Purpose: Measure how few stars Siril's plate solver needs to center.

Description: Mount centering solves a short frame from the header's position
and scale. This script finds the least the solver needs. It takes real library
frames (read only) and makes two kinds of weaker copies of each:

* "exposure" copies: a shorter exposure made by thinning the frame's light.
  Each electron is kept with probability f (the exposure ratio), so the shot
  noise of the shorter frame is right, and the read noise is topped up to its
  full size.
* "stars" copies: a frame of pure sky noise (the frame's own sky level and
  noise) with only the N brightest detected stars of the real frame pasted in.
  This separates the star count from the depth.

Each copy is counted with a 5-sigma star finder, then solved with the app's
own Siril operation (`solve_image`) from the header's position, as centering
does. A solve counts as good when its center is within 3 pixels and its scale
within 1 percent of the solution of the full frame. One JSON line per copy goes
to <study>/out/solver_needs.jsonl.

Run on copies only, through ``build/agent_tools/realrun.sh`` (see its README);
the script must sit inside ASTROMETRICS_SCRATCH. Usage::

    solver_needs.py --study <folder> [--only <label>]
        [--exposure-ratios ...] [--star-counts ...]
"""

import argparse
import json
import math
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.exceptions import AstrometricsError, ConfigurationError

warnings.simplefilter("ignore")

LIBRARY = Path("/home/michael/Astrometrics/Library/lights")
SIRIL = "flatpak run org.siril.Siril"

FRAMES = {
    "zwo_m13": "M 13/Apertura 75Q/ZWO ASI 533MM Pro/M_13_Light_Luminance_003.fits",
    "zwo_m31": "M 31/Apertura 75Q/ZWO ASI 533MM Pro/M_31_Light_Luminance_003.fits",
    "zwo_m81": "M 81/Apertura 75Q/ZWO ASI 533MM Pro/M_81_Light_Luminance_002.fits",
    "zwo_n2403": "NGC 2403/Apertura 75Q/ZWO ASI 533MM Pro/NGC_2403_Light_Luminance_002.fits",
    "nikon_m13": "M 13/Apertura 75Q/Nikon DSLR DSC D5300/M13_Light_001.fits",
    "nikon_m81": "M 81/Apertura 75Q/Nikon DSLR DSC D5300/M 81_Light_004.fits",
    "nikon_m101": "M 101/Apertura 75Q/Nikon DSLR DSC D5300/M101_Light_004.fits",
}
EXPOSURE_RATIOS = [1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125, 0.015625]
STAR_COUNTS = [400, 200, 100, 60, 40, 30, 25, 20, 15, 12, 10, 8, 6]
DETECTION_SIGMA = 5.0
EXPECTED_FWHM_PX = 4.0
STAMP_RADIUS = 12
CENTER_TOLERANCE_PX = 3.0
SCALE_TOLERANCE = 0.01


def detect(pixels: np.ndarray) -> tuple[Any, float, float]:
    """Find stars with the 5-sigma finder of the centering-exposure check.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The frame.

    Returns
    -------
    table, median, sigma : `tuple`
        The photutils table (or `None`), the sky median and the noise.
    """
    from astropy.stats import sigma_clipped_stats
    from photutils.detection import DAOStarFinder

    _, median, std = sigma_clipped_stats(pixels[::4, ::4], sigma=3.0)
    finder = DAOStarFinder(fwhm=EXPECTED_FWHM_PX, threshold=DETECTION_SIGMA * float(std))
    return finder(pixels.astype(np.float64) - float(median)), float(median), float(std)


def sensor_numbers(camera: str) -> tuple[float, float]:
    """Read gain (e/ADU) and read noise (e) from the camera profile.

    Parameters
    ----------
    camera : `str`
        The camera name in the header.

    Returns
    -------
    gain, read_noise : `tuple`
        Electrons per ADU and electrons.
    """
    try:
        from astrometricslib import Astrometrics
        from wayfindinglib.analytics.camera_profiles import profile_for_name

        profile = profile_for_name(Astrometrics(), camera)
        for gain_setting in (0, 100, 800, None):
            entry = profile.sensor_noise_for(gain_setting, 10, 1, clipped_at_zero=False)
            if entry is not None and entry.gain_e_per_adu is not None and entry.read_noise_e is not None:
                return float(entry.gain_e_per_adu.value), float(entry.read_noise_e.value)
    except (AstrometricsError, ConfigurationError, AttributeError) as error:
        print("profile lookup failed:", error, file=sys.stderr)
    return 1.0, 3.0


def thin(
    pixels: np.ndarray, ratio: float, gain: float, read_noise_e: float, rng: np.random.Generator
) -> np.ndarray:
    """Make the frame as a shorter exposure would have shown it.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The frame, in ADU.
    ratio : `float`
        The new exposure over the old one, at most 1.
    gain, read_noise_e : `float`
        Electrons per ADU and read noise in electrons.
    rng : `numpy.random.Generator`
        The random source.

    Returns
    -------
    thinned : `numpy.ndarray`
        The thinned frame (float32), with the bias level kept.
    """
    if ratio >= 1.0:
        return pixels.astype(np.float32)
    bias = float(np.percentile(pixels[::4, ::4], 0.1))
    electrons = np.clip(np.rint((pixels - bias) * gain), 0, None).astype(np.int64)
    kept = rng.binomial(electrons, ratio)
    top_up = read_noise_e * math.sqrt(max(1.0 - ratio * ratio, 0.0))
    out = bias + (kept + rng.normal(0.0, top_up, pixels.shape)) / gain
    return out.astype(np.float32)


def sky_with_stars(pixels: np.ndarray, count: int, rng: np.random.Generator) -> np.ndarray:
    """Make a sky-noise frame holding only the brightest stars of a real one.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The real frame.
    count : `int`
        How many of its brightest detected stars to keep.
    rng : `numpy.random.Generator`
        The random source.

    Returns
    -------
    frame : `numpy.ndarray`
        The cut-down frame (float32).
    """
    table, median, sigma = detect(pixels)
    out = rng.normal(median, sigma, pixels.shape).astype(np.float32)
    if table is None:
        return out
    table.sort("flux")
    x_name, y_name = (
        ("x_centroid", "y_centroid") if "x_centroid" in table.colnames else ("xcentroid", "ycentroid")
    )
    height, width = pixels.shape
    for row in table[::-1][:count]:
        x, y = round(row[x_name]), round(row[y_name])
        inside_x = STAMP_RADIUS <= x < width - STAMP_RADIUS
        inside_y = STAMP_RADIUS <= y < height - STAMP_RADIUS
        if inside_x and inside_y:
            window = (
                slice(y - STAMP_RADIUS, y + STAMP_RADIUS + 1),
                slice(x - STAMP_RADIUS, x + STAMP_RADIUS + 1),
            )
            out[window] = pixels[window]
    return out


def write_frame(path: Path, pixels: np.ndarray, header: fits.Header, exposure_s: float) -> None:
    """Write a variant frame with the original's hint cards.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write.
    pixels : `numpy.ndarray`
        The pixels.
    header : `astropy.io.fits.Header`
        The original header.
    exposure_s : `float`
        The exposure to record.
    """
    new = fits.Header()
    for card in header.cards:
        if card.keyword in (
            "SIMPLE",
            "BITPIX",
            "NAXIS",
            "NAXIS1",
            "NAXIS2",
            "NAXIS3",
            "EXTEND",
            "BZERO",
            "BSCALE",
            "COMMENT",
            "HISTORY",
            "",
        ) or card.keyword.startswith((
            "CRPIX",
            "CRVAL",
            "CD",
            "PC",
            "CDELT",
            "CTYPE",
            "CUNIT",
            "A_",
            "B_",
            "AP_",
            "BP_",
            "WCSAXES",
            "LONPOLE",
            "LATPOLE",
            "PLTSOLVD",
        )):
            continue
        new[card.keyword] = card.value
    new["EXPTIME"] = exposure_s
    fits.PrimaryHDU(pixels, header=new).writeto(path, overwrite=True)


def solution_summary(solved: fits.Header, nx: int, ny: int) -> tuple[float, float, float]:
    """Give the center and scale of a solution.

    Parameters
    ----------
    solved : `astropy.io.fits.Header`
        The solved header.
    nx, ny : `int`
        The image size.

    Returns
    -------
    ra, dec, scale : `tuple`
        Center in degrees and the scale in arcseconds per pixel.
    """
    wcs = WCS(solved)
    ra, dec = wcs.all_pix2world(nx / 2, ny / 2, 0)
    scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))) * 3600.0)
    return float(ra), float(dec), scale


def judge(reference: fits.Header | None, solved: fits.Header | None, nx: int, ny: int) -> dict:
    """Compare a variant's solution with the full frame's.

    Parameters
    ----------
    reference, solved : `astropy.io.fits.Header` or `None`
        The two solved headers.
    nx, ny : `int`
        The image size.

    Returns
    -------
    result : `dict`
        ``solved``, ``good``, and the center offset in pixels and scale ratio.
    """
    if solved is None:
        return {"solved": False, "good": False}
    ra0, dec0, scale0 = solution_summary(reference, nx, ny)
    ra1, dec1, scale1 = solution_summary(solved, nx, ny)
    separation = math.hypot((ra1 - ra0) * math.cos(math.radians(dec0)), dec1 - dec0) * 3600.0
    center_px = separation / scale0
    ratio = scale1 / scale0
    good = center_px <= CENTER_TOLERANCE_PX and abs(ratio - 1.0) <= SCALE_TOLERANCE
    return {
        "solved": True,
        "good": bool(good),
        "center_offset_px": round(center_px, 2),
        "scale_ratio": round(ratio, 4),
        "matched": getattr(solved, "matched_star_count", None),
    }


def main() -> None:
    """Run the sweep."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--study", required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--exposure-ratios", nargs="*", type=float, default=EXPOSURE_RATIOS)
    parser.add_argument("--star-counts", nargs="*", type=int, default=STAR_COUNTS)
    parser.add_argument("--repeats", type=int, default=1)
    arguments = parser.parse_args()
    study = Path(arguments.study).resolve()
    if (study / "lights").is_dir():
        sys.exit("Refusing: the study folder looks like a library.")
    (study / "out").mkdir(parents=True, exist_ok=True)
    (study / "frames").mkdir(exist_ok=True)
    from astrometricslib._internal.drivers.siril.astrometry.plate_solve import solve_image

    rng = np.random.default_rng(182)
    log = open(study / "out" / "solver_needs.jsonl", "a")
    for label, relative in FRAMES.items():
        if arguments.only and label not in arguments.only:
            continue
        source = LIBRARY / relative
        with fits.open(source, memmap=False) as hdus:
            pixels = np.asarray(hdus[0].data, dtype=np.float64)
            header = hdus[0].header.copy()
        ny, nx = pixels.shape
        exposure = float(header["EXPTIME"])
        gain, read_noise = sensor_numbers(str(header.get("INSTRUME", "")))
        work = study / "frames" / f"{label}.fits"
        write_frame(work, pixels.astype(np.float32), header, exposure)
        reference = solve_image(str(work), SIRIL, {})
        if reference is None:
            print(label, "reference did not solve", flush=True)
            continue
        print(label, "reference solved", gain, read_noise, flush=True)
        variants = [("exposure", ratio) for ratio in arguments.exposure_ratios] + [
            ("stars", count) for count in arguments.star_counts
        ]
        for kind, value in variants:
            for repeat in range(arguments.repeats):
                if kind == "exposure":
                    data, shown = thin(pixels, value, gain, read_noise, rng), exposure * value
                else:
                    data, shown = sky_with_stars(pixels, value, rng), exposure
                table, _, _ = detect(data.astype(np.float64))
                detected = 0 if table is None else len(table)
                write_frame(work, data, header, shown)
                started = time.time()
                solved = solve_image(str(work), SIRIL, {})
                row = {
                    "frame": label,
                    "kind": kind,
                    "value": value,
                    "repeat": repeat,
                    "exposure_s": shown,
                    "detected": detected,
                    "seconds": round(time.time() - started, 1),
                    **judge(reference, solved, nx, ny),
                }
                log.write(json.dumps(row) + "\n")
                log.flush()
                print(row, flush=True)
        work.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
