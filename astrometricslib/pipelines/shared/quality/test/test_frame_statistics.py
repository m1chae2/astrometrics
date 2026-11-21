"""Tests for measuring raw-frame quality before anything is stacked.

Per-frame quality numbers were previously calculated only during the stacking
process, meaning we had no quality data for frames that were rejected early.
These tests verify that we now measure frame quality independently, so we can
judge how good a frame is before we even try to stack it.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality import frame_statistics
from astrometricslib.pipelines.shared.quality.quality_metrics import measure_frame_input_quality


def _write_frame(path, background=500.0, saturated_pixels=0, shape=(64, 64)) -> str:  # ruff: ignore[missing-type-function-argument]
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


def _target_with_frames(paths, camera="ZWO ASI 533MM Pro") -> Target:  # ruff: ignore[missing-type-function-argument]
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


def test_measures_background_from_a_real_frame(tmp_path: Path) -> None:
    """The measured background matches the frame's actual level."""
    path = _write_frame(tmp_path / "frame.fits", background=1234.0)

    metrics = measure_frame_input_quality(path, saturation_threshold_adu=65000.0)

    assert metrics["background_level"] == pytest.approx(1234.0)
    assert metrics["saturated_pixel_fraction"] == pytest.approx(0.0)


def test_detects_saturated_pixels(tmp_path: Path) -> None:
    """Clipped pixels are reported as a fraction of the frame."""
    path = _write_frame(tmp_path / "frame.fits", background=500.0, saturated_pixels=64)

    metrics = measure_frame_input_quality(path, saturation_threshold_adu=65000.0)

    assert metrics["saturated_pixel_fraction"] == pytest.approx(64 / (64 * 64))


def test_fwhm_is_off_by_default(tmp_path: Path) -> None:
    """FWHM costs ~50x the other metrics, so it must be opt-in."""
    path = _write_frame(tmp_path / "frame.fits")

    assert measure_frame_input_quality(path, saturation_threshold_adu=65000.0)["fwhm_px"] is None


def test_unreadable_frame_yields_all_none(tmp_path: Path) -> None:
    """One bad frame must not abort a sweep across a whole target."""
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a FITS file at all")

    metrics = measure_frame_input_quality(str(broken), saturation_threshold_adu=65000.0)

    assert metrics == {
        "background_level": None,
        "saturated_pixel_fraction": None,
        "fwhm_px": None,
    }


def test_missing_file_yields_all_none(tmp_path: Path) -> None:
    """A frame record pointing at a deleted file measures as unknown."""
    metrics = measure_frame_input_quality(
        str(tmp_path / "does_not_exist.fits"), saturation_threshold_adu=65000.0
    )

    assert metrics["background_level"] is None


def test_task_populates_every_frame(tmp_path: Path) -> None:
    """Each frame record gains its own measured values."""
    paths = [
        _write_frame(tmp_path / "a.fits", background=100.0),
        _write_frame(tmp_path / "b.fits", background=900.0),
    ]
    target = _target_with_frames(paths)

    counts = frame_statistics.measure_frame_input_quality(target)

    assert counts == {"measured": 2, "skipped": 0, "failed": 0}
    assert target.frames[0].measurements.background_level == pytest.approx(100.0)
    assert target.frames[1].measurements.background_level == pytest.approx(900.0)


def test_task_is_incremental_by_default(tmp_path: Path) -> None:
    """An interrupted sweep resumes rather than re-measuring everything."""
    target = _target_with_frames([_write_frame(tmp_path / "a.fits")])
    frame_statistics.measure_frame_input_quality(target)

    counts = frame_statistics.measure_frame_input_quality(target)

    assert counts == {"measured": 0, "skipped": 1, "failed": 0}


def test_remeasure_overrides_the_incremental_skip(tmp_path: Path) -> None:
    """Passing remeasure=True re-reads frames that already have values."""
    target = _target_with_frames([_write_frame(tmp_path / "a.fits")])
    frame_statistics.measure_frame_input_quality(target)

    counts = frame_statistics.measure_frame_input_quality(target, remeasure=True)

    assert counts == {"measured": 1, "skipped": 0, "failed": 0}


def test_unreadable_frames_are_counted_not_raised(tmp_path: Path) -> None:
    """A broken frame is reported as failed while the rest still measure."""
    good = _write_frame(tmp_path / "good.fits")
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"garbage")
    target = _target_with_frames([good, str(broken)])

    counts = frame_statistics.measure_frame_input_quality(target)

    assert counts == {"measured": 1, "skipped": 0, "failed": 1}
    assert target.frames[0].measurements.background_level is not None
    assert target.frames[1].measurements.background_level is None


def test_camera_filter_restricts_measurement(tmp_path: Path) -> None:
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

    counts = frame_statistics.measure_frame_input_quality(target, camera_name="Nikon")

    assert counts["measured"] == 1
    assert target.frames[0].measurements.background_level is None
    assert target.frames[1].measurements.background_level is not None


def test_measured_fwhm_is_kept_apart_from_registration_fwhm(tmp_path: Path) -> None:
    """The two FWHM sources must never be mixed in one field.

    photutils measures ~1.53x Siril's PSF fit on identical frames, so a
    value written into registration_fwhm_x_px would look like a ~50%
    seeing change caused purely by which stage did the measuring.
    """
    target = _target_with_frames([_write_frame(tmp_path / "a.fits")])
    target.frames[0].measurements.registration_fwhm_x_px = 3.2

    frame_statistics.measure_frame_input_quality(target, measure_fwhm=lambda data: 5.1)

    assert target.frames[0].measurements.measured_fwhm_px == pytest.approx(5.1)
    assert target.frames[0].measurements.registration_fwhm_x_px == pytest.approx(3.2)


def test_each_frames_saturation_uses_its_own_cameras_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame is judged against the threshold in its camera's profile."""
    from astrometricslib._internal.drivers import camera_profile_store
    from astrometricslib.foundation.config import AppConfiguration

    config_path = tmp_path / "profiles.config.toml"
    config_path.write_text(
        '["Observatory.Camera.Generic"]\n'
        'is_generic_fallback = "true"\n'
        'clip_ceiling_adu = { value = 65535.0, kind = "assumed", source = "a test" }\n'
        'saturation_threshold_adu = { value = 65000.0, kind = "assumed", source = "a test" }\n'
        "\n"
        '["Observatory.Camera.Fourteen Bit Camera"]\n'
        'clip_ceiling_adu = { value = 65535.0, kind = "assumed", source = "a test" }\n'
        'saturation_threshold_adu = { value = 15000.0, kind = "assumed", source = "a test" }\n'
    )
    profile_config = AppConfiguration.__new__(AppConfiguration)
    profile_config._find_config_file = lambda: config_path
    profile_config.base_dir = tmp_path
    profile_config.config_file_path = None
    from astrometricslib.foundation.config import _TomlSectionedConfig

    profile_config.app_config = _TomlSectionedConfig()
    profile_config.app_config.read(str(config_path))
    monkeypatch.setattr(camera_profile_store, "get_configuration", lambda: profile_config)

    data = np.full((64, 64), 500.0, dtype=np.float32)
    data[:8, :] = 16000.0
    frame_path = str(tmp_path / "clipped.fits")
    fits.PrimaryHDU(data).writeto(frame_path)
    target = Target(
        id="TwoCameras",
        frames=[
            FrameRecord(path=frame_path, role="LIGHT", camera="Fourteen Bit Camera", exposure="30.0"),
            FrameRecord(path=frame_path, role="LIGHT", camera="Unlisted Camera", exposure="30.0"),
        ],
    )

    frame_statistics.measure_frame_input_quality(target)

    assert target.frames[0].measurements.saturated_pixel_fraction == pytest.approx(0.125)
    assert target.frames[1].measurements.saturated_pixel_fraction == pytest.approx(0.0)


def _library(tmp_path: Path) -> CalibrationLibrary:
    """Build an empty calibration library that saves inside `tmp_path`.

    Returns
    -------
    library : `CalibrationLibrary`
        A library with no frames.
    """
    app_config = SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    return CalibrationLibrary(app_config=app_config)


def _calibration_frame(
    path: Path, exposure: float, offset: float = 30.0, flat_filter: str | None = None
) -> str:
    """Write a small calibration FITS frame at gain 100.

    Returns
    -------
    path : `str`
        The path written.
    """
    hdu = fits.PrimaryHDU(np.full((8, 8), 100.0, dtype=np.float32))
    hdu.header["INSTRUME"] = "Cam"
    hdu.header["EXPTIME"] = exposure
    hdu.header["GAIN"] = 100
    hdu.header["OFFSET"] = offset
    hdu.header["XBINNING"] = 1
    hdu.header["YBINNING"] = 1
    if flat_filter is not None:
        hdu.header["FILTER"] = flat_filter
    hdu.writeto(path)
    return str(path)


def _light_target(offset: str = "30") -> Target:
    """Make a target with two 30 s and one 10 s Luminance lights at gain 100.

    Returns
    -------
    target : `Target`
        The target.
    """
    kwargs = {"filter": "Luminance", "role": "LIGHT", "camera": "Cam", "iso": "100", "offset": offset}
    return Target(
        id="Grouped",
        frames=[
            FrameRecord(path="a.fits", exposure="30.0", **kwargs),
            FrameRecord(path="b.fits", exposure="30.0", **kwargs),
            FrameRecord(path="c.fits", exposure="10.0", **kwargs),
        ],
    )


def test_grouped_stats_report_what_the_stacker_would_select(tmp_path: Path) -> None:
    """Each group's match equals the selection the stacker runs."""
    library = _library(tmp_path)
    library.add_dark_frame(_calibration_frame(tmp_path / "dark.fits", 30.0))
    library.add_bias_frame(_calibration_frame(tmp_path / "bias.fits", 0.0))
    library.add_flat_frame(_calibration_frame(tmp_path / "flat.fits", 1.0, flat_filter="Luminance"))
    target = _light_target()

    groups = frame_statistics.get_frame_stats_grouped(target, library)

    by_exposure = {group["exposure"]: group for group in groups}
    assert by_exposure["30"]["count"] == 2
    assert by_exposure["30"]["darks"] == "1"
    assert by_exposure["10"]["darks"] == "Missing"
    assert by_exposure["10"]["darkMatch"]["problem"]
    direct_bias = library.select_bias_frames(camera="Cam", iso="100", offset="30", binning="1x1")
    direct_flat = library.select_flat_frames(
        telescope="Unknown",
        camera="Cam",
        filter_type=target.frames[0].filter,
        iso="100",
        offset="30",
        binning="1x1",
    )
    assert by_exposure["30"]["biasMatch"]["count"] == len(direct_bias.frames) == 1
    assert by_exposure["30"]["flatMatch"]["count"] == len(direct_flat.frames) == 1
    assert by_exposure["30"]["biasMatch"]["problem"] == ""
    assert by_exposure["30"]["biasMatch"]["fallbacks"] == []


def test_a_bias_at_the_wrong_offset_is_a_fallback_with_the_reason(tmp_path: Path) -> None:
    """A bias at another offset is a fallback, with a note saying so."""
    library = _library(tmp_path)
    library.add_bias_frame(_calibration_frame(tmp_path / "bias.fits", 0.0, offset=10.0))
    groups = frame_statistics.get_frame_stats_grouped(_light_target(offset="30"), library)

    bias = groups[0]["biasMatch"]
    assert bias["count"] == 1
    assert bias["fallbacks"]
    assert groups[0]["flatMatch"]["count"] == 0
    assert "no flat frames" in groups[0]["flatMatch"]["problem"]
