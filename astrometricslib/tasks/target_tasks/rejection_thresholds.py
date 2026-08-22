"""Adaptive stack-rejection sigma via Chauvenet's criterion.

Chauvenet's criterion gives a rejection sigma that scales with sample
size. With more frames in a stack, an extreme pixel value is more
likely to occur by chance alone across all those samples, so a single
fixed sigma either over-rejects at high frame counts or is practically
inert at low frame counts. This computes the frame-count-adjusted
sigma so rejection stays statistically meaningful across very
different session sizes, rather than relying on one fixed constant for
every stack.
"""

import math

from scipy.special import erfcinv


def chauvenet_sigma(n_frames: int) -> float:
    r"""Return the Chauvenet-criterion sigma threshold for a stack.

    A pixel value is rejected under Chauvenet's criterion when the
    probability of seeing a deviation at least that extreme, among
    n_frames samples drawn from a Gaussian, is less than
    :math:`\frac{1}{2N}`. Solving that tail-probability equation for a
    two-tailed Gaussian gives:

    .. math::
       \sigma = \sqrt{2} \cdot \text{erfcinv}\left(\frac{1}{2N}\right)

    Parameters
    ----------
    n_frames : `int`
        The number of frames in the stack being rejected against.

    Returns
    -------
    sigma : `float`
        The Chauvenet-criterion sigma threshold for this stack size.

    Raises
    ------
    ValueError
        Raised if n_frames is less than 1.

    Notes
    -----
    Cross-checked against rejection_threshold_analysis.py sweeps on
    M 81 (45 frames), M 13 L-filter (40 frames, 2026-05-23), and
    NGC 2403 (70 frames, 2026-02-22 -- includes a cloud-affected
    session with a 5x sky-background step-change partway through).
    This formula recommends sigma of about 2.50 (n=40) to 2.69 (n=70)
    for that range, versus the fixed sigma=3.0 those scripts defaulted
    to. In all three sweeps, stacked-image FWHM was indistinguishable
    between sigma=2.5 and sigma=3.0 (well under 1% difference), while
    rejected-fraction rose from ~1% to ~2.5-2.8% at the lower sigma --
    so the lower, frame-count-derived threshold costs no measurable
    sharpness, it just rejects a more statistically-justified fraction
    of pixels. See logs/rejection_threshold_analysis_*.json for the
    raw sweep data.
    """
    if n_frames < 1:
        raise ValueError(f"n_frames must be at least 1, got {n_frames}")

    # Calculate the tail probability threshold for rejection (1 / 2N)
    # Then use the inverse complementary error function (erfcinv) to
    # map that probability to the corresponding sigma threshold for a
    # Gaussian distribution
    return math.sqrt(2) * float(erfcinv(1.0 / (2 * n_frames)))
