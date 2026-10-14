"""Purpose: Turn guide pulses into sky motion, and guide errors into RMS.

Description: A guide pulse moves the mount at the guide rate for a set
time. This module holds the arithmetic that links the two, so the
guiding drivers, the dither step of a capture, and the guiding history
all use the same numbers:

- `pulse_ms_to_arcsec` and `arcsec_to_pulse_ms` convert between a pulse
  length in milliseconds and the motion it causes, in arcseconds;
- `merge_close_pulses` joins the separate right ascension and
  declination pulses that a guider sends within a moment of each other
  into one correction;
- `signed_pulses_ms` reads one merged correction as a signed length on
  each axis;
- `rms_arcsec` gives the root-mean-square (RMS) of a list of guide
  errors, the usual measure of guiding accuracy.
"""

import math
from collections.abc import Iterable
from typing import Any

__all__ = [
    "GUIDE_RATE_ARCSEC_PER_S",
    "PULSE_MERGE_WINDOW_SECONDS",
    "arcsec_to_pulse_ms",
    "merge_close_pulses",
    "pulse_ms_to_arcsec",
    "rms_arcsec",
    "signed_pulses_ms",
]

GUIDE_RATE_ARCSEC_PER_S = 7.52
"""Sky motion during a guide pulse at half the sidereal rate, in arcsec/s.

The sidereal rate is about 15.04 arcseconds per second; most mounts guide
at half of it."""

PULSE_MERGE_WINDOW_SECONDS = 0.8
"""Pulses closer together than this belong to the same correction."""

_DIRECTION_KEYS = ("pulse_n", "pulse_s", "pulse_w", "pulse_e")


def pulse_ms_to_arcsec(duration_ms: float, guide_rate_arcsec_per_s: float = GUIDE_RATE_ARCSEC_PER_S) -> float:
    """Return the sky motion a guide pulse causes.

    Parameters
    ----------
    duration_ms : `float`
        Signed pulse length in milliseconds.
    guide_rate_arcsec_per_s : `float`, optional
        The mount's guide rate.

    Returns
    -------
    motion_arcsec : `float`
        The motion in arcseconds, with the sign of `duration_ms`.
    """
    return duration_ms / 1000.0 * guide_rate_arcsec_per_s


def arcsec_to_pulse_ms(
    motion_arcsec: float, guide_rate_arcsec_per_s: float = GUIDE_RATE_ARCSEC_PER_S
) -> float:
    """Return the pulse length that moves the mount by `motion_arcsec`.

    Parameters
    ----------
    motion_arcsec : `float`
        The wanted motion in arcseconds.
    guide_rate_arcsec_per_s : `float`, optional
        The mount's guide rate.

    Returns
    -------
    duration_ms : `float`
        The pulse length in milliseconds, with the sign of `motion_arcsec`.
    """
    return motion_arcsec / guide_rate_arcsec_per_s * 1000.0


def merge_close_pulses(
    pulses: Iterable[dict[str, Any]], window_seconds: float = PULSE_MERGE_WINDOW_SECONDS
) -> list[dict[str, Any]]:
    """Join pulses sent within `window_seconds` of each other into one.

    A guider sends its right ascension and declination corrections as two
    separate pulses. Seen from outside, they arrive as two records a few
    hundred milliseconds apart. Each record holds ``time`` (Unix seconds)
    and a length in milliseconds for each direction that moved:
    ``pulse_n``, ``pulse_s``, ``pulse_w`` and ``pulse_e``.

    Parameters
    ----------
    pulses : `Iterable` [`dict` [`str`, `Any`]]
        Pulse records in time order.
    window_seconds : `float`, optional
        How close two records must be to join.

    Returns
    -------
    merged : `list` [`dict` [`str`, `Any`]]
        One record per correction. A later pulse on the same direction
        replaces the earlier length.
    """
    merged: list[dict[str, Any]] = []
    for pulse in pulses:
        if merged and abs(pulse.get("time", 0.0) - merged[-1].get("time", 0.0)) < window_seconds:
            for key in _DIRECTION_KEYS:
                if pulse.get(key, 0) > 0:
                    merged[-1][key] = pulse[key]
        else:
            merged.append(dict(pulse))
    return merged


def signed_pulses_ms(pulse: dict[str, Any]) -> tuple[float, float]:
    """Read one merged correction as a signed length on each axis.

    West and north count as positive.

    Parameters
    ----------
    pulse : `dict` [`str`, `Any`]
        A record from `merge_close_pulses`.

    Returns
    -------
    ra_ms, dec_ms : `float`
        The right ascension (west-east) and declination (north-south)
        pulse lengths in milliseconds.
    """
    ra_ms = 0.0
    if pulse.get("pulse_w", 0) > 0:
        ra_ms = float(pulse["pulse_w"])
    elif pulse.get("pulse_e", 0) > 0:
        ra_ms = -float(pulse["pulse_e"])
    dec_ms = 0.0
    if pulse.get("pulse_n", 0) > 0:
        dec_ms = float(pulse["pulse_n"])
    elif pulse.get("pulse_s", 0) > 0:
        dec_ms = -float(pulse["pulse_s"])
    return ra_ms, dec_ms


def rms_arcsec(errors_arcsec: Iterable[float]) -> float:
    """Return the root-mean-square of a list of guide errors.

    Parameters
    ----------
    errors_arcsec : `Iterable` [`float`]
        Guide errors on one axis, in arcseconds.

    Returns
    -------
    rms : `float`
        The RMS in arcseconds, or 0 for an empty list.
    """
    values = list(errors_arcsec)
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))
