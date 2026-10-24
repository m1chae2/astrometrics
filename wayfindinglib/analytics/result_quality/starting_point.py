"""Purpose: A first exposure for a target that has no frames yet.

Description: A target with no frames has no noise history, so the report
cannot say "take N more". It gives a starting point instead (plan section
6.8), always labeled as one. The exposure comes from two limits:

* **Sky-dominated exposure.** Long enough that the sky's photon noise
  dominates the camera's read noise. For one pixel the sky variance is
  ``B t`` electrons squared and the read noise adds ``RN^2``, so the sky
  dominates when ``B t >= k RN^2``, that is ``t >= k RN^2 / B``. The
  constant ``k`` is `SKY_LIMITED_VARIANCE_RATIO` (9): at that ratio the read
  noise adds one ninth to the variance, which is 5.4 percent to the noise
  (the same ratio as the underexposure rule, register row WAY-D24).
* **Clipping cap.** Short enough that the brightest star does not clip. The
  cap needs the star's peak rate in ADU per second, which comes from a
  catalog magnitude and a photometric zero point. The library has no zero
  point that is not exploratory (decision S12), so the cap is "not computed"
  with that reason unless the caller supplies a measured peak rate. The star
  is assumed centered on a pixel, which gives the highest peak and so the
  shortest, safest cap.

The sky rate and the read noise are the library's own measurements for the
equipment, so the starting point follows the equipment. Nothing here reads a
database; the caller passes the numbers in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from astrometricslib import CaptureSetting
from wayfindinglib.analytics.result_quality.efficiency import SKY_LIMITED_VARIANCE_RATIO
from wayfindinglib.analytics.result_quality.rules import TARGET_PEAK_FRACTION
from wayfindinglib.models.planning.target_report import StartingPoint
from wayfindinglib.models.quality.statements import Confidence

STARTING_POINT_ENTRY = "WAY-D67"
"""Register row behind the starting point."""


def sky_dominated_exposure_seconds(
    sky_rate_e_per_s_per_px: float, read_noise_e: float, variance_ratio: float = SKY_LIMITED_VARIANCE_RATIO
) -> float:
    """Find the shortest exposure where the sky noise dominates the read noise.

    Parameters
    ----------
    sky_rate_e_per_s_per_px : `float`
        The sky background, in electrons per second per pixel. Above 0.
    read_noise_e : `float`
        The camera's read noise, in electrons. At least 0.
    variance_ratio : `float`, optional
        The least ratio of sky variance to read-noise variance. The default
        is `SKY_LIMITED_VARIANCE_RATIO`.

    Returns
    -------
    exposure_seconds : `float`
        ``variance_ratio * read_noise_e**2 / sky_rate_e_per_s_per_px``.

    Raises
    ------
    ValueError
        If the sky rate is not above 0 or the read noise is negative.
    """
    if sky_rate_e_per_s_per_px <= 0.0:
        raise ValueError("The sky rate must be above 0.")
    if read_noise_e < 0.0:
        raise ValueError("The read noise cannot be negative.")
    return variance_ratio * read_noise_e**2 / sky_rate_e_per_s_per_px


def clipping_cap_seconds(peak_rate_adu_per_s: float, room_adu: float) -> float:
    """Find the longest exposure that keeps the brightest star from clipping.

    The star's peak grows in proportion to the exposure. The cap aims the peak
    at `TARGET_PEAK_FRACTION` of the room between the sky and the ceiling (the
    same aim as the overexposure rule, register row WAY-D05).

    Parameters
    ----------
    peak_rate_adu_per_s : `float`
        The brightest star's peak above the sky, in ADU per second, for a star
        centered on a pixel. Above 0.
    room_adu : `float`
        The room above the sky, in ADU: the lower of the clip ceiling and the
        linearity limit, less the bias and the sky. Above 0.

    Returns
    -------
    exposure_seconds : `float`
        ``TARGET_PEAK_FRACTION * room_adu / peak_rate_adu_per_s``.

    Raises
    ------
    ValueError
        If the peak rate or the room is not above 0.
    """
    if peak_rate_adu_per_s <= 0.0 or room_adu <= 0.0:
        raise ValueError("The peak rate and the room must be above 0.")
    return TARGET_PEAK_FRACTION * room_adu / peak_rate_adu_per_s


@dataclass(frozen=True)
class StartingPointInputs:
    """What the starting point needs, with its sources or why it is missing.

    Attributes
    ----------
    capture_setting : `CaptureSetting` or `None`
        The setting the numbers are for.
    sky_rate_e_per_s_per_px : `float` or `None`
        The expected moonless sky rate, in electrons per second per pixel.
    sky_note : `str`
        Where the sky rate came from, or why there is none.
    read_noise_e : `float` or `None`
        The camera's measured read noise, in electrons.
    read_noise_note : `str`
        Where the read noise came from, or why there is none.
    peak_rate_adu_per_s : `float` or `None`
        The brightest star's peak rate in ADU per second, or ``None``.
    room_adu : `float` or `None`
        The room above the sky, in ADU, or ``None``.
    clipping_note : `str`
        Where the peak rate and the room came from, or why there are none.
    """

    capture_setting: CaptureSetting | None
    sky_rate_e_per_s_per_px: float | None
    sky_note: str
    read_noise_e: float | None
    read_noise_note: str
    peak_rate_adu_per_s: float | None
    room_adu: float | None
    clipping_note: str


def build_starting_point(inputs: StartingPointInputs) -> StartingPoint:
    """Build the starting point from its inputs.

    The exposure is the sky-dominated exposure, rounded up to a whole second,
    or the clipping cap when that is shorter (a star that clips is worse than
    a little read noise). A missing input leaves the number out and the reason
    says why.

    Parameters
    ----------
    inputs : `StartingPointInputs`
        The sky, the read noise and the clipping facts.

    Returns
    -------
    starting_point : `StartingPoint`
        The starting point, with a design-estimate confidence.
    """
    cap: float | None = None
    cap_reason = inputs.clipping_note or "No peak rate for the brightest star is available."
    if inputs.peak_rate_adu_per_s is not None and inputs.room_adu is not None:
        cap = clipping_cap_seconds(inputs.peak_rate_adu_per_s, inputs.room_adu)
        cap_reason = (
            f"{TARGET_PEAK_FRACTION:g} of the room ({inputs.room_adu:.0f} ADU) over the star's peak rate "
            f"({inputs.peak_rate_adu_per_s:.1f} ADU per second), for a star centered on a pixel. "
            f"{inputs.clipping_note}"
        ).strip()
    exposure: float | None = None
    if inputs.sky_rate_e_per_s_per_px is None or inputs.read_noise_e is None:
        missing = [
            note
            for note, value in (
                (inputs.sky_note, inputs.sky_rate_e_per_s_per_px),
                (inputs.read_noise_note, inputs.read_noise_e),
            )
            if value is None
        ]
        exposure_reason = "Not computed. " + " ".join(missing)
    else:
        exposure = float(
            math.ceil(sky_dominated_exposure_seconds(inputs.sky_rate_e_per_s_per_px, inputs.read_noise_e))
        )
        exposure = max(exposure, 1.0)
        exposure_reason = (
            f"The sky variance reaches {SKY_LIMITED_VARIANCE_RATIO:g} times the read-noise variance "
            f"(sky {inputs.sky_rate_e_per_s_per_px:.3f} e/s/px, read noise {inputs.read_noise_e:.2f} e). "
            f"{inputs.sky_note} {inputs.read_noise_note}"
        ).strip()
        if cap is not None and cap < exposure:
            exposure_reason += (
                f" The clipping cap of {cap:.0f} s is shorter, so the exposure is held to it and the read "
                "noise will cost more than the rule allows."
            )
            exposure = max(1.0, math.floor(cap))
    return StartingPoint(
        capture_setting=inputs.capture_setting,
        exposure_seconds=exposure,
        exposure_reason=exposure_reason,
        clipping_cap_seconds=cap,
        clipping_cap_reason=cap_reason,
        confidence=Confidence.DESIGN_ESTIMATE,
    )
