"""Line the group stacks up with Siril and describe what Siril did to each.

The Siril driver (`group_registration`) plate-solves every group stack and
moves the solved ones onto the grid of the reference stack. This module turns
what the driver returns into what the stacking pipeline works with, the same
three lists that `group_alignment.align_images_to_reference` gives for a
shift:

- the lined-up image of each group, or `None` for a group Siril could not
  solve;
- the mask of pixels that hold real data;
- an `AlignmentResult` that says how the group was moved and how good the
  fit was.

A group that Siril could not solve is not moved, and the caller decides what
to do with it. The decision does not use a star count of ours: the group is
unsolved exactly when Siril could not match its stars to the Gaia catalog. The
result then carries Siril's reason, the number of stars Siril found, and
nothing else, so the manifest and the job warnings can say what happened.

Siril writes zeros where the move brought in no data, and its Lanczos
resampling blends a few pixels at the border with those zeros. A stack can
also hold true zeros inside its data (a spectral stack clipped at zero holds
many), so the data mask is not "the nonzero pixels". It is the pixels whose
position in the group stack lies at least `RESAMPLING_EDGE_PIXELS` pixels
inside the stack's edge (`GroupPositionMap.covered_mask`).
"""

import logging

import numpy as np
from astropy.wcs.utils import proj_plane_pixel_scales

from astrometricslib._internal.drivers.siril.stacking.group_registration import (
    RegisteredGroup,
    register_group_stacks,
)
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.pipelines.stacking.processing.group_alignment import AlignmentResult
from astrometricslib.pipelines.stacking.processing.group_position_map import GroupPositionMap

logger = logging.getLogger(__name__)

__all__ = ["RESAMPLING_EDGE_PIXELS", "align_group_stacks_with_siril", "describe_registered_group"]

# The width, in pixels, of the border of a lined-up stack that is not counted
# as data. Siril's Lanczos kernel (``-interp=la``) reads 4 pixels on each side
# of the place it samples, so a pixel within 4 of the edge of the data mixes
# in the zeros beyond it.
RESAMPLING_EDGE_PIXELS = 4


def describe_registered_group(
    group: RegisteredGroup, is_reference: bool
) -> tuple[np.ndarray | None, np.ndarray | None, AlignmentResult]:
    """Turn what Siril made of one group into the pipeline's three results.

    Parameters
    ----------
    group : `RegisteredGroup`
        The group, as the driver returned it.
    is_reference : `bool`
        Whether this is the reference group.

    Returns
    -------
    aligned : `numpy.ndarray` or `None`
        The group stack on the common grid, or `None` when Siril did not
        solve it.
    covered : `numpy.ndarray` or `None`
        The mask of pixels that hold real data, or `None` with `aligned`.
    result : `AlignmentResult`
        The move and the fit. For an unsolved group it holds Siril's reason
        and no move.
    """
    unsolved = AlignmentResult(
        0.0,
        0.0,
        0.0,
        method="siril",
        stars_found=group.stars_found,
        matched_stars=group.matched_star_count,
        fit_residual_arcsec=group.fit_residual_rms_arcsec,
        fit_order=group.fit_order,
        is_reference=is_reference,
        failure_reason=group.failure_reason or "Siril did not solve the stack",
    )
    if not group.solved or group.aligned_image is None:
        return None, None, unsolved
    grid_wcs = celestial_wcs(group.aligned_header)
    group_wcs = celestial_wcs(group.solved_header)
    if grid_wcs is None or group_wcs is None:
        unsolved.failure_reason = "Siril's solution of the stack has no sky map"
        return None, None, unsolved
    grid_shape = (int(group.aligned_image.shape[-2]), int(group.aligned_image.shape[-1]))
    position_map = GroupPositionMap.from_sky_maps(grid_shape, grid_wcs, group_wcs)
    shift_rows, shift_columns = position_map.center_shift()
    rotation_degrees, scale = position_map.turn_and_scale()
    pixel_scale_arcsec = float(np.mean(proj_plane_pixel_scales(group_wcs))) * 3600.0
    fit_residual_pixels = (
        None
        if group.fit_residual_rms_arcsec is None or pixel_scale_arcsec <= 0
        else group.fit_residual_rms_arcsec / pixel_scale_arcsec
    )
    result = AlignmentResult(
        shift_rows,
        shift_columns,
        0.0,
        method="siril",
        position_map=position_map,
        rotation_degrees=rotation_degrees,
        scale=scale,
        stars_found=group.stars_found,
        matched_stars=group.matched_star_count,
        fit_residual_arcsec=group.fit_residual_rms_arcsec,
        fit_residual_pixels=fit_residual_pixels,
        fit_order=group.fit_order,
        is_reference=is_reference,
    )
    return group.aligned_image, position_map.covered_mask(RESAMPLING_EDGE_PIXELS), result


def align_group_stacks_with_siril(
    stack_paths: list[str], reference_index: int, siril_executable: str
) -> tuple[list[np.ndarray | None], list[np.ndarray | None], list[AlignmentResult]]:
    """Line up the group stacks with Siril.

    Parameters
    ----------
    stack_paths : `list` [`str`]
        The group stacks' FITS files, which are only read.
    reference_index : `int`
        The place of the stack the others are lined up with.
    siril_executable : `str`
        The command that starts Siril.

    Returns
    -------
    aligned : `list` [`numpy.ndarray` or `None`]
        Each stack on the common grid (the reference's too: Siril undistorts
        it with the others), or `None` for a stack Siril could not solve, or
        for every stack when the reference could not be solved.
    covered : `list` [`numpy.ndarray` or `None`]
        The mask of pixels that hold real data, `None` where `aligned` is.
    results : `list` [`AlignmentResult`]
        One for each stack: the move and the fit, or Siril's reason for a
        stack it did not solve.
    """
    groups = register_group_stacks(stack_paths, reference_index, siril_executable)
    aligned: list[np.ndarray | None] = []
    covered: list[np.ndarray | None] = []
    results: list[AlignmentResult] = []
    for index, group in enumerate(groups):
        image, mask, result = describe_registered_group(group, index == reference_index)
        aligned.append(image)
        covered.append(mask)
        results.append(result)
    return aligned, covered, results
