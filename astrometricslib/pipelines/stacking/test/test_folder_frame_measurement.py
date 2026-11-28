"""Purpose: Tests for measuring a folder of frames without storing anything.

Description: Checks ``quality.frames.measure(paths, include=["metrics"])``
and its `measure_frame_metrics` on small synthetic star fields whose star
width and sky level are known. The frames are drawn here, so no real image is
read, and the batch check is replaced. The tests check that the width and the
sky rate are recovered, that the bias level comes from the right place, that
the frames given are the frames measured, that a frame drawn by a simulator
raises a notice, that one bad file does not stop the others, and that nothing
reaches the quality store.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.quality import QualityRecords
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.run_bases import StarWidthEstimator, star_width_on_scale
from astrometricslib.models.quality.folder_measurement import FolderMeasurementReport
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared import frame_scanning
from astrometricslib.pipelines.shared.quality import raw_frame_check
from astrometricslib.pipelines.stacking import folder_frame_measurement
from astrometricslib.pipelines.stacking.test.test_frame_measurement_run import _Library, _profile, biases

GAIN_E_PER_ADU = 0.5
"""The gain of the test camera profile, in electrons per ADU."""

EXPOSURE_SECONDS = 10.0
SKY_ADU = 300.0
BIAS_ADU = 100.0
SIGMA_PX = 1.7
"""The Gaussian width (standard deviation) of the drawn stars, in pixels."""

__all__ = ["biases"]


def _write_star_field(
    path: Path, sigma: float = SIGMA_PX, seed: int = 1, synthetic: bool = False, bias: float = BIAS_ADU
) -> None:
    """Draw a frame of Gaussian stars on a flat sky with photon noise.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the frame.
    sigma : `float`, optional
        The stars' standard deviation, in pixels.
    seed : `int`, optional
        The random seed.
    synthetic : `bool`, optional
        Mark the frame as drawn by a simulator (header card SYNTHETC).
    bias : `float`, optional
        The pedestal added to every pixel, in ADU.
    """
    generator = np.random.default_rng(seed)
    size = 512
    rows, columns = np.mgrid[0:size, 0:size]
    image = np.full((size, size), bias + SKY_ADU, dtype=float)
    for _ in range(60):
        x, y = generator.uniform(30, size - 30, 2)
        image += generator.uniform(3000, 8000) * np.exp(
            -((columns - x) ** 2 + (rows - y) ** 2) / (2 * sigma**2)
        )
    image = generator.poisson(image / GAIN_E_PER_ADU) * GAIN_E_PER_ADU + generator.normal(0, 3, image.shape)
    hdu = fits.PrimaryHDU(image.clip(0, 65535).astype(np.uint16))
    hdu.header["EXPTIME"] = EXPOSURE_SECONDS
    hdu.header["DATE-OBS"] = "2026-11-06T04:00:00"
    if synthetic:
        hdu.header["SYNTHETC"] = True
    hdu.writeto(path, overwrite=True)


@pytest.fixture(autouse=True)
def stand_in_header_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the configuration-driven header reading and the profile lookup."""

    def fake_record(path: str, camera: str | None = None, config: object = None) -> FrameRecord:
        """Build the record the real reader would, without a configuration.

        Returns
        -------
        record : `FrameRecord`
            A record of the test camera at gain 100, offset 0.
        """
        return FrameRecord(
            path=path,
            camera="Test Camera",
            telescope="Test Scope",
            gain=100,
            offset=0,
            exposure_s=str(EXPOSURE_SECONDS),
            timestamp=1.78e9,
        )

    monkeypatch.setattr(frame_scanning, "create_frame_record_from_fits", fake_record)
    monkeypatch.setattr(folder_frame_measurement, "resolve_camera_profile", lambda name: _profile())
    monkeypatch.setattr(
        raw_frame_check, "check_raw_frames", lambda paths: {"frames": [], "batch": {"frame_count": 0}}
    )


def _measure(quality: QualityRecords, folder: Path, **options: object) -> FolderMeasurementReport:
    """Measure the FITS files of a folder with the frame metric set.

    Parameters
    ----------
    quality : `QualityRecords`
        The quality API.
    folder : `pathlib.Path`
        The folder; its ``*.fits`` files are measured in name order.
    **options : `object`
        Passed on to ``quality.frames.measure``.

    Returns
    -------
    report : `FolderMeasurementReport`
        The ``metrics`` part of the report.
    """
    paths = sorted(str(path) for path in folder.glob("*.fits"))
    report = quality.frames.measure(paths, include=["metrics"], **options)
    assert report.metrics is not None
    return report.metrics


def _quality(tmp_path: Path, bias_paths: list[str] | None = None) -> tuple[QualityRecords, MagicMock]:
    """Build the quality API over a stand-in catalog.

    Returns
    -------
    quality : `QualityRecords`
        The API; it has a calibration library only when bias paths are given.
    storage : `MagicMock`
        The stand-in catalog, whose calls show whether anything was stored.
    """
    storage = MagicMock()
    quality = QualityRecords(
        SimpleNamespace(get_library_path=lambda: tmp_path),
        storage,
        calibration_library=(lambda: _Library(bias_paths)) if bias_paths is not None else None,
    )
    return quality, storage


def test_star_width_and_sky_rate_are_recovered_and_nothing_is_stored(tmp_path: Path) -> None:
    """Width and sky rate match what was drawn; nothing is stored."""
    _write_star_field(tmp_path / "frame_001.fits")
    quality, storage = _quality(tmp_path)

    report = _measure(quality, tmp_path, bias_level_adu=BIAS_ADU)

    assert report.frames_matching == 1
    assert report.frames_measured == 1
    fwhm = 2.3548 * SIGMA_PX
    assert report.medians["frame.star_width_moffat_px"] == pytest.approx(fwhm, rel=0.12)
    expected_rate = SKY_ADU * GAIN_E_PER_ADU / EXPOSURE_SECONDS
    assert report.medians["frame.sky_rate_e_per_s_per_px"] == pytest.approx(expected_rate, rel=0.05)
    assert report.frames[0].exposure_s == pytest.approx(EXPOSURE_SECONDS)
    assert storage.method_calls == []


def test_each_star_width_names_its_estimator_and_its_value_on_the_scale(tmp_path: Path) -> None:
    """Each width names its estimator; one with a ratio has a scale value."""
    _write_star_field(tmp_path / "frame_001.fits")
    quality, _ = _quality(tmp_path)

    report = _measure(quality, tmp_path, bias_level_adu=BIAS_ADU)

    moffat = "frame.star_width_moffat_px"
    assert report.star_width_estimators[moffat] == "frame Moffat"
    expected = star_width_on_scale(report.medians[moffat], StarWidthEstimator.FRAME_MOFFAT)
    assert report.frames[0].widths_on_scale_px[moffat] == pytest.approx(expected)
    assert report.medians_on_scale_px[moffat] == pytest.approx(expected)
    assert moffat not in report.star_widths_not_on_scale
    assert "frame.sky_rate_e_per_s_per_px" not in report.star_width_estimators
    assert (
        report.star_width_estimators["frame.star_width_effective_px"]
        == "frame Moffat, square pixel blur added"
    )
    assert "frame.star_width_effective_px" not in report.medians_on_scale_px
    assert "AST-17" in report.star_widths_not_on_scale["frame.star_width_effective_px"]
    assert list(tmp_path.glob("*.db")) == []


def test_the_bias_level_comes_from_the_calibration_library_when_none_is_given(
    tmp_path: Path, biases: list[str]
) -> None:
    """Without a level, the library's bias frames set the sky rate."""
    folder = tmp_path / "staging"
    folder.mkdir()
    _write_star_field(folder / "frame_001.fits")
    quality, _ = _quality(tmp_path, biases)

    report = _measure(quality, folder)

    # The library's bias frames give a level, so the sky rate is measured.
    assert report.medians["frame.sky_rate_e_per_s_per_px"] > 0.0
    assert report.bias_level_adu is None


def test_without_a_bias_level_the_sky_metrics_say_why_they_are_missing(tmp_path: Path) -> None:
    """No level and no library: the sky rate is not measured, with a reason."""
    _write_star_field(tmp_path / "frame_001.fits")
    quality, _ = _quality(tmp_path)

    report = _measure(quality, tmp_path)

    assert "frame.sky_rate_e_per_s_per_px" not in report.frames[0].values
    assert "frame.sky_rate_e_per_s_per_px" in report.frames[0].not_measured
    assert report.notes
    assert "frame.star_width_moffat_px" in report.frames[0].values


def test_a_frame_drawn_by_a_simulator_raises_a_notice(tmp_path: Path) -> None:
    """A frame with the SYNTHETC card is reported with the warning."""
    _write_star_field(tmp_path / "frame_001.fits", synthetic=True)
    quality, _ = _quality(tmp_path)

    report = _measure(quality, tmp_path, bias_level_adu=BIAS_ADU)

    assert report.notices
    assert "frame.notice_synthetic" not in report.frames[0].values


def test_the_frames_given_are_the_frames_measured(tmp_path: Path) -> None:
    """Only the frames given are measured; the report names their folder."""
    for index in range(4):
        _write_star_field(tmp_path / f"frame_{index:03d}.fits", seed=index + 1)
    quality, _ = _quality(tmp_path)
    paths = [str(tmp_path / "frame_002.fits"), str(tmp_path / "frame_003.fits")]

    report = quality.frames.measure(paths, include=["metrics"], bias_level_adu=BIAS_ADU).metrics

    assert [frame.file_name for frame in report.frames] == ["frame_002.fits", "frame_003.fits"]
    assert report.frames_matching == 2
    assert report.folder_path == str(tmp_path)


def test_one_unreadable_file_does_not_stop_the_others(tmp_path: Path) -> None:
    """A file that is not a FITS image is skipped with a note."""
    (tmp_path / "frame_000.fits").write_text("not a fits file")
    _write_star_field(tmp_path / "frame_001.fits")
    quality, _ = _quality(tmp_path)

    report = _measure(quality, tmp_path, bias_level_adu=BIAS_ADU)

    assert [frame.file_name for frame in report.frames][-1] == "frame_001.fits"
    assert report.frames[-1].values["frame.star_width_moffat_px"] > 0.0


def test_a_missing_file_or_no_frames_is_refused(tmp_path: Path) -> None:
    """A path with no file is not found; an empty list is refused."""
    quality, _ = _quality(tmp_path)
    with pytest.raises(NotFoundError):
        quality.frames.measure([str(tmp_path / "nowhere.fits")], include=["metrics"])
    with pytest.raises(InvalidArgumentError, match="empty"):
        quality.frames.measure([], include=["metrics"])
