"""Purpose: Derive the performance limits for the equipment in use.

Description: Pure functions that work out every limit in a
`PerformanceEnvelope` from the equipment's dimensions, the camera's stored
profile, the image quality the equipment has actually achieved, and how the
same equipment has behaved in earlier sessions. Nothing here reads a file
or a database; the caller supplies the inputs, so the same inputs always
give the same limits and a change of equipment shows up as a change of
inputs.

Four kinds of limit are derived (see `ThresholdTier`):

* Geometry: plate scales, the size of the guide camera's field, and the
  fastest guide speed a mount can have.
* Sensor profile: the level at which a pixel counts as saturated.
* Physical budget: how large a guiding error or trailing can be before it
  widens a star image by more than a chosen fraction. This rests on the
  image quality the equipment really achieves, measured from its own frames.
* Own baseline: how low a guide-star signal, or how high a guiding error,
  has to be before it is unusual for this equipment. This rests on the
  equipment's own sessions and is not judged until there are enough of them.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from wayfindinglib.models.equipment_and_site.equipment import Camera, EquipmentConfiguration, GuideScope
from wayfindinglib.models.equipment_and_site.performance_envelope import (
    PerformanceEnvelope,
    PerformanceThreshold,
    ThresholdStatus,
    ThresholdTier,
)

DEFAULT_BLUR_TOLERANCE_FRACTION = 0.10
"""Most that guiding error or trailing may widen a star image, as a fraction.

This is a policy choice, not a measurement: it says how much loss of
sharpness to accept from guiding. Every physical-budget limit scales with
it, and a test checks that. On this observatory's measured 5.6 arcsecond
star width it gives 1.09 arcseconds of guiding error per axis. That sits in
the middle of the 0.8 to 1.4 arcseconds its 19 real guide logs show on good
nights, so the default neither calls every good night perfect nor every
good night a failure.
"""

SIGMA_TO_FWHM = 2.0 * math.sqrt(2.0 * math.log(2.0))
"""Width (FWHM) of a Gaussian in units of its standard deviation: 2.3548.

An exact property of the Gaussian curve, not an estimate.
"""

SIDEREAL_RATE_ARCSEC_PER_SECOND = 360.0 * 3600.0 / 86164.0905
"""How fast the sky turns, in arcseconds per second: 15.041.

The Earth turns 360 degrees in one sidereal day of 86164.0905 seconds.
"""

MAD_TO_SIGMA = 1.4826
"""Scales a median absolute deviation to a standard deviation (normal data)."""

BASELINE_SPREAD_MULTIPLIER = 3.0
"""How many robust standard deviations from the median count as unusual.

3 is the usual outlier convention. If the history were normally
distributed, about 1 session in 740 would land beyond it by chance in one
direction.
"""

EXCURSION_SIGMA_MULTIPLE = 5.0
"""How many times the acceptable guiding error counts as an excursion.

A guide error of 5 standard deviations or more happens by chance about once
in 1.7 million samples if the error is Gaussian noise at the acceptable
level. A sample that far out is therefore an event (a lock on the wrong
star, a jump), not noise, and pre-processing counts it separately.
"""

BASELINE_FRACTION_PERCENTILE = 90
"""Percentile of the equipment's own nights that a bad fraction must exceed.

Fractions such as the share of lost frames are zero on most good nights, so a
median plus a spread is degenerate: the spread is zero and any loss at all
would count as unusual. A percentile has no such problem. A night is unusual
if it is worse than 90 percent of this equipment's earlier nights.
"""

MINIMUM_GUIDE_CYCLES_PER_EXPOSURE = 3
"""Fewest guide cycles an exposure must span for guiding error to show in it.

The guider measures and corrects once per cycle, so guiding error changes
on about that timescale. An exposure of one or two cycles sees only one or
two values of the error, and its star width shows the seeing of that moment
rather than the guiding RMS. Three cycles is the smallest span that can show
the error varying at all.
"""

MINIMUM_IMAGE_QUALITY_SAMPLES = 20
"""Fewest frames the measured star width must rest on.

A median of fewer than 20 frames can be shifted by a single poor-seeing
night. 20 frames is about one night of one target.
"""

MINIMUM_SAMPLES_PER_SESSION = 100
"""Fewest guide samples a night needs to be analysed or to join a baseline.

The guider takes a sample every 3 to 4 seconds, so 100 samples is about six
minutes of guiding. A shorter run is a test or a false start, not a session
that says anything about how this equipment guides.
"""

MINIMUM_FRAMES_PER_NIGHT = 10
"""Fewest measured frames a night needs to join a star-quality baseline.

The median of 10 frames has a standard error of about 40 percent of one
frame's scatter (1.25 / sqrt(10)). Fewer would let the scatter of a handful
of frames, not the night, set the baseline.
"""

MINIMUM_ATTEMPTS_PER_NIGHT = 20
"""Fewest exposures a night needs to join the cancelled-exposure baseline.

A share of cancelled exposures from fewer than 20 attempts moves in steps of
5 percent or more, so one cancellation would look like a bad night.
"""

MINIMUM_BASELINE_SESSIONS = 5
"""Fewest sessions a baseline limit must rest on.

With fewer than 5, a robust spread (a median absolute deviation) is not
stable: one unusual session changes it a great deal.
"""


@dataclass(frozen=True)
class SensorLimits:
    """The saturation facts about one camera, with where each came from.

    Attributes
    ----------
    camera_name : `str`
        The camera the profile describes.
    clip_ceiling_adu : `float`
        The pixel value at which a saturated frame tops out.
    clip_ceiling_source : `str`
        Where that number came from (its kind and source).
    saturation_threshold_adu : `float`
        A pixel at or above this counts as saturated.
    saturation_threshold_source : `str`
        Where that number came from.
    is_generic_fallback : `bool`
        `True` if the camera had no profile of its own, so these are the
        cautious stand-in numbers, not facts about this camera.
    """

    camera_name: str
    clip_ceiling_adu: float
    clip_ceiling_source: str
    saturation_threshold_adu: float
    saturation_threshold_source: str
    is_generic_fallback: bool


@dataclass(frozen=True)
class MeasuredImageQuality:
    """How sharp this equipment's own images are.

    Attributes
    ----------
    fwhm_arcsec : `float`
        Median width of a star in the equipment's frames, in arcseconds.
    sample_count : `int`
        How many frames the median rests on.
    minimum_exposure_seconds : `float` or `None`
        Shortest exposure a frame needed to be included, or `None` if no
        cut was made. Short exposures are left out because guiding error
        only widens a star when the exposure spans several guide cycles.
    """

    fwhm_arcsec: float
    sample_count: int
    minimum_exposure_seconds: float | None = None


def robust_median_and_spread(values: Sequence[float]) -> tuple[float, float]:
    """Summarise values in a way a few bad ones cannot distort.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The values. At least one is required.

    Returns
    -------
    median : `float`
        The median.
    spread : `float`
        The median absolute deviation, scaled to match a standard deviation
        for normally distributed values.
    """
    array = np.asarray(values, dtype=float)
    median = float(np.median(array))
    return median, float(MAD_TO_SIGMA * np.median(np.abs(array - median)))


def _derived(
    name: str,
    value: float,
    unit: str,
    tier: ThresholdTier,
    derivation: str,
    inputs: dict[str, float | str | None],
    sample_count: int | None = None,
) -> PerformanceThreshold:
    """Build a limit that could be worked out.

    Returns
    -------
    threshold : `PerformanceThreshold`
        The limit, marked as derived.
    """
    return PerformanceThreshold(
        name=name,
        value=value,
        unit=unit,
        tier=tier,
        status=ThresholdStatus.DERIVED,
        derivation=derivation,
        inputs=inputs,
        sample_count=sample_count,
    )


def _insufficient(
    name: str,
    unit: str,
    tier: ThresholdTier,
    reason: str,
    inputs: dict[str, float | str | None] | None = None,
    sample_count: int | None = None,
) -> PerformanceThreshold:
    """Build a limit that could not be worked out, and say why.

    Returns
    -------
    threshold : `PerformanceThreshold`
        The limit, with no value and marked as lacking data.
    """
    return PerformanceThreshold(
        name=name,
        unit=unit,
        tier=tier,
        status=ThresholdStatus.INSUFFICIENT_DATA,
        derivation=reason,
        inputs=inputs or {},
        sample_count=sample_count,
    )


def _geometry_thresholds(
    equipment: EquipmentConfiguration, guide_scope: GuideScope | None, guide_camera: Camera | None
) -> list[PerformanceThreshold]:
    """Derive the limits that follow from dimensions and physics alone.

    Returns
    -------
    thresholds : `list` [`PerformanceThreshold`]
        Plate scales, the guide field size, and the fastest guide speed.
    """
    imaging_scale = equipment.plate_scale_arcsec_per_px
    guide_scale = equipment.guider_plate_scale_arcsec_per_px(guide_scope, guide_camera)
    guide_sensor = guide_camera or equipment.camera
    guide_focal_length = guide_scope.focal_length_mm if guide_scope else equipment.telescope.focal_length_mm
    guide_pixel_size = guide_sensor.pixel_size_um
    half_field = guide_scale * guide_sensor.sensor_width_px / 2.0
    return [
        _derived(
            "imaging_plate_scale",
            imaging_scale,
            "arcsec/px",
            ThresholdTier.GEOMETRY,
            "206.265 x pixel size (um) / focal length (mm)",
            {
                "pixel_size_um": equipment.camera.pixel_size_um,
                "focal_length_mm": equipment.telescope.focal_length_mm,
            },
        ),
        _derived(
            "guide_plate_scale",
            guide_scale,
            "arcsec/px",
            ThresholdTier.GEOMETRY,
            "206.265 x guide camera pixel size (um) / guide scope focal length (mm)",
            {"pixel_size_um": guide_pixel_size, "focal_length_mm": guide_focal_length},
        ),
        _derived(
            "max_credible_guide_error",
            half_field,
            "arcsec",
            ThresholdTier.GEOMETRY,
            "Half the guide camera's frame width. The guide star stays near the centre, "
            "so a measured offset larger than this cannot be the same star moving; "
            "the guider has locked onto a different one.",
            {"guide_plate_scale": guide_scale, "sensor_width_px": float(guide_sensor.sensor_width_px)},
        ),
        _derived(
            "max_credible_guide_speed",
            SIDEREAL_RATE_ARCSEC_PER_SECOND,
            "arcsec/s",
            ThresholdTier.GEOMETRY,
            "A mount's guide speed is a fraction of the sidereal rate, which is how fast "
            "the sky turns (360 degrees in 86164.0905 s). A calibration that measures a "
            "faster speed cannot be right.",
            {"sidereal_day_seconds": 86164.0905},
        ),
    ]


def _sensor_thresholds(sensor_limits: SensorLimits | None) -> list[PerformanceThreshold]:
    """Derive the saturation limits from the camera's stored profile.

    Returns
    -------
    thresholds : `list` [`PerformanceThreshold`]
        The clip ceiling and the saturation threshold, or limits marked as
        lacking data when no profile is given.
    """
    if sensor_limits is None:
        reason = "No camera profile was supplied."
        return [
            _insufficient("clip_ceiling", "ADU", ThresholdTier.SENSOR_PROFILE, reason),
            _insufficient("saturation_threshold", "ADU", ThresholdTier.SENSOR_PROFILE, reason),
        ]
    fallback_note = (
        " These are the generic stand-in numbers: this camera has no profile of its own."
        if sensor_limits.is_generic_fallback
        else ""
    )
    return [
        _derived(
            "clip_ceiling",
            sensor_limits.clip_ceiling_adu,
            "ADU",
            ThresholdTier.SENSOR_PROFILE,
            f"From the camera profile for {sensor_limits.camera_name}: {sensor_limits.clip_ceiling_source}"
            + fallback_note,
            {"camera": sensor_limits.camera_name},
        ),
        _derived(
            "saturation_threshold",
            sensor_limits.saturation_threshold_adu,
            "ADU",
            ThresholdTier.SENSOR_PROFILE,
            f"From the camera profile for {sensor_limits.camera_name}: "
            f"{sensor_limits.saturation_threshold_source}" + fallback_note,
            {"camera": sensor_limits.camera_name},
        ),
    ]


def _budget_thresholds(
    measured_image_quality: MeasuredImageQuality | None,
    blur_tolerance_fraction: float,
    minimum_samples: int,
) -> list[PerformanceThreshold]:
    """Derive the guiding-error and trailing limits from measured star width.

    Adding a blur of width ``b`` to a star of width ``w`` (both as FWHM,
    combined in quadrature, as widths of independent Gaussian blurs are)
    gives ``sqrt(w^2 + b^2)``. Requiring that to stay within ``(1 + f) w``
    gives ``b <= w * sqrt((1 + f)^2 - 1)``. Guiding error with standard
    deviation ``s`` per axis blurs by ``2.3548 s``, so the error limit is
    that budget divided by 2.3548. Trailing of length ``L`` adds about
    ``L`` to the width, so the trailing limit is the budget itself.

    Returns
    -------
    thresholds : `list` [`PerformanceThreshold`]
        The per-axis guiding RMS limit and the trailing limit, or limits
        marked as lacking data.
    """
    names = (("guiding_rms_limit", "arcsec"), ("trailing_limit", "arcsec"))
    if measured_image_quality is None:
        reason = "No frames have been measured for this equipment yet, so its star width is unknown."
        return [_insufficient(name, unit, ThresholdTier.PHYSICAL_BUDGET, reason) for name, unit in names]
    if measured_image_quality.sample_count < minimum_samples:
        reason = (
            f"Star width rests on {measured_image_quality.sample_count} frames; "
            f"at least {minimum_samples} are needed."
        )
        return [
            _insufficient(
                name,
                unit,
                ThresholdTier.PHYSICAL_BUDGET,
                reason,
                {"fwhm_arcsec": measured_image_quality.fwhm_arcsec},
                measured_image_quality.sample_count,
            )
            for name, unit in names
        ]
    fwhm = measured_image_quality.fwhm_arcsec
    budget = fwhm * math.sqrt((1.0 + blur_tolerance_fraction) ** 2 - 1.0)
    inputs: dict[str, float | str | None] = {
        "fwhm_arcsec": fwhm,
        "blur_tolerance_fraction": blur_tolerance_fraction,
        "minimum_exposure_seconds": measured_image_quality.minimum_exposure_seconds,
    }
    sample_count = measured_image_quality.sample_count
    return [
        _derived(
            "guiding_rms_limit",
            budget / SIGMA_TO_FWHM,
            "arcsec",
            ThresholdTier.PHYSICAL_BUDGET,
            f"Star width x sqrt((1 + {blur_tolerance_fraction:g})^2 - 1) / 2.3548: the per-axis "
            f"guiding error that widens a {fwhm:.2f} arcsec star by no more than "
            f"{blur_tolerance_fraction:.0%}.",
            inputs,
            sample_count,
        ),
        _derived(
            "trailing_limit",
            budget,
            "arcsec",
            ThresholdTier.PHYSICAL_BUDGET,
            f"Star width x sqrt((1 + {blur_tolerance_fraction:g})^2 - 1): the length of trailing "
            f"during one exposure that widens a {fwhm:.2f} arcsec star by no more than "
            f"{blur_tolerance_fraction:.0%}.",
            inputs,
            sample_count,
        ),
    ]


def _exposure_threshold(guide_cadence_seconds: float | None) -> PerformanceThreshold:
    """Derive the shortest exposure whose star measurements count.

    Parameters
    ----------
    guide_cadence_seconds : `float` or `None`
        The equipment's median time between guide samples, or `None` if it
        has not guided.

    Returns
    -------
    threshold : `PerformanceThreshold`
        The shortest exposure long enough to span a few guide cycles, or a
        limit marked as lacking data if the guide cycle is not known.
    """
    name = "minimum_star_measurement_exposure"
    if guide_cadence_seconds is None or guide_cadence_seconds <= 0:
        return _insufficient(
            name,
            "s",
            ThresholdTier.OWN_BASELINE,
            "The equipment's guide cycle is not known yet: no guiding session has been recorded for it.",
        )
    return _derived(
        name,
        MINIMUM_GUIDE_CYCLES_PER_EXPOSURE * guide_cadence_seconds,
        "s",
        ThresholdTier.OWN_BASELINE,
        f"{MINIMUM_GUIDE_CYCLES_PER_EXPOSURE} guide cycles of this equipment's own guider: the "
        "shortest exposure in which guiding error has time to vary.",
        {"guide_cadence_seconds": guide_cadence_seconds},
    )


def _excursion_threshold(
    guide_plate_scale: float, guiding_rms_limit: PerformanceThreshold
) -> PerformanceThreshold:
    """Derive the size of a guide error that is an event, not noise.

    The limit is the larger of two sizes. One is `EXCURSION_SIGMA_MULTIPLE`
    times the acceptable guiding error. The other is one guide pixel: the
    guider locates a star to a fraction of a pixel, so an error under one
    pixel is within what the measurement itself can produce. Both come from
    the equipment. When the acceptable guiding error is unknown, the pixel
    alone is used.

    Returns
    -------
    threshold : `PerformanceThreshold`
        The excursion limit, in arcseconds.
    """
    pixel = guide_plate_scale
    if guiding_rms_limit.value is None:
        return _derived(
            "guide_excursion_limit",
            pixel,
            "arcsec",
            ThresholdTier.GEOMETRY,
            "One guide pixel. The acceptable guiding error is not yet known, so the "
            "size of a statistical excursion cannot be worked out.",
            {"guide_plate_scale": guide_plate_scale},
        )
    statistical = EXCURSION_SIGMA_MULTIPLE * guiding_rms_limit.value
    return _derived(
        "guide_excursion_limit",
        max(statistical, pixel),
        "arcsec",
        ThresholdTier.PHYSICAL_BUDGET,
        f"The larger of {EXCURSION_SIGMA_MULTIPLE:g} x the acceptable guiding error "
        f"({statistical:.2f} arcsec) and one guide pixel ({pixel:.2f} arcsec). An error "
        "this large is an event, not noise.",
        {"guiding_rms_limit": guiding_rms_limit.value, "guide_plate_scale": guide_plate_scale},
        guiding_rms_limit.sample_count,
    )


def _baseline_thresholds(
    baseline_values: Mapping[str, Sequence[float]], minimum_sessions: int
) -> list[PerformanceThreshold]:
    """Derive the limits that rest on this equipment's own session history.

    Every quantity (guide-star SNR, guiding error, a night's star width and
    roundness) is positive and varies by factors, not by fixed amounts: a
    poor night's SNR is a fraction of a good night's, not a fixed number
    lower. So the median and spread
    are taken on their logarithms, and a limit is the median times
    ``exp(+/- 3 x spread)``. That can never go negative, which a limit of
    median minus 3 x spread can, as soon as the history holds two very
    different regimes (as a change of guide camera settings can cause).

    Returns
    -------
    thresholds : `list` [`PerformanceThreshold`]
        A lower limit for guide-star SNR and star roundness and an upper
        limit for guiding error and star width, each marked as lacking data
        until enough sessions exist.
    """
    specifications = (
        ("guide_snr", "guide_snr_low_limit", "", -1.0, "a guide star whose signal is below"),
        ("guiding_rms", "guiding_rms_high_limit", "arcsec", 1.0, "a session whose guiding error is above"),
        (
            "guide_star_mass",
            "guide_star_mass_low_limit",
            "counts",
            -1.0,
            "a guide star whose brightness is below",
        ),
        (
            "night_star_width",
            "night_star_width_high_limit",
            "arcsec",
            1.0,
            "a night whose median star width is above",
        ),
        (
            "night_star_roundness",
            "night_star_roundness_low_limit",
            "",
            -1.0,
            "a night whose median star roundness is below",
        ),
    )
    thresholds = []
    for series_name, limit_name, unit, direction, meaning in specifications:
        values = [
            value for value in baseline_values.get(series_name, ()) if math.isfinite(value) and value > 0
        ]
        if len(values) < minimum_sessions:
            thresholds.append(
                _insufficient(
                    limit_name,
                    unit,
                    ThresholdTier.OWN_BASELINE,
                    f"Rests on {len(values)} sessions with this equipment; at least "
                    f"{minimum_sessions} are needed before anything is called unusual.",
                    sample_count=len(values),
                )
            )
            continue
        log_median, log_spread = robust_median_and_spread([math.log(value) for value in values])
        limit = math.exp(log_median + direction * BASELINE_SPREAD_MULTIPLIER * log_spread)
        thresholds.append(
            _derived(
                limit_name,
                limit,
                unit,
                ThresholdTier.OWN_BASELINE,
                f"Median of this equipment's sessions times exp({direction * BASELINE_SPREAD_MULTIPLIER:+g} "
                f"x the robust spread of their logarithms): {meaning} this is unusual for this equipment.",
                {"median": math.exp(log_median), "log_spread": log_spread},
                len(values),
            )
        )
    return thresholds


def _percentile_baseline_thresholds(
    baseline_values: Mapping[str, Sequence[float]], minimum_sessions: int
) -> list[PerformanceThreshold]:
    """Derive upper limits for fractions that are zero on most good nights.

    Returns
    -------
    thresholds : `list` [`PerformanceThreshold`]
        An upper limit each for the share of lost guide frames, the share
        of guide-error excursions and the share of cancelled exposures,
        each marked as lacking data until enough sessions exist.
    """
    specifications = (
        ("guide_lost_fraction", "guide_lost_fraction_high_limit", "the share of guide frames lost"),
        (
            "guide_excursion_fraction",
            "guide_excursion_fraction_high_limit",
            "the share of guide-error excursions",
        ),
        (
            "capture_abort_fraction",
            "capture_abort_fraction_high_limit",
            "the share of exposures cancelled",
        ),
    )
    thresholds = []
    for series_name, limit_name, meaning in specifications:
        values = [
            value for value in baseline_values.get(series_name, ()) if math.isfinite(value) and value >= 0
        ]
        if len(values) < minimum_sessions:
            thresholds.append(
                _insufficient(
                    limit_name,
                    "fraction",
                    ThresholdTier.OWN_BASELINE,
                    f"Rests on {len(values)} sessions with this equipment; at least "
                    f"{minimum_sessions} are needed before anything is called unusual.",
                    sample_count=len(values),
                )
            )
            continue
        limit = float(np.percentile(values, BASELINE_FRACTION_PERCENTILE, method="inverted_cdf"))
        thresholds.append(
            _derived(
                limit_name,
                limit,
                "fraction",
                ThresholdTier.OWN_BASELINE,
                f"The {BASELINE_FRACTION_PERCENTILE}th percentile of {meaning} across this "
                "equipment's sessions: a session above it is worse than 90 percent of the earlier ones.",
                {"percentile": float(BASELINE_FRACTION_PERCENTILE)},
                len(values),
            )
        )
    return thresholds


def derive_performance_envelope(
    equipment: EquipmentConfiguration,
    guide_scope: GuideScope | None,
    guide_camera: Camera | None,
    equipment_fingerprint: str,
    sensor_limits: SensorLimits | None = None,
    measured_image_quality: MeasuredImageQuality | None = None,
    baseline_values: Mapping[str, Sequence[float]] | None = None,
    guide_cadence_seconds: float | None = None,
    blur_tolerance_fraction: float = DEFAULT_BLUR_TOLERANCE_FRACTION,
    minimum_image_quality_samples: int = MINIMUM_IMAGE_QUALITY_SAMPLES,
    minimum_baseline_sessions: int = MINIMUM_BASELINE_SESSIONS,
) -> PerformanceEnvelope:
    """Work out every limit for the equipment described by the inputs.

    Parameters
    ----------
    equipment : `EquipmentConfiguration`
        The active imaging telescope and camera.
    guide_scope : `GuideScope` or `None`
        The guide scope, or `None` if the main telescope guides.
    guide_camera : `Camera` or `None`
        The guide camera, or `None` if the main camera guides.
    equipment_fingerprint : `str`
        Identifies this equipment (see `build_equipment_fingerprint`).
    sensor_limits : `SensorLimits` or `None`, optional
        The imaging camera's saturation facts.
    measured_image_quality : `MeasuredImageQuality` or `None`, optional
        How sharp this equipment's own frames are.
    baseline_values : `Mapping` or `None`, optional
        One value per earlier session with this equipment: ``"guide_snr"``
        (median guide-star SNR), ``"guiding_rms"`` (guiding error, in
        arcseconds), ``"guide_star_mass"`` (median guide-star brightness, in
        camera counts),
        ``"guide_lost_fraction"``, ``"guide_excursion_fraction"``,
        ``"night_star_width"`` (median star width, in arcseconds),
        ``"night_star_roundness"`` and ``"capture_abort_fraction"``.
    guide_cadence_seconds : `float` or `None`, optional
        The equipment's median time between guide samples.
    blur_tolerance_fraction : `float`, optional
        The most guiding error and trailing may widen a star image.
    minimum_image_quality_samples : `int`, optional
        Fewest frames the measured star width must rest on.
    minimum_baseline_sessions : `int`, optional
        Fewest sessions a baseline limit must rest on.

    Returns
    -------
    envelope : `PerformanceEnvelope`
        Every limit. A limit that could not be worked out has no value and
        says why.
    """
    geometry = _geometry_thresholds(equipment, guide_scope, guide_camera)
    budget = _budget_thresholds(
        measured_image_quality, blur_tolerance_fraction, minimum_image_quality_samples
    )
    guide_plate_scale = next(t.value for t in geometry if t.name == "guide_plate_scale")
    guiding_rms_limit = next(t for t in budget if t.name == "guiding_rms_limit")
    thresholds = [
        *geometry,
        *_sensor_thresholds(sensor_limits),
        *budget,
        _excursion_threshold(guide_plate_scale, guiding_rms_limit),
        _exposure_threshold(guide_cadence_seconds),
        *_baseline_thresholds(baseline_values or {}, minimum_baseline_sessions),
        *_percentile_baseline_thresholds(baseline_values or {}, minimum_baseline_sessions),
    ]
    return PerformanceEnvelope(
        equipment_fingerprint=equipment_fingerprint,
        blur_tolerance_fraction=blur_tolerance_fraction,
        thresholds={threshold.name: threshold for threshold in thresholds},
    )
