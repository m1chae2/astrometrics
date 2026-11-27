"""Tests for the raw-frame check, ``astrometrics.quality.frames.measure``.

`QualityFrames.measure` takes the frames a caller chose (a frame query's
page, frame records or file paths) and works out from their filters whether
they are imaging or spectrum frames. These tests use small fake frames with
the measuring functions replaced, and check the choice of check, the
``metrics`` part, the refusals and that nothing is saved on the target.
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api._quality_frames import QualityFrames
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.page import Page
from astrometricslib.models.quality.folder_measurement import FolderMeasurementReport
from astrometricslib.models.target import FrameRecord, Target

RAW_CHECK = "astrometricslib.pipelines.shared.quality.raw_frame_check.check_raw_frames"
SPECTRAL_CHECK = (
    "astrometricslib.pipelines.spectroscopy.pre_processing.spectral_frame_check.check_spectral_frames"
)
FRAME_METRICS = "astrometricslib.pipelines.stacking.folder_frame_measurement.measure_frame_metrics"


def _frames(count: int = 4, filter_name: str = "Luminance", exposure: str = "60") -> list[FrameRecord]:
    """Build light frames numbered by time.

    Parameters
    ----------
    count : `int`, optional
        How many frames.
    filter_name : `str`, optional
        Their filter; ``"Star Analyzer 200"`` makes spectrum frames.
    exposure : `str`, optional
        Their exposure length, in seconds.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames, newest last.
    """
    return [
        FrameRecord(
            path=f"/lights/f{number}.fits",
            role="LIGHT",
            camera="ZWO ASI533",
            filter=filter_name,
            exposure=exposure,
            timestamp=float(number),
        )
        for number in range(count)
    ]


def _checked(paths: list[str]) -> dict:
    """Stand in for the batch check: one row per frame, nothing flagged.

    Parameters
    ----------
    paths : `list` [`str`]
        The frames checked.

    Returns
    -------
    report : `dict`
        Rows with a star count, width and sky level, and a batch summary.
    """
    rows = [
        {"path": path, "star_count": 100, "fwhm_px": 3.0, "sky_median_adu": 500.0, "flags": []}
        for path in paths
    ]
    return {"frames": rows, "batch": {"frame_count": len(rows), "flagged_count": 0}}


@pytest.fixture
def frames_api() -> QualityFrames:
    """Make the object under test.

    Returns
    -------
    frames : `QualityFrames`
        ``astrometrics.quality.frames`` with no configuration, which frame
        records do not need.
    """
    return QualityFrames(None)  # type: ignore[arg-type]


def test_imaging_frames_get_the_batch_check_in_time_order(frames_api: QualityFrames) -> None:
    """Every frame is checked, oldest first, and no metric set is made."""
    frames = list(reversed(_frames()))
    with patch(RAW_CHECK, side_effect=lambda paths: _checked(paths)) as check:
        report = frames_api.measure(frames)
    assert check.call_args.kwargs["paths"] == [f"/lights/f{number}.fits" for number in range(4)]
    assert report.kind == "imaging"
    assert report.frames_measured == 4
    assert report.checks is not None and report.checks.frames_checked == 4
    assert report.metrics is None and report.spectral is None
    assert report.checks.fwhm_estimator == "raw check second moment"


def test_the_sky_level_is_trended_only_for_one_exposure_length(frames_api: QualityFrames) -> None:
    """Frames of mixed exposure leave the sky level out of the drifts."""
    mixed = [*_frames(2), *_frames(2, exposure="30")]
    with patch(RAW_CHECK, side_effect=lambda paths: _checked(paths)):
        same = frames_api.measure(_frames())
        different = frames_api.measure(mixed)
    assert "sky_median_adu" in same.checks.trends["metrics"]
    assert "sky_median_adu" not in different.checks.trends["metrics"]
    assert "different exposure lengths" in different.checks.trends["notes"][0]


def test_the_metrics_part_measures_the_stored_metric_set(frames_api: QualityFrames) -> None:
    """``include=["metrics"]`` adds the metric set at the bias level given."""
    with (
        patch(RAW_CHECK, side_effect=lambda paths: _checked(paths)),
        patch(FRAME_METRICS, return_value=FolderMeasurementReport(frames_measured=4)) as metrics,
    ):
        report = frames_api.measure(_frames(), include=["metrics"], bias_level_adu=300.0)
    assert report.metrics is not None and report.metrics.frames_measured == 4
    assert metrics.call_args.kwargs["bias_level_adu"] == pytest.approx(300.0)
    assert len(metrics.call_args.args[0]) == 4


def test_spectrum_frames_get_the_spectrum_check(frames_api: QualityFrames) -> None:
    """Spectrum frames are found by their filter and given the prediction."""
    from astrometricslib.models.quality.quality_reports import SpectralFrameCheckReport

    with patch(SPECTRAL_CHECK, return_value=SpectralFrameCheckReport(frames_measured=2)) as check:
        report = frames_api.measure(_frames(2, filter_name="Star Analyzer 200"), predict_exposure_s=60.0)
    assert report.kind == "spectral"
    assert report.frames_measured == 2
    assert check.call_args.args[1] == pytest.approx(60.0)


def test_bad_requests_are_refused(frames_api: QualityFrames) -> None:
    """A mix of kinds, an option of the other kind, or no frames is refused."""
    with pytest.raises(InvalidArgumentError, match="mix imaging and spectrum"):
        frames_api.measure([*_frames(2), *_frames(1, filter_name="Star Analyzer 200")])
    with pytest.raises(InvalidArgumentError, match="spectrum frames only"):
        frames_api.measure(_frames(), predict_exposure_s=60.0)
    with pytest.raises(InvalidArgumentError, match="imaging frames only"):
        frames_api.measure(_frames(2, filter_name="Star Analyzer 200"), include=["metrics"])
    with pytest.raises(InvalidArgumentError, match="Unknown include section"):
        frames_api.measure(_frames(), include=["fwhm"])
    with pytest.raises(InvalidArgumentError, match="empty"):
        frames_api.measure([])
    with pytest.raises(InvalidArgumentError):
        frames_api.measure("/lights")  # type: ignore[arg-type]


def test_a_page_cut_short_is_refused_and_a_whole_page_is_measured(frames_api: QualityFrames) -> None:
    """A page that does not hold every match would measure part of a choice."""
    frames = _frames()
    with pytest.raises(InvalidArgumentError, match="cut short"):
        frames_api.measure(Page.of(frames, limit=2))
    with patch(RAW_CHECK, side_effect=lambda paths: _checked(paths)):
        assert frames_api.measure(Page.of(frames)).frames_measured == 4


def test_paths_are_read_from_their_headers_and_need_not_be_library_frames(tmp_path: Path) -> None:
    """A path outside the library is measured; a missing one is not found."""
    from astrometricslib import AppConfiguration

    path = tmp_path / "staging" / "frame.fits"
    path.parent.mkdir()
    header = fits.Header({"IMAGETYP": "Light Frame", "FILTER": "L", "EXPTIME": 60.0, "INSTRUME": "Cam"})
    fits.writeto(path, np.zeros((8, 8), dtype=np.float32), header)
    frames_api = QualityFrames(AppConfiguration())
    with patch(RAW_CHECK, side_effect=lambda paths: _checked(paths)) as check:
        report = frames_api.measure([str(path)])
    assert check.call_args.kwargs["paths"] == [str(path)]
    assert report.frames_measured == 1
    with pytest.raises(NotFoundError):
        frames_api.measure([str(tmp_path / "missing.fits")])


def test_the_frames_given_are_never_changed(frames_api: QualityFrames) -> None:
    """Measuring stores nothing on the frame records or a target."""
    target = Target(id="T 1", frames=_frames())
    before = [frame.model_dump() for frame in target.frames]
    with patch(RAW_CHECK, side_effect=lambda paths: _checked(paths)):
        frames_api.measure(target.frames)
    assert [frame.model_dump() for frame in target.frames] == before
