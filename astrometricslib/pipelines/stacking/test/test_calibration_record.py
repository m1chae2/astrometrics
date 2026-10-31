"""Purpose: Unit tests for the calibration record a library stack stores.

Description: Checks that the record lists the flat, bias and dark files the
run applied (each once, with size and change time), the most common camera,
optic and focal length of the frames, the Siril version and the stacking
algorithm version; that storing it twice for one run adds nothing; that a
failure never raises; and that the exposure groups' calibration lists are
joined into one when a group stack is merged.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrometricslib.drivers.calibration_library import (
    BIAS_FRAME_PATHS_KEY,
    DARK_FRAME_PATHS_KEY,
    FLAT_FRAME_PATHS_KEY,
)
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.post_processing import calibration_record
from astrometricslib.pipelines.stacking.post_processing.calibration_record import (
    build_calibration_identities,
    record_calibration_identities,
)
from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import STACKING_ALGORITHM_VERSION
from astrometricslib.pipelines.stacking.stack_runner import _merge_diagnostics


def make_context() -> PipelineRunContext:
    """Build a run context for target M 57.

    Returns
    -------
    context : `PipelineRunContext`
        A stacking run context with a fixed run id.
    """
    return PipelineRunContext(
        target_id="M 57",
        pipeline_name="stacking",
        pipeline_version="test",
        run_id="run-1",
        job_id=None,
        measured_at=datetime(2026, 10, 25, tzinfo=UTC),
    )


def make_frames() -> list[SimpleNamespace]:
    """Build three frames, two from the ZWO camera on the Apertura.

    Returns
    -------
    frames : `list` [`SimpleNamespace`]
        Frames with a camera, telescope and focal length.
    """
    return [
        SimpleNamespace(camera="ZWO ASI 533MM Pro", telescope="Apertura 75Q", focal_length_mm=405.0),
        SimpleNamespace(camera="ZWO ASI 533MM Pro", telescope="Apertura 75Q", focal_length_mm=405.0),
        SimpleNamespace(camera="Unknown", telescope="Unknown", focal_length_mm=None),
    ]


def test_the_record_lists_the_applied_files_and_the_equipment(tmp_path: Path) -> None:
    """Each applied file appears once, with its size and change time."""
    flat = tmp_path / "flat.fits"
    flat.write_bytes(b"12345")
    missing_dark = str(tmp_path / "gone.fits")
    diagnostics = {
        FLAT_FRAME_PATHS_KEY: [str(flat), str(flat)],
        BIAS_FRAME_PATHS_KEY: [],
        DARK_FRAME_PATHS_KEY: [missing_dark],
    }
    identities = build_calibration_identities(
        make_context(), "M_57_L_Stacked.fits", make_frames(), diagnostics, "1.4.4"
    )
    assert identities.run_id == "run-1"
    assert identities.subject_id == "M 57/M_57_L_Stacked.fits"
    assert identities.camera == "ZWO ASI 533MM Pro"
    assert identities.optic == "Apertura 75Q"
    assert identities.focal_length_mm == pytest.approx(405.0)
    assert [item.path for item in identities.flat_masters] == [str(flat)]
    assert identities.flat_masters[0].size_bytes == 5
    assert identities.flat_masters[0].modified_ns is not None
    assert identities.bias_masters == []
    assert identities.dark_masters[0].path == missing_dark
    assert identities.dark_masters[0].size_bytes is None
    assert identities.siril_version == "1.4.4"
    assert identities.stacking_algorithm_version == STACKING_ALGORITHM_VERSION


def test_a_record_is_stored_once_per_run(tmp_path: Path) -> None:
    """Storing the record of the same run twice adds one row."""
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    config = SimpleNamespace(get_siril_executable=lambda: None)
    with patch.object(calibration_record.QualityStore, "from_configuration", return_value=store):
        first = record_calibration_identities(
            make_context(), "/x/M_57_L_Stacked.fits", make_frames(), {}, config=config
        )
        second = record_calibration_identities(
            make_context(), "/x/M_57_L_Stacked.fits", make_frames(), {}, config=config
        )
    assert (first, second) == (True, False)
    (stored,) = store.get_calibration_identities(run_ids=["run-1"])
    assert stored.siril_version is None


def test_a_failure_is_logged_and_never_raised() -> None:
    """A store that cannot be opened gives False instead of an error."""
    config = SimpleNamespace(get_siril_executable=lambda: None)
    with patch.object(calibration_record.QualityStore, "from_configuration", side_effect=OSError("no disk")):
        assert (
            record_calibration_identities(make_context(), "/x/s.fits", make_frames(), {}, config=config)
            is False
        )


def test_a_group_stack_joins_the_calibration_files_of_every_group() -> None:
    """Each group's darks are kept; a file both groups used appears once."""
    first = SimpleNamespace(exposure_seconds=30.0, frames=[])
    second = SimpleNamespace(exposure_seconds=60.0, frames=[])
    merged = _merge_diagnostics([
        (first, "a.fits", {FLAT_FRAME_PATHS_KEY: ["flat"], DARK_FRAME_PATHS_KEY: ["dark30"]}),
        (second, "b.fits", {FLAT_FRAME_PATHS_KEY: ["flat"], DARK_FRAME_PATHS_KEY: ["dark60"]}),
    ])
    assert merged[FLAT_FRAME_PATHS_KEY] == ["flat"]
    assert merged[DARK_FRAME_PATHS_KEY] == ["dark30", "dark60"]
