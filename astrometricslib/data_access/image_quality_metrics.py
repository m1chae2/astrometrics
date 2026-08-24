"""Purpose: Post-Stack Image Quality Measurements.

Description: Pure measurement functions used both by the empirical analysis
scripts (astrometricslib/scripts/target_stacking_and_analysis/) and by the
production stacking pipeline's post-stack validation (StackQualitySummary).
Originally lived only in rejection_threshold_analysis.py; relocated here once
stacking_operations.py needed the same measurements for real-time quality
checks, not just offline sweeps.
"""

import logging
import os
import re

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib.tasks.shared.saturation_analysis import compute_saturated_pixel_fraction

logger = logging.getLogger(__name__)

FWHM_MEASUREMENT_BOX_RADIUS_PX = 15
FWHM_MEASUREMENT_STAR_COUNT = 15

# Just under the raw 16-bit unsigned max (65535) -- see
# measure_saturated_pixel_fraction's docstring for how this was chosen and its
# caveats.
DEFAULT_SATURATION_ADU_THRESHOLD = 65000.0

# Photometric linearity threshold for 14-bit CMOS sensors mapped to 16-bit ADC
# (e.g. ZWO ASI533MM Pro). Non-linear compression begins above ~60,000 ADU at
# Gain 0 / Offset 10 before hard clipping (Antonov 2026).
DEFAULT_PHOTOMETRIC_LINEARITY_ADU_THRESHOLD = 60000.0


_REGISTRATION_LINE_PATTERN = re.compile(r"^R(\d+) ")


def parse_seq_file(seq_path: str) -> list[dict[str, float]]:
    """Parse registration-summary lines from a Siril .seq file.

    Returns one dict per frame. Column layout confirmed against real
    registration runs: R{layer} fwhm_x fwhm_y roundness <unused> rmse nb_stars
    H <3x3 transform matrix, row-major> where dx/dy are the matrix's
    translation terms (indices 10 and 13). Each line corresponds to one input
    frame in original submission order, computed by Siril's own findstar pass
    during registration -- this is how median input FWHM is obtained without a
    second, separate measurement pass over the raw input frames.

    The registration layer number varies: a mono/uncalibrated sequence ("L 1")
    writes "R0" lines, but a calibrated, debayered color sequence ("L 3")
    writes only "R1" lines (the green channel, Siril's usual CFA
    quality-reference channel) with no R0 at all -- confirmed by a real
    mismatch found when this only matched "R0 " literally and silently parsed 0
    frames from a real 3-layer M 13 stack. Only one registration layer is ever
    present per file in practice, so matching whichever "R<n> " prefix appears
    is correct rather than assuming a fixed layer index.

    Parameters
    ----------
    seq_path : `str`
        Path to the Siril .seq file to parse.

    Returns
    -------
    frames : `list` of `dict`
        One dict per registered frame, in original submission order, with keys
        "fwhm_x", "fwhm_y", "roundness", "rmse", "nb_stars", "dx", and "dy".
        Empty if the file does not exist.
    """
    frames = []
    if not os.path.exists(seq_path):
        return frames
    with open(seq_path) as seq_file:
        for line in seq_file:
            if not _REGISTRATION_LINE_PATTERN.match(line):
                continue
            parts = line.split()
            frames.append({
                "fwhm_x": float(parts[1]),
                "fwhm_y": float(parts[2]),
                "roundness": float(parts[3]),
                "rmse": float(parts[5]),
                "nb_stars": int(parts[6]),
                "dx": float(parts[10]),
                "dy": float(parts[13]),
            })
    return frames


def parse_zero_order_star(lst_path: str) -> dict[str, float] | None:
    """Get the brightest star's stats from a Siril per-frame .lst list.

    Siril writes star-list rows sorted by descending amplitude (A), so the
    first data row is the brightest detected point source -- the SA200
    zero-order star in a typical frame. Column layout: star# layer B A beta X Y
    FWHMx[px] FWHMy[px] FWHMx["] FWHMy["] angle RMSE ...

    Amplitude/background are NOT directly comparable across frames: Siril's
    per-frame findstar pass reads the chosen reference frame at native ADU
    scale but reads every other frame from a normalized float32 (0-1) working
    buffer, so raw amplitude jumps by ~4 orders of magnitude between the
    reference frame and the rest (confirmed empirically against a real M 13
    SA200 session). peak_to_background_ratio (amplitude/background) cancels
    that per-frame normalization factor and is what's actually comparable.

    Parameters
    ----------
    lst_path : `str`
        Path to the Siril per-frame .lst star list.

    Returns
    -------
    result : `dict` or `None`
        Stats for the brightest star, with keys "background", "amplitude",
        "peak_to_background_ratio", "x", "y", "fwhm_x_px", "fwhm_y_px", and
        "rmse". `None` if the file does not exist or has no data rows.
    """
    if not os.path.exists(lst_path):
        return None
    with open(lst_path) as lst_file:
        for line in lst_file:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.split("\t")
            background = float(fields[2])
            amplitude = float(fields[3])
            return {
                "background": background,
                "amplitude": amplitude,
                "peak_to_background_ratio": amplitude / background if background else None,
                "x": float(fields[5]),
                "y": float(fields[6]),
                "fwhm_x_px": float(fields[7]),
                "fwhm_y_px": float(fields[8]),
                "rmse": float(fields[12]),
            }
    return None


def measure_image_fwhm(path: str, n_stars: int = FWHM_MEASUREMENT_STAR_COUNT) -> float | None:
    """Get the median FWHM (pixels) of the brightest stars in a FITS image.

    Detects sources with SourceDetector, then for the n_stars brightest
    measures FWHM in a small cutout around each centroid via
    photutils.morphology.data_properties after a sigma-clipped background
    subtraction, and returns the median across the successfully measured stars.

    Parameters
    ----------
    path : `str`
        Path to the FITS image to measure.
    n_stars : `int`, optional
        Maximum number of brightest detected stars to measure. Defaults to
        `FWHM_MEASUREMENT_STAR_COUNT`.

    Returns
    -------
    fwhm : `float` or `None`
        Median FWHM in pixels across the measured stars. `None` if the image
        has no data, no sources are detected, or no star cutout yields a
        finite, positive FWHM.
    """
    from photutils.morphology import data_properties

    from astrometricslib.tasks.shared.source_detection_shared import SourceDetector

    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    data = np.asarray(data, dtype=float)
    if data.ndim == 3:
        data = np.mean(data, axis=0 if data.shape[0] in (3, 4) else -1)

    sources = SourceDetector().detect(data)
    if not sources:
        return None

    box = FWHM_MEASUREMENT_BOX_RADIUS_PX
    fwhms = []
    for source in sources[:n_stars]:
        x = source.get("x_centroid", source.get("xcentroid"))
        y = source.get("y_centroid", source.get("ycentroid"))
        if x is None or y is None:
            continue
        x, y = round(x), round(y)
        y0, y1 = max(0, y - box), min(data.shape[0], y + box)
        x0, x1 = max(0, x - box), min(data.shape[1], x + box)
        cutout = data[y0:y1, x0:x1]
        if cutout.size == 0:
            continue
        try:
            _, median, _ = sigma_clipped_stats(cutout, sigma=3.0)
            fwhm = float(data_properties(cutout - median).fwhm.value)
            if np.isfinite(fwhm) and fwhm > 0:
                fwhms.append(fwhm)
        except Exception as exc:
            logger.debug("Skipping FWHM measurement for one star cutout: %s", exc)
            continue

    return float(np.median(fwhms)) if fwhms else None


def measure_frame_input_quality(path: str, include_fwhm: bool = False) -> dict[str, float | None]:
    """Measure a raw frame's own quality from a single file read.

    Answers "is this frame worth stacking?" *before* anything is stacked.
    The existing per-frame numbers on `FrameRecord` are all written during
    registration, so until now a frame that was never stacked carried no
    quality evidence at all -- exactly the frames a person most wants to
    triage. Measured on the 2026-08-23 catalog: only 238 of 4,244 frames
    (5.6%) had any.

    Every metric is derived from one `fits.open`, because reading the
    frame dominates the cheap measurements: on a cold cache a large frame
    took ~4s to read while the median and saturation count together took
    ~0.3s. `measure_frame_background_level` and
    `measure_frame_saturated_pixel_fraction` each reopen the file
    independently; this pays that cost once.

    Parameters
    ----------
    path : `str`
        Filesystem path to the FITS frame to measure.
    include_fwhm : `bool`, optional
        Whether to also measure FWHM (default `False`). Off by default
        because it is a different order of cost: FWHM runs full source
        detection plus a per-star morphology fit, ~16s per frame against
        ~0.3s for everything else here -- roughly 50x. Enable it for a
        focused review of one target, not a whole-catalog sweep.

    Returns
    -------
    metrics : `dict` [`str`, `float` or `None`]
        Keys ``background_level``, ``saturated_pixel_fraction``, and
        ``fwhm_px``. A value is `None` when that metric could not be
        measured; ``fwhm_px`` is `None` whenever `include_fwhm` is
        `False`. An unreadable frame yields all-`None` rather than
        raising, so one bad frame cannot abort a sweep.
    """
    metrics: dict[str, float | None] = {
        "background_level": None,
        "saturated_pixel_fraction": None,
        "fwhm_px": None,
    }

    try:
        with fits.open(path, memmap=False) as hdul:
            data = hdul[0].data
            if data is None and len(hdul) > 1:
                data = hdul[1].data
            if data is None:
                return metrics
            data = np.asarray(data, dtype=float)
    except Exception as read_error:
        logger.debug("Could not read %s for input-quality measurement: %s", path, read_error)
        return metrics

    # Matches the mono-flattening the existing per-frame measurements use,
    # so a value measured here is comparable with one measured there.
    if data.ndim == 3:
        data = np.mean(data, axis=0 if data.shape[0] in (3, 4) else -1)

    try:
        _, median, _ = sigma_clipped_stats(data, sigma=3.0)
        metrics["background_level"] = float(median)
    except Exception as background_error:
        logger.debug("Background measurement failed for %s: %s", path, background_error)

    try:
        metrics["saturated_pixel_fraction"] = compute_saturated_pixel_fraction(
            data, DEFAULT_SATURATION_ADU_THRESHOLD
        )
    except Exception as saturation_error:
        logger.debug("Saturation measurement failed for %s: %s", path, saturation_error)

    if include_fwhm:
        try:
            metrics["fwhm_px"] = _measure_fwhm_from_data(data)
        except Exception as fwhm_error:
            logger.debug("FWHM measurement failed for %s: %s", path, fwhm_error)

    return metrics


def _measure_fwhm_from_data(data: np.ndarray, n_stars: int = FWHM_MEASUREMENT_STAR_COUNT) -> float | None:
    """Measure median stellar FWHM from already-loaded frame data.

    The measurement half of `measure_image_fwhm`, split out so a caller
    that has already read the frame does not read it a second time.

    Parameters
    ----------
    data : `numpy.ndarray`
        Frame pixels, already flattened to 2D.
    n_stars : `int`, optional
        Maximum number of brightest detected stars to measure.

    Returns
    -------
    fwhm : `float` or `None`
        Median FWHM in pixels, or `None` if no star yields a finite,
        positive measurement.
    """
    from photutils.morphology import data_properties

    from astrometricslib.tasks.shared.source_detection_shared import SourceDetector

    sources = SourceDetector().detect(data)
    if not sources:
        return None

    box = FWHM_MEASUREMENT_BOX_RADIUS_PX
    fwhms = []
    for source in sources[:n_stars]:
        x = source.get("x_centroid", source.get("xcentroid"))
        y = source.get("y_centroid", source.get("ycentroid"))
        if x is None or y is None:
            continue
        x, y = round(x), round(y)
        y0, y1 = max(0, y - box), min(data.shape[0], y + box)
        x0, x1 = max(0, x - box), min(data.shape[1], x + box)
        cutout = data[y0:y1, x0:x1]
        if cutout.size == 0:
            continue
        try:
            _, median, _ = sigma_clipped_stats(cutout, sigma=3.0)
            fwhm = float(data_properties(cutout - median).fwhm.value)
            if np.isfinite(fwhm) and fwhm > 0:
                fwhms.append(fwhm)
        except Exception as exc:
            logger.debug("Skipping FWHM measurement for one star cutout: %s", exc)
            continue

    return float(np.median(fwhms)) if fwhms else None


def measure_saturated_pixel_fraction(
    path: str, saturation_threshold: float = DEFAULT_SATURATION_ADU_THRESHOLD
) -> float | None:
    """Get the fraction of pixels at or above saturation_threshold.

    saturation_threshold defaults to just under the raw 16-bit max (confirmed
    against a real ZWO ASI 533MM Pro light frame: dtype uint16,
    BZERO=32768/BSCALE=1, max observed 65532) -- no SATLEVEL header keyword or
    per-camera bit-depth config exists in this codebase to derive it from
    instead. Applied here to the final *stacked* image, which is a 32-bit float
    after Siril's addscale normalization, so this is an approximation of the
    original ADU scale, not an exact per-frame saturation measurement -- not
    yet validated against a frame with known saturated stars.

    Parameters
    ----------
    path : `str`
        Path to the FITS image to measure.
    saturation_threshold : `float`, optional
        ADU value at or above which a pixel is considered saturated. Defaults
        to `DEFAULT_SATURATION_ADU_THRESHOLD`.

    Returns
    -------
    fraction : `float` or `None`
        Fraction of pixels at or above saturation_threshold. `None` if the
        image has no data.
    """
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    return compute_saturated_pixel_fraction(np.asarray(data, dtype=float), saturation_threshold)


def measure_rejected_fraction(stacked_path: str) -> float | None:
    """Get the mean per-pixel rejected-frame fraction from the rejmap.

    Reads the sibling _RejMap.fits file. siril_interface.py copies the -rejmap
    output out next to the stacked file (appending "_RejMap" to the stem)
    before it deletes its scratch work directory. Each rejmap pixel already
    holds (frames rejected at that pixel / frames stacked at that pixel), so
    the mean over the map -- not a nonzero count -- is the actual mean rejected
    fraction.

    Parameters
    ----------
    stacked_path : `str`
        Path to the stacked FITS file. The sibling rejmap path is derived by
        appending "_RejMap.fits" to its stem.

    Returns
    -------
    fraction : `float` or `None`
        Mean rejected-pixel fraction over the rejmap. `None` if the sibling
        rejmap file does not exist or has no data.
    """
    rejmap_path = os.path.splitext(stacked_path)[0] + "_RejMap.fits"
    if not os.path.exists(rejmap_path):
        return None
    with fits.open(rejmap_path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    return float(np.mean(data))
