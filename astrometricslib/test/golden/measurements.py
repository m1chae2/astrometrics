"""Purpose: Measure the M 13 sample frames the way the golden suite pins them.

Description: Each ``measure_*`` function runs one library measurement on a
real sample frame and returns plain numbers, grouped by frame name. The
tests compare those numbers with ``golden_values.json``; the
``--update-golden`` option writes them there.

Only code that runs in pure Python is measured here. Anything that needs
Siril (stacking) or astrometry.net (plate solving) is left out, because
neither program is installed on the machines that run this suite.

This module also holds the tolerance rules (`tolerance_for`), so a new
pinned number gets a sensible tolerance without anyone typing it by hand.
"""

import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib.api.processing import QualityDiagnostics
from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import resolve_detector_noise
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    _measure_aperture_flux,
    _process_single_frame_worker,
    _read_exposure_seconds,
    measure_aperture_photometry,
)
from astrometricslib.pipelines.shared.quality.quality_metrics import measure_frame_input_quality
from astrometricslib.pipelines.shared.quality.spectral_frame_check import analyze_spectral_frame
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.stacking.processing.group_alignment import locate_zero_order
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

SAMPLE_FOLDER = (
    Path(__file__).resolve().parents[3]
    / "documentation"
    / "notebooks"
    / "astrometrics"
    / "sample_data"
    / "M 13"
)
"""Folder that holds the M 13 sample frames (Git LFS files)."""

LUMINANCE_FRAMES = tuple(f"M_13_Light_Luminance_{number:03d}" for number in range(19, 24))
"""The five 30 s luminance lights in time order (names without ``.fits``)."""

DETECTION_FRAME = "M_13_Light_Luminance_020"
"""The one luminance light used for source detection, FWHM and photometry."""

SPECTRAL_FRAME = "M_13_Light_Spectroscopy_024"
"""The one spectroscopy light used for the spectral frame check."""

REQUIRED_FRAMES = (*LUMINANCE_FRAMES, SPECTRAL_FRAME)
"""Every frame the suite reads. A Git LFS pointer among them skips it."""

LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
"""First bytes of a Git LFS pointer file (it stands in for the real frame)."""

BRIGHTEST_SOURCE_COUNT = 10
"""How many of the brightest detected sources are pinned."""

FWHM_STAR_COUNT = 5
"""How many of the brightest stars the FWHM fit uses."""

SEQUENCE_REFERENCE_FRAME = LUMINANCE_FRAMES[0]
"""The first light, which gives the star list and the alignment anchors."""

SEQUENCE_MEASURED_RANKS = (10, 70)
"""Detections ranked 10 up to (not including) 70 are the measured stars.

The ten brightest are skipped because they come closest to saturating. The
rule follows ``VariabilityAnalyzer``, whose star list also starts below the
brightest sources."""

SEQUENCE_ANCHOR_RANKS = (50, 100)
"""Detections ranked 50 up to (not including) 100 are the alignment anchors.

This is the slice ``VariabilityAnalyzer.process_sequence`` hands to the
worker as ``(x, y, flux)`` tuples."""

SEQUENCE_EDGE_MARGIN_PX = 50
"""Measured stars closer than this to a frame edge are dropped.

The worker looks for each star in a 40 pixel box, and a box that leaves the
frame gives no centroid, so the margin keeps every star measurable."""

CAMERA_NAME = "ZWO ASI533MM Pro"
"""Camera of the sample frames (the header says ``ZWO CCD ASI533MM Pro``)."""

CLUSTER_CORE_ROW_COLUMN = (1493.0, 1485.0)
"""Where the cluster core sits in the spectroscopy frames, as (row, column).

M 13 is a crowded cluster, so no single star is "the" zero-order star and the
library's finder rejects the frames (that rejection is itself pinned). The
point used here is the brightest spot of the frame smoothed with a Gaussian
of sigma 8 px, searched within 100 px of the frame centre. It is the same,
to a pixel, on all five spectroscopy frames.
"""

SPECTRAL_CONFIG_VALUES: dict[str, Any] = {
    "pixel_size_um": 3.76,
    "sensor_px": 3008,
    "sensor_min_wavelength": 300.0,
    "sensor_max_wavelength": 1000.0,
    "grating_distance_mm": 16.49,
    "grating_lines_per_mm": 200.0,
    "dispersion_start_px": 334.4,
    "max_extraction_length_px": 750.0,
}
"""Instrument settings for the spectral check, copied from the shipped config
template (``astrometrics.config.example.toml``, camera ``ZWO ASI533MM Pro``).
They are written out here so the pinned numbers do not depend on the
settings file of the machine that runs the test."""

_TOLERANCE_RULES: tuple[tuple[str, str, float], ...] = (
    (r"cv_percent$", "relative", 0.05),
    (
        r"(count|saturated_pixels|longest_trail_px|matched_stars|bands_found|_saturated|zero_order_found|zero_order_extended)$",
        "absolute",
        0.0,
    ),
    (r"(sky_median_adu|background_level_adu|sky_adu|global_background_adu)$", "absolute", 0.5),
    (r"(shift_[yx]_px|source_\d+_[xy]|tilt_degrees)$", "absolute", 0.05),
    (r"zero_order_(row|column)$", "absolute", 0.5),
    (r"(fwhm|flux|roundness|noise|fraction|width_px|contrast|peak|ratio)", "relative", 0.01),
)
"""Rules that give each pinned number its tolerance, first match wins.

The scatter of a light curve (``cv_percent``, the coefficient of variation
in percent) may move by 5 percent of its value, because it is a small number
built from noise. Counts must match exactly. Sky levels may move by 0.5 ADU
(analog-to-digital units, the camera's counts). Positions and the tilt may
move by 0.05 pixel or degree. FWHM values, fluxes and the other measured sizes
may move by 1 percent.
"""


def is_lfs_pointer(path: Path) -> bool:
    """Tell whether a file is a Git LFS pointer instead of the real frame.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to look at.

    Returns
    -------
    is_pointer : `bool`
        `True` if the file starts with the Git LFS pointer text.
    """
    with path.open("rb") as handle:
        return handle.read(len(LFS_POINTER_PREFIX)) == LFS_POINTER_PREFIX


def frame_path(name: str) -> Path:
    """Give the path of a sample frame.

    Parameters
    ----------
    name : `str`
        The file name without ``.fits``.

    Returns
    -------
    path : `pathlib.Path`
        The file inside `SAMPLE_FOLDER`.
    """
    return SAMPLE_FOLDER / f"{name}.fits"


def tolerance_for(metric: str) -> tuple[str, float]:
    """Choose the tolerance of a pinned number from its name.

    Parameters
    ----------
    metric : `str`
        The name of the number, such as ``"star_count"``.

    Returns
    -------
    tolerance_type : `str`
        ``"absolute"`` (same units as the number) or ``"relative"``
        (a fraction of the pinned value).
    tolerance : `float`
        The largest difference that still counts as a match.

    Raises
    ------
    KeyError
        If no rule covers the name. Add a rule on purpose rather than let a
        new number slip in with a guessed tolerance.
    """
    for pattern, tolerance_type, tolerance in _TOLERANCE_RULES:
        if re.search(pattern, metric):
            return tolerance_type, tolerance
    raise KeyError(f"No tolerance rule covers the pinned number {metric!r}; add one to _TOLERANCE_RULES.")


def load_frame(name: str) -> tuple[np.ndarray, fits.Header]:
    """Read a sample frame as a 2-D float array.

    Parameters
    ----------
    name : `str`
        The file name without ``.fits``.

    Returns
    -------
    data : `numpy.ndarray`
        The pixels as floats.
    header : `astropy.io.fits.Header`
        The primary header.
    """
    with fits.open(frame_path(name), memmap=False) as hdul:
        data = np.asarray(collapse_to_2d(np.asarray(hdul[0].data, dtype=float)))
        header = hdul[0].header.copy()
    return data, header


def _saturation_threshold_adu(header: fits.Header) -> float:
    """Look up the saturation level of the camera that took a frame.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The frame's header; ``INSTRUME`` names the camera.

    Returns
    -------
    threshold : `float`
        Pixels at or above this many ADU count as saturated.
    """
    return float(resolve_camera_profile(header.get("INSTRUME")).saturation_threshold_adu.value)


def measure_raw_frame_quality() -> dict[str, dict[str, float]]:
    """Measure the five luminance lights with the public quality API.

    The star count, FWHM, roundness, longest streak, sky level, saturated
    pixels, shift from the previous frame and flags come from
    ``QualityDiagnostics.frame_quality(kind="raw_check")``. The background
    level and saturated fraction come from `measure_frame_input_quality`. The
    background noise is the standard deviation of the sigma-clipped frame,
    computed the same way as the library's background level, because no
    library function reports it.

    Returns
    -------
    measurements : `dict` [`str`, `dict` [`str`, `float`]]
        One group per frame, plus a ``"batch"`` group of batch medians.
        The first frame has no shift numbers.
    """
    diagnostics = QualityDiagnostics(AppConfiguration(), MagicMock())
    report = diagnostics.frame_quality(
        folder_path=str(SAMPLE_FOLDER),
        kind="raw_check",
        filter_name="Luminance",
        first_file=LUMINANCE_FRAMES[0][-3:],
        last_file=LUMINANCE_FRAMES[-1][-3:],
        register_job=False,
    )
    measured: dict[str, dict[str, float]] = {}
    for row in report.frames:
        name = Path(row["path"]).stem
        values: dict[str, float] = {
            "star_count": row["star_count"],
            "fwhm_px": row["fwhm_px"],
            "roundness": row["roundness"],
            "longest_trail_px": row["longest_trail_px"],
            "sky_median_adu": row["sky_median_adu"],
            "saturated_pixels": row["saturated_pixels"],
            "flag_count": len(row["flags"]),
        }
        if row["shift_from_previous_px"] is not None:
            values["shift_y_px"], values["shift_x_px"] = row["shift_from_previous_px"]
            values["shift_matched_stars"] = row["shift_matched_stars"]
        data, header = load_frame(name)
        quality = measure_frame_input_quality(
            str(frame_path(name)), saturation_threshold_adu=_saturation_threshold_adu(header)
        )
        _, _, noise = sigma_clipped_stats(data, sigma=3.0)
        values["background_level_adu"] = quality["background_level"]
        values["background_noise_adu"] = float(noise)
        values["saturated_fraction"] = quality["saturated_pixel_fraction"]
        measured[name] = values
    measured["batch"] = {
        "median_star_count": report.batch["median_star_count"],
        "median_fwhm_px": report.batch["median_fwhm_px"],
        "flagged_frame_count": report.batch["flagged_count"],
    }
    return measured


def detect_sources(data: np.ndarray) -> list[dict[str, Any]]:
    """Find the stars in a frame with the astrometry source detector.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame.

    Returns
    -------
    sources : `list` [`dict`]
        The detected sources, brightest first.
    """
    return SourceDetector().detect(data)


def measure_source_detection(sources: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Summarize a detection: how many sources, and the brightest few.

    Parameters
    ----------
    sources : `Sequence` [`dict`]
        The output of `detect_sources`, brightest first.

    Returns
    -------
    values : `dict` [`str`, `float`]
        ``source_count`` and, for each of the brightest
        `BRIGHTEST_SOURCE_COUNT`, ``source_NN_x``, ``source_NN_y`` (pixels)
        and ``source_NN_flux``.
    """
    values: dict[str, float] = {"source_count": len(sources)}
    for rank, source in enumerate(sources[:BRIGHTEST_SOURCE_COUNT], start=1):
        values[f"source_{rank:02d}_x"] = float(source["x_centroid"])
        values[f"source_{rank:02d}_y"] = float(source["y_centroid"])
        values[f"source_{rank:02d}_flux"] = float(source["flux"])
    return values


def measure_fwhm(data: np.ndarray) -> dict[str, float]:
    """Fit the width of the brightest stars of a frame.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame.

    Returns
    -------
    values : `dict` [`str`, `float`]
        ``median_fwhm_px``: the median FWHM (full width at half maximum, the
        star's width across at half its peak height) of the
        `FWHM_STAR_COUNT` brightest stars the fit accepts.
    """
    return {"median_fwhm_px": float(measure_fwhm_from_data(data, n_stars=FWHM_STAR_COUNT))}


def measure_spectral_frame(data: np.ndarray, path: Path) -> dict[str, float]:
    """Measure one spectroscopy frame at the cluster core.

    The library's zero-order finder is run on the frame and its answer is
    pinned. The point-source search finds nothing in a crowded cluster, so
    the extended-target search answers, within a few pixels of the cluster
    core. The rest is measured at
    `CLUSTER_CORE_ROW_COLUMN` with the instrument settings of
    `SPECTRAL_CONFIG_VALUES`.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame.
    path : `pathlib.Path`
        The frame's file, which the streak-tilt measurement reads again.

    Returns
    -------
    values : `dict` [`str`, `float`]
        ``zero_order_found`` (1 if the finder found a target, else 0),
        ``zero_order_extended`` (1 if the extended-target search found it),
        the found ``zero_order_row`` and ``zero_order_column`` in pixels,
        the sky level, the zero-order and spectrum peaks, the peak-to-sky
        ratio, the cross-streak FWHM, the number of bands that held the
        streak, the saturated pixel count, the streak tilt and its contrast.
    """
    settings = SPECTRAL_CONFIG_VALUES
    camera = CameraConfig(
        name=CAMERA_NAME,
        pixel_size_um=settings["pixel_size_um"],
        sensor_width_px=settings["sensor_px"],
        sensor_height_px=settings["sensor_px"],
        sensor_min_wavelength=settings["sensor_min_wavelength"],
        sensor_max_wavelength=settings["sensor_max_wavelength"],
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_distance_mm=settings["grating_distance_mm"],
        grating_lines_per_mm=settings["grating_lines_per_mm"],
        dispersion_orientation="vertical",
        dispersion_direction="positive",
        dispersion_start_px=settings["dispersion_start_px"],
        max_extraction_length_px=settings["max_extraction_length_px"],
    )
    pipeline = SpectroscopyPipeline(config=config)
    row, column = CLUSTER_CORE_ROW_COLUMN
    found = locate_zero_order(data)
    frame = analyze_spectral_frame(
        data,
        (row, column),
        vertical=True,
        positive=True,
        offset_px=float(pipeline.instrument.zero_order_offset_px),
        length_px=float(pipeline.instrument.expected_length_px),
        exposure_seconds=30.0,
    )
    tilt, contrast = pipeline.measure_dispersion_trail(AstrometricsImage(str(path)), (column, row))
    return {
        "zero_order_found": 0 if found is None else 1,
        "zero_order_extended": 0 if found is None else int(found.is_extended_target),
        "zero_order_row": float("nan") if found is None else float(found.row),
        "zero_order_column": float("nan") if found is None else float(found.column),
        "sky_adu": frame["sky_adu"],
        "zero_order_peak_adu": frame["zero_order_peak_adu"],
        "spectrum_peak_above_sky_adu": frame["spectrum_peak_above_sky_adu"],
        "zero_order_peak_to_sky_ratio": frame["zero_order_peak_adu"] / frame["sky_adu"],
        "spectrum_width_px": frame["spectrum_width_px"],
        "streak_bands_found": frame["streak_bands_found"],
        "saturated_pixels": frame["saturated_pixels"],
        "tilt_degrees": float(tilt),
        "trail_contrast": float(contrast),
    }


def measure_photometry(
    data: np.ndarray, header: fits.Header, sources: Sequence[dict[str, Any]], whole_pixel_centres: bool
) -> dict[str, float]:
    """Run the pipeline's aperture photometry on the brightest sources.

    This calls `_measure_aperture_flux`, the one function that does the
    aperture sum, at each detected position. Fluxes are in ADU per second.

    Before item S2 of the review plan, the function rounded every position
    to a whole pixel. Rounding the position here reproduces that behaviour
    with the current code, so the ``photometry_pre_S2`` pins keep guarding
    the aperture sum and the background ring. With
    ``whole_pixel_centres=False`` the apertures sit on the exact detected
    position, which is what the pipeline does after S2.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame.
    header : `astropy.io.fits.Header`
        The frame's header, for the exposure time and the camera's
        saturation level.
    sources : `Sequence` [`dict`]
        The output of `detect_sources`, brightest first.
    whole_pixel_centres : `bool`
        If `True`, round each position to a whole pixel first.

    Returns
    -------
    values : `dict` [`str`, `float`]
        For each of the brightest `BRIGHTEST_SOURCE_COUNT` sources,
        ``star_NN_flux_adu_per_s`` and ``star_NN_saturated`` (1 if the
        aperture holds saturated pixels).
    """
    exposure_seconds = _read_exposure_seconds(header)
    threshold = _saturation_threshold_adu(header)
    values: dict[str, float] = {}
    for rank, source in enumerate(sources[:BRIGHTEST_SOURCE_COUNT], start=1):
        x, y = float(source["x_centroid"]), float(source["y_centroid"])
        if whole_pixel_centres:
            x, y = float(round(x)), float(round(y))
        flux, saturated = _measure_aperture_flux(data, x, y, saturation_threshold_adu=threshold)
        values[f"star_{rank:02d}_flux_adu_per_s"] = flux / exposure_seconds
        values[f"star_{rank:02d}_saturated"] = int(bool(saturated))
    return values


def measure_photometry_sequence() -> dict[str, float]:
    """Run the photometry worker over the five luminance lights.

    The steps copy ``VariabilityAnalyzer.process_sequence``. The first light
    is searched for stars with ``SourceDetector`` (3 sigma, FWHM 4 px, the
    analyzer's first setting). Detections in `SEQUENCE_MEASURED_RANKS` that
    sit at least `SEQUENCE_EDGE_MARGIN_PX` from every edge are the measured
    stars. Detections in `SEQUENCE_ANCHOR_RANKS` become the ``(x, y, flux)``
    anchors that line the later lights up with the first. The first light is
    measured in this process, as the analyzer does. Lights 020 to 023 go
    through ``_process_single_frame_worker``, one after the other.

    Returns
    -------
    values : `dict` [`str`, `float`]
        ``frame_NNN_shift_x_px`` and ``frame_NNN_shift_y_px``: how far the
        light moved against the first one. ``measured_star_count`` and
        ``unsaturated_star_count``: how many stars were measured, and how
        many stayed below saturation in all five lights.
        ``median_star_cv_percent``: the median over the unsaturated stars of
        the standard deviation of the five fluxes divided by their mean
        (the coefficient of variation, CV), in percent.

    Raises
    ------
    RuntimeError
        If the worker returns no measurement for a light.
    """
    reference, header = load_frame(SEQUENCE_REFERENCE_FRAME)
    threshold = _saturation_threshold_adu(header)
    noise = resolve_detector_noise(resolve_camera_profile(header.get("INSTRUME")), header)
    sources = SourceDetector(threshold_sigma=3.0, fwhm=4.0).detect(reference)
    height, width = reference.shape
    margin = SEQUENCE_EDGE_MARGIN_PX
    stars = [
        (f"star_{rank}", float(source["x_centroid"]), float(source["y_centroid"]))
        for rank, source in enumerate(sources[: SEQUENCE_MEASURED_RANKS[1]])
        if rank >= SEQUENCE_MEASURED_RANKS[0]
        and margin <= source["x_centroid"] < width - margin
        and margin <= source["y_centroid"] < height - margin
    ]
    anchors = [
        (float(source["x_centroid"]), float(source["y_centroid"]), float(source["flux"]))
        for source in sources[slice(*SEQUENCE_ANCHOR_RANKS)]
    ]
    exposure_seconds = _read_exposure_seconds(header)
    fluxes = [[], [], [], [], []]
    saturated = [[], [], [], [], []]
    for _, x, y in stars:
        measurement = measure_aperture_photometry(
            reference, x, y, saturation_threshold_adu=threshold, noise=noise
        )
        fluxes[0].append(measurement.net_flux_adu / exposure_seconds)
        saturated[0].append(measurement.is_saturated)
    values: dict[str, float] = {}
    for index, name in enumerate(LUMINANCE_FRAMES[1:], start=1):
        _, result = _process_single_frame_worker((str(frame_path(name)), stars, anchors, threshold, noise))
        if not isinstance(result, tuple):
            raise RuntimeError(f"The photometry worker returned no measurement for {name}: {result!r}")
        star_fluxes, shift_x, shift_y = result[1], result[2], result[3]
        values[f"frame_{name[-3:]}_shift_x_px"] = float(shift_x)
        values[f"frame_{name[-3:]}_shift_y_px"] = float(shift_y)
        fluxes[index] = [star_fluxes[star_id][0] for star_id, _, _ in stars]
        saturated[index] = [star_fluxes[star_id][1] for star_id, _, _ in stars]
    flux_table = np.asarray(fluxes, dtype=float)
    unsaturated = ~np.asarray(saturated, dtype=bool).any(axis=0)
    kept = flux_table[:, unsaturated]
    variation = kept.std(axis=0, ddof=1) / kept.mean(axis=0)
    values["measured_star_count"] = len(stars)
    values["unsaturated_star_count"] = int(unsaturated.sum())
    values["median_star_cv_percent"] = float(100.0 * np.median(variation))
    return values


def within_tolerance(measured: float, pinned: float, tolerance_type: str, tolerance: float) -> bool:
    """Compare a measured number with a pinned one.

    Parameters
    ----------
    measured : `float`
        The number the code gives now.
    pinned : `float`
        The pinned number.
    tolerance_type : `str`
        ``"absolute"`` or ``"relative"``.
    tolerance : `float`
        The allowed difference (a fraction of `pinned` when relative).

    Returns
    -------
    matches : `bool`
        `True` if the two agree within the tolerance.
    """
    allowed = tolerance * abs(pinned) if tolerance_type == "relative" else tolerance
    return math.isfinite(measured) and abs(measured - pinned) <= allowed
