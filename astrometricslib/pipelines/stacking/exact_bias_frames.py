"""Finds the bias frames taken at exactly one camera setting.

Two jobs need bias frames that match a camera setting exactly: the sensor
noise measurement (gain and read noise) and the frame measurement job (the
bias level that the sky in electrons is measured above). A bias frame taken at
another gain setting, offset or binning has a different level and different
noise, so neither job may borrow one. The calibration library's own selection
(`CalibrationLibrary.select_bias_frames`) falls back to a nearby setting when
it has no exact match and says so with a flag. This helper turns any flag into
"no exact bias frames", so both jobs apply the same rule in one place.
"""

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ExactBiasFrames", "exact_setting_bias_frames"]


@dataclass(frozen=True)
class ExactBiasFrames:
    """The bias frames at one exact camera setting, or why there are none.

    Attributes
    ----------
    paths : `list` [`str`]
        The bias frame paths. Empty when the library holds none at exactly
        this setting.
    reason : `str`
        Why `paths` is empty, as a sentence. Empty when frames were found.
    """

    paths: list[str] = field(default_factory=list)
    reason: str = ""


def exact_setting_bias_frames(
    library: Any, camera: str, gain: str, offset: str, binning: str
) -> ExactBiasFrames:
    """List the bias frames taken at exactly one camera setting.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The calibration library that lists the bias frames.
    camera : `str`
        The camera name.
    gain : `str`
        The gain setting (or ISO), in standard form.
    offset : `str`
        The offset, in standard form.
    binning : `str`
        The binning, such as ``"1x1"``, in standard form.

    Returns
    -------
    frames : `ExactBiasFrames`
        The bias frame paths, or an empty list with the reason. The library's
        flags (it fell back to a nearby setting) and blocking flags are
        treated as "no exact match", and are repeated in the reason.

    Notes
    -----
    The library's own errors (`AstrometricsError` and the data errors) are
    not caught here. The caller decides what a failed listing means.
    """
    setting_text = f"gain {gain}, offset {offset}, binning {binning}"
    selection = library.select_bias_frames(camera, iso=gain, offset=offset, binning=binning)
    problems = [*selection.flags, *selection.blocking_flags]
    if problems or not selection.frames:
        reason = f"the calibration library holds no bias frames for {camera} at exactly {setting_text}"
        if problems:
            reason += ": " + "; ".join(problems)
        return ExactBiasFrames(reason=reason)
    return ExactBiasFrames(paths=list(selection.frames))
