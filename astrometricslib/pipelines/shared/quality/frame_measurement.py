"""Measure one raw light frame and return its quality measurements.

`measure_frame` reads the pixels of one frame once and returns one
`QualityMeasurement` for every frame metric declared in `metrics.py`. It writes
nothing: the caller stores the result with `record_measurements`.

What is measured, in plain words:

- The width of the stars. Up to about 200 isolated, unsaturated stars spread
  on a grid over the field are fitted with a Moffat profile (a bright core with
  slowly fading wings, closer to a real star than a Gaussian). The frame's
  value is the median over the stars, with a bootstrap uncertainty. No star is
  dropped for being stretched, so a trailed frame reads wide, not sharp.
- The elongation of the stars (long axis divided by short axis) and the
  direction of the long axis, from the second moments of the same stars. The
  direction comes from the average of the stars' ellipticity components, so a
  round star, whose direction is only noise, counts for little.
- The sky brightness in electrons (a sigma-clipped mean, found with the same
  estimator as the bias level), the expected photon noise of that sky, and the
  read noise of the camera at the frame's setting. The ratio of the two noises
  is never stored. The sky includes dark current and amp glow; for a camera
  with no recorded sensor temperature it is stored as ``assumed``.
- The temperatures the frame recorded, and the metrics the library already
  measured (background level, saturated fraction, Gaussian star width of the
  brightest stars, count of bright regions).

A value that cannot be measured comes back with the status ``not_measured`` and
a reason. It is never replaced by a default number.

One-shot-color frames: a frame stored as separate color planes is measured per
plane, and the plane's name goes on the subject. A raw mosaic (one plane with a
color filter array, header card ``BAYERPAT``) has its star shapes measured on
2 x 2 super-pixels (the sum of each red, green, green, blue cell), so the
Bayer pattern does not bend the second moments or the angle; the widths are
given back in original pixels. Its sky and background use all color pixels
mixed. Its reasons say so.

A time budget keeps one slow frame from stalling a batch. It counts the CPU
time of the measuring thread, not the wall clock, so time spent waiting for
Python's global lock, for the disk or for other programs does not use it up. It
is checked between the steps; when it has run out, the remaining slow steps
(the star detections and fits) come back ``not_measured`` and the quick
header and background metrics are still returned.

The work is spread over four modules in this folder:

- `frame_measurement` (this file): `measure_frame`, which reads a frame and
  turns every result into a `QualityMeasurement`, plus the background,
  saturation and header measurements.
- `star_profile_fitting`: finding stars and fitting their width, shape and
  direction.
- `sky_electrons`: the sky brightness and noise in electrons.
- `time_budget`: the stopwatch that keeps one slow frame from stalling a batch.
"""

import hashlib
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, open_primary_hdu
from astrometricslib.models.camera_profile import CameraProfile, ProvenancedValue, ProvenanceKind
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_grouping import frame_exposure_seconds
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_BACKGROUND_LEVEL,
    FRAME_DETECTED_REGION_COUNT,
    FRAME_FOCUSER_TEMPERATURE,
    FRAME_METRICS,
    FRAME_READ_NOISE_E,
    FRAME_SATURATED_PIXEL_FRACTION,
    FRAME_SENSOR_TEMPERATURE,
    FRAME_SKY_NOISE_E,
    FRAME_SKY_RATE_E_PER_S_PER_PX,
    FRAME_STAR_ELONGATION,
    FRAME_STAR_ELONGATION_ANGLE_DEG,
    FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
    FRAME_STAR_WIDTH_EFFECTIVE_PX,
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
    FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
    FRAME_STAR_WIDTH_MOFFAT_PX,
    FRAME_STAR_WIDTH_REGION_PX,
)
from astrometricslib.pipelines.shared.quality.raw_frame_check import detect_stars
from astrometricslib.pipelines.shared.quality.saturation import compute_saturated_pixel_fraction
from astrometricslib.pipelines.shared.quality.sky_electrons import (
    SkyElectrons,
    look_up_frame_sensor_noise,
    measure_sky_electrons,
    provenance_text,
)
from astrometricslib.pipelines.shared.quality.star_profile_fitting import (
    BAYER_CELL_SIZE_PX,
    FIELD_GRID_CELLS_PER_SIDE,
    FIELD_REGION_CELLS_PER_SIDE,
    MOFFAT_MINIMUM_STAR_COUNT,
    PIXEL_BLUR_FWHM_SQUARED_FACTOR,
    StarProfileResult,
    effective_star_width_px,
    measure_field_region_widths,
    measure_star_profiles,
    usable_top_level_adu,
)
from astrometricslib.pipelines.shared.quality.time_budget import (
    FRAME_MEASUREMENT_TIME_BUDGET_SECONDS,
    Stopwatch,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.frame_identity import frame_subject_id
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)


# What the reason of a pixel metric starts with when the frame could not be
# read at all. The measurement job tests for it so that an unreadable frame (a
# network mount that dropped out, a file still being written) is retried on the
# next run instead of being recorded as measured.
UNREADABLE_FRAME_REASON_PREFIX = "the frame could not be read"

# The names of the planes of a frame stored as three color planes.
_COLOR_PLANE_NAMES = {3: ("R", "G", "B")}

_DEFINITION_VERSIONS = {spec.name: spec.definition_version for spec in FRAME_METRICS}


@cache
def frame_metric_set_fingerprint() -> str:
    """Summarize which frame metrics a measurement covers, and how defined.

    A frame is measured once and then skipped while its file is unchanged. When
    a metric is added to the frame registry, or an existing one is redefined
    (its ``definition_version`` is raised), the frames measured earlier lack
    the new numbers. The measurement job stores this fingerprint on every
    frame it measures and measures a frame again when its stored fingerprint
    differs.

    Returns
    -------
    fingerprint : `str`
        The first 12 hexadecimal digits of the SHA-256 hash of the sorted
        ``name:definition_version`` lines of `FRAME_METRICS`. It does not
        change when the order of the declarations changes.
    """
    lines = sorted(f"{spec.name}:{spec.definition_version}" for spec in FRAME_METRICS)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]


@dataclass
class _Context:
    """What `measure_frame` carries from step to step."""

    frame: FrameRecord
    target_id: str
    run_id: str
    job_id: str | None
    measured_at: datetime
    capture_setting_id: int | None
    path: str
    measurements: list[QualityMeasurement] = field(default_factory=list)

    def add(
        self,
        metric: str,
        *,
        value: float | None = None,
        uncertainty: float | None = None,
        sample_count: int | None = None,
        reason: str = "",
        channel: str | None = None,
        status: MeasurementStatus | None = None,
    ) -> None:
        """Append one measurement; a missing value becomes ``not_measured``.

        Parameters
        ----------
        metric : `str`
            The registered metric name.
        value : `float` or `None`, optional
            The value. `None` (or a non-finite number) makes the measurement
            ``not_measured``, and `reason` must then say why.
        uncertainty : `float` or `None`, optional
            The uncertainty of the value.
        sample_count : `int` or `None`, optional
            The number of samples behind the value.
        reason : `str`, optional
            Why the value is missing, or a note on its source.
        channel : `str` or `None`, optional
            The color plane for a one-shot-color frame.
        status : `MeasurementStatus` or `None`, optional
            ``measured`` or ``assumed``. Default ``measured``.
        """
        if value is None or not math.isfinite(value):
            status = MeasurementStatus.NOT_MEASURED
            value = None
            uncertainty = None
            reason = reason or "the value could not be computed"
        elif uncertainty is not None and not math.isfinite(uncertainty):
            uncertainty = None
        self.measurements.append(
            QualityMeasurement(
                metric=metric,
                definition_version=_DEFINITION_VERSIONS[metric],
                subject=frame_subject(
                    self.path,
                    self.target_id,
                    self.frame.timestamp,
                    capture_setting_id=self.capture_setting_id,
                    channel=channel,
                ),
                status=status or MeasurementStatus.MEASURED,
                value=value,
                uncertainty=uncertainty,
                sample_count=sample_count,
                reason=reason,
                origin=MeasurementOrigin.MEASURED,
                run_id=self.run_id,
                job_id=self.job_id,
                measured_at=self.measured_at,
            )
        )

    def add_not_measured(self, metrics: tuple[str, ...], reason: str, channel: str | None = None) -> None:
        """Append a ``not_measured`` record for each metric.

        Parameters
        ----------
        metrics : `tuple` of `str`
            The metric names.
        reason : `str`
            Why none of them could be measured.
        channel : `str` or `None`, optional
            The color plane, if any.
        """
        for metric in metrics:
            self.add(metric, reason=reason, channel=channel)


def frame_subject(
    path: str,
    target_id: str,
    timestamp: float | None,
    *,
    capture_setting_id: int | None = None,
    channel: str | None = None,
) -> QualitySubject:
    """Describe one frame as the subject of a measurement.

    Every row about a frame is built here: the frame job's rows, the
    quarantine rows, the stacking run's frame weight and rotation rows and the
    astrometry run's target-clipping rows. One builder means they all share
    one subject id, so a reader can join them.

    Parameters
    ----------
    path : `str`
        The frame's path.
    target_id : `str`
        The target's id.
    timestamp : `float` or `None`
        The frame's start time (the frame record's ``timestamp``), which names
        its observing night. `None` leaves the night out.
    capture_setting_id : `int` or `None`, optional
        The frame's capture setting row, when the caller has it.
    channel : `str` or `None`, optional
        The color plane, for a row about one plane.

    Returns
    -------
    subject : `QualitySubject`
        Level ``frame``, with the id of `frame_subject_id`.
    """
    return QualitySubject(
        level=SubjectLevel.FRAME,
        subject_id=frame_subject_id(path, target_id),
        target_id=target_id,
        observing_night_id=observing_night_id(timestamp) if timestamp is not None else None,
        capture_setting_id=capture_setting_id,
        channel=channel,
    )


def _read_planes(path: str) -> tuple[list[tuple[str | None, np.ndarray]], np.ndarray, fits.Header]:
    """Read the pixels once and split them into the planes to measure.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.

    Returns
    -------
    planes : `list` of `tuple`
        ``(channel name or None, 2-D float array)``. A plain frame, or one
        raw mosaic, is one plane with no name. A frame stored as color planes
        gives one entry per plane.
    collapsed : `numpy.ndarray`
        The planes averaged into one image, for the metrics declared on the
        collapsed frame.
    header : `astropy.io.fits.Header`
        The header of the HDU that holds the pixels.

    Raises
    ------
    ValueError
        If the file holds no pixels.
    """
    with open_primary_hdu(path) as hdu:
        data = hdu.data
        header = hdu.header.copy()
    if data is None:
        raise ValueError("the file holds no pixel data")
    data = np.asarray(data, dtype=float)
    collapsed = collapse_to_2d(data)
    if data.ndim == 3 and data.shape[0] in (1, 3, 4):
        stack = list(data)
    elif data.ndim == 3:
        stack = [data[..., index] for index in range(data.shape[-1])]
    else:
        stack = [data]
    if len(stack) == 1:
        return [(None, stack[0])], collapsed, header
    names = _COLOR_PLANE_NAMES.get(len(stack), tuple(f"plane{index}" for index in range(len(stack))))
    return list(zip(names, stack, strict=True)), collapsed, header


def _pixel_scale_note(frame: FrameRecord) -> str:
    """Name the source of the pixel scale used for the arcsecond width.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.

    Returns
    -------
    note : `str`
        A sentence fragment for the measurement's reason.
    """
    return (
        f"pixel scale {frame.pixel_scale_arcsec:g} arcsec/px from the frame "
        "record (FrameRecord.pixel_scale_arcsec)"
    )


def _emit_star_profile_measurements(
    context: _Context, result: StarProfileResult, channel: str | None, mosaic_note: str
) -> None:
    """Turn one plane's star profile result into the four star measurements.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    result : `StarProfileResult`
        The result for one plane.
    channel : `str` or `None`
        The plane's name.
    mosaic_note : `str`
        Text added to the reasons for a raw mosaic, else empty.
    """
    star_metrics = (
        FRAME_STAR_WIDTH_MOFFAT_PX,
        FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
        FRAME_STAR_ELONGATION,
        FRAME_STAR_ELONGATION_ANGLE_DEG,
        FRAME_STAR_WIDTH_EFFECTIVE_PX,
        FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
        *FRAME_STAR_WIDTH_REGION_PX.values(),
    )
    if result.width_px is None:
        context.add_not_measured(star_metrics, result.reason, channel)
        return
    fit_note = (
        f"{result.star_count} stars from {result.candidate_count} tried ({result.refusals.describe()}), "
        f"shared Moffat beta {result.median_beta:.2f}{mosaic_note}"
    )
    context.add(
        FRAME_STAR_WIDTH_MOFFAT_PX,
        value=result.width_px,
        uncertainty=result.width_px_uncertainty,
        sample_count=result.star_count,
        reason=fit_note,
        channel=channel,
    )
    pixel_scale = context.frame.pixel_scale_arcsec
    if pixel_scale is None or not math.isfinite(pixel_scale) or pixel_scale <= 0.0:
        context.add(
            FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
            reason="the frame record has no pixel scale, so the width cannot be given in arcseconds",
            channel=channel,
        )
    else:
        context.add(
            FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
            value=result.width_px * pixel_scale,
            uncertainty=(result.width_px_uncertainty or 0.0) * pixel_scale,
            sample_count=result.star_count,
            reason=f"{fit_note}; {_pixel_scale_note(context.frame)}",
            channel=channel,
        )
    context.add(
        FRAME_STAR_ELONGATION,
        value=result.elongation,
        uncertainty=result.elongation_uncertainty,
        sample_count=result.star_count,
        reason=fit_note,
        channel=channel,
    )
    context.add(
        FRAME_STAR_ELONGATION_ANGLE_DEG,
        value=result.angle_deg,
        uncertainty=result.angle_uncertainty_deg,
        sample_count=result.star_count,
        reason=(
            f"{fit_note}; angles agree with a coherence of {result.angle_coherence:.2f} "
            "(0 means no preferred direction, so the angle means little)"
        ),
        channel=channel,
    )
    _emit_effective_width_measurements(context, result, channel, fit_note)
    _emit_field_region_measurements(context, result, channel, mosaic_note)


def _emit_effective_width_measurements(
    context: _Context, result: StarProfileResult, channel: str | None, fit_note: str
) -> None:
    """Add the pixel's blur to the frame's star width and store it twice.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    result : `StarProfileResult`
        The result for one plane, with a width.
    channel : `str` or `None`
        The plane's name.
    fit_note : `str`
        The sentence that says how many stars the width rests on.
    """
    width = result.width_px
    effective = effective_star_width_px(width)
    uncertainty = None
    if result.width_px_uncertainty is not None:
        # d(effective) / d(width) = width / effective: the pixel term is a
        # constant, so only the width's own error passes through.
        uncertainty = result.width_px_uncertainty * width / effective
    model_note = (
        f"model-derived: sqrt(FWHM^2 + c p^2) with c = 2 ln 2 / 3 = {PIXEL_BLUR_FWHM_SQUARED_FACTOR:.4f} "
        "(theory for a Gaussian core and a square pixel, not fitted) and p one original pixel; "
        "the error carries the width's bootstrap error only"
    )
    context.add(
        FRAME_STAR_WIDTH_EFFECTIVE_PX,
        value=effective,
        uncertainty=uncertainty,
        sample_count=result.star_count,
        reason=f"{fit_note}; {model_note}",
        channel=channel,
    )
    pixel_scale = context.frame.pixel_scale_arcsec
    if pixel_scale is None or not math.isfinite(pixel_scale) or pixel_scale <= 0.0:
        context.add(
            FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
            reason="the frame record has no pixel scale, so the width cannot be given in arcseconds",
            channel=channel,
        )
        return
    context.add(
        FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
        value=effective * pixel_scale,
        uncertainty=None if uncertainty is None else uncertainty * pixel_scale,
        sample_count=result.star_count,
        reason=f"{fit_note}; {model_note}; {_pixel_scale_note(context.frame)}",
        channel=channel,
    )


def _emit_field_region_measurements(
    context: _Context, result: StarProfileResult, channel: str | None, mosaic_note: str
) -> None:
    """Store the median star width of the center and of each corner.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    result : `StarProfileResult`
        The result for one plane, with a width.
    channel : `str` or `None`
        The plane's name.
    mosaic_note : `str`
        Text added to the reasons for a raw mosaic, else empty.
    """
    for region in measure_field_region_widths(result):
        metric = FRAME_STAR_WIDTH_REGION_PX[region.region]
        if region.width_px is None:
            context.add(
                metric,
                reason=(
                    f"only {region.star_count} fitted stars lie in this region of the field; "
                    f"at least {MOFFAT_MINIMUM_STAR_COUNT} are needed"
                ),
                channel=channel,
            )
            continue
        context.add(
            metric,
            value=region.width_px,
            uncertainty=region.width_px_uncertainty,
            sample_count=region.star_count,
            reason=(
                f"{region.star_count} of the frame's {result.star_count} fitted stars lie in this region "
                f"(a block of {FIELD_REGION_CELLS_PER_SIDE} x {FIELD_REGION_CELLS_PER_SIDE} of the "
                f"{FIELD_GRID_CELLS_PER_SIDE} x {FIELD_GRID_CELLS_PER_SIDE} grid cells){mosaic_note}"
            ),
            channel=channel,
        )


def _emit_sky_measurements(
    context: _Context,
    sky: SkyElectrons,
    read_noise: ProvenancedValue | None,
    read_noise_reason: str,
    channel: str | None,
    mosaic_note: str,
    uncooled_note: str = "",
) -> None:
    """Turn one plane's sky result and the read noise into measurements.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    sky : `SkyElectrons`
        The sky in electrons.
    read_noise : `ProvenancedValue` or `None`
        The read noise for the frame's setting.
    read_noise_reason : `str`
        Why the read noise is missing, when it is.
    channel : `str` or `None`
        The plane's name.
    mosaic_note : `str`
        Text added to the reasons for a raw mosaic, else empty.
    uncooled_note : `str`, optional
        Why the camera is taken as uncooled, or an empty string for a camera
        with a recorded sensor temperature. When it is given, the sky rows are
        stored as assumed, because the dark current and amp glow in the sky
        are then unknown and no dark frame was subtracted.
    """
    if sky.rate_e_per_s_per_px is None:
        context.add(FRAME_SKY_RATE_E_PER_S_PER_PX, reason=sky.reason, channel=channel)
        context.add(FRAME_SKY_NOISE_E, reason=sky.reason, channel=channel)
    else:
        is_measured = sky.gain_is_measured and not uncooled_note
        status = MeasurementStatus.MEASURED if is_measured else MeasurementStatus.ASSUMED
        note = sky.reason + ("" if sky.gain_is_measured else "; the gain is not measured")
        note += (f"; {uncooled_note}" if uncooled_note else "") + mosaic_note
        context.add(
            FRAME_SKY_RATE_E_PER_S_PER_PX,
            value=sky.rate_e_per_s_per_px,
            reason=note,
            channel=channel,
            status=status,
        )
        context.add(FRAME_SKY_NOISE_E, value=sky.noise_e, reason=note, channel=channel, status=status)
    context.add(FRAME_READ_NOISE_E, channel=channel, **_read_noise_arguments(read_noise, read_noise_reason))


def measure_frame(
    path: str,
    frame: FrameRecord,
    target_id: str,
    camera_profile: CameraProfile | None,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
    capture_setting_id: int | None,
    time_budget_seconds: float = FRAME_MEASUREMENT_TIME_BUDGET_SECONDS,
    bias_level_adu: float | None = None,
    measure_gaussian_width: Callable[[np.ndarray], float | None] | None = None,
    bias_level_reason: str = "",
    clipped_zero_fraction_limit: float | None = None,
) -> list[QualityMeasurement]:
    """Measure one light frame and return one measurement per frame metric.

    The pixels are read once. Nothing is written. A value that cannot be
    measured comes back ``not_measured`` with a reason.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.
    frame : `FrameRecord`
        The frame's record: its gain setting (``iso``), offset, binning,
        exposure, pixel scale, timestamp and temperatures.
    target_id : `str`
        The target the frame belongs to.
    camera_profile : `CameraProfile` or `None`
        The camera's profile, for the saturation level, the gain and the read
        noise. `None` makes every metric that needs it ``not_measured``.
    run_id : `str`
        The job id, or a generated id when there was no job.
    job_id : `str` or `None`
        A soft link to the job's activity record.
    measured_at : `datetime`
        When the measurement is made. It must carry a time zone.
    capture_setting_id : `int` or `None`
        The row of the capture-setting table for this frame.
    time_budget_seconds : `float`, optional
        The seconds allowed for the frame. See
        `FRAME_MEASUREMENT_TIME_BUDGET_SECONDS`.
    bias_level_adu : `float` or `None`, optional
        The bias level in ADU for the frame's gain, offset and binning, such as
        the median of the master bias. A raw frame carries it inside its pixel
        values and does not state it, so without it the sky metrics in
        electrons are ``not_measured``. This argument is an addition to the
        planned interface; leaving it out is valid.
    measure_gaussian_width : `Callable` or `None`, optional
        The function that gives `frame.star_width_gaussian_brightest_px` from
        the collapsed 2-D pixels, which is
        ``pipelines.astrometry.pre_processing.fwhm.measure_fwhm_from_data``.
        This module sits in a layer below the astrometry pipeline and may not
        import it, so the caller passes it in. Without it that metric is
        ``not_measured``. This argument is an addition to the planned
        interface.
    bias_level_reason : `str`, optional
        Why `bias_level_adu` is `None` (for example, that the calibration
        library holds no bias frames at the frame's setting). It becomes the
        reason of the sky metrics that need the bias level.
    clipped_zero_fraction_limit : `float` or `None`, optional
        A frame with more than this fraction of its pixels at exactly zero is
        clipped at zero. The caller passes
        ``CLIPPED_FRAME_ZERO_FRACTION`` (this folder may not import the
        stacking pipeline). A frame at or below it may use the camera
        profile's gain and read noise from another offset (the offset
        fallback). `None` turns the fallback off.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One per frame metric (and per color plane for the star and sky
        metrics of a frame stored as color planes).
    """
    stopwatch = Stopwatch(time_budget_seconds)
    context = _Context(
        frame=frame,
        target_id=target_id,
        run_id=run_id,
        job_id=job_id,
        measured_at=measured_at,
        capture_setting_id=capture_setting_id,
        path=path,
    )
    _emit_header_measurements(context)

    pixel_metrics = (
        FRAME_BACKGROUND_LEVEL,
        FRAME_SATURATED_PIXEL_FRACTION,
        FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
        FRAME_DETECTED_REGION_COUNT,
        FRAME_STAR_WIDTH_MOFFAT_PX,
        FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
        FRAME_STAR_ELONGATION,
        FRAME_STAR_ELONGATION_ANGLE_DEG,
        FRAME_STAR_WIDTH_EFFECTIVE_PX,
        FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
        *FRAME_STAR_WIDTH_REGION_PX.values(),
        FRAME_SKY_RATE_E_PER_S_PER_PX,
        FRAME_SKY_NOISE_E,
    )
    try:
        planes, collapsed, header = _read_planes(path)
    except FITS_READ_ERRORS as read_error:
        _, read_noise, noise_reason = look_up_frame_sensor_noise(camera_profile, frame)
        context.add_not_measured(pixel_metrics, f"{UNREADABLE_FRAME_REASON_PREFIX}: {read_error}")
        context.add(FRAME_READ_NOISE_E, **_read_noise_arguments(read_noise, noise_reason))
        return context.measurements

    # The offset fallback needs to know the frame is not cut off at zero.
    clipped_at_zero = (
        None
        if clipped_zero_fraction_limit is None
        else frame_zero_fraction(planes) > clipped_zero_fraction_limit
    )
    gain, read_noise, noise_reason = look_up_frame_sensor_noise(
        camera_profile, frame, clipped_at_zero=clipped_at_zero
    )

    is_mosaic = bool(str(header.get("BAYERPAT", "")).strip()) and len(planes) == 1
    mosaic_note = (
        f"; raw color mosaic: the sky and background use all color pixels mixed, and the star shapes use "
        f"{BAYER_CELL_SIZE_PX} x {BAYER_CELL_SIZE_PX} super-pixels (widths given back in original pixels)"
        if is_mosaic
        else ""
    )
    uncooled_note = (
        ""
        if frame.sensor_temperature_c is not None
        else (
            "the frame records no sensor temperature (no CCD-TEMP card), so the camera is taken as "
            "uncooled, and the dark current and amp glow in the sky are unknown"
        )
    )
    top_level = usable_top_level_adu(camera_profile)
    exposure_seconds = frame_exposure_seconds(frame)

    # Quick metrics first: they are returned even when the budget runs out.
    _emit_background_and_saturation(context, collapsed, camera_profile)
    for channel, plane in planes:
        # The sky uses the clipped mean, not the median of the background
        # metric: a 14-bit sensor stores steps of 4 ADU, and a median of such
        # data takes only a few values (see `measure_clipped_mean_adu`).
        sky = measure_sky_electrons(
            measure_clipped_mean_adu(plane),
            bias_level_adu,
            gain,
            exposure_seconds,
            noise_reason,
            bias_level_reason,
        )
        _emit_sky_measurements(context, sky, read_noise, noise_reason, channel, mosaic_note, uncooled_note)

    # Slow steps: star detection and fits.
    _emit_region_count(context, collapsed, stopwatch)
    _emit_gaussian_width(context, collapsed, stopwatch, measure_gaussian_width)
    for channel, plane in planes:
        if stopwatch.expired():
            result = StarProfileResult(reason=stopwatch.reason())
        else:
            try:
                result = measure_star_profiles(plane, top_level, stopwatch, bayer_mosaic=is_mosaic)
            except DATA_ERRORS as error:
                logger.warning("Moffat star profiles failed for %s: %s", path, error)
                result = StarProfileResult(reason=f"the star fits failed: {error}")
        _emit_star_profile_measurements(context, result, channel, mosaic_note)
    return context.measurements


def frame_zero_fraction(planes: list[tuple[str | None, np.ndarray]]) -> float:
    """Measure the fraction of a frame's pixels that are exactly zero.

    Parameters
    ----------
    planes : `list` of `tuple`
        The planes from `_read_planes`.

    Returns
    -------
    zero_fraction : `float`
        The share of all pixels, over every plane, at exactly zero. A frame
        cut off at zero has many; a frame with sky well above the offset has
        none.
    """
    total = sum(plane.size for _, plane in planes)
    zeros = sum(int(np.count_nonzero(plane == 0)) for _, plane in planes)
    return zeros / total if total else 0.0


def _read_noise_arguments(read_noise: ProvenancedValue | None, reason: str) -> dict:
    """Build the arguments of the read-noise measurement for `_Context.add`.

    Parameters
    ----------
    read_noise : `ProvenancedValue` or `None`
        The read noise for the frame's setting.
    reason : `str`
        Why it is missing.

    Returns
    -------
    arguments : `dict`
        Keyword arguments for `_Context.add`, without the metric name.
    """
    if read_noise is None:
        return {"reason": reason or "no read noise for this setting"}
    is_measured = read_noise.provenance.kind == ProvenanceKind.MEASURED
    return {
        "value": read_noise.value,
        "reason": provenance_text("read noise", read_noise),
        "status": MeasurementStatus.MEASURED if is_measured else MeasurementStatus.ASSUMED,
    }


def _emit_header_measurements(context: _Context) -> None:
    """Emit the two temperatures from the frame record.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    """
    frame = context.frame
    for metric, value, card in (
        (FRAME_SENSOR_TEMPERATURE, frame.sensor_temperature_c, "sensor temperature (header card CCD-TEMP)"),
        (
            FRAME_FOCUSER_TEMPERATURE,
            frame.focuser_temperature_c,
            "focuser temperature (header card FOCUSTEM)",
        ),
    ):
        context.add(
            metric, value=value, reason="" if value is not None else f"the frame record has no {card}"
        )


def _clipped_median(plane: np.ndarray) -> float:
    """Give the sigma-clipped median of a plane, as the background metric does.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The pixels.

    Returns
    -------
    median : `float`
        The median after clipping at 3 sigma (astropy's default 5 passes).
    """
    _, median, _ = sigma_clipped_stats(plane, sigma=3.0)
    return float(median)


# The clipping of the sky and bias estimator. 3 sigma and astropy's default
# of up to 5 passes are the same as the background metric's, so the two
# estimators differ only in the statistic taken from the clipped pixels (mean,
# not median).
SKY_CLIP_SIGMA = 3.0


def measure_clipped_mean_adu(plane: np.ndarray) -> float:
    """Give the sigma-clipped mean of a plane, for the sky and the bias level.

    The sky in electrons and the bias level are both found with this one
    estimator, so they cancel each other's bias. The mean is used, not the
    median, because the stored values are whole steps. A 14-bit sensor such as
    the ASI 533 writes its values in steps of 4 ADU, and the median of such a
    plane takes only a few values: on 61 real M 57 frames the clipped median
    took 13 different values (792 to 952 ADU, in steps of 4), so the sky in
    electrons moved in steps of 4 ADU times the gain. The mean of millions of
    pixels varies smoothly. Clipping at `SKY_CLIP_SIGMA` sigma (up to 5 passes)
    removes stars and hot pixels, which a plain mean would not.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The pixels.

    Returns
    -------
    mean_adu : `float`
        The mean of the pixels left after sigma clipping.
    """
    mean, _, _ = sigma_clipped_stats(plane, sigma=SKY_CLIP_SIGMA)
    return float(mean)


def _emit_background_and_saturation(
    context: _Context, collapsed: np.ndarray, camera_profile: CameraProfile | None
) -> float:
    """Emit the background level and saturated fraction of the collapsed frame.

    The background uses the same call as `measure_frame_background_level`
    (sigma-clipped median at 3 sigma), applied to the pixels already in memory
    so the file is not read again.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    collapsed : `numpy.ndarray`
        The frame with its color planes averaged to one.
    camera_profile : `CameraProfile` or `None`
        The camera's profile, for the saturation threshold.

    Returns
    -------
    background_adu : `float`
        The collapsed frame's background, for the sky metrics of a
        single-plane frame.
    """
    background_adu = _clipped_median(collapsed)
    context.add(FRAME_BACKGROUND_LEVEL, value=background_adu)
    if camera_profile is None:
        context.add(
            FRAME_SATURATED_PIXEL_FRACTION,
            reason="no camera profile was given, so the saturation level is unknown",
        )
    else:
        threshold = float(camera_profile.pixel_saturation_level_adu.value)
        context.add(
            FRAME_SATURATED_PIXEL_FRACTION,
            value=compute_saturated_pixel_fraction(collapsed, threshold),
            reason=f"{camera_profile.camera_name} profile saturation level {threshold:g} ADU",
        )
    return background_adu


def _emit_region_count(context: _Context, collapsed: np.ndarray, stopwatch: Stopwatch) -> None:
    """Emit the count of bright regions, if there is time left.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    collapsed : `numpy.ndarray`
        The collapsed frame.
    stopwatch : `Stopwatch`
        The frame's time budget.
    """
    if stopwatch.expired():
        context.add(FRAME_DETECTED_REGION_COUNT, reason=stopwatch.reason())
        return
    try:
        _, region_count, _, _ = detect_stars(collapsed)
    except DATA_ERRORS as error:
        context.add(FRAME_DETECTED_REGION_COUNT, reason=f"the bright-region count failed: {error}")
        return
    context.add(FRAME_DETECTED_REGION_COUNT, value=float(region_count))


def _emit_gaussian_width(
    context: _Context,
    collapsed: np.ndarray,
    stopwatch: Stopwatch,
    measure_gaussian_width: Callable[[np.ndarray], float | None] | None,
) -> None:
    """Emit the Gaussian width of the brightest stars, if there is time left.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    collapsed : `numpy.ndarray`
        The collapsed frame.
    stopwatch : `Stopwatch`
        The frame's time budget.
    measure_gaussian_width : `Callable` or `None`
        The width function the caller passed in, or `None`.
    """
    if measure_gaussian_width is None:
        context.add(
            FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
            reason="the caller did not pass the Gaussian width function",
        )
        return
    if stopwatch.expired():
        context.add(FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX, reason=stopwatch.reason())
        return
    try:
        width = measure_gaussian_width(collapsed)
    except DATA_ERRORS as error:
        context.add(FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX, reason=f"the Gaussian width failed: {error}")
        return
    context.add(
        FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
        value=width,
        reason="no star could be fitted" if width is None else "",
    )
