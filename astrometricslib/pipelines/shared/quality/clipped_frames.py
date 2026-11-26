"""The test for a raw frame that is cut off at zero, shared by every pipeline.

A camera adds a constant (the offset) to every pixel before it stores the
value. When the offset is small next to the read noise, some pixels would be
negative and are stored as exactly zero instead. A frame like that shows a
noise that is too low, so the gain and read noise measured at another offset
cannot stand in for it. A frame with no such zeros can use them, because the
offset changes neither the gain nor the read noise.

`CLIPPED_FRAME_ZERO_FRACTION` is the limit. `frame_is_clipped_at_zero` applies
it to one frame's pixels. The stacking, photometry and spectroscopy pipelines
all use it, so they agree on which frames are clipped.
"""

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_zero_fraction

# A raw frame with more than this fraction of its pixels at exactly zero is
# treated as clipped: the camera's offset sits below the read noise, so the
# frame cannot show negative noise and its measured noise is too low. On the
# Vega session (ASI533MM Pro, gain 0) the zero fraction was 71% at 0.5 s, 31%
# at 1 s, 1% at 2 s, 0.2% at 3 s and 0.02% at 5 s, and the measured noise was
# 0 counts, 4.2 counts and 8.4 counts (2, 3 and 5 s all gave 8.4), so the
# clipped frames read low by up to a half or more. 0.2 sits between the 1 s
# group (clipped) and the 2 s group (not). Unvalidated on other cameras or
# offsets. Photometry and spectroscopy use the same limit (see
# `frame_is_clipped_at_zero`); the offset-0 lights of the ASI533MM Pro have an
# exact-zero fraction of 0.0, far under it.
CLIPPED_FRAME_ZERO_FRACTION = TunedParameter(
    name="stacking.clipped_frame_zero_fraction",
    default=0.2,
    unit="fraction",
    description="Share of pixels at exactly zero above which a raw frame is treated as clipped.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-22",
)


def frame_is_clipped_at_zero(pixels: np.ndarray) -> bool:
    """Say whether a frame has so many exact zeros that it is cut off at zero.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The frame's pixels, one plane or several.

    Returns
    -------
    is_clipped : `bool`
        `True` when the share of pixels at exactly zero is above
        `CLIPPED_FRAME_ZERO_FRACTION`. `False` otherwise. Pass the result as
        ``clipped_at_zero`` to `CameraProfile.sensor_noise_for`.
    """
    zero_fraction = frame_zero_fraction([(None, np.asarray(pixels))])
    return zero_fraction > resolve(CLIPPED_FRAME_ZERO_FRACTION).value
