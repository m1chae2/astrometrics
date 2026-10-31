"""Measuring the sky brightness and the noise it brings, in electrons.

The sky level is a sigma-clipped mean, found with the same estimator as the
bias level. With the camera's gain it gives the sky in electrons, and the
expected photon noise follows. The read noise of the camera at the frame's
setting comes from the camera profile. The sky includes dark current and amp
glow; for a camera with no recorded sensor temperature it is stored as
``assumed``.

`measure_frame` in `frame_measurement.py` calls these functions and writes the
results as measurements.
"""

import math
from dataclasses import dataclass

from astrometricslib.models.camera_profile import CameraProfile, ProvenancedValue, ProvenanceKind
from astrometricslib.models.target import FrameRecord


@dataclass(frozen=True)
class SkyElectrons:
    """The sky of one plane of one frame, in electrons.

    Attributes
    ----------
    rate_e_per_s_per_px : `float` or `None`
        Sky electrons per second per pixel.
    noise_e : `float` or `None`
        The expected photon (Poisson) noise of the sky in one exposure, per
        pixel: the square root of the sky electrons. It is not the measured
        scatter of the pixels, which also holds read noise, dark current and
        the faint stars.
    background_adu : `float` or `None`
        The sigma-clipped mean of the plane, in ADU.
    reason : `str`
        Why the values are missing, or the source of the gain.
    gain_is_measured : `bool`
        Whether the gain behind the values was measured (not assumed).
    """

    rate_e_per_s_per_px: float | None = None
    noise_e: float | None = None
    background_adu: float | None = None
    reason: str = ""
    gain_is_measured: bool = False


def provenance_text(label: str, value: ProvenancedValue) -> str:
    """Say where a camera-profile number came from.

    Parameters
    ----------
    label : `str`
        What the number is, such as "gain".
    value : `ProvenancedValue`
        The number with its provenance.

    Returns
    -------
    text : `str`
        For example ``"gain 0.9 e-/ADU (measured: flat pairs 2026-10-20)"``.
    """
    return f"{label} {value.value:g} ({value.provenance.kind.value}: {value.provenance.source})"


def look_up_frame_sensor_noise(
    camera_profile: CameraProfile | None, frame: FrameRecord, *, clipped_at_zero: bool | None = None
) -> tuple[ProvenancedValue | None, ProvenancedValue | None, str]:
    """Find the gain and read noise the profile holds for a frame's setting.

    An entry whose gain setting, offset and binning all match always wins.
    The frame record keeps the gain setting in its ``iso`` field (the header's
    ``GAIN`` card, or the ISO of a DSLR). With ``clipped_at_zero=False`` and
    no exact entry, the entry of the same gain and binning at the nearest
    other offset is used (see `CameraProfile.sensor_noise_for`), and its
    source text says so.

    Parameters
    ----------
    camera_profile : `CameraProfile` or `None`
        The camera's profile.
    frame : `FrameRecord`
        The frame.
    clipped_at_zero : `bool` or `None`, optional
        Whether the frame's pixels are cut off at zero. `False` allows the
        offset fallback; `True` and `None` (not known) do not.

    Returns
    -------
    gain : `ProvenancedValue` or `None`
        Electrons per ADU at the frame's setting.
    read_noise : `ProvenancedValue` or `None`
        Read noise in electrons at the frame's setting.
    reason : `str`
        Why a value is missing (empty when both were found).
    """
    if camera_profile is None:
        return None, None, "no camera profile was given for this frame"
    gap = camera_profile.sensor_noise_gap_reason(
        frame.iso, frame.offset, frame.binning, clipped_at_zero=clipped_at_zero
    )
    if gap is not None:
        return None, None, gap
    entry = camera_profile.sensor_noise_for(
        frame.iso, frame.offset, frame.binning, clipped_at_zero=clipped_at_zero
    )
    if entry is None:
        return None, None, "camera profile has no entry for this setting"
    reasons = []
    if entry.gain_e_per_adu is None:
        reasons.append("the profile entry for this setting holds no gain")
    if entry.read_noise_e is None:
        reasons.append("the profile entry for this setting holds no read noise")
    return entry.gain_e_per_adu, entry.read_noise_e, "; ".join(reasons)


def measure_sky_electrons(
    background_adu: float,
    bias_level_adu: float | None,
    gain: ProvenancedValue | None,
    exposure_seconds: float | None,
    gain_reason: str,
    bias_reason: str = "",
) -> SkyElectrons:
    """Turn a plane's background into electrons.

    The sky signal in ADU is the background minus the bias level (the constant
    the camera adds to every pixel, which the raw frame carries in its pixel
    values; the header's ``OFFSET`` card is a camera setting, not that level in
    ADU). The signal times the gain is the sky in electrons for one exposure,
    and its square root is the expected photon counting (Poisson) noise of that
    sky per pixel. Dark current and amp glow (light from the sensor's own
    electronics) are inside the signal, because no dark frame is subtracted.

    Parameters
    ----------
    background_adu : `float`
        The sigma-clipped mean of the plane, in ADU (see
        `measure_clipped_mean_adu`).
    bias_level_adu : `float` or `None`
        The bias level for the frame's setting, in ADU, found with the same
        estimator. `None` means unknown.
    gain : `ProvenancedValue` or `None`
        Electrons per ADU at the frame's setting.
    exposure_seconds : `float` or `None`
        The exposure time.
    gain_reason : `str`
        Why the gain is missing, used when it is.
    bias_reason : `str`, optional
        Why the bias level is missing, used when it is. A generic sentence is
        used when this is empty.

    Returns
    -------
    sky : `SkyElectrons`
        The rate and noise, or the reason they are missing.
    """
    if gain is None:
        return SkyElectrons(background_adu=background_adu, reason=gain_reason or "no gain for this setting")
    if bias_level_adu is None:
        return SkyElectrons(
            background_adu=background_adu,
            reason=bias_reason
            or (
                "the bias level of this setting in ADU is not known, and the raw frame does not state it "
                "(the OFFSET card is a camera setting, not a level in ADU)"
            ),
        )
    if exposure_seconds is None:
        return SkyElectrons(background_adu=background_adu, reason="the frame has no usable exposure time")
    signal_adu = background_adu - bias_level_adu
    if signal_adu <= 0.0:
        return SkyElectrons(
            background_adu=background_adu,
            reason=(
                f"the background ({background_adu:.1f} ADU) is not above the bias "
                f"level ({bias_level_adu:.1f} ADU)"
            ),
        )
    sky_electrons = signal_adu * gain.value
    return SkyElectrons(
        rate_e_per_s_per_px=sky_electrons / exposure_seconds,
        noise_e=math.sqrt(sky_electrons),
        background_adu=background_adu,
        reason=(
            f"clipped-mean background {background_adu:.1f} ADU minus bias level "
            f"{bias_level_adu:.1f} ADU, times "
            f"{provenance_text('gain (e-/ADU)', gain)}"
        ),
        gain_is_measured=gain.provenance.kind == ProvenanceKind.MEASURED,
    )
