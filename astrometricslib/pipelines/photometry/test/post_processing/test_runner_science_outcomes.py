"""Test that the photometry run stores its noise model, peaks and amplitude.

The real `validate_output` is run on an empty result whose payload carries one
session's noise inputs and zero points, and whose target has frames with a
telescope and a header airmass. The rows must be stored, and a failure in one
of the three steps must not drop the others or fail the run.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.observatory_setups import ObservatorySetups, OpticConfig
from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.metrics import (
    NOISE_MODEL_MAGNITUDES,
    NOISE_MODEL_PARTS,
    PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG,
    PHOTOMETRY_TARGET_CATALOG_AMPLITUDE_MAG,
    WINDOW_ALIAS_RANKS,
    model_noise_metric_name,
    window_alias_frequency_metric_name,
)
from astrometricslib.pipelines.photometry.post_processing import record_noise_budget
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import CatalogBand
from astrometricslib.pipelines.photometry.processing.noise_budget import SessionNoiseInputs
from astrometricslib.pipelines.photometry.test.conformance_fixture import _fixture_session_zero_points
from astrometricslib.pipelines.pipeline_base import PipelineRequest

MEASURED_AT = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _store_and_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[QualityStore, PipelineRequest]:
    """Point the quality store at a temporary library and build a request.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The temporary library folder.
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the library path and the optics for the test.

    Returns
    -------
    store, request : `tuple` [`QualityStore`, `PipelineRequest`]
        The store and a request for a target with two frames.
    """
    from astrometricslib.foundation.config import get_configuration

    configuration = get_configuration()
    monkeypatch.setattr(configuration, "get_library_path", lambda: tmp_path)
    monkeypatch.setattr(
        configuration,
        "get_observatory_setups",
        lambda: ObservatorySetups(
            optics=(OpticConfig(name="Apertura 75Q", focal_length_mm=405.0, focal_ratio=5.4),)
        ),
    )
    frames = [
        FrameRecord(path=f"/lights/M 99/frame_{index}.fits", telescope="Apertura 75Q", airmass=1.25)
        for index in range(2)
    ]
    request = PipelineRequest(
        target=Target(id="M 99", frames=frames),
        catalog_access=None,
        options={"job_id": "job-11", "measured_at": MEASURED_AT},
    )
    return QualityStore(str(tmp_path / "astrometrics.db")), request


def _result_with_session() -> object:
    """Build an empty result with one session's noise inputs and zero points.

    Returns
    -------
    result : `Result`
        The result `validate_output` reads.
    """
    result = runner._empty_photometry_result("no frames for the requested filter")
    result.payload["image_paths"] = ["/lights/M 99/frame_0.fits", "/lights/M 99/frame_1.fits"]
    result.payload["all_session_zero_points"] = [_fixture_session_zero_points(CatalogBand.GAIA_G, "night-1")]
    result.payload["all_session_noise_inputs"] = [
        SessionNoiseInputs("night-1", 10.0, 0.783, False, 3.8, False, 4.0e5, 500)
    ]
    return result


def test_the_run_stores_the_noise_model_the_alias_rows_and_the_amplitude_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each of the three steps stores its rows; airmass comes from frames."""
    store, request = _store_and_request(tmp_path, monkeypatch)

    runner.PhotometryPipelineAdapter().validate_output(request, _result_with_session())

    rows = {
        row.metric: row for row in store.query(target_id="M 99", latest_only=False) if row.run_id == "job-11"
    }
    for magnitude in NOISE_MODEL_MAGNITUDES:
        for part in NOISE_MODEL_PARTS:
            assert rows[model_noise_metric_name(part, magnitude)].value is not None
    scintillation = rows[PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG]
    assert scintillation.status is MeasurementStatus.ASSUMED
    assert "site height" in scintillation.reason
    assert scintillation.sample_count == 2
    for rank in WINDOW_ALIAS_RANKS:
        assert rows[window_alias_frequency_metric_name(rank)].value is None
    assert rows[PHOTOMETRY_TARGET_CATALOG_AMPLITUDE_MAG].value is None


def test_a_broken_noise_step_does_not_drop_the_other_rows_or_fail_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A noise-step error does not drop the alias and amplitude rows."""
    store, request = _store_and_request(tmp_path, monkeypatch)

    def explode(*_arguments: object) -> None:
        """Fail like a bug in the gathering of inputs.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("inputs broke")

    monkeypatch.setattr(runner, "noise_budget_inputs", explode)

    runner.PhotometryPipelineAdapter().validate_output(request, _result_with_session())

    names = {row.metric for row in store.query(target_id="M 99", latest_only=False) if row.run_id == "job-11"}
    assert window_alias_frequency_metric_name(1) in names
    assert PHOTOMETRY_TARGET_CATALOG_AMPLITUDE_MAG in names
    assert PHOTOMETRY_MODEL_NOISE_SCINTILLATION_MAG not in names


def test_the_noise_step_is_the_one_in_the_record_module() -> None:
    """The runner calls the record module's function."""
    assert runner.noise_budget_inputs is record_noise_budget.noise_budget_inputs
