"""Matching the sky background of one exposure group to the reference group.

Two group stacks of one field never share a background. Each stack carries
the sky of its own night, and Siril adds its own offset when it scales a
stack. When the groups are lined up and averaged, a region seen by only one
group takes that group's background, and a region seen by both takes a
weighted mix. Wherever the set of groups changes (the edge of a shifted
group), the background steps. A single sky number for the whole group
cannot remove the step on a color image, because the sky differs from one
color channel to the next and from one side of the frame to the other (a
gradient from the moon or from city light that changed between the nights).

This module measures the difference between a group and the reference group
over the pixels both hold. It fits one flat tilted surface (a plane) to that
difference for each color channel, so the group can be lowered onto the
reference background before the average. The fit uses the median of blocks
of pixels, and drops the blocks that stars or the target disturb, so stars
and nebulae that both groups hold do not bend the plane.

The overview of the whole method is in the `exposure_groups` module
docstring, and the caller is `group_combination`.
"""

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.binned_sky_plane import (
    block_medians,
    fit_clipped_plane,
    whole_block_edges,
)
from astrometricslib.pipelines.stacking.processing.group_gains import SKY_CLIP_PASSES, SKY_CLIP_SIGMAS

# The difference between two sky backgrounds is a smooth surface that changes
# over thousands of pixels, so it is read from the median of square blocks
# rather than from single pixels. A block of 64 by 64 pixels holds 4096
# pixels, which makes its median far quieter than one pixel, and a 4000 by
# 6000 pixel stack gives several thousand blocks to fit. Measured on NGC 6992
# (Nikon D5300, a 180 s and a 300 s group): the plane left the difference
# within about 1 percent of the sky outside the nebula.
SKY_MATCH_BLOCK_PIXELS = TunedParameter(
    name="stacking.sky_match_block_pixels",
    default=64,
    unit="pixels",
    description="Side of the square blocks whose medians are fitted when matching a group's sky background.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D10",
)

# A block counts only when at least this share of its pixels hold trusted
# data in both groups. Blocks cut by the edge of a shifted group, or by a
# saturated star, would otherwise be measured from a few pixels and from
# one side only. Design estimate.
SKY_MATCH_BLOCK_VALID_FRACTION = TunedParameter(
    name="stacking.sky_match_block_valid_fraction",
    default=0.9,
    unit="fraction",
    description="Share of a block that must be trusted in both groups to use the block.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D10",
)

# A plane has three numbers, and the clipped fit needs many more blocks than
# that to tell a tilt from the noise. Fewer blocks than this means the two
# groups barely overlap, and no plane is fitted. Design estimate.
SKY_MATCH_MINIMUM_BLOCKS = TunedParameter(
    name="stacking.sky_match_minimum_blocks",
    default=12,
    unit="blocks",
    description="Fewest usable blocks with which a sky plane is fitted between two groups.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D10",
)


def fit_sky_plane(difference: np.ndarray, valid: np.ndarray) -> np.ndarray | None:
    """Fit a plane to the sky difference between two groups.

    The plane is fitted to block medians (see `SKY_MATCH_BLOCK_PIXELS`),
    dropping the blocks more than `SKY_CLIP_SIGMAS` spreads from the fit on
    either side in up to `SKY_CLIP_PASSES` passes, so stars and the target
    in either group do not bend it (`binned_sky_plane.fit_clipped_plane`).

    Parameters
    ----------
    difference : `numpy.ndarray`
        A 2-D image: the group minus the reference group, in the same units.
    valid : `numpy.ndarray`
        A 2-D boolean mask, `True` where both groups hold trusted data.

    Returns
    -------
    plane : `numpy.ndarray` or `None`
        The three numbers (level at the image center, slope along the rows,
        slope along the columns), or `None` when fewer than
        `SKY_MATCH_MINIMUM_BLOCKS` blocks could be used.
    """
    difference = np.asarray(difference, dtype=np.float64)
    block = int(resolve(SKY_MATCH_BLOCK_PIXELS).value)
    row_edges = whole_block_edges(difference.shape[0], block)
    column_edges = whole_block_edges(difference.shape[1], block)
    medians = block_medians(
        difference,
        row_edges,
        column_edges,
        valid=np.asarray(valid, dtype=bool),
        minimum_valid_fraction=float(resolve(SKY_MATCH_BLOCK_VALID_FRACTION).value),
    )
    used = np.isfinite(medians)
    minimum_blocks = int(resolve(SKY_MATCH_MINIMUM_BLOCKS).value)
    if int(used.sum()) < minimum_blocks:
        return None
    block_rows, block_columns = np.nonzero(used)
    center_rows = (row_edges[:-1] + row_edges[1:]) / 2.0
    center_columns = (column_edges[:-1] + column_edges[1:]) / 2.0
    plane, _kept = fit_clipped_plane(
        center_rows[block_rows] - difference.shape[0] / 2.0,
        center_columns[block_columns] - difference.shape[1] / 2.0,
        medians[used],
        side="both",
        clip_sigmas=float(resolve(SKY_CLIP_SIGMAS).value),
        rounds=int(resolve(SKY_CLIP_PASSES).value),
        minimum_kept=minimum_blocks,
    )
    return plane


def evaluate_sky_plane(plane: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Draw a fitted sky plane as an image.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The three numbers from `fit_sky_plane`.
    shape : `tuple` [`int`, `int`]
        The (rows, columns) of the image.

    Returns
    -------
    surface : `numpy.ndarray`
        The plane at every pixel (`float64`).
    """
    rows = np.arange(shape[0], dtype=np.float64) - shape[0] / 2.0
    columns = np.arange(shape[1], dtype=np.float64) - shape[1] / 2.0
    return plane[0] + plane[1] * rows[:, None] + plane[2] * columns[None, :]


def match_sky_to_reference(
    image: np.ndarray, reference: np.ndarray, valid: np.ndarray
) -> tuple[np.ndarray, bool]:
    """Lower a group's sky background onto the reference group's.

    Each color channel gets its own plane. A channel whose plane cannot be
    fitted is returned as it came.

    Parameters
    ----------
    image : `numpy.ndarray`
        The group's image (2-D, or color with the channel first) after its
        brightness scale and its overall sky level were matched to the
        reference.
    reference : `numpy.ndarray`
        The reference group's image, same shape and units.
    valid : `numpy.ndarray`
        A boolean mask of the same shape, `True` where both groups hold
        trusted data.

    Returns
    -------
    matched : `numpy.ndarray`
        The image with the fitted planes taken off.
    fitted : `bool`
        `True` when a plane was fitted for every channel.
    """
    channels_image = image[None] if image.ndim == 2 else image
    channels_reference = reference[None] if reference.ndim == 2 else reference
    channels_valid = valid[None] if valid.ndim == 2 else valid
    matched = np.array(channels_image, dtype=np.float64)
    fitted = True
    for channel in range(matched.shape[0]):
        plane = fit_sky_plane(matched[channel] - channels_reference[channel], channels_valid[channel])
        if plane is None:
            fitted = False
            continue
        matched[channel] -= evaluate_sky_plane(plane, matched[channel].shape)
    return (matched[0] if image.ndim == 2 else matched), fitted
