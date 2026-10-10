"""Mathematical decomposition of telescope pointing errors into physical terms.

Decomposes plate-solve coordinate offsets (delta_ra, delta_dec) across the sky
into physical mount terms using the classic geometric pointing model:
- IH, ID: Index errors in RA and Dec
- ME, MA: Polar axis elevation and azimuth misalignment
- CH: Cone error (optical tube non-orthogonality to Dec axis)
- TF: Tube flexure / mechanical gravity sag
"""

import math
from typing import Any

import numpy as np

from wayfindinglib.models.session.telemetry import MountPointingModel


def _pointing_geometry(ra_value: float, dec_deg: float, latitude_deg: float) -> dict[str, float]:
    """Compute the trig terms `fit_pointing_model`'s rows are built from.

    Shared by `fit_pointing_model` (fitting the six coefficients) and
    `predict_pointing_error` (evaluating them at a given position), so
    the two can never drift apart on the geometric convention they use
    -- including the `h` term below, which is derived from `ra_value`
    alone rather than true local sidereal time, an existing
    simplification this function preserves rather than corrects.

    Parameters
    ----------
    ra_value : `float`
        Right Ascension, in hours if `<= 24.0`, else already degrees.
    dec_deg : `float`
        Declination in decimal degrees.
    latitude_deg : `float`
        Observer latitude in decimal degrees.

    Returns
    -------
    geometry : `dict` [`str`, `float`]
        `sin_h`, `cos_h`, `tan_dec`, `sec_dec`, `sin_dec`, `cos_dec`,
        `sin_phi`, `cos_phi` -- the trig terms the RA/Dec projection
        equations below are assembled from.
    """
    ra_deg = ra_value * 15.0 if ra_value <= 24.0 else ra_value

    delta = math.radians(dec_deg)
    clamped_dec = max(min(delta, math.radians(89.5)), math.radians(-89.5))
    sin_dec = math.sin(clamped_dec)
    cos_dec = max(math.cos(clamped_dec), 1e-4)

    h = math.radians((ra_deg * 3.0) % 360.0 - 180.0)
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


def predict_pointing_error(
    ra_value: float, dec_deg: float, model: MountPointingModel, latitude_deg: float = 45.0
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
    ra_value : `float`
        Right Ascension, in hours if `<= 24.0`, else already degrees --
        same convention `fit_pointing_model` accepts.
    dec_deg : `float`
        Declination in decimal degrees.
    model : `MountPointingModel`
        The already-fit coefficients to evaluate.
    latitude_deg : `float`, optional
        Observer latitude in decimal degrees, defaults to 45.0.

    Returns
    -------
    delta_ra_arcsec, delta_dec_arcsec : `tuple` [`float`, `float`]
        The model's predicted RA/Dec pointing offset at this position,
        in the same ``solved - commanded`` convention `fit_pointing_model`
        fits against.
    """
    g = _pointing_geometry(ra_value, dec_deg, latitude_deg)

    d_ra_cos_dec = (
        -model.ih_arcsec
        + model.me_arcsec * g["sin_h"] * g["tan_dec"]
        - model.ma_arcsec * g["cos_h"] * g["tan_dec"]
        + model.ch_arcsec * g["sec_dec"]
        + model.tf_arcsec * g["cos_h"] * g["cos_phi"] * g["tan_dec"]
    )
    d_dec = (
        -model.id_arcsec
        + model.me_arcsec * g["cos_h"]
        + model.ma_arcsec * g["sin_h"]
        + model.tf_arcsec * (g["cos_h"] * g["sin_phi"] * g["sin_dec"] - g["cos_phi"] * g["cos_dec"])
    )
    return d_ra_cos_dec / g["cos_dec"], d_dec


def fit_pointing_model(
    attempts: list[dict[str, Any]],
    latitude_deg: float = 45.0,
) -> MountPointingModel:
    r"""Fit geometric mount pointing terms to a set of plate solves.

    Decomposes pointing offsets using geometric TPOINT conventions
    for index offsets (IH, ID), polar misalignment (ME, MA),
    cone non-orthogonality (CH), and gravity tube flexure (TF).

    Parameters
    ----------
    attempts : `list` [`dict` [`str`, `Any`]]
        Plate solve records containing `ra`, `dec`, `delta_ra_arcsec`,
        `delta_dec_arcsec`, and optionally `timestamp`.
    latitude_deg : `float`, optional
        Observer latitude in decimal degrees, defaults to 45.0.

    Returns
    -------
    model : `MountPointingModel`
        Fitted model coefficients, residual improvement, and diagnostics.
    """
    valid_points: list[dict[str, Any]] = []
    for a in attempts:
        ra = a.get("ra", a.get("mount_ra"))
        dec = a.get("dec", a.get("mount_dec"))
        d_ra = a.get("delta_ra_arcsec", a.get("deltaRaArcsec"))
        d_dec = a.get("delta_dec_arcsec", a.get("deltaDecArcsec"))
        if ra is not None and dec is not None and d_ra is not None and d_dec is not None:
            # Filter non-finite or extreme outliers (> 5 degrees)
            if math.isfinite(ra) and math.isfinite(dec) and math.isfinite(d_ra) and math.isfinite(d_dec):
                err = math.hypot(d_ra, d_dec)
                # Ignore outliers (> 5 deg) and degenerate echoes (< 0.5")
                if 0.5 <= err < 18000.0 and not (abs(d_dec) < 1e-4 and abs(d_ra) < 1.0):
                    valid_points.append({
                        "ra": float(ra),
                        "dec": float(dec),
                        "d_ra": float(d_ra),
                        "d_dec": float(d_dec),
                        "time": a.get("timestamp", 0.0),
                    })

    n = len(valid_points)
    if n < 4:
        # Insufficient degrees of freedom for least squares
        raw_errors = [math.hypot(p["d_ra"], p["d_dec"]) for p in valid_points]
        raw_rms = float(np.sqrt(np.mean(np.square(raw_errors)))) if raw_errors else 0.0
        return MountPointingModel(
            sample_count=n,
            raw_rms_arcsec=round(raw_rms, 2),
            residual_rms_arcsec=round(raw_rms, 2),
            improvement_percent=0.0,
            confidence="insufficient_data",
            message=f"Need at least 4 plate-solve points to decompose terms; found {n}.",
        )

    # Parameters to solve: [IH, ID, ME, MA, CH, TF]
    a_rows = []
    b_rows = []
    raw_sq_errors = []

    for p in valid_points:
        d_ra = p["d_ra"]
        d_dec = p["d_dec"]

        raw_sq_errors.append(d_ra**2 + d_dec**2)

        g = _pointing_geometry(p["ra"], p["dec"], latitude_deg)

        # 1. RA projection equation (IH, ID, ME, MA, CH, TF)
        row_ra = [
            -1.0,
            0.0,
            g["sin_h"] * g["tan_dec"],
            -g["cos_h"] * g["tan_dec"],
            g["sec_dec"],
            g["cos_h"] * g["cos_phi"] * g["tan_dec"],
        ]
        a_rows.append(row_ra)
        b_rows.append(d_ra * g["cos_dec"])

        # 2. Dec equation (IH, ID, ME, MA, CH, TF)
        row_dec = [
            0.0,
            -1.0,
            g["cos_h"],
            g["sin_h"],
            0.0,
            g["cos_h"] * g["sin_phi"] * g["sin_dec"] - g["cos_phi"] * g["cos_dec"],
        ]
        a_rows.append(row_dec)
        b_rows.append(d_dec)

    a_matrix = np.array(a_rows, dtype=np.float64)
    b_vector = np.array(b_rows, dtype=np.float64)

    # Solve via SVD / least squares with regularization
    coeffs, _residuals, rank, _s = np.linalg.lstsq(a_matrix, b_vector, rcond=1e-3)

    ih, id_err, me, ma, ch, tf = [float(c) for c in coeffs]

    # Calculate model predicted errors and residuals
    predicted = a_matrix @ coeffs
    res_vector = b_vector - predicted

    # Reconstruct residual RA and Dec components
    res_sq_sum = 0.0
    for i in range(n):
        res_ra_proj = res_vector[2 * i]
        res_dec = res_vector[2 * i + 1]
        res_sq_sum += res_ra_proj**2 + res_dec**2

    raw_rms = math.sqrt(sum(raw_sq_errors) / n)
    res_rms = math.sqrt(res_sq_sum / n)

    improvement = 0.0
    if raw_rms > 1e-4:
        improvement = max(0.0, (1.0 - (res_rms / raw_rms)) * 100.0)

    total_polar_error = math.hypot(me, ma)
    confidence = "high" if n >= 15 and rank >= 5 else ("medium" if n >= 8 else "low")

    msg = f"Model fitted over {n} points with {round(improvement, 1)}% error reduction."

    return MountPointingModel(
        sample_count=n,
        raw_rms_arcsec=round(raw_rms, 2),
        residual_rms_arcsec=round(res_rms, 2),
        improvement_percent=round(improvement, 1),
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
