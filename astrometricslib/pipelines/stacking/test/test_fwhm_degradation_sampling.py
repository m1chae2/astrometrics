"""Tests for how the stack's star width is compared with its input frames.

The check uses every frame in the stack, reuses the widths the frame quality
step stored, weights each frame as the stacking program weighted it, and
compares the stack with the width its inputs predict.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.quality.quality_summary import StackingPipelineQualityMetrics
from astrometricslib.pipelines.stacking import fwhm_sampling

MEASURE = "astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm"


@pytest.fixture(autouse=True)
def measure_in_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measure serially so in-process fakes can record their calls."""
    monkeypatch.setattr(fwhm_sampling, "_fwhm_worker_count", lambda frame_count: 1)


def make_summary() -> SimpleNamespace:
    """Build a stand-in summary holding empty stacking metrics.

    Returns
    -------
    summary : `SimpleNamespace`
        An object with a `stacking_metrics` attribute.
    """
    return SimpleNamespace(
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False, frames_submitted=10, frames_stacked=10
        )
    )


def test_every_frame_is_measured_not_a_sample() -> None:
    """With 60 frames, all 60 are measured, not a sample of 15."""
    frames = [SimpleNamespace(path=str(index)) for index in range(60)]
    measured: list[str] = []

    def fake_measure(path: str) -> float:
        """Record the path and give a fixed width.

        Returns
        -------
        width : `float`
            A width of 2 pixels for a frame and 2.2 for the stack.
        """
        measured.append(path)
        return 2.2 if path == "stack.fits" else 2.0

    with patch(MEASURE, side_effect=fake_measure):
        fwhm_sampling.measure_fwhm_degradation(make_summary(), "stack.fits", frames)

    frame_paths = sorted(int(path) for path in measured if path != "stack.fits")
    assert frame_paths == list(range(60))


def test_stored_widths_are_reused_and_only_the_missing_frames_are_measured() -> None:
    """A frame with a stored width is not measured again; the rest are."""
    frames = [SimpleNamespace(path=str(index)) for index in range(4)]
    measured: list[str] = []

    def fake_measure(path: str) -> float:
        """Record the path and give a fixed width.

        Returns
        -------
        width : `float`
            A width of 4 pixels.
        """
        measured.append(path)
        return 4.0

    summary = make_summary()
    with patch(MEASURE, side_effect=fake_measure):
        fwhm_sampling.measure_fwhm_degradation(
            summary, "stack.fits", frames, stacked_fwhm=3.0, stored_widths={"0": 2.0, "1": 2.0}
        )

    assert sorted(measured) == ["2", "3"]
    assert summary.stacking_metrics.expected_stack_fwhm_px == pytest.approx(10.0**0.5)
    assert summary.stacking_metrics.median_input_fwhm_px == pytest.approx(3.0)


def test_one_frame_more_or_less_changes_the_prediction() -> None:
    """Dropping the last of 121 frames changes the numbers.

    The old 15-frame sample (every 8th frame) picked the same frames from 121
    and from 120 frames, so a restack without the last frame stored the same
    widths to every digit.
    """
    widths = {str(index): 2.0 + index / 100 for index in range(121)}
    results = []
    for count in (121, 120):
        summary = make_summary()
        frames = [SimpleNamespace(path=str(index)) for index in range(count)]
        fwhm_sampling.measure_fwhm_degradation(
            summary, "stack.fits", frames, stacked_fwhm=2.5, stored_widths=widths
        )
        results.append(summary.stacking_metrics.expected_stack_fwhm_px)
    assert results[0] != results[1]


def wfwhm_diagnostics(paths: list[str], weighted_widths: list[float], included: list[bool]) -> dict:
    """Build what a single-group ``-weight=wfwhm`` stacking run reports.

    Parameters
    ----------
    paths : `list` [`str`]
        The frames in the order given to Siril.
    weighted_widths : `list` [`float`]
        Siril's weighted FWHM of each frame, from its sequence file.
    included : `list` [`bool`]
        Whether Siril kept each frame while registering.

    Returns
    -------
    diagnostics : `dict`
        The keys `determine_stack_members` reads.
    """
    return {
        "symlinked_light_paths": paths,
        "registration_frames": [
            {"weighted_fwhm_px": width, "dx": 0.0, "dy": 0.0} for width in weighted_widths
        ],
        "registration_included": included,
    }


def test_the_prediction_uses_siril_star_width_weights_and_only_stacked_frames() -> None:
    """Sharp frames count more, and a frame Siril dropped is left out."""
    from astrometricslib.pipelines.stacking.post_processing.stack_membership import wfwhm_weights
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import expected_stack_fwhm

    paths = ["a", "b", "c", "d"]
    frames = [SimpleNamespace(path=path, exposure=60.0) for path in paths]
    widths = {"a": 2.0, "b": 3.0, "c": 4.0, "d": 9.0}
    diagnostics = wfwhm_diagnostics(paths, [2.0, 3.0, 4.0, 9.0], [True, True, True, False])
    summary = make_summary()

    fwhm_sampling.measure_fwhm_degradation(
        summary,
        "stack.fits",
        frames,
        stacked_fwhm=3.0,
        diagnostics=diagnostics,
        stack_weight="wfwhm",
        stored_widths=widths,
    )

    metrics = summary.stacking_metrics
    weights = wfwhm_weights([2.0, 3.0, 4.0])
    assert metrics.expected_stack_fwhm_px == pytest.approx(expected_stack_fwhm([2.0, 3.0, 4.0], weights))
    assert metrics.expected_stack_fwhm_px < expected_stack_fwhm([2.0, 3.0, 4.0])
    assert metrics.median_input_fwhm_px == pytest.approx(3.0)


def test_an_equally_weighted_stack_predicts_the_plain_rms_of_its_members() -> None:
    """Without a weight mode every member counts the same."""
    paths = ["a", "b"]
    frames = [SimpleNamespace(path=path, exposure=60.0) for path in paths]
    summary = make_summary()

    fwhm_sampling.measure_fwhm_degradation(
        summary,
        "stack.fits",
        frames,
        stacked_fwhm=3.0,
        diagnostics=wfwhm_diagnostics(paths, [2.0, 4.0], [True, True]),
        stack_weight=None,
        stored_widths={"a": 2.0, "b": 4.0},
    )

    assert summary.stacking_metrics.expected_stack_fwhm_px == pytest.approx(10.0**0.5)


def test_stored_frame_widths_reads_current_measured_rows_only(tmp_path: Path) -> None:
    """Only measured whole-frame rows of the current definition are used."""
    from datetime import UTC, datetime

    from astrometricslib._internal.drivers.quality_store import QualityStore
    from astrometricslib.models.quality.measurement import (
        MeasurementOrigin,
        MeasurementStatus,
        QualityMeasurement,
        QualitySubject,
        SubjectLevel,
    )
    from astrometricslib.pipelines.shared.quality.metrics import FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX

    root = "/library/lights/M 31/Scope/Camera"
    frames = [SimpleNamespace(path=f"{root}/{name}.fits") for name in ("a", "b", "c", "d")]

    def row(name: str, **overrides: object) -> QualityMeasurement:
        """Build a stored width row of one frame.

        Returns
        -------
        row : `QualityMeasurement`
            A frame-level width measurement.
        """
        fields: dict = {
            "metric": FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
            "definition_version": 1,
            "subject": QualitySubject(
                level=SubjectLevel.FRAME, subject_id=f"M 31/Scope/Camera/{name}.fits", target_id="M 31"
            ),
            "status": MeasurementStatus.MEASURED,
            "value": 2.5,
            "origin": MeasurementOrigin.MEASURED,
            "run_id": f"run-{name}",
            "measured_at": datetime(2026, 11, 1, tzinfo=UTC),
            "unit": "px",
        }
        fields.update(overrides)
        return QualityMeasurement(**fields)

    store = QualityStore(str(tmp_path / "astrometrics.db"))
    store.write_measurements([
        row("a"),
        row("b", status=MeasurementStatus.NOT_MEASURED, value=None, reason="no star could be fitted"),
        row("c", definition_version=0),
    ])
    with patch.object(QualityStore, "from_configuration", return_value=store):
        widths = fwhm_sampling.stored_frame_widths("M 31", frames)

    assert widths == {f"{root}/a.fits": 2.5}


def test_the_stack_is_compared_with_the_rms_of_its_inputs() -> None:
    """A stack matching the RMS of mixed frames is not flagged."""
    frames = [SimpleNamespace(path=str(index)) for index in range(10)]
    widths = {str(index): (2.0 if index < 5 else 4.0) for index in range(10)}
    summary = make_summary()

    with patch(MEASURE, side_effect=lambda path: 3.2 if path == "stack.fits" else widths[path]):
        fwhm_sampling.measure_fwhm_degradation(summary, "stack.fits", frames)

    metrics = summary.stacking_metrics
    assert metrics.expected_stack_fwhm_px == pytest.approx(10.0**0.5)
    assert metrics.stacked_fwhm_px == pytest.approx(3.2)
    assert metrics.median_input_fwhm_px == pytest.approx(3.0)
    assert metrics.stacked_fwhm_px / metrics.expected_stack_fwhm_px < 1.2


def test_a_stack_much_wider_than_its_inputs_is_flagged() -> None:
    """A stack 1.5 times wider than uniform frames is degraded."""
    frames = [SimpleNamespace(path=str(index)) for index in range(10)]
    summary = make_summary()

    with patch(MEASURE, side_effect=lambda path: 3.0 if path == "stack.fits" else 2.0):
        fwhm_sampling.measure_fwhm_degradation(summary, "stack.fits", frames)

    metrics = summary.stacking_metrics
    assert metrics.stacked_fwhm_px / metrics.expected_stack_fwhm_px == pytest.approx(1.5)


def test_a_width_measured_by_the_combine_is_used_instead_of_measuring_the_file() -> None:
    """A stack combined from exposure groups brings its own width."""
    frames = [SimpleNamespace(path=str(index)) for index in range(4)]
    measured: list[str] = []

    def fake_measure(path: str) -> float:
        """Record the path and give a fixed width.

        Returns
        -------
        width : `float`
            A width of 2 pixels, as for a frame.
        """
        measured.append(path)
        return 2.0

    summary = make_summary()
    with patch(MEASURE, side_effect=fake_measure):
        fwhm_sampling.measure_fwhm_degradation(summary, "stack.fits", frames, stacked_fwhm=2.3)

    assert "stack.fits" not in measured
    assert summary.stacking_metrics.stacked_fwhm_px == pytest.approx(2.3)
    metrics = summary.stacking_metrics
    assert metrics.stacked_fwhm_px / metrics.expected_stack_fwhm_px < 1.2


def test_a_width_far_above_the_inputs_still_flags_the_stack() -> None:
    """A width given by the combine is checked the same way."""
    frames = [SimpleNamespace(path=str(index)) for index in range(4)]
    summary = make_summary()
    with patch(MEASURE, return_value=2.0):
        fwhm_sampling.measure_fwhm_degradation(summary, "stack.fits", frames, stacked_fwhm=3.4)

    metrics = summary.stacking_metrics
    assert metrics.stacked_fwhm_px / metrics.expected_stack_fwhm_px > 1.2


def write_star_frame(path: Path, star_sigma_pixels: float, seed: int) -> str:
    """Write a small frame of Gaussian stars on a noisy sky.

    Worker processes start fresh ("spawn"), so they cannot see a patched
    measurer; they read real files instead.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the FITS file.
    star_sigma_pixels : `float`
        Width (standard deviation) of every star, in pixels.
    seed : `int`
        Seed of the random generator, so each frame is repeatable.

    Returns
    -------
    path_text : `str`
        The path as text, as the sampling code takes it.
    """
    random_generator = np.random.default_rng(seed)
    image = random_generator.normal(1000.0, 5.0, (400, 400))
    rows, columns = np.mgrid[0:400, 0:400]
    for _ in range(40):
        star_x, star_y = random_generator.uniform(30, 370, 2)
        squared_distance = (columns - star_x) ** 2 + (rows - star_y) ** 2
        image += 3000.0 * np.exp(-squared_distance / (2.0 * star_sigma_pixels**2))
    fits.PrimaryHDU(image.astype("float32")).writeto(path, overwrite=True)
    return str(path)


def test_parallel_measurement_keeps_order_and_skips_failures(tmp_path: Path) -> None:
    """Spawned workers give the same ordered widths as a serial loop."""
    good_paths = [
        write_star_frame(tmp_path / f"frame_{index}.fits", 1.5 + index / 2, index) for index in range(3)
    ]
    paths = [good_paths[0], str(tmp_path / "missing.fits"), good_paths[1], good_paths[2]]
    serial = fwhm_sampling._measure_frame_fwhms(paths, 1)
    parallel = fwhm_sampling._measure_frame_fwhms(paths, 3)
    assert serial[1] is None
    assert serial[0] < serial[2] < serial[3]
    assert parallel == serial


def test_parallel_widths_give_the_same_summary_as_serial(tmp_path: Path) -> None:
    """The median and expected width do not depend on the worker count."""
    frames = [
        SimpleNamespace(path=write_star_frame(tmp_path / f"frame_{index}.fits", 1.5 + index / 10, index))
        for index in range(6)
    ]
    results = []
    for count in (1, 3):
        summary = make_summary()
        with patch.object(fwhm_sampling, "_fwhm_worker_count", return_value=count):
            fwhm_sampling.measure_fwhm_degradation(summary, "stack.fits", frames, stacked_fwhm=3.5)
        metrics = summary.stacking_metrics
        results.append((metrics.median_input_fwhm_px, metrics.expected_stack_fwhm_px))
    assert results[0] == results[1]


def test_one_frame_needs_no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """One frame is measured in this process."""
    monkeypatch.undo()
    assert fwhm_sampling._fwhm_worker_count(1) == 1
