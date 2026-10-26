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
   records where each number came from. A profile keeps one gain and read
   noise for each camera setting (gain setting, offset and binning), and a
   frame uses the entry for its own setting only. A frame whose setting has
   no entry gets nothing from the profile, never the value of a nearby
   setting.
2. The FITS header: ``EGAIN`` for the gain (electrons per ADU) and
   ``RDNOISE`` for the read noise (electrons). The ``GAIN`` card is never
   used as electrons per ADU. Cameras such as the ZWO ASI533 write a gain
   setting index into ``GAIN`` (the sample frames say ``GAIN = 0.0``), not a
   conversion factor.
3. An assumption: 1 electron per ADU and no read noise. The result records
   that it assumed these, so the uncertainties can be reported as "assume
   unit gain".

The numbers depend on the camera's gain setting, offset and binning, so the
result also says why the profile gave nothing when it did not (see
`DetectorNoise.profile_gap_reason`).
"""

import math
from dataclasses import dataclass
from typing import Any

from astrometricslib.drivers.calibration_library import header_binning
from astrometricslib.models.camera_profile import CameraProfile, SensorSettingNoise
from astrometricslib.utilities.iso_text import iso_or_gain_text


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
    profile_gap_reason : `str` or `None`
        Why the camera profile did not supply the gain, the read noise, or
        both (for example, it has no entry for the frame's setting). `None`
        when the profile supplied both. A header value may still have filled
        the gap; the ``*_is_assumed`` flags say whether anything was assumed.
    """

    gain_e_per_adu: float = 1.0
    read_noise_e: float = 0.0
    gain_is_assumed: bool = True
    read_noise_is_assumed: bool = True
    profile_gap_reason: str | None = None


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


def _header_gain_setting(header: Any) -> str | None:
    """Read the gain setting (or ISO) a frame states in its header.

    Returns
    -------
    gain_setting : `str` or `None`
        The ``ISOSPEED`` or ``GAIN`` card as text, or `None` when the
        header has neither (or there is no header).
    """
    return iso_or_gain_text(header) if header is not None else None


def _header_offset_setting(header: Any) -> Any:
    """Read the offset a frame states in its header.

    Returns
    -------
    offset : `Any`
        The ``OFFSET`` card, else ``BLKLEVEL``, else `None`. A missing offset
        is treated as 0 when the setting is compared, as the calibration
        library does.
    """
    if header is None:
        return None
    offset = header.get("OFFSET")
    return header.get("BLKLEVEL") if offset is None else offset


def resolve_detector_noise(
    profile: CameraProfile | None,
    header: Any = None,
    *,
    gain_setting: Any = None,
    offset: Any = None,
    binning: Any = None,
    clipped_at_zero: bool | None = None,
) -> DetectorNoise:
    """Pick the gain and read noise for a camera and frame.

    Parameters
    ----------
    profile : `CameraProfile` or `None`
        The camera's profile. Its ``sensor_noise`` entry for the frame's
        setting comes first.
    header : `astropy.io.fits.Header`, optional
        The frame's header. It gives the frame's setting (``ISOSPEED`` or
        ``GAIN``, ``OFFSET`` or ``BLKLEVEL``, ``XBINNING`` and
        ``YBINNING``), and its ``EGAIN`` and ``RDNOISE`` are used for a
        number the profile does not give.
    gain_setting, offset, binning : `Any`, optional
        The frame's setting, when it is known from somewhere other than a
        header (for example a frame record's ``iso``, ``offset`` and
        ``binning``). Each one given replaces what the header says.
    clipped_at_zero : `bool` or `None`, optional
        Whether the frame is cut off at zero. `False` lets the profile's
        entry from another offset stand in when none has the frame's offset
        (see `CameraProfile.sensor_noise_for`). `None` (not known, the
        default) and `True` allow only an exact match.

    Returns
    -------
    noise : `DetectorNoise`
        The numbers, with a flag for each one that had to be assumed and the
        reason when the profile gave nothing.
    """
    if gain_setting is None:
        gain_setting = _header_gain_setting(header)
    if offset is None:
        offset = _header_offset_setting(header)
    if binning is None:
        binning = header_binning(header) if header is not None else None

    entry: SensorSettingNoise | None = None
    profile_gap_reason: str | None = None
    if profile is None:
        profile_gap_reason = "no camera profile"
    else:
        entry = (
            profile.sensor_noise_for(gain_setting, offset, binning, clipped_at_zero=clipped_at_zero)
            if gain_setting is not None
            else None
        )
        if entry is None:
            profile_gap_reason = profile.sensor_noise_gap_reason(
                gain_setting, offset, binning, clipped_at_zero=clipped_at_zero
            )
        elif entry.gain_e_per_adu is None:
            profile_gap_reason = "the profile entry for this setting has no gain"
        elif entry.read_noise_e is None:
            profile_gap_reason = "the profile entry for this setting has no read noise"

    gain = entry.gain_e_per_adu.value if entry is not None and entry.gain_e_per_adu else None
    if gain is None and header is not None:
        gain = _positive_number(header.get("EGAIN"))

    read_noise = entry.read_noise_e.value if entry is not None and entry.read_noise_e else None
    if read_noise is None and header is not None:
        read_noise = _positive_number(header.get("RDNOISE"), allow_zero=True)

    return DetectorNoise(
        gain_e_per_adu=gain if gain is not None else 1.0,
        read_noise_e=read_noise if read_noise is not None else 0.0,
        gain_is_assumed=gain is None,
        read_noise_is_assumed=read_noise is None,
        profile_gap_reason=profile_gap_reason,
    )
