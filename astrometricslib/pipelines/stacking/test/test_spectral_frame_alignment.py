"""Purpose: Unit tests for aligning raw spectral frames without star detection.

Description: Builds small synthetic "calibrated frame" FITS files with a
known per-frame shift, writes them to disk the way Siril's own `calibrate`
step would, and checks that `align_calibrated_frames` recovers the shift,
writes a contiguously-numbered aligned sequence, and leaves out a frame
that cannot be confidently aligned instead of silently misaligning it.
"""

import numpy as np
from astropy.io import fits

from astrometricslib.pipelines.stacking.group_alignment import apply_shift
from astrometricslib.pipelines.stacking.spectral_frame_alignment import (
    align_calibrated_frames,
    find_calibrated_frame_paths,
)


def _make_field(seed: int, size: int = 200) -> np.ndarray:
    """Build a noisy field with a couple of bright compact sources.

    Returns
    -------
    field : `numpy.ndarray`
        A ``size`` x ``size`` float32 image, standing in for one calibrated
        raw spectral frame (a couple of star-like blobs, not a full trail --
        the alignment code only cares about compact structure to correlate
        on, the same as it would find at a star's own zero order).
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    image = rng.normal(50.0, 2.0, (size, size)).astype(np.float32)
    for cx, cy, amplitude in [(80, 90, 4000.0), (95, 78, 800.0)]:
        image += amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 2.0**2))
    return image


def _write_frame(path: object, data: np.ndarray, exptime: float = 0.2) -> None:
    """Write one synthetic calibrated frame FITS file."""
    header = fits.Header()
    header["EXPTIME"] = exptime
    fits.PrimaryHDU(data=data, header=header).writeto(path)


def test_find_calibrated_frame_paths_matches_sirils_pp_prefix_naming(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify only one sequence's pp_-prefixed calibrated frames are found."""
    process_directory = tmp_path / "process"
    process_directory.mkdir()
    _write_frame(process_directory / "pp_light_source_00001.fits", _make_field(1))
    _write_frame(process_directory / "pp_light_source_00002.fits", _make_field(2))
    # A different sequence's calibrated frames, and an uncalibrated raw
    # frame, must not be picked up.
    _write_frame(process_directory / "pp_other_source_00001.fits", _make_field(3))
    _write_frame(process_directory / "light_source_00001.fits", _make_field(4))

    paths = find_calibrated_frame_paths(str(process_directory), "pp_light_source")

    assert [p.split("/")[-1] for p in paths] == [
        "pp_light_source_00001.fits",
        "pp_light_source_00002.fits",
    ]


def test_align_calibrated_frames_recovers_a_known_shift_and_writes_a_sequence(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a shifted frame is moved back onto the reference frame's grid."""
    process_directory = tmp_path / "process"
    process_directory.mkdir()
    reference = _make_field(1)
    shifted, _ = apply_shift(reference, 2.0, -1.5)

    path_a = process_directory / "pp_light_source_00001.fits"
    path_b = process_directory / "pp_light_source_00002.fits"
    _write_frame(path_a, reference)
    _write_frame(path_b, shifted)

    output_directory = tmp_path / "aligned"
    aligned_paths, counts = align_calibrated_frames([str(path_a), str(path_b)], str(output_directory))

    assert counts == {"registered": 2, "failed": 0}
    assert len(aligned_paths) == 2
    assert [p.split("/")[-1] for p in aligned_paths] == [
        "raw_aligned_00001.fits",
        "raw_aligned_00002.fits",
    ]

    from astrometricslib.drivers.fits_access import read_data

    realigned_b = np.asarray(read_data(aligned_paths[1]))
    # Undoing a real shift and resampling loses a little precision at the
    # edges and to interpolation, so this compares the two frames' bright
    # cores rather than requiring an exact pixel match.
    core = slice(85, 95), slice(75, 85)
    assert np.corrcoef(realigned_b[core].ravel(), reference[core].ravel())[0, 1] > 0.95


def test_align_calibrated_frames_leaves_out_a_frame_that_does_not_match(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unrelated frame is left out, not forced into the stack."""
    process_directory = tmp_path / "process"
    process_directory.mkdir()
    path_a = process_directory / "pp_light_source_00001.fits"
    path_b = process_directory / "pp_light_source_00002.fits"
    _write_frame(path_a, _make_field(1))
    # An unrelated noise field: no shift lines this up with the reference.
    _write_frame(path_b, np.random.default_rng(99).normal(50.0, 2.0, (200, 200)).astype(np.float32))

    output_directory = tmp_path / "aligned"
    aligned_paths, counts = align_calibrated_frames([str(path_a), str(path_b)], str(output_directory))

    assert counts == {"registered": 1, "failed": 1}
    assert len(aligned_paths) == 1


def test_align_calibrated_frames_handles_an_empty_list(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no frames in means no error and an empty result out."""
    aligned_paths, counts = align_calibrated_frames([], str(tmp_path / "aligned"))
    assert aligned_paths == []
    assert counts == {"registered": 0, "failed": 0}
