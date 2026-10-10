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

from typing import Any

import numpy as np
from scipy import ndimage

from astrometricslib.foundation.errors import AstrometricsError, ProcessingError

SATURATION_ADU = 65000
"""Pixels at or above this value count as saturated when no camera profile
gives a level (16-bit frames). The same level the raw frame check uses."""

ZERO_ORDER_PEAK_HALF_WINDOW_PX = 6
"""Half-size of the box around the zero order in which its peak is read."""

BAND_ALONG_PX = 10
"""Length along the streak of each band used to follow it and measure its
width. Ten rows average away pixel noise and still resolve the change in
width along a streak a few hundred pixels long."""

TRACE_SEARCH_HALF_WIDTH_PX = 80
"""How far across, from the zero order's own column, the streak is looked
for in each band. A tilt of 5 degrees moves a 630 px streak by 55 px."""

MINIMUM_BAND_PEAK_SIGMA = 5.0
"""A band counts as holding the streak when its peak stands this many noise
units above the sky."""

CENTRE_SMOOTHING_SIGMA_PX = 1.5
"""Smoothing used only to locate the streak's centre in a noisy band."""

MAXIMUM_BLOBS_REPORTED = 5
"""Most saturated patches listed for one frame."""

ZERO_ORDER_SATURATION_RADIUS_PX = 12
"""A saturated patch this close to the zero order is the zero order itself."""

_MAD_TO_SIGMA = 1.4826
"""A robust standard deviation is 1.4826 times the median absolute deviation
for Gaussian noise."""


def resolve_saturation_threshold(camera_name: str | None) -> tuple[float, str]:
    """Find the level at which this camera's pixels count as saturated.

    The level is the camera profile's saturation threshold. A profile can
    list a threshold above the value a clipped pixel actually reaches (its
    clip ceiling), which would make saturation impossible to detect. In that
    case the clip ceiling is used instead, and the source text says so. The
    default `SATURATION_ADU` is used only when no profile can be read.

    Parameters
    ----------
    camera_name : `str` or `None`
        The camera that took the frame.

    Returns
    -------
    threshold_adu : `float`
        Pixels at or above this value count as saturated.
    source : `str`
        A sentence saying where the level came from.
    """
    from astrometricslib.drivers.camera_profile_store import resolve_camera_profile

    try:
        profile = resolve_camera_profile(camera_name)
    except AstrometricsError as error:
        return float(
            SATURATION_ADU
        ), f"default {SATURATION_ADU:g} ADU (no camera profile could be read: {error})"
    threshold = float(profile.saturation_threshold_adu.value)
    if profile.is_generic_fallback:
        return (
            threshold,
            f"generic fallback camera profile, {threshold:g} ADU (camera {camera_name!r} is not listed)",
        )
    ceiling = float(profile.clip_ceiling_adu.value)
    if not profile.saturation_threshold_can_be_reached:
        return ceiling, (
            f"{profile.camera_name} profile clip ceiling, {ceiling:g} ADU "
            f"(its saturation threshold of {threshold:g} ADU is above the ceiling and no pixel can reach it)"
        )
    return threshold, f"{profile.camera_name} profile saturation threshold, {threshold:g} ADU"


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
    exposure_seconds: float,
    predict_exposure_seconds: float | None = None,
    saturation_threshold_adu: float = SATURATION_ADU,
    saturation_source: str = f"default {SATURATION_ADU:g} ADU (no camera profile given)",
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
    exposure_seconds : `float`
        The frame's exposure, used to predict brightness at another one.
    predict_exposure_seconds : `float`, optional
        If given, also predict the peaks at this exposure.
    saturation_threshold_adu : `float`, optional
        Pixels at or above this value count as saturated. See
        `resolve_saturation_threshold`. Defaults to `SATURATION_ADU`.
    saturation_source : `str`, optional
        Where that level came from, copied into the result.

    Returns
    -------
    measurements : `dict` [`str`, `Any`]
        Sky level, zero-order peak, spectrum peak and width, the saturated
        pixel count, and the saturated patches with their distance along
        the streak. Distances are positive toward the spectrum. The
        saturation level used and its source are in
        ``saturation_threshold_adu`` and ``saturation_threshold_source``.
    """
    image = np.asarray(data, dtype=np.float64)
    along_image = image if vertical else image.T
    row, column = zero_order_row_column
    zero_along, zero_across = (row, column) if vertical else (column, row)
    direction = 1.0 if positive else -1.0

    sky = float(np.median(image))
    sigma = _MAD_TO_SIGMA * float(np.median(np.abs(image - sky)))

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
                <= ZERO_ORDER_SATURATION_RADIUS_PX
            ):
                zero_order_saturated = True
                continue
            blobs.append({"distance_along_spectrum_px": round(float(distance)), "pixels": int(size)})
    blobs.sort(key=lambda blob: -blob["pixels"])
    inside_spectrum = [
        blob for blob in blobs if offset_px <= blob["distance_along_spectrum_px"] <= offset_px + length_px
    ]

    row_slice = slice(
        max(round(row) - ZERO_ORDER_PEAK_HALF_WINDOW_PX, 0),
        round(row) + ZERO_ORDER_PEAK_HALF_WINDOW_PX + 1,
    )
    column_slice = slice(
        max(round(column) - ZERO_ORDER_PEAK_HALF_WINDOW_PX, 0),
        round(column) + ZERO_ORDER_PEAK_HALF_WINDOW_PX + 1,
    )
    zero_order_peak = float(image[row_slice, column_slice].max())

    first = round(zero_along + direction * offset_px)
    stop = round(zero_along + direction * (offset_px + length_px))
    bands = []
    start, end = (first, stop) if positive else (stop, first)
    start, end = max(start, 0), min(end, along_image.shape[0])
    for band_start in range(start, end - BAND_ALONG_PX + 1, BAND_ALONG_PX):
        strip = along_image[band_start : band_start + BAND_ALONG_PX].mean(axis=0) - sky
        low = max(round(zero_across) - TRACE_SEARCH_HALF_WIDTH_PX, 0)
        high = min(round(zero_across) + TRACE_SEARCH_HALF_WIDTH_PX + 1, strip.size)
        window = strip[low:high]
        # Smoothing only finds where the streak is. The width and the peak
        # are read from the unsmoothed profile, since smoothing widens a
        # narrow streak and lowers its peak.
        smoothed = ndimage.gaussian_filter1d(window, CENTRE_SMOOTHING_SIGMA_PX)
        found = int(np.argmax(smoothed))
        if smoothed[found] < MINIMUM_BAND_PEAK_SIGMA * sigma / np.sqrt(BAND_ALONG_PX):
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
    if predict_exposure_seconds and exposure_seconds > 0:
        scale = predict_exposure_seconds / exposure_seconds
        zero_order_predicted = (zero_order_peak - sky) * scale + sky
        spectrum_predicted = spectrum_peak * scale + sky
        result["predicted"] = {
            "exposure_seconds": predict_exposure_seconds,
            "zero_order_peak_adu": round(zero_order_predicted),
            "zero_order_peak_is_lower_bound": zero_order_peak >= saturation_threshold_adu,
            "spectrum_peak_adu": round(spectrum_predicted),
            "spectrum_would_saturate": spectrum_predicted >= saturation_threshold_adu,
        }
    return result


def load_dispersion_geometry(camera_name: str) -> dict[str, Any]:
    """Read where a camera's spectrum runs from the spectroscopy settings.

    Parameters
    ----------
    camera_name : `str`
        The camera the frames were taken with.

    Returns
    -------
    geometry : `dict` [`str`, `Any`]
        ``vertical``, ``positive``, ``offset_px`` and ``length_px``.
    """
    from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
    from astrometricslib.utilities import ConfigLoader

    config = ConfigLoader.load_spectroscopy_config(camera_name=camera_name)
    pipeline = SpectroscopyPipeline(config)
    return {
        "vertical": config.dispersion_orientation == "vertical",
        "positive": config.dispersion_direction == "positive",
        "offset_px": float(pipeline.instrument.zero_order_offset_px),
        "length_px": float(pipeline.instrument.expected_length_px),
    }


def measure_spectral_frame_file(
    path: str,
    camera_name: str,
    exposure_seconds: float,
    geometry: dict[str, Any],
    predict_exposure_seconds: float | None = None,
    zero_order_row_column: tuple[float, float] | None = None,
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
    exposure_seconds : `float`
        The frame's exposure length.
    geometry : `dict` [`str`, `Any`]
        From `load_dispersion_geometry`.
    predict_exposure_seconds : `float`, optional
        An exposure to predict the peaks at.
    zero_order_row_column : `tuple` [`float`, `float`], optional
        The zero order's (row, column), when the caller already knows it.
        The search is then skipped, which is the way to measure a frame whose
        target the search cannot find.

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
    from astrometricslib.drivers.fits_access import collapse_to_2d, read_data
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
        exposure_seconds=exposure_seconds,
        predict_exposure_seconds=predict_exposure_seconds,
        saturation_threshold_adu=threshold_adu,
        saturation_source=threshold_source,
        **geometry,
    )
    tilt, contrast = measure_trail_angle_degrees(path, (position[1], position[0]), camera_name)
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
        One measured frame each, with ``exposure_seconds``, ``pier_side``,
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
    for exposure in sorted({row["exposure_seconds"] for row in rows}):
        group = [row for row in measured if row["exposure_seconds"] == exposure]
        by_exposure[f"{exposure:g}"] = {
            "frames": sum(1 for row in rows if row["exposure_seconds"] == exposure),
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


def _exposure_matches(recorded: Any, wanted: float) -> bool:
    """Say whether a frame's recorded exposure equals a wanted length.

    Returns
    -------
    matches : `bool`
        `True` if the two agree to a thousandth of a second.
    """
    try:
        return abs(float(recorded) - wanted) < 0.001
    except TypeError, ValueError:
        return False


def check_spectral_frames(
    target: Any,
    selection: Any,
    exposure_seconds: float | None,
    predict_exposure_seconds: float | None,
    limit: int,
    zero_order_row_column: tuple[float, float] | None = None,
) -> Any:
    """Measure a target's raw spectrum frames and summarize where they clip.

    This is the work behind `QualityDiagnostics.spectral_frame_check`. Each
    frame takes about a second. A frame that cannot be read gets an
    ``error`` in its row, and the other frames are still measured.

    Parameters
    ----------
    target : `Target`
        The target whose spectrum frames to measure.
    selection : `FrameSelection`
        The file range and time window to keep.
    exposure_seconds : `float` or `None`
        Only frames with this exposure length.
    predict_exposure_seconds : `float` or `None`
        An exposure to predict the peaks at, in seconds.
    limit : `int`
        How many frames to measure.
    zero_order_row_column : `tuple` [`float`, `float`], optional
        The zero order's (row, column), when the caller knows it. It is used
        for every frame, so it suits frames of one target taken without
        moving the mount, and it skips the search.

    Returns
    -------
    report : `SpectralFrameCheckReport`
        One row per frame and the summary by exposure and by pier side.
    """
    import os

    from astrometricslib.foundation.jobs.runner import get_current_job
    from astrometricslib.models.quality_reports import SpectralFrameCheckReport
    from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
    from astrometricslib.pipelines.shared.quality.frame_selection import select_library_frames
    from astrometricslib.pipelines.stacking.processing.group_derotation import MINIMUM_TRAIL_CONTRAST_SIGMA

    lights = [
        frame for frame in target.frames if str(frame.role).upper() == "LIGHT" and frame_is_spectral(frame)
    ]
    chosen = select_library_frames(lights, selection)
    if exposure_seconds is not None:
        chosen = [frame for frame in chosen if _exposure_matches(frame.exposure, exposure_seconds)]
    matching = len(chosen)
    if not chosen:
        return SpectralFrameCheckReport(target_id=target.id)
    chosen = chosen[:limit] if selection.has_bounds else chosen[-limit:]

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
            "exposure_seconds": float(frame.exposure),
            "pier_side": frame.pier_side,
        }
        try:
            if frame.camera not in geometry_by_camera:
                geometry_by_camera[frame.camera] = load_dispersion_geometry(frame.camera)
            row.update(
                measure_spectral_frame_file(
                    frame.path,
                    frame.camera,
                    row["exposure_seconds"],
                    geometry_by_camera[frame.camera],
                    predict_exposure_seconds,
                    zero_order_row_column,
                )
            )
        except (OSError, ValueError, AstrometricsError) as error:
            row["error"] = str(error)
        rows.append(row)
    return SpectralFrameCheckReport(
        target_id=target.id,
        frames_matching=matching,
        frames_measured=len(rows),
        summary=summarize_spectral_frames(rows, MINIMUM_TRAIL_CONTRAST_SIGMA),
        frames=rows,
        note=(
            "Nothing was saved. Peaks at or above the saturation level are lower bounds; "
            "each row gives its level and where it came from. Predictions scale linearly."
        ),
    )
