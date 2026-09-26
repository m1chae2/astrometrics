"""Line up a batch of calibrated spectral frames without star detection.

Siril's own frame-to-frame registration works by matching point-like stars
between frames. That assumption breaks down for a slitless spectrograph
image: every star is smeared into a trail rather than a compact dot, and a
field with only one or two bright stars (a close visual double, for
instance -- see `logs/stack_exposure_groups_20260920.json`'s Albireo entry)
does not give Siril's star finder enough real point sources to match
reliably. Measured on that real Albireo session, both of Siril's own
detection settings left most of the frames in some exposure groups
unregistered, and one group failed outright.

This measures each frame's own shift against a reference frame straight
from the pixel data instead, reusing the phase-correlation approach
`group_alignment` already uses to line up one exposure group's *stack*
against another's -- the same idea, applied one raw calibrated frame at a
time instead of once per group.
"""

import glob
import logging
import os

import numpy as np

from astrometricslib.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.pipelines.stacking.group_alignment import align_images_to_reference

logger = logging.getLogger(__name__)

# The basename this module writes its own aligned copies under:
# `<basename>_00001.fits`, `<basename>_00002.fits`, and so on. Deliberately
# not the same name as the Siril sequence built from them afterward
# (`ALIGNED_SIRIL_SEQUENCE_NAME`): Siril's own `convert` picks up every
# image file already sitting in its target directory and relinks it under
# its own numbering, and pointed at a directory where the sequence name
# and the files already share one basename, its output paths collide with
# its own input paths mid-conversion, corrupting the sequence it is
# building. Two different names keep the raw files convert reads
# distinct from the sequence it writes.
ALIGNED_FRAME_BASENAME = "raw_aligned"

# The name of the Siril sequence built by running `convert` over the
# `ALIGNED_FRAME_BASENAME`-named files in their own directory.
ALIGNED_SIRIL_SEQUENCE_NAME = "aligned_light"


def find_calibrated_frame_paths(process_directory: str, sequence_name: str) -> list[str]:
    """List Siril's own converted/calibrated frames for one sequence.

    Parameters
    ----------
    process_directory : `str`
        Siril's scratch ``process`` folder for this stacking run.
    sequence_name : `str`
        The sequence's exact name as Siril wrote it, e.g.
        ``"pp_light_source"`` for calibrated frames named
        ``pp_light_source_00001.fits``, or plain ``"light_source"`` when
        there were no calibration masters to apply.

    Returns
    -------
    paths : `list` [`str`]
        The frames, sorted by their frame number.
    """
    pattern = os.path.join(process_directory, f"{sequence_name}_*.fits")
    return sorted(glob.glob(pattern))


def _reference_frame_index(images: list[np.ndarray]) -> int:
    """Pick the frame with the most real signal to align every other frame to.

    A frame that happens to be cloud-covered or otherwise blank gives
    phase correlation nothing real to lock onto, so the frame with the
    most light above its own sky level is used rather than always
    trusting the first frame in the sequence.

    Returns
    -------
    index : `int`
        The index, into `images`, of the frame to align everything else to.
    """
    signal_totals = []
    for image in images:
        data = np.asarray(image, dtype=np.float64)
        sky = float(np.median(data))
        signal_totals.append(float(np.sum(np.clip(data - sky, 0.0, None))))
    return int(np.argmax(signal_totals))


def align_calibrated_frames(
    calibrated_frame_paths: list[str], output_directory: str
) -> tuple[list[str], dict[str, int]]:
    """Align a batch of calibrated frames to the frame with the most signal.

    Parameters
    ----------
    calibrated_frame_paths : `list` [`str`]
        The calibrated frames to align, from `find_calibrated_frame_paths`.
    output_directory : `str`
        Where the aligned copies are written, created if it does not
        already exist. Numbered contiguously from 1, the way Siril's own
        ``convert`` names a sequence it builds itself, so this folder can
        be handed straight back to Siril to stack.

    Returns
    -------
    aligned_paths : `list` [`str`]
        The frames that aligned with enough confidence to trust.
    counts : `dict` [`str`, `int`]
        ``"registered"`` and ``"failed"`` frame counts, in the same shape
        `siril_stacking` already reads from Siril's own registration
        output, so the two sources are interchangeable to its retry logic.
    """
    if not calibrated_frame_paths:
        return [], {"registered": 0, "failed": 0}

    images = [np.asarray(read_data(path)) for path in calibrated_frame_paths]
    reference_index = _reference_frame_index(images)
    aligned_images, _covered_masks, alignments = align_images_to_reference(images, reference_index)

    os.makedirs(output_directory, exist_ok=True)
    aligned_paths = []
    for path, image, alignment in zip(calibrated_frame_paths, aligned_images, alignments, strict=True):
        if image is None:
            logger.warning(
                "Could not confidently align %s to the group's reference frame (correlation %.2f); "
                "leaving it out of the stack.",
                os.path.basename(path),
                alignment.correlation if alignment else 0.0,
            )
            continue
        out_path = os.path.join(
            output_directory, f"{ALIGNED_FRAME_BASENAME}_{len(aligned_paths) + 1:05d}.fits"
        )
        write_image(out_path, image, header=read_header(path))
        aligned_paths.append(out_path)

    failed_count = len(calibrated_frame_paths) - len(aligned_paths)
    return aligned_paths, {"registered": len(aligned_paths), "failed": failed_count}
