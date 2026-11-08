"""Purpose: Choose the centering exposure from the equipment's measurements.

Description: A plate solve needs enough stars in the frame, and how many stars
a frame shows depends on its exposure. A frame that is too short shows too few
stars and every solve fails. This module works out the shortest exposure that
shows enough catalog stars, from numbers the equipment measured for itself.

The steps are:

1. Read what the library measured for the active telescope and camera: the
   Gaia G zero point and the star width of recent frames, and the camera's
   gain and read noise from its profile.
2. Predict the sky background for the pointing and the time
   (`sky_rate_tasks.get_sky_rate_prediction`).
3. For each exposure on a short ladder, find the faintest star that is still
   detected (`limiting_magnitude`), and count the Gaia stars that bright in
   the field.
4. Take the shortest exposure whose count reaches the target. If the longest
   one still falls short, use it and say so.

If any input is missing, the fixed ``alignment_exposure_seconds`` setting is
used, and the result says which input was missing. Nothing here writes or
moves anything.

The model is a point-source signal-to-noise ratio (Howell, *Handbook of
CCD Astronomy*, 2nd ed., eq. 3.1) with the aperture replaced by the
noise-equivalent area of the star detector's matched filter.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from astrometricslib.exceptions import AstrometricsError, ConfigurationError
from astrometricslib.reports import CaptureSetting, QualityMeasurement
from wayfindinglib.analytics.capture_history import equipment_spellings, normalize_name
from wayfindinglib.analytics.result_quality.efficiency import STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, baseline_key
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.models.session.centering_exposure import CenteringExposure, CenteringExposureTrial
from wayfindinglib.models.sky_position import SkyPosition
from wayfindinglib.tasks.control_tasks import sky_rate_tasks

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

__all__ = [
    "choose_centering_exposure",
    "choose_exposure",
    "limiting_magnitude",
    "point_source_signal_to_noise",
    "predict_star_counts",
]

logger = logging.getLogger(__name__)

ZERO_POINT_METRIC = "photometry.catalog_zero_point_gaia_g_mag"
"""The per-frame Gaia G zero point. Its convention (the estimator text of the
metric) is ``m + 2.5 log10(flux in ADU per second)`` for a star of magnitude
``m``, so a star of magnitude ``m`` gives ``10**(0.4 * (zero_point - m))`` ADU
per second. Gaia G is the band of the star catalog used for the count."""

EXPOSURE_LADDER_SECONDS: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0)
"""The exposures tried, shortest first. The first is 1 s, the exposure
centering used before this model and the shortest worth the settle and
readout time. The steps grow by a factor of 1.5 to 2, which is 0.4 to 0.75
magnitude each (2.5 log10 of the ratio), small enough that the chosen step is
at most one step longer than needed. The last, 30 s, is a trailing limit:
centering is unguided, and at an assumed drift of a few arcseconds over 30 s a
star stays within a few pixels at 1.9 arcsec per pixel (the drift is not yet
measured for this mount). Design estimate, register row WAY-D122."""

TARGET_STAR_COUNT = 15
"""Detectable catalog stars the exposure should reach. The local solver
needs at least 10 detected sources (`MINIMUM_SOURCES_FOR_LOCAL_SOLVE`), and a
detection list also holds hot pixels and merged pairs and misses stars the
model counts, so 15 leaves a margin of half again. Design estimate, register
row WAY-D122."""

DETECTION_SIGNAL_TO_NOISE = 5.0
"""Signal-to-noise ratio at which the star detector finds a star. It is the
default ``threshold_sigma`` of `SourceDetector`, which the plate solve uses
(`StarIdentifier` builds `SourceDetector()` with defaults)."""

ZERO_POINT_LOOKBACK_DAYS = 180.0
"""How far back frames count. About one observing season: long enough to hold
several nights of a part-time observatory, short enough that a cleaned mirror
or a new filter shows. Design estimate, register row WAY-D122."""

MAXIMUM_FRAMES_USED = 300
"""The most recent frames whose zero point and star width are medians. A night
has 20 to 100 frames, so this is a few nights, and the median shrugs off the
cloudy frames among them. Design estimate, register row WAY-D122."""

MINIMUM_FRAMES_USED = 5
"""Fewest frames that make the median trustworthy. With fewer, the fixed
setting is used. Design estimate, register row WAY-D122."""

FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
"""Full width at half maximum of a Gaussian, in sigmas (2.3548)."""

ARCSEC_PER_RADIAN = 206264.806
"""Arcseconds in a radian."""


@dataclass(frozen=True)
class SensorAndSky:
    """The numbers that fix a star's signal and noise.

    Attributes
    ----------
    zero_point_mag : `float`
        Gaia G zero point for ADU per second.
    gain_e_per_adu : `float`
        Electrons per ADU.
    read_noise_e : `float`
        Read noise per pixel, in electrons (RMS).
    sky_rate_e_per_s_per_px : `float`
        Sky background, in electrons per second per pixel.
    star_width_px : `float`
        Star full width at half maximum, in pixels.
    """

    zero_point_mag: float
    gain_e_per_adu: float
    read_noise_e: float
    sky_rate_e_per_s_per_px: float
    star_width_px: float

    @property
    def noise_pixels(self) -> float:
        """Give the star's noise-equivalent area, in pixels.

        For a Gaussian star, the matched filter that finds it collects all of
        its light from ``4 pi sigma**2`` pixels' worth of noise.

        Returns
        -------
        area : `float`
            ``4 pi (width / 2.3548)**2``, about ``2.27 width**2``.
        """
        sigma = self.star_width_px / FWHM_PER_SIGMA
        return 4.0 * math.pi * sigma * sigma


def star_electrons(magnitude: float, exposure_seconds: float, sensor: SensorAndSky) -> float:
    """Give the electrons a star of a magnitude collects in an exposure.

    Parameters
    ----------
    magnitude : `float`
        Gaia G magnitude.
    exposure_seconds : `float`
        Exposure, in seconds.
    sensor : `SensorAndSky`
        The zero point and the gain.

    Returns
    -------
    electrons : `float`
        ``gain * 10**(0.4 (zero_point - m)) * t``.
    """
    return sensor.gain_e_per_adu * 10.0 ** (0.4 * (sensor.zero_point_mag - magnitude)) * exposure_seconds


def point_source_signal_to_noise(magnitude: float, exposure_seconds: float, sensor: SensorAndSky) -> float:
    """Give the signal-to-noise ratio of a star in one exposure.

    ``S / sqrt(S + n (B t + R**2))``: the star's electrons ``S``, its own shot
    noise, and over the ``n`` pixels of the star the sky electrons ``B t`` and
    the squared read noise ``R**2``. Dark current is left out: the camera is
    cooled and a centering frame is short.

    Parameters
    ----------
    magnitude : `float`
        Gaia G magnitude.
    exposure_seconds : `float`
        Exposure, in seconds.
    sensor : `SensorAndSky`
        The camera, sky and star-width numbers.

    Returns
    -------
    ratio : `float`
        The signal-to-noise ratio. It rises with exposure time.
    """
    signal = star_electrons(magnitude, exposure_seconds, sensor)
    background = sensor.sky_rate_e_per_s_per_px * exposure_seconds + sensor.read_noise_e**2
    return signal / math.sqrt(signal + sensor.noise_pixels * background)


def limiting_magnitude(
    exposure_seconds: float, sensor: SensorAndSky, signal_to_noise: float = DETECTION_SIGNAL_TO_NOISE
) -> float:
    """Give the faintest magnitude that reaches a signal-to-noise ratio.

    Solves ``S / sqrt(S + n V) = q`` for ``S`` (with ``V = B t + R**2``) as
    ``S = (q**2 + sqrt(q**4 + 4 q**2 n V)) / 2`` and turns ``S`` back into a
    magnitude with the zero point.

    Parameters
    ----------
    exposure_seconds : `float`
        Exposure, in seconds.
    sensor : `SensorAndSky`
        The camera, sky and star-width numbers.
    signal_to_noise : `float`, optional
        The ratio a star must reach, `DETECTION_SIGNAL_TO_NOISE` by default.

    Returns
    -------
    magnitude : `float`
        Gaia G magnitude. It rises with exposure time.
    """
    background = sensor.sky_rate_e_per_s_per_px * exposure_seconds + sensor.read_noise_e**2
    square = signal_to_noise**2
    electrons = (square + math.sqrt(square * square + 4.0 * square * sensor.noise_pixels * background)) / 2.0
    adu_per_second = electrons / sensor.gain_e_per_adu / exposure_seconds
    return sensor.zero_point_mag - 2.5 * math.log10(adu_per_second)


def choose_exposure(
    sensor: SensorAndSky,
    magnitudes: Sequence[float],
    catalog_limit_magnitude: float | None = None,
    catalog_complete: bool = True,
    target_star_count: int = TARGET_STAR_COUNT,
    ladder: Sequence[float] = EXPOSURE_LADDER_SECONDS,
) -> tuple[float, list[CenteringExposureTrial], bool, bool]:
    """Pick the shortest exposure on the ladder that shows enough stars.

    Parameters
    ----------
    sensor : `SensorAndSky`
        The camera, sky and star-width numbers.
    magnitudes : `Sequence` [`float`]
        Gaia G magnitudes of the catalog stars in the field.
    catalog_limit_magnitude : `float`, optional
        Faintest magnitude the catalog holds. Unknown when omitted.
    catalog_complete : `bool`, optional
        Whether the whole sky is downloaded.
    target_star_count : `int`, optional
        Stars the exposure should reach.
    ladder : `Sequence` [`float`], optional
        Candidate exposures, shortest first.

    Returns
    -------
    exposure_seconds : `float`
        The shortest ladder step that reaches the target, else the longest.
    trials : `list` [`CenteringExposureTrial`]
        Every ladder step up to the chosen one, with its limit and count.
    reaches_target : `bool`
        Whether the chosen step reaches the target.
    lower_bound : `bool`
        Whether the catalog is partly downloaded or shallower than the
        chosen step's limiting magnitude, so the count is a lower bound.
    """
    ordered = sorted(magnitudes)
    trials: list[CenteringExposureTrial] = []
    lower_bound = False
    for exposure in ladder:
        limit = limiting_magnitude(exposure, sensor)
        count = sum(1 for magnitude in ordered if magnitude <= limit)
        trials.append(
            CenteringExposureTrial(
                exposure_seconds=exposure, limiting_magnitude=limit, expected_star_count=count
            )
        )
        lower_bound = not catalog_complete or (
            catalog_limit_magnitude is not None and limit > catalog_limit_magnitude
        )
        if count >= target_star_count:
            return exposure, trials, True, lower_bound
    return ladder[-1], trials, False, lower_bound


def _median_recent(rows: Sequence[QualityMeasurement]) -> tuple[float, int] | None:
    """Take the median value of the newest rows that hold a number.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        Frame rows of one metric.

    Returns
    -------
    median, count : `tuple` or `None`
        The median of the newest `MAXIMUM_FRAMES_USED` values and how many
        values it used, or `None` when no row holds a number.
    """
    values = [
        row.value
        for row in sorted(rows, key=lambda item: item.measured_at, reverse=True)
        if row.value is not None and math.isfinite(row.value)
    ][:MAXIMUM_FRAMES_USED]
    if not values:
        return None
    return statistics.median(values), len(values)


class _MissingInputError(Exception):
    """An input of the calculation is missing; the message says which."""


@dataclass(frozen=True)
class _Equipment:
    """The active telescope and camera, as far as the field needs them.

    Attributes
    ----------
    camera_name, telescope_name : `str`
        The names of the active camera and telescope.
    focal_length_mm, pixel_size_um : `float`
        The telescope's focal length and the camera's pixel pitch.
    width_px, height_px : `int`
        The sensor size, in pixels.
    """

    camera_name: str
    telescope_name: str
    focal_length_mm: float
    pixel_size_um: float
    width_px: int
    height_px: int


def _active_equipment(context: ControlContext) -> _Equipment:
    """Read the active telescope and camera.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the equipment settings.

    Returns
    -------
    equipment : `_Equipment`
        The names, focal length, pixel size and sensor size.

    Raises
    ------
    _MissingInputError
        If no telescope or no camera is active.
    """
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        raise _MissingInputError("no imaging telescope and camera are active")
    return _Equipment(
        camera_name=camera.name,
        telescope_name=telescope.name,
        focal_length_mm=telescope.focal_length_mm,
        pixel_size_um=camera.pixel_size_um,
        width_px=camera.sensor_width_px,
        height_px=camera.sensor_height_px,
    )


def _library_rows(
    context: ControlContext, equipment: _Equipment, now: float, filter_name: str | None
) -> tuple[CaptureSetting, list[QualityMeasurement], list[QualityMeasurement]]:
    """Find the recent zero point and star width rows of the active equipment.

    Of the capture settings (filter, gain, offset, binning) of the active
    camera and telescope, the one with the most zero point rows is used, or
    the one of ``filter_name`` when given.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library.
    equipment : `_Equipment`
        The active camera and telescope.
    now : `float`
        The time, in seconds since the Unix epoch.
    filter_name : `str` or `None`
        Use only this filter's frames when given.

    Returns
    -------
    setting, zero_point_rows, width_rows : `tuple`
        The chosen setting and its zero point and star width rows.

    Raises
    ------
    _MissingInputError
        If the library has no zero point for the equipment.
    """
    from datetime import UTC, datetime

    astrometrics = context.astrometrics
    since = datetime.fromtimestamp(now - ZERO_POINT_LOOKBACK_DAYS * 86400.0, UTC)
    settings = astrometrics.quality.capture_settings()
    camera_names = equipment_spellings(astrometrics, equipment.camera_name)
    telescope_name = normalize_name(equipment.telescope_name)
    by_key: dict[str, list[QualityMeasurement]] = {}
    chosen: dict[str, CaptureSetting] = {}
    zero_points = astrometrics.quality.query(
        metric=ZERO_POINT_METRIC, level="frame", since=since, library_only=True
    )
    widths = astrometrics.quality.query(
        metric=STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, level="frame", since=since, library_only=True
    )
    # The photometry rows carry no capture setting; the frame measurements of
    # the same frame do, so a zero point takes its frame's setting from them.
    setting_by_frame = {
        row.subject.subject_id: row.subject.capture_setting_id
        for row in widths.rows
        if row.subject.capture_setting_id
    }
    for row in zero_points.rows:
        setting_id = row.subject.capture_setting_id or setting_by_frame.get(row.subject.subject_id)
        setting = settings.get(setting_id) if setting_id else None
        if setting is None or normalize_name(setting.camera) not in camera_names:
            continue
        if normalize_name(setting.telescope) != telescope_name:
            continue
        if filter_name is not None and setting.filter_name != filter_name:
            continue
        key = baseline_key(setting)
        by_key.setdefault(key, []).append(row)
        chosen.setdefault(key, setting)
    if not by_key:
        raise _MissingInputError(
            "the library has no recent Gaia G zero point for this telescope and camera "
            "(run photometry on a target imaged with them)"
        )
    key = max(by_key, key=lambda name: len(by_key[name]))
    width_rows = [
        row
        for row in widths.rows
        if (found := settings.get(row.subject.capture_setting_id) if row.subject.capture_setting_id else None)
        is not None
        and baseline_key(found) == key
    ]
    return chosen[key], by_key[key], width_rows


def _sensor_numbers(
    context: ControlContext,
    equipment: _Equipment,
    setting: CaptureSetting,
    zero_point_rows: Sequence[QualityMeasurement],
    width_rows: Sequence[QualityMeasurement],
    position: SkyPosition,
    now: float,
) -> tuple[SensorAndSky, dict[str, Any]]:
    """Gather the camera, sky and star-width numbers.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library and the sky model.
    equipment : `_Equipment`
        The active camera and telescope.
    setting : `CaptureSetting`
        The capture setting the rows belong to.
    zero_point_rows, width_rows : `Sequence` [`QualityMeasurement`]
        The setting's recent rows.
    position : `SkyPosition`
        The pointing.
    now : `float`
        The time, in seconds since the Unix epoch.

    Returns
    -------
    sensor, facts : `tuple`
        The numbers for the model and a dictionary of the extra facts the
        result reports (pixel scale, frame count and so on).

    Raises
    ------
    _MissingInputError
        If a number is missing.
    """
    zero_point = _median_recent(zero_point_rows)
    if zero_point is None or zero_point[1] < MINIMUM_FRAMES_USED:
        raise _MissingInputError(
            f"fewer than {MINIMUM_FRAMES_USED} recent frames have a Gaia G zero point for this equipment"
        )
    width = _median_recent(width_rows)
    if width is None:
        raise _MissingInputError("the library has no star width for this equipment")
    binning = setting.binning or 1
    pixel_scale = (
        ARCSEC_PER_RADIAN * equipment.pixel_size_um * 1e-6 * binning / (equipment.focal_length_mm * 1e-3)
    )
    profile = context.astrometrics.processing.calibration.camera_profile(setting.camera)
    if profile.is_generic_fallback:
        raise _MissingInputError("the camera has no profile, so its gain and read noise are unknown")
    entry = profile.sensor_noise_for(
        setting.gain_setting, setting.offset or 0, binning, clipped_at_zero=False
    )
    if entry is None or entry.gain_e_per_adu is None or entry.read_noise_e is None:
        raise _MissingInputError("the camera profile has no gain and read noise for the capture setting")
    prediction = sky_rate_tasks.get_sky_rate_prediction(
        context, now, position.ra_deg, position.dec_deg, setting.filter_name
    )
    if prediction.sky_rate_e_per_s_per_px is None:
        raise _MissingInputError(f"no sky rate prediction ({prediction.message.strip()})")
    sensor = SensorAndSky(
        zero_point_mag=zero_point[0],
        gain_e_per_adu=float(entry.gain_e_per_adu.value),
        read_noise_e=float(entry.read_noise_e.value),
        sky_rate_e_per_s_per_px=prediction.sky_rate_e_per_s_per_px,
        star_width_px=width[0] / pixel_scale,
    )
    if min(sensor.gain_e_per_adu, sensor.star_width_px) <= 0.0:
        raise _MissingInputError("the gain or the star width is not positive")
    field_radius = min(equipment.width_px, equipment.height_px) / binning * pixel_scale / 2.0 / 3600.0
    return sensor, {
        "pixel_scale_arcsec": pixel_scale,
        "field_radius_deg": field_radius,
        "zero_point_frame_count": zero_point[1],
        "star_width_arcsec": width[0],
        "setting_key": baseline_key(setting),
    }


def _fallback(context: ControlContext, reason: str) -> CenteringExposure:
    """Give the fixed setting as the answer, with the reason.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the ``alignment_exposure_seconds`` setting.
    reason : `str`
        Which input was missing.

    Returns
    -------
    result : `CenteringExposure`
        The fixed exposure, marked as the fallback.
    """
    exposure = context.correction_config.alignment_exposure_seconds
    return CenteringExposure(
        exposure_seconds=exposure,
        source="fallback",
        reason=f"Used the fixed {exposure:g} s setting (alignment_exposure_seconds) because {reason}.",
        target_star_count=TARGET_STAR_COUNT,
    )


def _gather_inputs(
    context: ControlContext,
    position: SkyPosition,
    now: float,
    filter_name: str | None,
    find_stars: Callable[..., list[tuple[int, float, float, float]] | None],
    catalog_status: Callable[[Any], dict[str, Any]],
    deepest_exposure: float = EXPOSURE_LADDER_SECONDS[-1],
) -> tuple[SensorAndSky, dict[str, Any], list[tuple[int, float, float, float]], dict[str, Any]]:
    """Read every input of the calculation.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the equipment, the science library and the configuration.
    position : `SkyPosition`
        The pointing.
    now : `float`
        The time, in seconds since the Unix epoch.
    filter_name : `str` or `None`
        Use only this filter's frames when given.
    find_stars : `Callable`
        Looks up the Gaia stars in a circle of sky.
    catalog_status : `Callable`
        Reads how much of the star catalog is installed.
    deepest_exposure : `float`, optional
        The longest exposure that will be asked about, in seconds. Stars are
        read down to its limiting magnitude.

    Returns
    -------
    sensor, facts, stars, status : `tuple`
        The model's numbers, the extra facts, the catalog stars in the field
        (down to the limit of the longest exposure) and the catalog status.

    Raises
    ------
    _MissingInputError
        If an input is missing; the message says which.
    """
    equipment = _active_equipment(context)
    setting, zero_point_rows, width_rows = _library_rows(context, equipment, now, filter_name)
    sensor, facts = _sensor_numbers(context, equipment, setting, zero_point_rows, width_rows, position, now)
    status = catalog_status(context.config)
    if not status.get("installed"):
        raise _MissingInputError("the deep star catalog is not installed")
    stars = find_stars(
        context.config,
        position.ra_deg,
        position.dec_deg,
        facts["field_radius_deg"],
        limiting_magnitude(deepest_exposure, sensor),
    )
    if stars is None:
        raise _MissingInputError("the deep star catalog could not be read")
    if not stars and not status.get("complete"):
        # A partly downloaded catalog with no stars here has not covered this
        # part of the sky yet; a count of zero says nothing about the field.
        raise _MissingInputError("the deep star catalog has not been downloaded for this part of the sky yet")
    return sensor, facts, stars, status


def predict_star_counts(
    context: ControlContext,
    position: SkyPosition,
    exposures: Sequence[float],
    epoch_seconds: float | None = None,
    filter_name: str | None = None,
) -> tuple[list[CenteringExposureTrial], dict[str, Any], str | None]:
    """Predict the limiting magnitude and star count of given exposures.

    This is the model of `choose_centering_exposure` asked about exposures
    of the caller's choosing. The validation script uses it to compare the
    prediction with the stars real frames show.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the equipment, the science library and the configuration.
    position : `SkyPosition`
        Where the telescope points, in ICRS (J2000) degrees.
    exposures : `Sequence` [`float`]
        The exposures to predict, in seconds.
    epoch_seconds : `float`, optional
        When the frame was taken, in seconds since the Unix epoch. Now when
        omitted.
    filter_name : `str`, optional
        Use this filter's measurements.

    Returns
    -------
    trials, facts, missing : `tuple`
        One `CenteringExposureTrial` per exposure (counted inside the
        inscribed circle of the field), the extra facts (pixel scale, field
        radius, frame count, star width, setting key) and `None`; or an empty
        list, empty facts and the reason an input was missing.
    """
    from wayfindinglib.drivers.catalog import deep_star_store

    now = time.time() if epoch_seconds is None else epoch_seconds
    try:
        sensor, facts, stars, _ = _gather_inputs(
            context,
            position,
            now,
            filter_name,
            deep_star_store.find_deep_stars,
            deep_star_store.read_catalog_status,
            deepest_exposure=max(exposures),
        )
    except _MissingInputError as missing:
        return [], {}, str(missing)
    magnitudes = sorted(star[3] for star in stars)
    trials = []
    for exposure in exposures:
        limit = limiting_magnitude(exposure, sensor)
        count = sum(1 for magnitude in magnitudes if magnitude <= limit)
        trials.append(
            CenteringExposureTrial(
                exposure_seconds=exposure, limiting_magnitude=limit, expected_star_count=count
            )
        )
    return trials, facts, None


def choose_centering_exposure(
    context: ControlContext,
    position: SkyPosition,
    epoch_seconds: float | None = None,
    filter_name: str | None = None,
    find_stars: Callable[..., list[tuple[int, float, float, float]] | None] | None = None,
    catalog_status: Callable[[Any], dict[str, Any]] | None = None,
) -> CenteringExposure:
    """Choose the centering exposure for a pointing.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the equipment, the science library, the star catalog's
        location and the fixed fallback setting.
    position : `SkyPosition`
        Where the telescope will point, in ICRS (J2000) degrees.
    epoch_seconds : `float`, optional
        When the frame will be taken, in seconds since the Unix epoch. Now
        when omitted.
    filter_name : `str`, optional
        Use this filter's measurements. The filter with the most zero point
        frames when omitted.
    find_stars, catalog_status : `Callable`, optional
        Replacements for the deep-star catalog lookups, for tests.

    Returns
    -------
    result : `CenteringExposure`
        The exposure, whether it was computed or the fallback, the reason and
        the numbers used. A missing input never raises; it gives the
        fallback.
    """
    from wayfindinglib.drivers.catalog import deep_star_store

    now = time.time() if epoch_seconds is None else epoch_seconds
    try:
        sensor, facts, stars, status = _gather_inputs(
            context,
            position,
            now,
            filter_name,
            find_stars or deep_star_store.find_deep_stars,
            catalog_status or deep_star_store.read_catalog_status,
        )
    except _MissingInputError as missing:
        return _fallback(context, str(missing))
    except (AstrometricsError, ConfigurationError, OSError, ValueError, sqlite3.Error) as error:
        logger.debug("Centering exposure inputs could not be read: %s", error)
        return _fallback(context, f"an input could not be read ({error})")
    catalog_limit = status.get("magnitude_limit")
    exposure, trials, reaches, lower_bound = choose_exposure(
        sensor,
        [star[3] for star in stars],
        catalog_limit_magnitude=catalog_limit,
        catalog_complete=bool(status.get("complete")),
    )
    last = trials[-1]
    reason = (
        f"{exposure:g} s is the shortest exposure expected to show {last.expected_star_count} "
        f"catalog stars (target {TARGET_STAR_COUNT}), detectable down to "
        f"Gaia G {last.limiting_magnitude:.1f}."
        if reaches
        else f"Even {exposure:g} s is expected to show only {last.expected_star_count} catalog stars "
        f"(target {TARGET_STAR_COUNT}), so the longest exposure is used and the solve may fail."
    )
    if lower_bound:
        reason += (
            " The star catalog is partly downloaded or shallower than this limit, so the true count is "
            "at least that."
        )
    return CenteringExposure(
        exposure_seconds=exposure,
        source="computed",
        reason=reason,
        target_star_count=TARGET_STAR_COUNT,
        limiting_magnitude=last.limiting_magnitude,
        expected_star_count=last.expected_star_count,
        reaches_target=reaches,
        catalog_is_lower_bound=lower_bound,
        catalog_limit_magnitude=catalog_limit,
        zero_point_mag=sensor.zero_point_mag,
        zero_point_frame_count=facts["zero_point_frame_count"],
        star_width_arcsec=facts["star_width_arcsec"],
        sky_rate_e_per_s_per_px=sensor.sky_rate_e_per_s_per_px,
        read_noise_e=sensor.read_noise_e,
        gain_e_per_adu=sensor.gain_e_per_adu,
        pixel_scale_arcsec=facts["pixel_scale_arcsec"],
        field_radius_deg=facts["field_radius_deg"],
        setting_key=facts["setting_key"],
        trials=trials,
    )
