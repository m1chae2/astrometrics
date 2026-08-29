"""Adjusting the pixel rejection threshold based on how many images there are.

When combining (stacking) many images, the chance of seeing random noise
goes up. If a fixed limit is used for throwing out bad pixels, it might
throw out too few pixels for small stacks, and too many for large stacks.
This file calculates a sliding limit based on the number of images, keeping
the pixel rejection balanced.
"""

import math

from scipy.special import erfcinv


def chauvenet_sigma(n_frames: int) -> float:
    """Calculate the pixel rejection limit for a certain number of images.

    This uses a statistical rule called Chauvenet's criterion. It figures
    out the maximum difference from the average that should be allowed before
    deciding a pixel is bad (like a cosmic ray or hot pixel).

    Parameters
    ----------
    n_frames : `int`
        The number of images being stacked.

    Returns
    -------
    sigma : `float`
        The cutoff point (in standard deviations) for throwing out a pixel.

    Raises
    ------
    ValueError
        If the number of frames is less than 1.
    """
    if n_frames < 1:
        raise ValueError(f"n_frames must be at least 1, got {n_frames}")

    # Calculate the tail probability threshold for rejection (1 / 2N)
    # Then map that probability to the corresponding standard deviation
    # threshold for a normal distribution.
    return math.sqrt(2) * float(erfcinv(1.0 / (2 * n_frames)))
