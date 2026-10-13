"""Quality checkpoint 0: the raw frame, as seen for one star.

The checkpoint reports the raw-frame numbers the pipeline already holds for a
star right after it extracts the star's spectrum: how much of the zero-order
image (the star's undispersed image) was saturated, how much of the requested
spectrum landed on the image, how wide the trail was, and how the extractor
read the sky. It re-measures none of these. It gathers the numbers the
extraction produced into the common `StageQualityCheckpoint` shape.

It also reports atmospheric differential refraction (DAR), because refraction
is a property of the frame: how far the air shifts each wavelength of the
spectrum along and across the trail. The refraction record comes from
`differential_refraction`, which finds the numbers from the target's
altitude, the sky direction of the dispersion and the air's conditions. That
module also corrects the wavelengths.

The frame-level check in `pipelines/shared/quality/spectral_frame_check.py`
measures the whole frame (streak tilt, peak above the sky, the saturation
level and its source). The spectroscopy pipeline does not run it per star.
When a caller has that result, passing it as ``frame_check`` adds its numbers.
"""

from collections.abc import Mapping, Sequence

import numpy as np

from astrometricslib.models.spectroscopy_quality import StageQualityCheckpoint, StageQualityMetric, metric
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_refraction import (
    MINIMUM_ALTITUDE_DEGREES,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.differential_refraction import (
    BLUE_REPORT_WAVELENGTH_ANGSTROM,
    RED_REPORT_WAVELENGTH_ANGSTROM,
    DifferentialRefraction,
)

STAGE = "raw_frame"

# The largest spread of the wavelength error from refraction, in Angstroms,
# between the blue and red ends of the spectrum that the pipeline accepts
# without a flag. It is a design limit, not a measurement: half the
# resolution element of this setup at 5000 A, which is about 40 A (35 to
# 48 A measured on Vega; see `spectral_resolution`). A spread of 20 A moves
# a line by half of what the instrument can tell apart.
DAR_ALONG_DISPERSION_LIMIT_ANGSTROM = 20.0


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


def _refraction_metrics(refraction: DifferentialRefraction) -> tuple[list[StageQualityMetric], list[str]]:
    """Build the refraction metrics and flags for checkpoint 0.

    Parameters
    ----------
    refraction : `DifferentialRefraction`
        The refraction record for this spectrum.

    Returns
    -------
    metrics : `list` [`StageQualityMetric`]
        The four refraction metrics. A metric has no value when the
        refraction could not be computed.
    flags : `list` [`str`]
        ``dar_large`` when the along-dispersion span is over its limit.
        ``dar_not_computed`` when there was no site, WCS, time or usable
        altitude. ``target_altitude_low`` when the target was too low for
        the plane-parallel model.
    """
    span_note = (
        f"span of the wavelength error between {BLUE_REPORT_WAVELENGTH_ANGSTROM:.0f} and "
        f"{RED_REPORT_WAVELENGTH_ANGSTROM:.0f} A before the correction; the limit is a design "
        "choice (half the resolution element at 5000 A), not a measurement"
        f"; correction applied: {'yes' if refraction.is_applied else 'no'}"
    )
    along = metric(
        "dar_along_dispersion_angstrom",
        refraction.along_dispersion_span_angstrom,
        "angstrom",
        limit=DAR_ALONG_DISPERSION_LIMIT_ANGSTROM,
        higher_is_better=False,
        note=span_note,
    )
    altitude = metric(
        "target_altitude_degrees",
        refraction.altitude_degrees,
        "degree",
        limit=MINIMUM_ALTITUDE_DEGREES,
        note=(
            f"below {MINIMUM_ALTITUDE_DEGREES:.0f} degrees the plane-parallel refraction model is "
            "refused; the limit is a design choice, not a measurement"
        ),
    )
    metrics = [
        along,
        metric(
            "dar_across_dispersion_px",
            refraction.across_dispersion_span_px,
            "pixel",
            note="how much refraction widens the trail between the blue and red ends; report only",
        ),
        altitude,
        metric(
            "parallactic_to_dispersion_angle_degrees",
            refraction.parallactic_to_dispersion_angle_degrees,
            "degree",
            note="0 means the red end of the spectrum points at the zenith; report only",
        ),
    ]
    flags = []
    if along.passed is False:
        flags.append("dar_large")
    if not refraction.is_computed:
        flags.append("dar_not_computed")
    if altitude.passed is False:
        flags.append("target_altitude_low")
    return metrics, flags


def assess_raw_frame_quality(
    *,
    zero_order_saturated_pixel_fraction: float | None,
    valid_fraction: float | None,
    trail_width_px: Sequence[float] | None,
    extraction_diagnostics: Mapping[str, object] | None,
    frame_check: Mapping[str, object] | None = None,
    differential_refraction: DifferentialRefraction | None = None,
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
    differential_refraction : `DifferentialRefraction`, optional
        The refraction record for this spectrum. When given, the checkpoint
        carries the four refraction metrics. Left out, they are absent.

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
    if differential_refraction is not None:
        refraction_metrics, refraction_flags = _refraction_metrics(differential_refraction)
        metrics.extend(refraction_metrics)
        flags.extend(refraction_flags)
    return StageQualityCheckpoint(stage=STAGE, metrics=metrics, flags=flags)
