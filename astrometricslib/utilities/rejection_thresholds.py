"""Adjusting the pixel rejection threshold based on how many images there are.

When combining (stacking) many images, the chance of seeing random noise
goes up. If a fixed limit is used for throwing out bad pixels, it might
throw out too few pixels for small stacks, and too many for large stacks.
This file calculates a sliding limit based on the number of images, keeping
the pixel rejection balanced.

The sliding limit has two safeguards for small stacks. A floor keeps the
limit from dropping below a minimum, because with only a handful of values
per pixel the spread (standard deviation) is itself a noisy estimate. The
low bound sits slightly looser than the high bound, because bad pixels
(satellite trails, cosmic ray hits, hot pixels) are brighter than their
neighbors, not darker.
"""

import math
from typing import NamedTuple

from scipy.special import erfcinv

from astrometricslib.foundation.errors import InvalidArgumentError


class RejectionBounds(NamedTuple):
    """The rejection limits for a stack, and whether the floor set them.

    Attributes
    ----------
    low : `float`
        How many standard deviations below the middle value a pixel value may
        fall before the stacker throws it out.
    high : `float`
        How many standard deviations above the middle value a pixel value may
        rise before the stacker throws it out.
    floor_applied : `bool`
        True when the Chauvenet limit for this many frames was below the floor,
        so the floor set the limits instead.
    """

    low: float
    high: float
    floor_applied: bool


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
    InvalidArgumentError
        If the number of frames is less than 1.
    """
    if n_frames < 1:
        raise InvalidArgumentError(f"n_frames must be at least 1, got {n_frames}")

    # Calculate the tail probability threshold for rejection (1 / 2N)
    # Then map that probability to the corresponding standard deviation
    # threshold for a normal distribution.
    return math.sqrt(2) * float(erfcinv(1.0 / (2 * n_frames)))


def rejection_bounds(n_frames: int, floor: float = 2.5, low_extra: float = 0.5) -> RejectionBounds:
    """Calculate the low and high rejection limits for a stack size.

    The high limit is the Chauvenet limit, raised to the floor when the
    Chauvenet limit is smaller. The low limit is the high limit plus
    ``low_extra``. Small stacks need the floor because the Chauvenet limit
    assumes the spread of the values is known exactly. With five values the
    spread is a rough estimate, so the plain Chauvenet limit (1.64 standard
    deviations for five images) throws out good values. The low limit is
    looser because the bad values in astronomical images (satellite trails,
    cosmic ray hits, hot pixels) are brighter than the true value. Dark
    outliers are rare.

    Parameters
    ----------
    n_frames : `int`
        The number of images being stacked.
    floor : `float`, optional
        The smallest high limit allowed, in standard deviations. Must be above
        zero.
    low_extra : `float`, optional
        How many standard deviations to add to the high limit to get the low
        limit. Must not be negative.

    Returns
    -------
    bounds : `RejectionBounds`
        The ``low`` and ``high`` limits in standard deviations, and
        ``floor_applied``, which is true when the floor set the limits.

    Raises
    ------
    InvalidArgumentError
        If the number of frames is less than 1, the floor is not above zero,
        or ``low_extra`` is negative.
    """
    if floor <= 0:
        raise InvalidArgumentError(f"floor must be above zero, got {floor}")
    if low_extra < 0:
        raise InvalidArgumentError(f"low_extra must not be negative, got {low_extra}")

    adaptive_sigma = chauvenet_sigma(n_frames)
    floor_applied = adaptive_sigma < floor
    high = floor if floor_applied else adaptive_sigma
    return RejectionBounds(low=high + low_extra, high=high, floor_applied=floor_applied)
