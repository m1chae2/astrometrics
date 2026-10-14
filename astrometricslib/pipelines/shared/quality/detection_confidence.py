"""A shared way to turn a detection's signal-to-noise into a confidence score.

Several pipelines flag something -- a named spectral feature, a
brightness dip -- by comparing its strength against the surrounding
noise. This turns that significance into one 0-1 confidence number that
saturates as the signal grows, so each caller doesn't invent its own
scaling. It's a heuristic curve chosen to look reasonable, not a
calibrated statistical detection probability.
"""

import numpy as np


def significance_to_confidence(significance: float, scale: float = 2.0) -> float:
    """Turn a signal-to-noise-style significance into a 0-1 confidence.

    Saturates smoothly as significance grows: 0 gives 0.0, `scale`
    gives about 0.63, twice `scale` gives about 0.86, and so on toward
    1.0. `significance` can be `float("inf")` for a noise-free signal.

    Parameters
    ----------
    significance : `float`
        A detection's strength relative to noise (e.g. a dip's depth
        divided by the local noise, or a signal-to-noise ratio). Zero
        or negative means no real signal.
    scale : `float`, optional
        The significance at which confidence reaches about 63%.
        Default 2.0.

    Returns
    -------
    confidence : `float`
        A value from 0.0 to 1.0.
    """
    if significance <= 0 or scale <= 0:
        return 0.0
    return float(min(max(1.0 - np.exp(-significance / scale), 0.0), 1.0))
