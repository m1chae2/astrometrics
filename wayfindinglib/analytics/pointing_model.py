"""Mathematical decomposition of telescope pointing errors into physical terms.

Decomposes plate-solve coordinate offsets (delta_ra, delta_dec) across the sky
into physical mount terms using the classic geometric pointing model:
- IH, ID: Index errors in RA and Dec
- ME, MA: Polar axis elevation and azimuth misalignment
- CH: Cone error (optical tube non-orthogonality to Dec axis)
- TF: Tube flexure / mechanical gravity sag

Every term except the two index errors depends on the hour angle, so the fit
needs the true hour angle of each solve: the local sidereal time at the
site's longitude and the time of the solve, minus the right ascension. A
fit also needs points spread over the sky. `fit_pointing_model` measures
how well the points separate the six terms, and it refuses a fit that
cannot (see `REFUSE_NOISE_GAIN`) and marks a weak one low confidence (see
`LOW_NOISE_GAIN`).
"""

import itertools
import math
from typing import Any

import numpy as np
from astropy.time import Time

from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib.models.session.telemetry import MountPointingModel

MINIMUM_POINTS = 4
"""Fewest valid plate-solve points a fit is tried with."""

RANK_TOLERANCE = 1e-3
"""Ratio of the smallest to the largest singular value of the design matrix
below which the six terms cannot be told apart (the matrix has lost a
column)."""

REFUSE_NOISE_GAIN = TunedParameter(
    name="wayfinding.pointing_refuse_noise_gain",
    default=10.0,
    unit="arcsec/arcsec",
    description="A pointing fit is refused when a term moves more than this per arcsecond of solve scatter.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D149",
)
"""A fit is refused when some term would move by more than this many arcseconds
for one arcsecond of scatter in the solves (see `_term_noise_gain`).

Design estimate, register row WAY-D149."""

LOW_NOISE_GAIN = TunedParameter(
    name="wayfinding.pointing_low_noise_gain",
    default=3.0,
    unit="arcsec/arcsec",
    description="A pointing fit is low confidence when a term moves more than this per arcsecond of scatter.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D149",
)
"""A fit is marked low confidence when some term would move by more than this
many arcseconds for one arcsecond of scatter in the solves.

Design estimate, register row WAY-D149."""

HIGH_NOISE_GAIN = TunedParameter(
    name="wayfinding.pointing_high_noise_gain",
    default=1.5,
    unit="arcsec/arcsec",
    description="The largest noise gain a high confidence pointing fit may have.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D149",
)
"""The largest noise gain a high confidence fit may have.

Design estimate, register row WAY-D149."""

LOW_POINT_COUNT = TunedParameter(
    name="wayfinding.pointing_low_point_count",
    default=8,
    unit="solves",
    description="A pointing fit from fewer solves than this is low confidence.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D149",
)
"""A fit from fewer points than this is low confidence.

Design estimate, register row WAY-D149."""

HIGH_POINT_COUNT = TunedParameter(
    name="wayfinding.pointing_high_point_count",
    default=15,
    unit="solves",
    description="A pointing fit needs at least this many solves to be high confidence.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D149",
)
"""A fit needs at least this many points to be high confidence.

Design estimate, register row WAY-D149."""

_TERM_COUNT = 6
"""Number of fitted terms: IH, ID, ME, MA, CH, TF."""


def hour_angle_deg(ra_deg: float, timestamp: float, longitude_deg: float) -> float:
    """Return the true hour angle of a position at a time.

    The hour angle is the local mean sidereal time minus the right
    ascension. Local sidereal time follows from the time and the site's
    longitude.

    Parameters
    ----------
    ra_deg : `float`
        Right Ascension in degrees, as the alignment records store it.
    timestamp : `float`
        Time of the solve or sync, in seconds since 1970-01-01 UTC.
    longitude_deg : `float`
        Observer longitude in decimal degrees, east positive.

    Returns
    -------
    hour_angle_deg : `float`
        Hour angle in degrees, in the range -180 to 180.
    """
    lst_deg = float(Time(timestamp, format="unix").sidereal_time("mean", longitude=longitude_deg).deg)
    return (lst_deg - ra_deg + 180.0) % 360.0 - 180.0


def _pointing_geometry(dec_deg: float, latitude_deg: float, hour_angle_degrees: float) -> dict[str, float]:
    """Compute the trig terms `fit_pointing_model`'s rows are built from.

    Shared by `fit_pointing_model` (fitting the six coefficients) and
    `predict_pointing_error` (evaluating them at a given position), so
    the two can never drift apart on the geometric convention they use.

    Parameters
    ----------
    dec_deg : `float`
        Declination in decimal degrees.
    latitude_deg : `float`
        Observer latitude in decimal degrees.
    hour_angle_degrees : `float`
        True hour angle in degrees (see `hour_angle_deg`).

    Returns
    -------
    geometry : `dict` [`str`, `float`]
        `sin_h`, `cos_h`, `tan_dec`, `sec_dec`, `sin_dec`, `cos_dec`,
        `sin_phi`, `cos_phi` -- the trig terms the RA/Dec projection
        equations below are assembled from.
    """
    delta = math.radians(dec_deg)
    clamped_dec = max(min(delta, math.radians(89.5)), math.radians(-89.5))
    sin_dec = math.sin(clamped_dec)
    cos_dec = max(math.cos(clamped_dec), 1e-4)

    h = math.radians(hour_angle_degrees)
    phi = math.radians(latitude_deg)

    return {
        "sin_h": math.sin(h),
        "cos_h": math.cos(h),
        "tan_dec": sin_dec / cos_dec,
        "sec_dec": 1.0 / cos_dec,
        "sin_dec": sin_dec,
        "cos_dec": cos_dec,
        "sin_phi": math.sin(phi),
        "cos_phi": math.cos(phi),
    }


def _design_rows(geometry: dict[str, float]) -> tuple[list[float], list[float]]:
    """Build the two equations one solve gives, one for RA and one for Dec.

    Parameters
    ----------
    geometry : `dict` [`str`, `float`]
        The trig terms from `_pointing_geometry`.

    Returns
    -------
    row_ra, row_dec : `tuple` [`list` [`float`], `list` [`float`]]
        The coefficients of [IH, ID, ME, MA, CH, TF] in the RA projection
        equation (the offset times cos dec) and in the Dec equation.
    """
    g = geometry
    row_ra = [
        -1.0,
        0.0,
        g["sin_h"] * g["tan_dec"],
        -g["cos_h"] * g["tan_dec"],
        g["sec_dec"],
        g["cos_h"] * g["cos_phi"] * g["tan_dec"],
    ]
    row_dec = [
        0.0,
        -1.0,
        g["cos_h"],
        g["sin_h"],
        0.0,
        g["cos_h"] * g["sin_phi"] * g["sin_dec"] - g["cos_phi"] * g["cos_dec"],
    ]
    return row_ra, row_dec


def predict_pointing_error(
    ra_deg: float,
    dec_deg: float,
    model: MountPointingModel,
    latitude_deg: float,
    longitude_deg: float,
    timestamp: float,
) -> tuple[float, float]:
    """Predict the systematic pointing error `model` implies at a position.

    Evaluates the same RA/Dec projection equations `fit_pointing_model`
    solves, using `model`'s already-fit IH/ID/ME/MA/CH/TF coefficients
    instead of solving for them -- the forward direction of the same
    geometric model, used by `pointing_correction.py`'s feedforward to
    tell a genuinely new pointing failure apart from one the model
    already explains.

    Parameters
    ----------
    ra_deg : `float`
        Right Ascension in degrees, the same unit `fit_pointing_model`
        reads.
    dec_deg : `float`
        Declination in decimal degrees.
    model : `MountPointingModel`
        The already-fit coefficients to evaluate.
    latitude_deg : `float`
        Observer latitude in decimal degrees. Required: the pointing terms
        depend on the site, so no stand-in latitude is assumed.
    longitude_deg : `float`
        Observer longitude in decimal degrees, east positive. Required for
        the same reason: it sets the hour angle.
    timestamp : `float`
        Time of the position, in seconds since 1970-01-01 UTC.

    Returns
    -------
    delta_ra_arcsec, delta_dec_arcsec : `tuple` [`float`, `float`]
        The model's predicted RA/Dec pointing offset at this position,
        in the same ``solved - commanded`` convention `fit_pointing_model`
        fits against.
    """
    g = _pointing_geometry(dec_deg, latitude_deg, hour_angle_deg(ra_deg, timestamp, longitude_deg))
    row_ra, row_dec = _design_rows(g)
    coefficients = [
        model.ih_arcsec,
        model.id_arcsec,
        model.me_arcsec,
        model.ma_arcsec,
        model.ch_arcsec,
        model.tf_arcsec,
    ]
    d_ra_cos_dec = sum(row * term for row, term in zip(row_ra, coefficients, strict=True))
    d_dec = sum(row * term for row, term in zip(row_dec, coefficients, strict=True))
    return d_ra_cos_dec / g["cos_dec"], d_dec


def _term_noise_gain(a_matrix: np.ndarray) -> float:
    """Measure how much a fit magnifies the scatter of the solves.

    A least-squares fit with scatter `s` in every equation has a term
    uncertainty of `s` times the square root of the matching diagonal entry
    of ``(A^T A)^-1``. The largest such factor over the six terms is the
    noise gain: arcseconds of term uncertainty per arcsecond of scatter.
    Points close together on the sky, or too few of them, make some
    columns nearly alike and give a large gain.

    Parameters
    ----------
    a_matrix : `numpy.ndarray`
        The design matrix, two rows per solve and six columns.

    Returns
    -------
    gain : `float`
        The largest noise gain over the six terms, or infinity when the
        terms cannot be told apart at all.
    """
    singular_values = np.linalg.svd(a_matrix, compute_uv=False)
    if singular_values.size < _TERM_COUNT or singular_values[-1] < RANK_TOLERANCE * singular_values[0]:
        return math.inf
    covariance = np.linalg.inv(a_matrix.T @ a_matrix)
    return float(math.sqrt(float(np.max(np.diag(covariance)))))


def _usable_points(
    attempts: list[dict[str, Any]], longitude_deg: float
) -> tuple[list[dict[str, float]], int]:
    """Pick the plate-solve records a fit can use and give each its hour angle.

    Parameters
    ----------
    attempts : `list` [`dict` [`str`, `Any`]]
        Plate solve records. See `fit_pointing_model`.
    longitude_deg : `float`
        Observer longitude in decimal degrees, east positive.

    Returns
    -------
    points, untimed : `tuple` [`list` [`dict` [`str`, `float`]], `int`]
        The usable points, each with ``ra``, ``dec``, ``d_ra``, ``d_dec``
        and ``hour_angle``, and the number of otherwise valid records
        dropped because they carry no time (no true hour angle).
    """
    points: list[dict[str, float]] = []
    untimed = 0
    for a in attempts:
        ra = a.get("ra", a.get("mount_ra"))
        dec = a.get("dec", a.get("mount_dec"))
        d_ra = a.get("delta_ra_arcsec", a.get("deltaRaArcsec"))
        d_dec = a.get("delta_dec_arcsec", a.get("deltaDecArcsec"))
        if ra is None or dec is None or d_ra is None or d_dec is None:
            continue
        if not (math.isfinite(ra) and math.isfinite(dec) and math.isfinite(d_ra) and math.isfinite(d_dec)):
            continue
        err = math.hypot(d_ra, d_dec)
        # Ignore outliers (> 5 deg) and degenerate echoes (< 0.5")
        if not (0.5 <= err < 18000.0) or (abs(d_dec) < 1e-4 and abs(d_ra) < 1.0):
            continue
        timestamp = a.get("timestamp")
        if timestamp is None or not math.isfinite(timestamp) or timestamp <= 0.0:
            untimed += 1
            continue
        points.append({
            "ra": float(ra),
            "dec": float(dec),
            "d_ra": float(d_ra),
            "d_dec": float(d_dec),
            "hour_angle": hour_angle_deg(float(ra), float(timestamp), longitude_deg),
        })
    return points, untimed


def _unfitted_model(
    points: list[dict[str, float]], confidence: str, message: str, raw_rms: float
) -> MountPointingModel:
    """Make the model for a fit that was not done: all terms zero.

    Parameters
    ----------
    points : `list` [`dict` [`str`, `float`]]
        The usable points.
    confidence : `str`
        ``"insufficient_data"``.
    message : `str`
        Why no fit was made.
    raw_rms : `float`
        RMS of the raw errors in arcseconds.

    Returns
    -------
    model : `MountPointingModel`
        A model that predicts nothing.
    """
    return MountPointingModel(
        sample_count=len(points),
        raw_rms_arcsec=round(raw_rms, 2),
        residual_rms_arcsec=round(raw_rms, 2),
        improvement_pct=0.0,
        confidence=confidence,
        message=message,
    )


def fit_pointing_model(
    attempts: list[dict[str, Any]],
    latitude_deg: float,
    longitude_deg: float,
) -> MountPointingModel:
    r"""Fit geometric mount pointing terms to a set of plate solves.

    Decomposes pointing offsets using geometric TPOINT conventions
    for index offsets (IH, ID), polar misalignment (ME, MA),
    cone non-orthogonality (CH), and gravity tube flexure (TF).

    The hour angle of each solve is the local sidereal time at its
    ``timestamp`` and `longitude_deg`, minus its right ascension. A
    record with no time is left out.

    Confidence follows from how well the points separate the six terms
    (`_term_noise_gain`): a fit whose terms cannot be told apart, or move
    by more than `REFUSE_NOISE_GAIN` arcseconds per arcsecond of scatter, is
    refused (``"insufficient_data"``, all terms zero, with a message that says
    why). A gain above `LOW_NOISE_GAIN`, or fewer than `LOW_POINT_COUNT`
    points, gives ``"low"``. A refused or low-confidence fit is a status for
    the caller to show, not an error.

    Parameters
    ----------
    attempts : `list` [`dict` [`str`, `Any`]]
        Plate solve records containing `ra` and `dec` in degrees,
        `delta_ra_arcsec`, `delta_dec_arcsec`, and `timestamp` (seconds
        since 1970).
    latitude_deg : `float`
        Observer latitude in decimal degrees. Required: the pointing terms
        depend on the site, so no stand-in latitude is assumed.
    longitude_deg : `float`
        Observer longitude in decimal degrees, east positive. Required: it
        sets the hour angle.

    Returns
    -------
    model : `MountPointingModel`
        Fitted model coefficients, residual improvement, and diagnostics.
    """
    valid_points, untimed = _usable_points(attempts, longitude_deg)
    n = len(valid_points)
    raw_errors = [math.hypot(p["d_ra"], p["d_dec"]) for p in valid_points]
    raw_rms = float(np.sqrt(np.mean(np.square(raw_errors)))) if raw_errors else 0.0
    untimed_note = f" {untimed} solves with no time were left out." if untimed else ""

    if n < MINIMUM_POINTS:
        return _unfitted_model(
            valid_points,
            "insufficient_data",
            f"Need at least {MINIMUM_POINTS} timed plate-solve points to decompose terms; found {n}."
            + untimed_note,
            raw_rms,
        )

    # Parameters to solve: [IH, ID, ME, MA, CH, TF]
    a_rows: list[list[float]] = []
    b_rows: list[float] = []
    for p in valid_points:
        g = _pointing_geometry(p["dec"], latitude_deg, p["hour_angle"])
        row_ra, row_dec = _design_rows(g)
        a_rows.extend([row_ra, row_dec])
        b_rows.extend([p["d_ra"] * g["cos_dec"], p["d_dec"]])
    a_matrix = np.array(a_rows, dtype=np.float64)
    b_vector = np.array(b_rows, dtype=np.float64)

    noise_gain = _term_noise_gain(a_matrix)
    hour_angles = [p["hour_angle"] for p in valid_points]
    declinations = [p["dec"] for p in valid_points]
    spread_note = (
        f"The points span {_circular_span_deg(hour_angles):.0f} degrees of hour angle and "
        f"{max(declinations) - min(declinations):.0f} degrees of declination."
    )
    if noise_gain > resolve_parameter(REFUSE_NOISE_GAIN).value:
        return _unfitted_model(
            valid_points,
            "insufficient_data",
            f"The {n} points are too close together on the sky to separate the six terms. {spread_note} "
            "Add solves at other hour angles and declinations." + untimed_note,
            raw_rms,
        )

    coeffs, _residuals, _rank, _s = np.linalg.lstsq(a_matrix, b_vector, rcond=RANK_TOLERANCE)
    ih, id_err, me, ma, ch, tf = [float(c) for c in coeffs]

    res_vector = b_vector - a_matrix @ coeffs
    res_sq_sum = float(np.sum(np.square(res_vector)))
    raw_rms = math.sqrt(sum(e**2 for e in raw_errors) / n)
    res_rms = math.sqrt(res_sq_sum / n)

    improvement = 0.0
    if raw_rms > 1e-4:
        improvement = max(0.0, (1.0 - (res_rms / raw_rms)) * 100.0)

    total_polar_error = math.hypot(me, ma)
    if noise_gain > resolve_parameter(LOW_NOISE_GAIN).value or n < resolve_parameter(LOW_POINT_COUNT).value:
        confidence = "low"
    elif (
        noise_gain > resolve_parameter(HIGH_NOISE_GAIN).value or n < resolve_parameter(HIGH_POINT_COUNT).value
    ):
        confidence = "medium"
    else:
        confidence = "high"

    uncertainty = noise_gain * res_rms * math.sqrt(2.0 * n / max(2 * n - _TERM_COUNT, 1))
    msg = (
        f"Model fitted over {n} points with {round(improvement, 1)}% error reduction. "
        f"{spread_note} Each term is good to about {uncertainty:.1f} arcseconds."
    )
    if confidence == "low":
        msg += " Treat the terms as rough: the points do not separate them well."
    msg += untimed_note

    return MountPointingModel(
        sample_count=n,
        raw_rms_arcsec=round(raw_rms, 2),
        residual_rms_arcsec=round(res_rms, 2),
        improvement_pct=round(improvement, 1),
        ih_arcsec=round(ih, 2),
        id_arcsec=round(id_err, 2),
        me_arcsec=round(me, 2),
        ma_arcsec=round(ma, 2),
        ch_arcsec=round(ch, 2),
        tf_arcsec=round(tf, 2),
        total_polar_error_arcsec=round(total_polar_error, 2),
        confidence=confidence,
        message=msg,
    )


def _circular_span_deg(angles_deg: list[float]) -> float:
    """Return the smallest arc that holds all the angles.

    Parameters
    ----------
    angles_deg : `list` [`float`]
        Angles in degrees.

    Returns
    -------
    span_deg : `float`
        The arc length in degrees, 0 to 360. It is 360 minus the largest
        gap between neighbouring angles around the circle.
    """
    ordered = sorted(angle % 360.0 for angle in angles_deg)
    gaps = [b - a for a, b in itertools.pairwise(ordered)]
    gaps.append(ordered[0] + 360.0 - ordered[-1])
    return 360.0 - max(gaps)
