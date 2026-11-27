"""Purpose: Measure a raw slitless-spectrum frame and its saturation.

Description: A slitless spectrum frame holds a bright star (the zero order)
and a streak of light running away from it (the spectrum). Whether a frame
is useful depends on where it clips: a clipped zero order is expected and
harmless, but a clipped patch inside the spectrum loses real data. This
module measures, for one frame, where the zero order is, how tilted the
streak is, how wide it is across, how bright it is, and where any
saturated pixels sit measured along the streak from the zero order. From
the brightness it can also predict the peak at another exposure length.

The measuring is split in two. `analyze_spectral_frame` works on a loaded
array and is plain arithmetic, so it can be tested on made-up streaks.
`measure_spectral_frame_file` reads a file, finds the zero order and the
tilt with the same code the stacking stage uses, and calls it. A caller who
already knows where the zero order is can pass that position and skip the
search. The search also handles extended targets such as globular clusters,
and the result says which kind of search found the position.

The saturation level comes from the camera's profile (a 14-bit camera clips
near 16383 ADU, far below a 16-bit camera's 65535 ADU), and the result
records where the level came from.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy import ndimage

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.errors import AstrometricsError, ProcessingError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.saturation import (
    SATURATION_ADU,
    resolve_saturation_threshold,
)
from astrometricslib.utilities.robust_statistics import MAD_TO_SIGMA

# It stands for about two star widths (6 on the reference setup, 2 × the star
# width), so it follows the star width the run measures.
ZERO_ORDER_PEAK_HALF_WINDOW_PX = TunedParameter(
    name="spectroscopy.zero_order_peak_half_window_px",
    default=factor_for(6, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Half-size of the box around the zero order in which its peak is read.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="SPE-D36",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=6,
)
"""Half-size of the box around the zero order in which its peak is read."""

BAND_ALONG_PX = TunedParameter(
    name="spectroscopy.band_along_px",
    default=10,
    unit="px",
    description="Length along the streak of each band used to follow it and measure its width.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="SPE-D36",
)
"""Length along the streak of each band used to follow it and measure its
width. Ten rows average away pixel noise and still resolve the change in
width along a streak a few hundred pixels long."""

TRACE_SEARCH_HALF_WIDTH_PX = TunedParameter(
    name="spectroscopy.trace_search_half_width_px",
    default=80,
    unit="px",
    description="How far across from the zero order's column the streak is looked for in each band.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.EQUIPMENT_PROFILE,
    equipment=frozenset({EquipmentKind.CAMERA, EquipmentKind.FILTER_OR_GRATING}),
    register_entry="SPE-D36",
)
"""How far across, from the zero order's own column, the streak is looked
for in each band. A tilt of 5 degrees moves a 630 px streak by 55 px."""

MINIMUM_BAND_PEAK_SIGMA = TunedParameter(
    name="spectroscopy.minimum_band_peak_sigma",
    default=5.0,
    unit="noise sigma",
    description="A band counts as holding the streak when its peak stands this far above the sky.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="SPE-D36",
)
"""A band counts as holding the streak when its peak stands this many noise
units above the sky."""

# It stands for a fraction of a trail width (1.5 on the reference setup, 0.38
# × the trail width), so it follows the trail width the run measures.
CENTRE_SMOOTHING_SIGMA_PX = TunedParameter(
    name="spectroscopy.centre_smoothing_sigma_px",
    default=factor_for(1.5, RunBasis.TRAIL_WIDTH_PX),
    unit="× trail width",
    description="Smoothing used only to locate the streak centre in a noisy band.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="SPE-D36",
    basis=RunBasis.TRAIL_WIDTH_PX,
    reference_value=1.5,
)
"""Smoothing used only to locate the streak's centre in a noisy band."""

MAXIMUM_BLOBS_REPORTED = 5
"""Most saturated patches listed for one frame."""

# It stands for about four star widths (12 on the reference setup, 4 × the
# star width), so it follows the star width the run measures.
ZERO_ORDER_SATURATION_RADIUS_PX = TunedParameter(
    name="spectroscopy.zero_order_saturation_radius_px",
    default=factor_for(12, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="A saturated patch this close to the zero order is taken to be the zero order itself.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="SPE-D36",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=12,
)
"""A saturated patch this close to the zero order is the zero order itself."""


def _fwhm_across(profile: np.ndarray, centre: int) -> float | None:
    """Measure the full width at half maximum of one cross-streak profile.

    Parameters
    ----------
    profile : `numpy.ndarray`
        The band's brightness across the streak, sky removed.
    centre : `int`
        The index of the brightest point.

    Returns
    -------
    width : `float` or `None`
        The width in pixels, or `None` if the profile does not fall to half
        its peak on both sides inside the window.
    """
    peak = profile[centre]
    if peak <= 0:
        return None
    half = peak / 2.0
    left = centre
    while left > 0 and profile[left] > half:
        left -= 1
    right = centre
    while right < len(profile) - 1 and profile[right] > half:
        right += 1
    if profile[left] > half or profile[right] > half:
        return None
    left_edge = left + (half - profile[left]) / (profile[left + 1] - profile[left])
    right_edge = right - (half - profile[right]) / (profile[right - 1] - profile[right])
    return float(right_edge - left_edge)


def analyze_spectral_frame(
    data: np.ndarray,
    zero_order_row_column: tuple[float, float],
    vertical: bool,
    positive: bool,
    offset_px: float,
    length_px: float,
    exposure_s: float,
    predict_exposure_seconds: float | None = None,
    saturation_threshold_adu: float | None = None,
    saturation_source: str | None = None,
) -> dict[str, Any]:
    """Measure the zero order, the streak and the saturation of one frame.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame, in ADU.
    zero_order_row_column : `tuple` [`float`, `float`]
        Where the zero-order star is, as (row, column).
    vertical : `bool`
        `True` if the streak runs along the rows (up or down the image),
        `False` if along the columns.
    positive : `bool`
        `True` if the streak runs toward larger row (or column) numbers.
    offset_px : `float`
        Distance from the zero order at which the spectrum begins.
    length_px : `float`
        Length of the spectrum.
    exposure_s : `float`
        The frame's exposure, used to predict brightness at another one.
    predict_exposure_seconds : `float`, optional
        If given, also predict the peaks at this exposure.
    saturation_threshold_adu : `float`, optional
        Pixels at or above this value count as saturated. See
        `resolve_saturation_threshold`. Defaults to `SATURATION_ADU`.
    saturation_source : `str`, optional
        Where that level came from, copied into the result. Defaults to a
        note that the `SATURATION_ADU` default was used.

    Returns
    -------
    measurements : `dict` [`str`, `Any`]
        Sky level, zero-order peak, spectrum peak and width, the saturated
        pixel count, and the saturated patches with their distance along
        the streak. Distances are positive toward the spectrum. The
        saturation level used and its source are in
        ``saturation_threshold_adu`` and ``saturation_threshold_source``.
    """
    zero_order_saturation_radius_px = resolve(ZERO_ORDER_SATURATION_RADIUS_PX).value
    band_along_px = resolve(BAND_ALONG_PX).value
    trace_search_half_width_px = resolve(TRACE_SEARCH_HALF_WIDTH_PX).value
    centre_smoothing_sigma_px = resolve(CENTRE_SMOOTHING_SIGMA_PX).value
    minimum_band_peak_sigma = resolve(MINIMUM_BAND_PEAK_SIGMA).value
    if saturation_threshold_adu is None:
        saturation_threshold_adu = resolve(SATURATION_ADU).value
    if saturation_source is None:
        saturation_source = f"default {resolve(SATURATION_ADU).value:g} ADU (no camera profile given)"
    image = np.asarray(data, dtype=np.float64)
    along_image = image if vertical else image.T
    row, column = zero_order_row_column
    zero_along, zero_across = (row, column) if vertical else (column, row)
    direction = 1.0 if positive else -1.0

    sky = float(np.median(image))
    sigma = MAD_TO_SIGMA * float(np.median(np.abs(image - sky)))

    saturated = image >= saturation_threshold_adu
    labels, count = ndimage.label(saturated)
    blobs = []
    zero_order_saturated = False
    if count:
        centres = ndimage.center_of_mass(saturated, labels, range(1, count + 1))
        sizes = ndimage.sum(saturated, labels, range(1, count + 1))
        for (blob_row, blob_column), size in zip(centres, sizes, strict=True):
            blob_along, blob_across = (blob_row, blob_column) if vertical else (blob_column, blob_row)
            distance = (blob_along - zero_along) * direction
            if (
                np.hypot(blob_along - zero_along, blob_across - zero_across)
                <= zero_order_saturation_radius_px
            ):
                zero_order_saturated = True
                continue
            blobs.append({"distance_along_spectrum_px": round(float(distance)), "pixels": int(size)})
    blobs.sort(key=lambda blob: -blob["pixels"])
    inside_spectrum = [
        blob for blob in blobs if offset_px <= blob["distance_along_spectrum_px"] <= offset_px + length_px
    ]

    row_slice = slice(
        max(round(row) - resolve(ZERO_ORDER_PEAK_HALF_WINDOW_PX).value, 0),
        round(row) + resolve(ZERO_ORDER_PEAK_HALF_WINDOW_PX).value + 1,
    )
    column_slice = slice(
        max(round(column) - resolve(ZERO_ORDER_PEAK_HALF_WINDOW_PX).value, 0),
        round(column) + resolve(ZERO_ORDER_PEAK_HALF_WINDOW_PX).value + 1,
    )
    zero_order_peak = float(image[row_slice, column_slice].max())

    first = round(zero_along + direction * offset_px)
    stop = round(zero_along + direction * (offset_px + length_px))
    bands = []
    start, end = (first, stop) if positive else (stop, first)
    start, end = max(start, 0), min(end, along_image.shape[0])
    for band_start in range(start, end - band_along_px + 1, band_along_px):
        strip = along_image[band_start : band_start + band_along_px].mean(axis=0) - sky
        low = max(round(zero_across) - trace_search_half_width_px, 0)
        high = min(round(zero_across) + trace_search_half_width_px + 1, strip.size)
        window = strip[low:high]
        # Smoothing only finds where the streak is. The width and the peak
        # are read from the unsmoothed profile, since smoothing widens a
        # narrow streak and lowers its peak.
        smoothed = ndimage.gaussian_filter1d(window, centre_smoothing_sigma_px)
        found = int(np.argmax(smoothed))
        if smoothed[found] < minimum_band_peak_sigma * sigma / np.sqrt(band_along_px):
            continue
        near = slice(max(found - 2, 0), found + 3)
        centre = max(found - 2, 0) + int(np.argmax(window[near]))
        bands.append((window[centre], _fwhm_across(window, centre)))
    widths = [width for _, width in bands if width is not None]
    spectrum_peak = float(max((peak for peak, _ in bands), default=0.0))

    result: dict[str, Any] = {
        "sky_adu": sky,
        "saturation_threshold_adu": float(saturation_threshold_adu),
        "saturation_threshold_source": saturation_source,
        "zero_order_peak_adu": zero_order_peak,
        "zero_order_saturated": zero_order_saturated,
        "saturated_pixels": int(saturated.sum()),
        "saturated_patches_in_spectrum": inside_spectrum[:MAXIMUM_BLOBS_REPORTED],
        "spectrum_pixels_saturated": sum(blob["pixels"] for blob in inside_spectrum),
        "spectrum_peak_above_sky_adu": round(spectrum_peak, 1),
        "spectrum_width_px": round(float(np.median(widths)), 2) if widths else None,
        "streak_bands_found": len(bands),
    }
    if predict_exposure_seconds and exposure_s > 0:
        scale = predict_exposure_seconds / exposure_s
        zero_order_predicted = (zero_order_peak - sky) * scale + sky
        spectrum_predicted = spectrum_peak * scale + sky
        result["predicted"] = {
            "exposure_s": predict_exposure_seconds,
            "zero_order_peak_adu": round(zero_order_predicted),
            "zero_order_peak_is_lower_bound": zero_order_peak >= saturation_threshold_adu,
            "spectrum_peak_adu": round(spectrum_predicted),
            "spectrum_would_saturate": spectrum_predicted >= saturation_threshold_adu,
        }
    return result


def load_dispersion_geometry(camera_name: str, drivers: Drivers | None = None) -> dict[str, Any]:
    """Read where a camera's spectrum runs from the spectroscopy settings.

    Parameters
    ----------
    camera_name : `str`
        The camera the frames were taken with.
    drivers : `Drivers`, optional
        The outside services the caller chose. Reading the geometry calls
        none of them.

    Returns
    -------
    geometry : `dict` [`str`, `Any`]
        ``vertical``, ``positive``, ``offset_px`` and ``length_px``.
    """
    from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
    from astrometricslib.utilities import ConfigLoader

    config = ConfigLoader.load_spectroscopy_config(camera_name=camera_name)
    pipeline = SpectroscopyPipeline(config, drivers=drivers)
    return {
        "vertical": config.dispersion_orientation == "vertical",
        "positive": config.dispersion_direction == "positive",
        "offset_px": float(pipeline.instrument.zero_order_offset_px),
        "length_px": float(pipeline.instrument.expected_length_px),
    }


def measure_spectral_frame_file(
    path: str,
    camera_name: str,
    exposure_s: float,
    geometry: dict[str, Any],
    predict_exposure_seconds: float | None = None,
    zero_order_row_column: tuple[float, float] | None = None,
    drivers: Drivers | None = None,
) -> dict[str, Any]:
    """Read one raw spectrum frame and measure it.

    The zero-order star and the streak's tilt are found with the same code
    the stacking stage uses (`locate_zero_order`, which tries
    `find_zero_order_position` first, and `measure_trail_angle_degrees`), so
    the numbers agree with it. A target that is a broad glow rather than a
    point, such as a globular cluster, is found by the extended-target
    search, and ``zero_order_source`` says so. The saturation level comes
    from the camera's profile (see `resolve_saturation_threshold`).

    Parameters
    ----------
    path : `str`
        The frame's FITS file.
    camera_name : `str`
        The camera, for the instrument geometry.
    exposure_s : `float`
        The frame's exposure length.
    geometry : `dict` [`str`, `Any`]
        From `load_dispersion_geometry`.
    predict_exposure_seconds : `float`, optional
        An exposure to predict the peaks at.
    zero_order_row_column : `tuple` [`float`, `float`], optional
        The zero order's (row, column), when the caller already knows it.
        The search is then skipped, which is the way to measure a frame whose
        target the search cannot find.
    drivers : `Drivers`, optional
        The outside services the caller chose, passed to the trail
        measurement.

    Returns
    -------
    measurements : `dict` [`str`, `Any`]
        See `analyze_spectral_frame`, plus ``zero_order_xy``,
        ``zero_order_source`` (``"given"``, ``"point source"`` or
        ``"extended target"``), ``tilt_degrees`` and ``trail_contrast``.

    Raises
    ------
    ProcessingError
        No clear zero-order target is near the centre of the frame, and no
        position was given.
    """
    from astrometricslib._internal.drivers.fits_access import collapse_to_2d, read_data
    from astrometricslib.pipelines.stacking.processing.group_alignment import locate_zero_order
    from astrometricslib.pipelines.stacking.processing.group_derotation import measure_trail_angle_degrees

    data = np.asarray(collapse_to_2d(np.asarray(read_data(path), dtype=np.float64)))
    if zero_order_row_column is not None:
        position = (float(zero_order_row_column[0]), float(zero_order_row_column[1]))
        zero_order_source = "given"
    else:
        found = locate_zero_order(data)
        if found is None:
            raise ProcessingError(
                "No single clear zero-order star near the centre of the frame.", details={"path": path}
            )
        position = found.row_column
        zero_order_source = "extended target" if found.is_extended_target else "point source"
    threshold_adu, threshold_source = resolve_saturation_threshold(camera_name)
    measured = analyze_spectral_frame(
        data,
        position,
        exposure_s=exposure_s,
        predict_exposure_seconds=predict_exposure_seconds,
        saturation_threshold_adu=threshold_adu,
        saturation_source=threshold_source,
        **geometry,
    )
    tilt, contrast = measure_trail_angle_degrees(path, (position[1], position[0]), camera_name, drivers)
    return {
        "zero_order_xy": [round(position[1], 1), round(position[0], 1)],
        "zero_order_source": zero_order_source,
        "tilt_degrees": round(tilt, 3) if contrast > 0 else None,
        "trail_contrast": round(contrast, 1),
        **measured,
    }


def summarize_spectral_frames(rows: list[dict[str, Any]], minimum_contrast: float) -> dict[str, Any]:
    """Group measured frames by exposure and by pier side.

    Parameters
    ----------
    rows : `list` [`dict`]
        One measured frame each, with ``exposure_s``, ``pier_side``,
        ``tilt_degrees``, ``trail_contrast`` and the saturation fields.
    minimum_contrast : `float`
        A tilt is used only when the streak stood out at least this much.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        ``by_exposure`` (frames, how many clip the zero order or the
        spectrum, median tilt and width) and ``by_pier_side`` (frames and
        the median, smallest and largest tilt), so a tilt that depends on
        the side of the pier shows up.
    """
    measured = [row for row in rows if "error" not in row]

    def middle(values: list[float]) -> float | None:
        """Give the median of the values, or `None` if there are none.

        Returns
        -------
        median : `float` or `None`
            The median, rounded.
        """
        return round(float(np.median(values)), 3) if values else None

    by_exposure: dict[str, Any] = {}
    for exposure in sorted({row["exposure_s"] for row in rows}):
        group = [row for row in measured if row["exposure_s"] == exposure]
        by_exposure[f"{exposure:g}"] = {
            "frames": sum(1 for row in rows if row["exposure_s"] == exposure),
            "measured": len(group),
            "zero_order_saturated": sum(1 for row in group if row["zero_order_saturated"]),
            "spectrum_has_saturated_pixels": sum(1 for row in group if row["spectrum_pixels_saturated"]),
            "median_spectrum_width_px": middle([
                row["spectrum_width_px"] for row in group if row["spectrum_width_px"]
            ]),
        }
    by_pier_side: dict[str, Any] = {}
    for side in sorted({str(row.get("pier_side")) for row in rows}):
        tilts = [
            row["tilt_degrees"]
            for row in measured
            if str(row.get("pier_side")) == side
            and row["tilt_degrees"] is not None
            and row["trail_contrast"] >= minimum_contrast
        ]
        by_pier_side[side] = {
            "frames": sum(1 for row in rows if str(row.get("pier_side")) == side),
            "frames_with_a_clear_tilt": len(tilts),
            "median_tilt_degrees": middle(tilts),
            "smallest_tilt_degrees": round(min(tilts), 3) if tilts else None,
            "largest_tilt_degrees": round(max(tilts), 3) if tilts else None,
        }
    return {"by_exposure": by_exposure, "by_pier_side": by_pier_side}


def check_spectral_frames(
    frames: Sequence[Any],
    predict_exposure_seconds: float | None,
    zero_order_row_column: tuple[float, float] | None = None,
    drivers: Drivers | None = None,
) -> Any:
    """Measure raw spectrum frames and summarize where they clip.

    This is the spectral part of `quality.frames.measure`. Each frame takes
    about a second. A frame that cannot be read gets an ``error`` in its
    row, and the other frames are still measured.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The spectrum frames, in the order to measure them.
    predict_exposure_seconds : `float` or `None`
        An exposure to predict the peaks at, in seconds.
    zero_order_row_column : `tuple` [`float`, `float`], optional
        The zero order's (row, column), when the caller knows it. It is used
        for every frame, so it suits frames of one target taken without
        moving the mount, and it skips the search.
    drivers : `Drivers`, optional
        The outside services the caller chose, passed to the trail
        measurements.

    Returns
    -------
    report : `SpectralFrameCheckReport`
        One row per frame and the summary by exposure and by pier side.
    """
    import os

    from astrometricslib.foundation.jobs.runner import get_current_job
    from astrometricslib.models.quality.quality_reports import SpectralFrameCheckReport
    from astrometricslib.pipelines.stacking.processing.group_derotation import MINIMUM_TRAIL_CONTRAST_SIGMA

    chosen = list(frames)
    matching = len(chosen)
    if not chosen:
        return SpectralFrameCheckReport()

    job = get_current_job()
    geometry_by_camera: dict[str, dict[str, Any]] = {}
    rows = []
    for index, frame in enumerate(chosen):
        if job is not None:
            job.mark(
                "running",
                index,
                progress_total=len(chosen),
                message=f"Measured {index} of {len(chosen)} spectrum frames",
            )
        row: dict[str, Any] = {
            "file": os.path.basename(frame.path),
            "exposure_s": float(frame.exposure_s),
            "pier_side": frame.pier_side,
        }
        try:
            if frame.camera not in geometry_by_camera:
                geometry_by_camera[frame.camera] = load_dispersion_geometry(frame.camera, drivers)
            row.update(
                measure_spectral_frame_file(
                    frame.path,
                    frame.camera,
                    row["exposure_s"],
                    geometry_by_camera[frame.camera],
                    predict_exposure_seconds,
                    zero_order_row_column,
                    drivers,
                )
            )
        except (OSError, ValueError, AstrometricsError) as error:
            row["error"] = str(error)
        rows.append(row)
    return SpectralFrameCheckReport(
        frames_matching=matching,
        frames_measured=len(rows),
        summary=summarize_spectral_frames(rows, resolve(MINIMUM_TRAIL_CONTRAST_SIGMA).value),
        frames=rows,
        note=(
            "Nothing was saved. Peaks at or above the saturation level are lower bounds; "
            "each row gives its level and where it came from. Predictions scale linearly."
        ),
    )
