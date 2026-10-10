"""Finds the gain and read noise to use for a frame's flux uncertainties.

The noise in a brightness measurement depends on how the camera turns light
into numbers. Two camera facts matter:

* The gain, in electrons per ADU (ADU is analog-to-digital unit, one step of
  the stored pixel value). Light arrives as individual particles, so the
  random scatter of a count is set by the number of electrons collected, not
  by the number of ADU. Without the gain, ADU cannot be turned into
  electrons.
* The read noise, in electrons: the random error added to every pixel each
  time the sensor is read.

This module picks both numbers in this order:

1. The camera's profile (`astrometricslib.models.camera_profile`), which
   records where each number came from.
2. The FITS header: ``EGAIN`` for the gain (electrons per ADU) and
   ``RDNOISE`` for the read noise (electrons). The ``GAIN`` card is never
   used as electrons per ADU. Cameras such as the ZWO ASI533 write a gain
   setting index into ``GAIN`` (the sample frames say ``GAIN = 0.0``), not a
   conversion factor.
3. An assumption: 1 electron per ADU and no read noise. The result records
   that it assumed these, so the uncertainties can be reported as "assume
   unit gain".

The numbers depend on the camera's gain setting. A camera profile holds one
value, so it is exact only for frames taken at that setting.
"""

import math
from dataclasses import dataclass
from typing import Any

from astrometricslib.models.camera_profile import CameraProfile


@dataclass(frozen=True)
class DetectorNoise:
    """The camera numbers used to compute flux uncertainties.

    Attributes
    ----------
    gain_e_per_adu : `float`
        Electrons per ADU. Above zero.
    read_noise_e : `float`
        Read noise in electrons (RMS, root mean square). Zero or more.
    gain_is_assumed : `bool`
        `True` when no source gave the gain and `gain_e_per_adu` is the
        assumed 1.0.
    read_noise_is_assumed : `bool`
        `True` when no source gave the read noise and `read_noise_e` is the
        assumed 0.0.
    """

    gain_e_per_adu: float = 1.0
    read_noise_e: float = 0.0
    gain_is_assumed: bool = True
    read_noise_is_assumed: bool = True


def _positive_number(value: Any, allow_zero: bool = False) -> float | None:
    """Read a header value as a finite number above zero.

    Parameters
    ----------
    value : `Any`
        The header value, or `None`.
    allow_zero : `bool`, optional
        Whether zero counts as valid.

    Returns
    -------
    number : `float` or `None`
        The number, or `None` when the value is missing, not a number, not
        finite, or not above zero (or not below zero when `allow_zero`).
    """
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        return None
    return number


def resolve_detector_noise(profile: CameraProfile | None, header: Any = None) -> DetectorNoise:
    """Pick the gain and read noise for a camera and frame.

    Parameters
    ----------
    profile : `CameraProfile` or `None`
        The camera's profile. Its ``gain_e_per_adu`` and ``read_noise_e``
        come first.
    header : `astropy.io.fits.Header`, optional
        The frame's header. ``EGAIN`` and ``RDNOISE`` are used for a number
        the profile does not give.

    Returns
    -------
    noise : `DetectorNoise`
        The numbers, with a flag for each one that had to be assumed.
    """
    gain = profile.gain_e_per_adu.value if profile is not None and profile.gain_e_per_adu else None
    if gain is None and header is not None:
        gain = _positive_number(header.get("EGAIN"))

    read_noise = profile.read_noise_e.value if profile is not None and profile.read_noise_e else None
    if read_noise is None and header is not None:
        read_noise = _positive_number(header.get("RDNOISE"), allow_zero=True)

    return DetectorNoise(
        gain_e_per_adu=gain if gain is not None else 1.0,
        read_noise_e=read_noise if read_noise is not None else 0.0,
        gain_is_assumed=gain is None,
        read_noise_is_assumed=read_noise is None,
    )
