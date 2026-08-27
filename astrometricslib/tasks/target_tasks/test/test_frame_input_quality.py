"""Tests for measuring raw-frame quality before anything is stacked.

Per-frame quality numbers were previously written only during
registration, so a frame that had never been stacked carried no evidence
at all -- exactly the frames worth triaging. On the 2026-08-23 catalog
only 238 of 4,244 frames (5.6%) had any.
"""

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.image_processing.quality_metrics import measure_frame_input_quality
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.tasks.target_tasks import statistics_operations


def _write_frame(path, background=500.0, saturated_pixels=0, shape=(64, 64)):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a synthetic FITS frame with a known background.

    Returns
    -------
    path : `str`
        The path just written, as a string.
    """
    data = np.full(shape, background, dtype=np.float32)
    if saturated_pixels:
        flat = data.reshape(-1)
        flat[:saturated_pixels] = 70000.0
    fits.PrimaryHDU(data).writeto(path, overwrite=True)
    return str(path)


def _target_with_frames(paths, camera="ZWO ASI 533MM Pro"):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a Target whose frames point at the given paths.

    Returns
    -------
    target : `Target`
        Target carrying one LIGHT FrameRecord per path.
    """
    frames = [
        FrameRecord(path=path, filter="Luminance", role="LIGHT", camera=camera, exposure="30.0")
        for path in paths
    ]
    return Target(id="InputQualityTestTarget", frames=frames)


def test_measures_background_from_a_real_frame(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The measured background matches the frame's actual level."""
    path = _write_frame(tmp_path / "frame.fits", background=1234.0)

    metrics = measure_frame_input_quality(path)

    assert metrics["background_level"] == pytest.approx(1234.0)
    assert metrics["saturated_pixel_fraction"] == pytest.approx(0.0)


def test_detects_saturated_pixels(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Clipped pixels are reported as a fraction of the frame."""
    path = _write_frame(tmp_path / "frame.fits", background=500.0, saturated_pixels=64)

    metrics = measure_frame_input_quality(path)

    assert metrics["saturated_pixel_fraction"] == pytest.approx(64 / (64 * 64))


def test_fwhm_is_off_by_default(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """FWHM costs ~50x the other metrics, so it must be opt-in."""
    path = _write_frame(tmp_path / "frame.fits")

    assert measure_frame_input_quality(path)["fwhm_px"] is None


def test_unreadable_frame_yields_all_none(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """One bad frame must not abort a sweep across a whole target."""
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a FITS file at all")

    metrics = measure_frame_input_quality(str(broken))

    assert metrics == {
        "background_level": None,
        "saturated_pixel_fraction": None,
        "fwhm_px": None,
    }


def test_missing_file_yields_all_none(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A frame record pointing at a deleted file measures as unknown."""
    metrics = measure_frame_input_quality(str(tmp_path / "does_not_exist.fits"))

    assert metrics["background_level"] is None


def test_task_populates_every_frame(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Each frame record gains its own measured values."""
    paths = [
        _write_frame(tmp_path / "a.fits", background=100.0),
        _write_frame(tmp_path / "b.fits", background=900.0),
    ]
    target = _target_with_frames(paths)

    counts = statistics_operations.measure_frame_input_quality(target)

    assert counts == {"measured": 2, "skipped": 0, "failed": 0}
    assert target.frames[0].background_level == pytest.approx(100.0)
    assert target.frames[1].background_level == pytest.approx(900.0)


def test_task_is_incremental_by_default(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """An interrupted sweep resumes rather than re-measuring everything."""
    target = _target_with_frames([_write_frame(tmp_path / "a.fits")])
    statistics_operations.measure_frame_input_quality(target)

    counts = statistics_operations.measure_frame_input_quality(target)

    assert counts == {"measured": 0, "skipped": 1, "failed": 0}


def test_remeasure_overrides_the_incremental_skip(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Passing remeasure=True re-reads frames that already have values."""
    target = _target_with_frames([_write_frame(tmp_path / "a.fits")])
    statistics_operations.measure_frame_input_quality(target)

    counts = statistics_operations.measure_frame_input_quality(target, remeasure=True)

    assert counts == {"measured": 1, "skipped": 0, "failed": 0}


def test_unreadable_frames_are_counted_not_raised(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A broken frame is reported as failed while the rest still measure."""
    good = _write_frame(tmp_path / "good.fits")
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"garbage")
    target = _target_with_frames([good, str(broken)])

    counts = statistics_operations.measure_frame_input_quality(target)

    assert counts == {"measured": 1, "skipped": 0, "failed": 1}
    assert target.frames[0].background_level is not None
    assert target.frames[1].background_level is None


def test_camera_filter_restricts_measurement(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A camera-scoped sweep leaves other cameras' frames untouched."""
    mono = _write_frame(tmp_path / "mono.fits")
    dslr = _write_frame(tmp_path / "dslr.fits")
    target = Target(
        id="MixedCameraTarget",
        frames=[
            FrameRecord(path=mono, role="LIGHT", camera="ZWO ASI 533MM Pro", exposure="30.0"),
            FrameRecord(path=dslr, role="LIGHT", camera="Nikon DSLR DSC D5300", exposure="30.0"),
        ],
    )

    counts = statistics_operations.measure_frame_input_quality(target, camera_name="Nikon")

    assert counts["measured"] == 1
    assert target.frames[0].background_level is None
    assert target.frames[1].background_level is not None


def test_measured_fwhm_is_kept_apart_from_registration_fwhm(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The two FWHM sources must never be mixed in one field.

    photutils measures ~1.53x Siril's PSF fit on identical frames, so a
    value written into registration_fwhm_x/y_px would look like a ~50%
    seeing change caused purely by which stage did the measuring.
    """
    target = _target_with_frames([_write_frame(tmp_path / "a.fits")])
    target.frames[0].registration_fwhm_x_px = 3.2
    target.frames[0].registration_fwhm_y_px = 3.4

    statistics_operations.measure_frame_input_quality(target, include_fwhm=True)

    assert target.frames[0].registration_fwhm_x_px == pytest.approx(3.2)
    assert target.frames[0].registration_fwhm_y_px == pytest.approx(3.4)
