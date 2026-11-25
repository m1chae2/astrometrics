"""Joining the per-group Siril files into one file each.

Siril writes a rejection map and a registration sequence for every
exposure group. The stack record needs one of each for the whole stack,
so these functions read the per-group files and write the combined ones.

The overview of the whole method is in the `exposure_groups` module
docstring.
"""

import os
import re
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.fits_access import read_data, write_image

# A per-frame registration line in Siril's ``.seq`` file starts with R and the
# frame's number (the same pattern `parse_seq_file` reads).
_REGISTRATION_LINE_PATTERN = re.compile(r"^R\d+ ")

# An image line of a Siril sequence file ("I <number> <included> ...").
_IMAGE_LINE_PATTERN = re.compile(r"^I\s+\d+\s+[01](?:\s|$)")


def merge_rejection_maps(
    map_paths: list[str],
    frame_counts: list[int],
    output_path: str,
    header: Any | None = None,
    alignments: list[Any | None] | None = None,
) -> bool:
    """Combine each group's rejection map into one map.

    A rejection map holds, for each pixel, the fraction of that pixel's
    values that the stacker threw away. The combined map is the average of
    the groups' maps weighted by how many frames each group stacked, which
    is the fraction thrown away overall.

    Parameters
    ----------
    map_paths : `list` [`str`]
        The rejection map file of each group. Groups without one are
        skipped.
    frame_counts : `list` [`int`]
        The number of frames each group stacked, in the same order.
    output_path : `str`
        Where to write the combined map.
    header : `astropy.io.fits.Header`, optional
        A FITS header to give the combined map.
    alignments : `list` [`AlignmentResult` or `None`], optional
        For each group, the move (shift, rotation and scale) that lined its
        stack up with the reference group. The group's map gets the same
        move, so the maps line up with the combined image. A group turned by
        a half turn (a meridian flip between sessions) has its map turned
        too. `None` for a group that was not moved. The map is resampled
        linearly (spline order 1): a cubic spline would overshoot beside a
        sharp edge and give fractions below 0 or above 1, and a nearest-pixel
        copy would make the map's edges jagged. Where the move brings in no
        data the map is 0, as before.

    Returns
    -------
    written : `bool`
        `True` when a map was written, `False` if no group had one.
    """
    from astrometricslib.pipelines.stacking.processing.group_alignment import apply_alignment

    total = None
    weight_sum = 0.0
    for position, (path, count) in enumerate(zip(map_paths, frame_counts, strict=True)):
        if not path or not os.path.exists(path):
            continue
        data = np.asarray(read_data(path), dtype=np.float64)
        alignment = alignments[position] if alignments is not None else None
        if alignment is not None:
            data = apply_alignment(data, alignment, order=1)[0].astype(np.float64)
        total = data * count if total is None else total + data * count
        weight_sum += count
    if total is None or weight_sum == 0:
        return False
    write_image(output_path, (total / weight_sum).astype(np.float32), header)
    return True


def merge_registration_sequences(sequence_paths: list[str], output_path: str) -> bool:
    """Join the groups' registration sequence files into one.

    Only the per-frame registration lines (``R``) and the image lines (``I``,
    which say whether Siril kept the image) are kept, in group order. Those are
    the lines the stack quality checks read (see `parse_seq_file` and
    `parse_seq_inclusion`).

    Parameters
    ----------
    sequence_paths : `list` [`str`]
        Each group's registration ``.seq`` file, in group order.
    output_path : `str`
        Where to write the joined file.

    Returns
    -------
    written : `bool`
        `True` when at least one registration line was written.
    """
    lines = []
    for path in sequence_paths:
        if not path or not os.path.exists(path):
            continue
        with open(path) as sequence_file:
            lines.extend(
                line
                for line in sequence_file
                if _REGISTRATION_LINE_PATTERN.match(line) or _IMAGE_LINE_PATTERN.match(line)
            )
    if not lines:
        return False
    with open(output_path, "w") as output_file:
        output_file.writelines(lines)
    return True
