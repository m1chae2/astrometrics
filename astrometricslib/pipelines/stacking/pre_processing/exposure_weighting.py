"""Chooses how to weight frames of different exposure lengths in one stack.

An equal-weight average treats a 30-second frame like a 120-second one. For
frames limited by the noise of the sky (these are, at every exposure the
ASI533MM Pro stacks use), a frame's signal-to-noise squared grows in step with
its exposure, so a short frame carries less information and should count for
less. Weighting each frame by the inverse of its noise gives the best
combination and lowers the noise of the stack.

How much an equal-weight stack loses follows from the exposure times alone.
For exposures t, equal weights leave the noise of the stack higher than the
best weighting by the factor

    sqrt(mean(1 / t) * mean(t)),

which is 1 for equal exposures. M 27 stacks 26 frames of 30 s, 33 of 60 s, 22
of 120 s and 1 of 300 s, and the factor is 1.17. Siril's noise weighting
(`-weight=noise`) lowered the measured pixel noise of that stack by 13%.
"""

import logging
from collections.abc import Sequence

import numpy as np

logger = logging.getLogger(__name__)

__all__ = ["NOISE_WEIGHT_MODE", "choose_stack_weight", "equal_weight_noise_penalty"]

# The Siril `stack -weight=` value that weights each frame by its own noise.
NOISE_WEIGHT_MODE = "noise"

# Noise weighting is switched on when equal weights would leave the stack at
# least this much noisier than the best weighting (a factor of 1.05 is 5%).
# Exposures that differ by a few percent give a factor within 1.005 of 1, so
# they are left alone. M 27 gives 1.17 and measured 13% less noise with the
# weighting.
MINIMUM_PENALTY_TO_WEIGHT = 1.05


def equal_weight_noise_penalty(exposures: Sequence[float]) -> float:
    """Give how much noisier an equal-weight stack is than a weighted one.

    Parameters
    ----------
    exposures : `Sequence` [`float`]
        The exposure time of every frame, in seconds.

    Returns
    -------
    penalty : `float`
        The factor by which equal weights raise the stack's noise, for
        sky-limited frames. 1.0 for equal exposures or no frames.
    """
    times = np.asarray([t for t in exposures if t and t > 0], dtype=float)
    if times.size == 0:
        return 1.0
    return float(np.sqrt(np.mean(1.0 / times) * np.mean(times)))


def choose_stack_weight(
    frame_exposures: Sequence[float], configured_weight: str | None, *, is_spectral: bool
) -> str | None:
    """Pick the weighting mode for a stack.

    A mode the user configured always wins, and spectral stacks are left to
    their own exposure-group handling.

    Parameters
    ----------
    frame_exposures : `Sequence` [`float`]
        The exposure time of every frame in the stack, in seconds.
    configured_weight : `str` or `None`
        The mode that was asked for, or `None` to choose.
    is_spectral : `bool`
        Whether this is a spectral stack.

    Returns
    -------
    weight : `str` or `None`
        The Siril ``-weight`` value to use, or `None` for equal weights.
    """
    if configured_weight or is_spectral:
        return configured_weight
    penalty = equal_weight_noise_penalty(frame_exposures)
    if penalty < MINIMUM_PENALTY_TO_WEIGHT:
        return None
    logger.info(
        "Frames of different exposure lengths: equal weights would add %.0f%% noise, "
        "so the stack is weighted by noise.",
        100 * (penalty - 1.0),
    )
    return NOISE_WEIGHT_MODE
