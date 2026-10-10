"""Quality checkpoint 0: the raw frame, as seen for one star.

The checkpoint reports the raw-frame numbers the pipeline already holds for a
star right after it extracts the star's spectrum: how much of the zero-order
image (the star's undispersed image) was saturated, how much of the requested
spectrum landed on the image, how wide the trail was, and how the extractor
read the sky. It re-measures nothing. It only gathers numbers the extraction
produced into the common `StageQualityCheckpoint` shape.

The frame-level check in `pipelines/shared/quality/spectral_frame_check.py`
measures the whole frame (streak tilt, peak above the sky, the saturation
level and its source). The spectroscopy pipeline does not run it per star.
When a caller has that result, passing it as ``frame_check`` adds its numbers.
"""

from collections.abc import Mapping, Sequence

import numpy as np

from astrometricslib.models.spectroscopy_quality import StageQualityCheckpoint, metric
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD

STAGE = "raw_frame"


def _optional_float(value: object) -> float | None:
    """Read a number from a loosely typed value.

    Parameters
    ----------
    value : `object`
        A number, or `None`, or anything else.

    Returns
    -------
    number : `float` or `None`
        The value as a `float` if it is a number, otherwise `None`.
    """
    return float(value) if isinstance(value, int | float | np.integer | np.floating) else None


def _median_trail_width_px(trail_width_px: Sequence[float] | None) -> float | None:
    """Find the median width of the spectrum trail.

    Parameters
    ----------
    trail_width_px : `Sequence` [`float`], optional
        The trail width at each sample, in pixels. A value of 0.0 marks a
        sample where no width could be fitted.

    Returns
    -------
    width : `float` or `None`
        The median of the fitted widths, or `None` when there are none.
    """
    if trail_width_px is None:
        return None
    widths = [float(width) for width in trail_width_px if width > 0.0]
    return float(np.median(widths)) if widths else None


def assess_raw_frame_quality(
    *,
    zero_order_saturated_pixel_fraction: float | None,
    valid_fraction: float | None,
    trail_width_px: Sequence[float] | None,
    extraction_diagnostics: Mapping[str, object] | None,
    frame_check: Mapping[str, object] | None = None,
) -> StageQualityCheckpoint:
    """Build quality checkpoint 0 for one extracted spectrum.

    Parameters
    ----------
    zero_order_saturated_pixel_fraction : `float`, optional
        The fraction (0 to 1) of pixels at the zero-order position that were
        saturated. `None` when it was not measured.
    valid_fraction : `float`, optional
        The fraction (0 to 1) of the requested spectrum that landed on the
        image and inside the camera's sensitive range.
    trail_width_px : `Sequence` [`float`], optional
        The trail width at each sample, in pixels.
    extraction_diagnostics : `Mapping`, optional
        The extractor's summary (``ExtractionDiagnostics.as_dict``), with
        ``contaminated_sky_fraction`` and ``dominant_sky_mode``.
    frame_check : `Mapping`, optional
        The frame-level result of `measure_spectral_frame_file`. When given,
        its ``tilt_degrees`` and ``spectrum_peak_above_sky_adu`` become
        metrics and its ``saturation_threshold_source`` goes into the
        saturation note. Left out, those numbers are absent.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        The ``raw_frame`` checkpoint.
    """
    frame = frame_check or {}
    diagnostics = extraction_diagnostics or {}
    saturation_source = frame.get("saturation_threshold_source")
    saturation_note = "limit is the run's zero-order saturation flag level; a guess, not validated"
    if saturation_source:
        saturation_note += f"; pixel saturation level: {saturation_source}"

    contaminated_sky = _optional_float(diagnostics.get("contaminated_sky_fraction"))
    sky_mode = diagnostics.get("dominant_sky_mode")
    saturation = metric(
        "zero_order_saturated_fraction",
        zero_order_saturated_pixel_fraction,
        "fraction",
        limit=DEFAULT_SATURATION_FLAG_THRESHOLD,
        higher_is_better=False,
        note=saturation_note,
        limit_is_a_pass=False,
    )
    metrics = [
        saturation,
        metric(
            "valid_fraction",
            valid_fraction,
            "fraction",
            note="below 1 means part of the requested spectrum ran off the image or the camera's range",
        ),
        metric(
            "median_trail_width",
            _median_trail_width_px(trail_width_px),
            "pixel",
            note="median of the fitted trail widths",
        ),
        metric(
            "contaminated_sky_fraction",
            contaminated_sky,
            "fraction",
            note=f"dominant sky mode: {sky_mode}" if sky_mode else "",
        ),
    ]
    if "tilt_degrees" in frame:
        metrics.append(metric("streak_tilt", _optional_float(frame["tilt_degrees"]), "degree"))
    if "spectrum_peak_above_sky_adu" in frame:
        metrics.append(metric("peak_above_sky", _optional_float(frame["spectrum_peak_above_sky_adu"]), "ADU"))

    flags = []
    if saturation.passed is False:
        flags.append("zero_order_saturated")
    if valid_fraction is not None and valid_fraction < 1.0:
        flags.append("trail_partly_off_image")
    if contaminated_sky:
        flags.append("sky_band_contaminated")
    return StageQualityCheckpoint(stage=STAGE, metrics=metrics, flags=flags)
