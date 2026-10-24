"""Purpose: The observing values of a frame, found from the frame's own data.

Description: Observing values describe the conditions a frame was taken in:
airmass and altitude, the Moon's altitude, separation and illumination, the
guide error during the exposure, the pointing error, the dew risk, and the
polar-alignment error that the rotation of the field shows. They are
conditions, not judgments. No rule turns one into advice unless its metric is
registered as non-exploratory (decision S12).

The polar-alignment error is labeled exploratory. It comes from the rotation
that frame registration finds between frames
(`stacking.frame_registration_rotation_deg`),
which is itself exploratory, and the signs of its two components have not
been checked against a real mount. Only its size is reported.

All functions here are pure: the caller reads the data and passes it in.
A value that cannot be found is ``None`` with a reason, never a default.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, SkyCoord, get_body, get_sun
from astropy.time import Time
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from wayfindinglib.astronomy.coordinate_transforms import (
    angular_separation_arcsec,
    earth_location,
    hour_angle_deg,
)

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)

KASTEN_YOUNG_OFFSET_DEG = 6.07995
KASTEN_YOUNG_SCALE = 0.50572
KASTEN_YOUNG_POWER = -1.6364
"""Constants of the Kasten and Young (1989, Applied Optics 28, 4735) airmass
formula ``X = 1 / (sin(h) + 0.50572 (h + 6.07995)^-1.6364)`` with the altitude
h in degrees. It stays accurate to the horizon, unlike the plain ``1 /
sin(h)``. Published values, register row WAY-D26."""

DEW_SPREAD_LIMIT_C = 3.0
"""The dew risk is raised when the ambient temperature is within this many
degrees
Celsius of the dew point. Optics cool below the air by a few degrees on a clear
night, so dew can form before the air reaches its dew point. A common rule of
thumb for dew heaters; design estimate, register row WAY-D27."""

MINIMUM_HOUR_ANGLE_SPAN_DEG = 45.0
"""The polar-alignment fit separates its altitude and azimuth parts only when
the
frames of a guiding run cover at least this span of hour angle (45 degrees is 3
hours). A shorter span gives a lower bound only. Derivation: the two parts
differ in how they depend on cos(H) and sin(H), and over a short arc these two
look alike; at 45 degrees the condition number of the design matrix stays below
about 10 for mid-sky fields. Design estimate, register row WAY-D28."""

MINIMUM_POLAR_SAMPLES_PER_RUN = 5
"""Fewest rotation samples in one guiding run for the polar-alignment fit. The
fit
of a run needs its own offset and a slope; five samples leave three degrees of
freedom to judge the scatter. Design estimate, register row WAY-D28."""

MINIMUM_COS_DECLINATION = 0.17
"""The guided field rotation rate from a polar error scales with
1/cos(declination), so the signal is largest near the pole. The limit is not
there to save signal. Below cos = 0.17 (about 80 degrees of declination) the
rate grows without bound, the small-angle model no longer holds, and the
rotation that plate solving reports is poorly defined because every star is
close to the pole. Design estimate, register row WAY-D28."""

ARCMIN_PER_RADIAN = 180.0 * 60.0 / math.pi
"""Arcminutes in one radian."""


class SkyGeometry(BaseModel):
    """Where a target and the Moon are at the time of a frame.

    Parameters
    ----------
    epoch_seconds : `float`
        The time, in seconds since the Unix epoch.
    altitude_deg : `float`
        The target's altitude, in degrees.
    airmass : `float` or `None`
        The airmass (Kasten and Young). ``None`` when the target is below
        the horizon.
    moon_altitude_deg : `float`
        The Moon's altitude, in degrees (it can be negative).
    moon_separation_deg : `float`
        The angle between the target and the Moon, in degrees.
    moon_illumination_fraction : `float`
        The lit fraction of the Moon's disc, from 0 to 1. Found from the
        Moon-Sun angle as seen from Earth, ``(1 - cos(elongation)) / 2``,
        which differs from the exact value by under 1 percent.
    """

    model_config = _MODEL_CONFIG

    epoch_seconds: float
    altitude_deg: float
    airmass: float | None = None
    moon_altitude_deg: float
    moon_separation_deg: float
    moon_illumination_fraction: float = Field(ge=0.0, le=1.0)


def airmass_from_altitude(altitude_deg: float) -> float | None:
    """Give the airmass at an altitude (Kasten and Young, 1989).

    Parameters
    ----------
    altitude_deg : `float`
        The altitude above the horizon, in degrees.

    Returns
    -------
    airmass : `float` or `None`
        The airmass, 1 at the zenith. ``None`` at or below the horizon.
    """
    if altitude_deg <= 0.0:
        return None
    sine = math.sin(math.radians(altitude_deg))
    return 1.0 / (sine + KASTEN_YOUNG_SCALE * (altitude_deg + KASTEN_YOUNG_OFFSET_DEG) ** KASTEN_YOUNG_POWER)


def sky_geometry(
    epoch_seconds: Sequence[float],
    ra_deg: Sequence[float],
    dec_deg: Sequence[float],
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float,
) -> list[SkyGeometry]:
    """Find the target's and the Moon's place in the sky at several times.

    Parameters
    ----------
    epoch_seconds : `Sequence` [`float`]
        The times, in seconds since the Unix epoch (UTC).
    ra_deg, dec_deg : `Sequence` [`float`]
        The target's ICRS position at each time, in degrees. The same length
        as `epoch_seconds`.
    latitude_deg, longitude_deg, elevation_m : `float`
        The observing site (take them from `SiteProfile`).

    Returns
    -------
    geometry : `list` [`SkyGeometry`]
        One per time, in order. The Moon is the topocentric Moon from
        astropy's built-in ephemeris, which needs no download.

    Raises
    ------
    ValueError
        If the three sequences differ in length.
    """
    if not (len(epoch_seconds) == len(ra_deg) == len(dec_deg)):
        raise ValueError("epoch_seconds, ra_deg and dec_deg must have the same length.")
    if len(epoch_seconds) == 0:
        return []
    location = earth_location(latitude_deg, longitude_deg, elevation_m)
    times = Time(np.asarray(epoch_seconds, dtype=float), format="unix", scale="utc")
    target = SkyCoord(ra=np.asarray(ra_deg) * u.deg, dec=np.asarray(dec_deg) * u.deg, frame="icrs")
    frame = AltAz(obstime=times, location=location)
    target_altaz = target.transform_to(frame)
    target_altitude = target_altaz.alt.deg
    moon_altaz = get_body("moon", times, location).transform_to(frame)
    moon_altitude = moon_altaz.alt.deg
    # Both in one horizontal frame, so the angle is the topocentric one.
    separation = target_altaz.separation(moon_altaz).deg
    moon_geocentric = get_body("moon", times)
    sun = get_sun(times)
    elongation = moon_geocentric.separation(sun).rad
    illumination = (1.0 - np.cos(elongation)) / 2.0
    return [
        SkyGeometry(
            epoch_seconds=float(epoch_seconds[index]),
            altitude_deg=float(target_altitude[index]),
            airmass=airmass_from_altitude(float(target_altitude[index])),
            moon_altitude_deg=float(moon_altitude[index]),
            moon_separation_deg=float(separation[index]),
            moon_illumination_fraction=float(min(1.0, max(0.0, illumination[index]))),
        )
        for index in range(len(epoch_seconds))
    ]


class DewRisk(BaseModel):
    """The dew risk from the ambient temperature and the dew point.

    Parameters
    ----------
    spread_c : `float` or `None`
        Ambient temperature minus dew point, in degrees Celsius. ``None``
        when either is not recorded.
    at_risk : `bool` or `None`
        Whether the spread is at or below `DEW_SPREAD_LIMIT_C`. ``None`` with
        no spread.
    reason : `str` or `None`
        Why the risk could not be found (an insufficient-data reason).
    """

    model_config = _MODEL_CONFIG

    spread_c: float | None = None
    at_risk: bool | None = None
    reason: str | None = None


def dew_risk(ambient_c: float | None, dew_point_c: float | None) -> DewRisk:
    """Find the dew risk, or say that the data to find it is missing.

    Parameters
    ----------
    ambient_c : `float` or `None`
        The ambient (air) temperature, in degrees Celsius.
    dew_point_c : `float` or `None`
        The dew point, in degrees Celsius.

    Returns
    -------
    risk : `DewRisk`
        The spread and whether it is at risk, or a reason when either
        temperature is missing.
    """
    if ambient_c is None or dew_point_c is None:
        missing = "ambient temperature" if ambient_c is None else "dew point"
        return DewRisk(reason=f"The {missing} was not recorded, so the dew risk cannot be found.")
    spread = ambient_c - dew_point_c
    return DewRisk(spread_c=spread, at_risk=spread <= DEW_SPREAD_LIMIT_C)


def pointing_error_arcsec(
    requested_ra_deg: float, requested_dec_deg: float, solved_ra_deg: float, solved_dec_deg: float
) -> float:
    """Give how far a frame's solved center is from where the mount was sent.

    Parameters
    ----------
    requested_ra_deg, requested_dec_deg : `float`
        The position the mount was asked to point at, in degrees.
    solved_ra_deg, solved_dec_deg : `float`
        The plate-solved center of the frame, in degrees. Both in the same
        frame (ICRS).

    Returns
    -------
    error_arcsec : `float`
        The angle between them, in arcseconds.
    """
    return angular_separation_arcsec(requested_ra_deg, requested_dec_deg, solved_ra_deg, solved_dec_deg)


class FrameObservingValues(BaseModel):
    """The observing values of one frame.

    Parameters
    ----------
    frame_id : `str`
        The frame.
    geometry : `SkyGeometry` or `None`
        Airmass, altitude and Moon values. ``None`` when the frame's time or
        position is missing.
    guide_rms_arcsec : `float` or `None`
        The total RMS guide error during the exposure.
    pointing_error_arcsec : `float` or `None`
        The distance between the requested and solved centers.
    dew : `DewRisk`
        The dew risk.
    insufficient : `dict` [`str`, `str`]
        The reason for each value that is ``None``.
    """

    model_config = _MODEL_CONFIG

    frame_id: str
    geometry: SkyGeometry | None = None
    guide_rms_arcsec: float | None = None
    pointing_error_arcsec: float | None = None
    dew: DewRisk
    insufficient: dict[str, str] = Field(default_factory=dict)


class FrameObservingInputs(BaseModel):
    """What the caller knows about one frame, for `observing_values`.

    Parameters
    ----------
    frame_id : `str`
        The frame.
    start_epoch_seconds : `float` or `None`
        When the exposure began, in seconds since the Unix epoch.
    exposure_seconds : `float` or `None`
        The exposure length, in seconds.
    solved_ra_deg, solved_dec_deg : `float` or `None`
        The plate-solved center, in degrees (ICRS).
    requested_ra_deg, requested_dec_deg : `float` or `None`
        Where the mount was sent, in degrees (ICRS).
    guide_rms_total_arcsec : `float` or `None`
        The ``rms_total_arcsec`` that `guiding_join.join_frames_to_guiding`
        gave for the frame.
    ambient_c, dew_point_c : `float` or `None`
        The ambient temperature and the dew point, in degrees Celsius.
    """

    model_config = _MODEL_CONFIG

    frame_id: str
    start_epoch_seconds: float | None = None
    exposure_seconds: float | None = None
    solved_ra_deg: float | None = None
    solved_dec_deg: float | None = None
    requested_ra_deg: float | None = None
    requested_dec_deg: float | None = None
    guide_rms_total_arcsec: float | None = None
    ambient_c: float | None = None
    dew_point_c: float | None = None


def observing_values(
    frames: Sequence[FrameObservingInputs], latitude_deg: float, longitude_deg: float, elevation_m: float
) -> list[FrameObservingValues]:
    """Find the observing values of several frames.

    The sky geometry is found at the middle of each exposure.

    Parameters
    ----------
    frames : `Sequence` [`FrameObservingInputs`]
        What is known about each frame.
    latitude_deg, longitude_deg, elevation_m : `float`
        The observing site.

    Returns
    -------
    values : `list` [`FrameObservingValues`]
        One per frame, in order. Each missing value has a reason in
        ``insufficient``.
    """
    with_position = [
        index
        for index, frame in enumerate(frames)
        if frame.start_epoch_seconds is not None
        and (frame.solved_ra_deg is not None or frame.requested_ra_deg is not None)
    ]
    geometry_by_index: dict[int, SkyGeometry] = {}
    if with_position:
        centers = []
        for index in with_position:
            frame = frames[index]
            ra = frame.solved_ra_deg if frame.solved_ra_deg is not None else frame.requested_ra_deg
            dec = frame.solved_dec_deg if frame.solved_ra_deg is not None else frame.requested_dec_deg
            middle = (frame.start_epoch_seconds or 0.0) + (frame.exposure_seconds or 0.0) / 2.0
            centers.append((middle, ra, dec))
        found = sky_geometry(
            [item[0] for item in centers],
            [float(item[1]) for item in centers],
            [float(item[2]) for item in centers],
            latitude_deg,
            longitude_deg,
            elevation_m,
        )
        geometry_by_index = dict(zip(with_position, found, strict=True))
    results: list[FrameObservingValues] = []
    for index, frame in enumerate(frames):
        insufficient: dict[str, str] = {}
        geometry = geometry_by_index.get(index)
        if geometry is None:
            insufficient["geometry"] = "The frame's start time or position is missing."
        pointing = None
        if None in (
            frame.requested_ra_deg,
            frame.requested_dec_deg,
            frame.solved_ra_deg,
            frame.solved_dec_deg,
        ):
            insufficient["pointing_error"] = "The requested or the solved position is missing."
        else:
            pointing = pointing_error_arcsec(
                float(frame.requested_ra_deg or 0.0),
                float(frame.requested_dec_deg or 0.0),
                float(frame.solved_ra_deg or 0.0),
                float(frame.solved_dec_deg or 0.0),
            )
        if frame.guide_rms_total_arcsec is None:
            insufficient["guide_rms"] = "No guiding samples fall inside the frame's exposure."
        dew = dew_risk(frame.ambient_c, frame.dew_point_c)
        if dew.reason:
            insufficient["dew_risk"] = dew.reason
        results.append(
            FrameObservingValues(
                frame_id=frame.frame_id,
                geometry=geometry,
                guide_rms_arcsec=frame.guide_rms_total_arcsec,
                pointing_error_arcsec=pointing,
                dew=dew,
                insufficient=insufficient,
            )
        )
    return results


# -----------------------------------------------------------------------------
# Polar alignment from field rotation
# -----------------------------------------------------------------------------


class RotationSample(BaseModel):
    """The rotation of one frame in its guiding run.

    It is relative to the run's reference frame.

    Parameters
    ----------
    guiding_run_id : `str`
        The guiding run the frame belongs to. Each run has its own rotation
        zero point, because the camera angle is not kept between runs.
    epoch_seconds : `float`
        The time of the frame, in seconds since the Unix epoch.
    rotation_deg : `float`
        The rotation found by frame registration
        (``stacking.frame_registration_rotation_deg``), in degrees.
    ra_deg, dec_deg : `float`
        Where the frame points, in degrees.
    """

    model_config = _MODEL_CONFIG

    guiding_run_id: str
    epoch_seconds: float
    rotation_deg: float
    ra_deg: float
    dec_deg: float


class PolarAlignmentEstimate(BaseModel):
    """The polar-alignment error that field rotation shows. Exploratory.

    Parameters
    ----------
    exploratory : `bool`
        Always true. The input rotation metric is exploratory and the signs
        of the components are unchecked, so no rule may use this value.
    kind : `str`
        ``"fit"`` when both parts were separated, ``"lower_bound"`` when the
        hour-angle span was too short to separate them, or
        ``"insufficient_data"``.
    total_error_arcmin : `float` or `None`
        The angle between the mount's polar axis and the celestial pole, in
        arcminutes. For a lower bound, the least it can be.
    altitude_error_arcmin, azimuth_error_arcmin : `float` or `None`
        The size of the two parts, as angles of the axis in altitude and in
        azimuth. Only for a fit.
    uncertainty_arcmin : `float` or `None`
        One standard error of the total, from the scatter of the fit. Only
        for a fit.
    hour_angle_span_deg : `float` or `None`
        The widest hour-angle span of any run.
    sample_count : `int`
        Samples used.
    reason : `str`
        Why the kind is what it is, in one sentence.
    """

    model_config = _MODEL_CONFIG

    exploratory: bool = True
    kind: str
    total_error_arcmin: float | None = None
    altitude_error_arcmin: float | None = None
    azimuth_error_arcmin: float | None = None
    uncertainty_arcmin: float | None = None
    hour_angle_span_deg: float | None = None
    sample_count: int = 0
    reason: str


def polar_alignment_from_rotation(
    samples: Sequence[RotationSample], latitude_deg: float, longitude_deg: float, elevation_m: float
) -> PolarAlignmentEstimate:
    """Estimate the polar-alignment error from the rotation of the field.

    If the mount's polar axis is off the celestial pole by a small vector
    with parts ``a`` (in the meridian plane, the altitude error) and ``b``
    (east-west, equal to the azimuth error times cos(latitude)), the field
    rotates against the sky by

        theta(H) = theta_0 + (a sin H - b cos H) / cos(dec)

    where H is the hour angle. The derivation: the camera turns about the
    mount's axis instead of the pole's, so a guided field rotates against the
    sky at the sidereal rate omega times the axis offset along the line of
    sight, divided by cos(dec): d theta / d H = (a cos H + b sin H) / cos(dec).
    The rotation is largest near the pole. Each guiding run has its own
    ``theta_0``.

    The fit solves for ``a``, ``b`` and one ``theta_0`` per run by least
    squares. When the widest hour-angle span of any run is below
    `MINIMUM_HOUR_ANGLE_SPAN_DEG`, ``a`` and ``b`` cannot be told apart, and
    only a lower bound is given: the largest slope of any run, which is
    ``|d theta / d H| * cos(dec)``, never exceeds the axis offset.

    Parameters
    ----------
    samples : `Sequence` [`RotationSample`]
        Rotation samples. Samples within about 10 degrees of a celestial pole
        (declination beyond 80 degrees) are dropped, because the rotation
        there is too small.
    latitude_deg, longitude_deg, elevation_m : `float`
        The observing site.

    Returns
    -------
    estimate : `PolarAlignmentEstimate`
        The estimate, labeled exploratory.
    """
    location = earth_location(latitude_deg, longitude_deg, elevation_m)
    usable = [s for s in samples if math.cos(math.radians(s.dec_deg)) >= MINIMUM_COS_DECLINATION]
    runs: dict[str, list[RotationSample]] = {}
    for sample in usable:
        runs.setdefault(sample.guiding_run_id, []).append(sample)
    runs = {name: items for name, items in runs.items() if len(items) >= MINIMUM_POLAR_SAMPLES_PER_RUN}
    if not runs:
        return PolarAlignmentEstimate(
            kind="insufficient_data",
            sample_count=0,
            reason=(
                f"No guiding run has {MINIMUM_POLAR_SAMPLES_PER_RUN} rotation samples away from the pole."
            ),
        )

    hour_angles: dict[str, np.ndarray] = {}
    for name, items in runs.items():
        angles = [
            hour_angle_deg(item.ra_deg, location, Time(item.epoch_seconds, format="unix", scale="utc"))
            for item in items
        ]
        hour_angles[name] = np.unwrap(np.radians(angles))
    spans = {name: math.degrees(float(np.ptp(angles))) for name, angles in hour_angles.items()}
    widest = max(spans.values())
    sample_count = sum(len(items) for items in runs.values())

    if widest < MINIMUM_HOUR_ANGLE_SPAN_DEG:
        lower = 0.0
        for name, items in runs.items():
            hour = hour_angles[name]
            cos_dec = np.cos(np.radians([item.dec_deg for item in items]))
            rotation = np.radians([item.rotation_deg for item in items])
            slope = float(np.polyfit(hour, rotation, 1)[0])
            lower = max(lower, abs(slope) * float(np.mean(cos_dec)))
        return PolarAlignmentEstimate(
            kind="lower_bound",
            total_error_arcmin=lower * ARCMIN_PER_RADIAN,
            hour_angle_span_deg=widest,
            sample_count=sample_count,
            reason=(
                f"The widest run covers {widest:.0f} degrees of hour angle, below "
                f"{MINIMUM_HOUR_ANGLE_SPAN_DEG:.0f}, so the altitude and azimuth parts cannot be told apart."
            ),
        )

    names = sorted(runs)
    rows: list[list[float]] = []
    targets: list[float] = []
    for column, name in enumerate(names):
        for item, hour in zip(runs[name], hour_angles[name], strict=True):
            cos_dec = math.cos(math.radians(item.dec_deg))
            row = [0.0] * len(names)
            row[column] = 1.0
            rows.append([*row, math.sin(hour) / cos_dec, -math.cos(hour) / cos_dec])
            targets.append(math.radians(item.rotation_deg))
    design = np.asarray(rows)
    observed = np.asarray(targets)
    solution, _residuals, rank, _singular = np.linalg.lstsq(design, observed, rcond=None)
    if rank < design.shape[1]:
        return PolarAlignmentEstimate(
            kind="insufficient_data",
            hour_angle_span_deg=widest,
            sample_count=sample_count,
            reason="The rotation samples do not separate the altitude and azimuth parts.",
        )
    altitude_part, east_west_part = float(solution[-2]), float(solution[-1])
    degrees_of_freedom = max(1, design.shape[0] - design.shape[1])
    residual = observed - design @ solution
    sigma_squared = float(residual @ residual) / degrees_of_freedom
    covariance = sigma_squared * np.linalg.inv(design.T @ design)
    total = math.hypot(altitude_part, east_west_part)
    gradient = np.array([altitude_part, east_west_part]) / total if total > 0.0 else np.zeros(2)
    uncertainty = float(math.sqrt(max(0.0, gradient @ covariance[-2:, -2:] @ gradient)))
    return PolarAlignmentEstimate(
        kind="fit",
        total_error_arcmin=total * ARCMIN_PER_RADIAN,
        altitude_error_arcmin=abs(altitude_part) * ARCMIN_PER_RADIAN,
        azimuth_error_arcmin=abs(east_west_part) / math.cos(math.radians(latitude_deg)) * ARCMIN_PER_RADIAN,
        uncertainty_arcmin=uncertainty * ARCMIN_PER_RADIAN,
        hour_angle_span_deg=widest,
        sample_count=sample_count,
        reason="Both parts were fitted over runs that span enough hour angle. The signs are unchecked.",
    )
