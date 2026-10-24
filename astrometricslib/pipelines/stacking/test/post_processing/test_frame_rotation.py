"""Tests for the per-frame rotation about the registration reference.

The rotation is read from Siril's registration results, which the tests give
as plain dictionaries with a 3 x 3 transform for each frame. The last tests
run the stacking record step and check which rows it stores.
"""

import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject, frame_subject_id
from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.shared.quality.record_measurements import (
    check_measurement_against_registry,
)
from astrometricslib.pipelines.shared.registration_record import RegistrationRecord, build_registration_record
from astrometricslib.pipelines.stacking import metrics as stacking_metrics
from astrometricslib.pipelines.stacking.metrics import STACKING_FRAME_REGISTRATION_ROTATION_DEG
from astrometricslib.pipelines.stacking.post_processing import record_quality
from astrometricslib.pipelines.stacking.post_processing.field_rotation import (
    field_rotation_measurements,
    image_parity_text,
    rotation_from_homography,
)
from astrometricslib.pipelines.stacking.post_processing.record_quality import FrameGeometryRows
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    registration_reference_from_diagnostics,
)
from astrometricslib.pipelines.stacking.test.conformance_fixture import fixture_summary

POINTING_METRICS = (
    stacking_metrics.STACKING_REGISTRATION_STEP_MEDIAN_PX,
    stacking_metrics.STACKING_REGISTRATION_STEP_MAD_PX,
    stacking_metrics.STACKING_REGISTRATION_STEP_JUMP_COUNT,
    stacking_metrics.STACKING_REGISTRATION_STEP_DEGENERATE_COUNT,
    stacking_metrics.STACKING_REGISTRATION_STEP_AFTER_DRIFT_MEDIAN_PX,
    stacking_metrics.STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR,
    stacking_metrics.STACKING_REGISTRATION_DRIFT_DIRECTION_DEG,
    stacking_metrics.STACKING_REGISTRATION_DRIFT_EXTENT_PX,
)
CONTEXT = PipelineRunContext(
    "M 99", "stacking", "1.3.0", "job-1", "job-1", datetime(2026, 10, 23, tzinfo=UTC)
)
IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def _frames(count: int) -> list[SimpleNamespace]:
    """Make frame records with paths in a library-style folder.

    Parameters
    ----------
    count : `int`
        How many frames.

    Returns
    -------
    frames : `list` of `SimpleNamespace`
        Records with a path, camera and timestamp.
    """
    return [
        SimpleNamespace(
            path=f"/frames/lights/M 99/Scope/Cam/f{index:03d}.fits",
            camera="Cam",
            timestamp=1_792_000_000.0 + 60.0 * index,
        )
        for index in range(count)
    ]


def _turned(angle_deg: float, shift: float = 1.0) -> list[float]:
    """Build a transform that turns by an angle and shifts by a column count.

    Parameters
    ----------
    angle_deg : `float`
        The rotation in degrees.
    shift : `float`, optional
        The shift along x in pixels.

    Returns
    -------
    homography : `list` of `float`
        The nine numbers in row order.
    """
    angle = math.radians(angle_deg)
    return [math.cos(angle), -math.sin(angle), shift, math.sin(angle), math.cos(angle), 0.0, 0.0, 0.0, 1.0]


def _diagnostics(frames: list[SimpleNamespace], homographies: list[list[float]]) -> dict:
    """Build the registration results of a run.

    Parameters
    ----------
    frames : `list`
        The frames, in Siril's order.
    homographies : `list` of `list` of `float`
        Siril's transform for each frame.

    Returns
    -------
    diagnostics : `dict`
        The three keys the steps read.
    """
    return {
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": [True] * len(frames),
        "registration_frames": [
            {"dx": h[2], "dy": h[5], "homography": h, "star_count": 100} for h in homographies
        ],
    }


def _record_of(diagnostics: dict, *, is_spectral: bool = False) -> RegistrationRecord | None:
    """Read a run's registration results into the record the steps take.

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported.
    is_spectral : `bool`, optional
        Whether the stack is spectral.

    Returns
    -------
    record : `RegistrationRecord` or `None`
        The record, with the frame whose transform is the identity as the
        reference; `None` when the lists differ in length.
    """
    reference = registration_reference_from_diagnostics(diagnostics)
    return build_registration_record(
        diagnostics, (0, 0), None if reference is None else reference[0], is_spectral=is_spectral
    )


def test_each_frame_gets_its_rotation_in_its_own_subject() -> None:
    """A frame's row holds the angle and the id of the frame's weight."""
    frames = _frames(3)
    homographies = [_turned(0.004), IDENTITY, _turned(-0.006)]
    rows = field_rotation_measurements(
        frames, _record_of(_diagnostics(frames, homographies)), CONTEXT, is_spectral=False
    )
    assert [row.value for row in rows] == pytest.approx([0.004, 0.0, -0.006])
    assert rows[0].subject.subject_id == "M 99/Scope/Cam/f000.fits"
    assert rows[0].subject.observing_night_id is not None
    assert all(row.status == MeasurementStatus.MEASURED for row in rows)
    for row in rows:
        check_measurement_against_registry(row)


def test_a_measured_row_names_the_reference_frame_and_the_parity() -> None:
    """The reason of a measured row says which frame was the reference."""
    frames = _frames(3)
    homographies = [_turned(0.004), IDENTITY, _turned(-0.006)]
    rows = field_rotation_measurements(
        frames, _record_of(_diagnostics(frames, homographies)), CONTEXT, is_spectral=False
    )
    assert "reference frame f001.fits" in rows[0].reason
    assert "parity (sign of det(CD) of the stack's plate solution): unknown" in rows[0].reason


def test_the_parity_is_the_sign_of_the_cd_determinant_of_the_stack_header(tmp_path: Path) -> None:
    """A north-up solution has parity -1, a mirrored one +1, none unknown."""
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [50.0, 50.0]
    wcs.wcs.crval = [250.0, 36.0]
    wcs.wcs.cdelt = [-2.0 / 3600.0, 2.0 / 3600.0]
    normal = tmp_path / "normal.fits"
    fits.PrimaryHDU(data=np.zeros((10, 10), dtype=np.float32), header=wcs.to_header()).writeto(normal)
    wcs.wcs.cdelt = [2.0 / 3600.0, 2.0 / 3600.0]
    mirrored = tmp_path / "mirrored.fits"
    fits.PrimaryHDU(data=np.zeros((10, 10), dtype=np.float32), header=wcs.to_header()).writeto(mirrored)
    bare = tmp_path / "bare.fits"
    fits.PrimaryHDU(data=np.zeros((10, 10), dtype=np.float32)).writeto(bare)

    assert image_parity_text(str(normal)) == "-1"
    assert image_parity_text(str(mirrored)) == "+1"
    assert image_parity_text(str(bare)) == "unknown"
    assert image_parity_text(str(tmp_path / "missing.fits")) == "unknown"

    frames = _frames(2)
    rows = field_rotation_measurements(
        frames,
        _record_of(_diagnostics(frames, [IDENTITY, _turned(0.01)])),
        CONTEXT,
        is_spectral=False,
        stack_path=str(normal),
    )
    assert "sign of det(CD) of the stack's plate solution): -1" in rows[1].reason


def test_the_angle_comes_from_the_polar_decomposition_and_ignores_a_stretch() -> None:
    """A stretch on top of a turn does not move the polar angle."""
    angle = math.radians(0.5)
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    stretch = np.array([[1.02, 0.03], [0.03, 0.98]])
    matrix = rotation @ stretch
    homography = [matrix[0, 0], matrix[0, 1], 3.0, matrix[1, 0], matrix[1, 1], -2.0, 0.0, 0.0, 1.0]
    polar = rotation_from_homography(homography)
    naive = math.degrees(math.atan2(homography[3], homography[0]))
    assert polar == pytest.approx(0.5, abs=1e-9)
    assert abs(naive - 0.5) > 0.1


def test_the_angle_wraps_at_180_degrees_across_a_meridian_flip() -> None:
    """Just under a half turn reads near +180 and just over reads near -180."""
    assert rotation_from_homography(_turned(179.99)) == pytest.approx(179.99, abs=1e-9)
    assert rotation_from_homography(_turned(180.01)) == pytest.approx(-179.99, abs=1e-9)


def test_a_transform_that_mirrors_the_image_has_no_angle() -> None:
    """A negative determinant gives no angle."""
    mirror = [1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 1.0]
    assert rotation_from_homography(mirror) is None
    frames = _frames(3)
    rows = field_rotation_measurements(
        frames,
        _record_of(_diagnostics(frames, [IDENTITY, mirror, _turned(0.01)])),
        CONTEXT,
        is_spectral=False,
    )
    assert rows[1].status == MeasurementStatus.NOT_MEASURED
    assert "reverses the image" in rows[1].reason


def test_the_rotation_metric_is_exploratory_and_has_a_question() -> None:
    """The rotation is not a decision metric and says what it asks."""
    spec = get_metric_registry().get(STACKING_FRAME_REGISTRATION_ROTATION_DEG)
    assert spec.purpose.value == "exploratory"
    assert spec.exploratory_question
    assert spec.review_date is not None
    assert "single `run_id`" in spec.sample_definition or "one `run_id`" in spec.sample_definition
    assert "DATE-OBS" in spec.sample_definition
    assert "start of the exposure" in spec.sample_definition


def test_a_spectral_stack_has_no_rotation() -> None:
    """A shift-only registration gives not-measured rows with the reason."""
    frames = _frames(2)
    rows = field_rotation_measurements(
        frames,
        _record_of(_diagnostics(frames, [IDENTITY, _turned(0.0, 2.0)]), is_spectral=True),
        CONTEXT,
        is_spectral=True,
    )
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("shift only" in row.reason for row in rows)


def test_a_sequence_of_identity_transforms_is_not_measured() -> None:
    """Frames registered before Siril read them show only meaningless zeros."""
    frames = _frames(3)
    rows = field_rotation_measurements(
        frames, _record_of(_diagnostics(frames, [IDENTITY] * 3)), CONTEXT, is_spectral=False
    )
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("identity" in row.reason for row in rows)


def test_a_frame_siril_dropped_is_not_measured_but_the_others_are() -> None:
    """A frame Siril could not register gets a reason, not a made-up angle."""
    frames = _frames(3)
    diagnostics = _diagnostics(frames, [_turned(0.004), IDENTITY, _turned(-0.006)])
    diagnostics["registration_included"] = [True, False, True]
    rows = field_rotation_measurements(frames, _record_of(diagnostics), CONTEXT, is_spectral=False)
    assert [row.status for row in rows] == [
        MeasurementStatus.MEASURED,
        MeasurementStatus.NOT_MEASURED,
        MeasurementStatus.MEASURED,
    ]


def test_a_frame_missing_from_sirils_table_gets_a_not_measured_row() -> None:
    """A frame Siril was never given is not silently left out."""
    frames = _frames(4)
    diagnostics = _diagnostics(frames[:3], [_turned(0.004), IDENTITY, _turned(-0.006)])
    rows = field_rotation_measurements(frames, _record_of(diagnostics), CONTEXT, is_spectral=False)
    assert len(rows) == 4
    assert rows[3].subject.subject_id == "M 99/Scope/Cam/f003.fits"
    assert rows[3].status == MeasurementStatus.NOT_MEASURED
    assert "no entry for this frame" in rows[3].reason


def test_registration_results_that_do_not_match_the_frames_give_no_angle() -> None:
    """Lists of different length cannot name a frame, so none is measured."""
    frames = _frames(3)
    diagnostics = _diagnostics(frames[:2], [_turned(0.004), IDENTITY])
    diagnostics["symlinked_light_paths"] = [frame.path for frame in frames]
    assert _record_of(diagnostics) is None
    rows = field_rotation_measurements(frames, None, CONTEXT, is_spectral=False)
    assert len(rows) == 3
    assert all("could not be matched" in row.reason for row in rows)


# --- The record step stores the geometry rows ------------------------------


def _use_temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualityStore:
    """Point the quality store at a temporary library.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The temporary folder.
    monkeypatch : `pytest.MonkeyPatch`
        Used to redirect the library path.

    Returns
    -------
    store : `QualityStore`
        The store on the temporary library's database.
    """
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    return QualityStore(str(tmp_path / "astrometrics.db"))


def _stack_row() -> QualityMeasurement:
    """Build one stack-level row, to stand for the stack's own rows.

    Returns
    -------
    row : `QualityMeasurement`
        A zero-pixel-fraction row on the stack.
    """
    spec = next(
        spec for spec in stacking_metrics.STACKING_METRICS if spec.name == "stacking.zero_pixel_fraction"
    )
    return build_measurement(spec, 0.01, CONTEXT, CONTEXT.stack_subject("M99_L_Stacked.fits"))


def _record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, list[SimpleNamespace]]:
    """Run `record_stacking_quality` with the heavy steps replaced.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The temporary folder.
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the noise, science and stack-row steps.

    Returns
    -------
    new_count : `int`
        What `record_stacking_quality` returned.
    frames : `list` of `SimpleNamespace`
        The three frames given to it.
    """
    monkeypatch.setattr(
        record_quality,
        "measure_noise_safely",
        lambda *_args, **_kwargs: SimpleNamespace(result=None, reason="test"),
    )
    monkeypatch.setattr(record_quality, "determine_stack_members", lambda *_: None)
    monkeypatch.setattr(record_quality, "compute_stack_science", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(record_quality, "stacking_measurements", lambda *_: [_stack_row()])
    # The flat-to-light match has its own tests (test_flat_match.py).
    monkeypatch.setattr(record_quality, "flat_match_rows", lambda *_args, **_kwargs: [])
    frames = _frames(3)
    diagnostics = _diagnostics(frames, [_turned(0.004), IDENTITY, _turned(-0.006)])
    new_count = record_quality.record_stacking_quality(
        fixture_summary(), CONTEXT, str(tmp_path / "M99_L_Stacked.fits"), frames, diagnostics, None
    )
    return new_count, frames


def test_the_record_step_stores_the_stack_rows_and_the_rotation_of_every_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`record_stacking_quality` stores the geometry rows too."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    new_count, frames = _record(tmp_path, monkeypatch)
    rows = store.query(target_id="M 99", latest_only=False)
    rotation_rows = [row for row in rows if row.metric == STACKING_FRAME_REGISTRATION_ROTATION_DEG]

    # One stack row, one rotation row for each of three frames, and the eight
    # pointing rows of the stack (three frames give no step, so they say why).
    assert new_count == 12
    assert {row.metric for row in rows} == {
        "stacking.zero_pixel_fraction",
        STACKING_FRAME_REGISTRATION_ROTATION_DEG,
        *POINTING_METRICS,
    }
    assert all(row.reason for row in rows if row.metric in POINTING_METRICS)
    assert sorted(row.subject.subject_id for row in rotation_rows) == sorted(
        frame_subject_id(frame.path, "M 99") for frame in frames
    )


def test_a_bad_geometry_row_leaves_the_stack_rows_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A geometry row the registry refuses cannot undo the stack's rows."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    bad = _stack_row().model_copy(update={"metric": "stacking.not_a_declared_metric"})
    monkeypatch.setattr(
        record_quality, "frame_geometry_measurements", lambda *_: FrameGeometryRows([bad], [])
    )
    new_count, _ = _record(tmp_path, monkeypatch)
    rows = store.query(target_id="M 99", latest_only=False)

    assert new_count == 1
    assert [row.metric for row in rows] == ["stacking.zero_pixel_fraction"]


def test_a_geometry_step_that_raises_leaves_the_stack_rows_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error building the geometry rows leaves the stack rows stored."""
    store = _use_temporary_store(tmp_path, monkeypatch)

    def explode(*_arguments: object) -> list[QualityMeasurement]:
        """Raise as a broken geometry step would.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("broken")

    monkeypatch.setattr(record_quality, "frame_geometry_measurements", explode)
    new_count, _ = _record(tmp_path, monkeypatch)

    assert new_count == 1
    assert [row.metric for row in store.query(target_id="M 99", latest_only=False)] == [
        "stacking.zero_pixel_fraction"
    ]


def _boom(*_arguments: object, **_keywords: object) -> list[QualityMeasurement]:
    """Raise as a broken row builder would.

    Raises
    ------
    RuntimeError
        Always.
    """
    raise RuntimeError("broken")


def test_pointing_rows_that_cannot_be_built_leave_the_rotation_rows_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error in the pointing step costs only the pointing rows."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    monkeypatch.setattr(record_quality, "pointing_measurements", _boom)
    new_count, _ = _record(tmp_path, monkeypatch)
    metrics = {row.metric for row in store.query(target_id="M 99", latest_only=False)}

    assert new_count == 4
    assert metrics == {"stacking.zero_pixel_fraction", STACKING_FRAME_REGISTRATION_ROTATION_DEG}


def test_pointing_rows_the_registry_refuses_leave_the_rotation_rows_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pointing rows are stored in a call of their own."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    bad = _stack_row().model_copy(update={"metric": "stacking.not_a_declared_metric"})
    monkeypatch.setattr(record_quality, "pointing_measurements", lambda *_, **__: [bad])
    new_count, _ = _record(tmp_path, monkeypatch)
    metrics = {row.metric for row in store.query(target_id="M 99", latest_only=False)}

    assert new_count == 4
    assert metrics == {"stacking.zero_pixel_fraction", STACKING_FRAME_REGISTRATION_ROTATION_DEG}


def test_rotation_rows_that_cannot_be_built_leave_the_pointing_rows_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error in the rotation step costs only the rotation rows."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    monkeypatch.setattr(record_quality, "field_rotation_measurements", _boom)
    new_count, _ = _record(tmp_path, monkeypatch)
    metrics = {row.metric for row in store.query(target_id="M 99", latest_only=False)}

    assert new_count == 9
    assert metrics == {"stacking.zero_pixel_fraction", *POINTING_METRICS}


def test_a_stack_of_several_exposure_groups_gets_its_own_reason() -> None:
    """Both row groups say the stack joins groups, not that lists differ."""
    frames = _frames(3)
    diagnostics = _diagnostics(frames, [_turned(0.004), IDENTITY, _turned(-0.006)])
    diagnostics["exposure_groups"] = [{"exposure_seconds": 30.0}, {"exposure_seconds": 60.0}]
    rows = record_quality.frame_geometry_measurements(
        fixture_summary(), CONTEXT, frames, diagnostics, "M99_Stacked.fits"
    )

    assert len(rows.rotation) == 3
    assert len(rows.pointing) == 8
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in [*rows.rotation, *rows.pointing])
    assert all("several exposure groups" in row.reason for row in [*rows.rotation, *rows.pointing])
    assert not any("could not be matched" in row.reason for row in [*rows.rotation, *rows.pointing])


def test_the_registration_is_read_into_one_record_for_both_row_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`frame_geometry_measurements` builds the record once and hands it on."""
    frames = _frames(3)
    diagnostics = _diagnostics(frames, [_turned(0.004), IDENTITY, _turned(-0.006)])
    built: list[RegistrationRecord | None] = []
    real_build = record_quality.build_registration_record

    def counting_build(*arguments: object, **keywords: object) -> RegistrationRecord | None:
        """Build a record and note it.

        Returns
        -------
        record : `RegistrationRecord` or `None`
            What the real builder gives.
        """
        built.append(real_build(*arguments, **keywords))
        return built[-1]

    seen: list[object] = []
    real_rotation = record_quality.field_rotation_measurements
    real_pointing = record_quality.pointing_measurements

    def spy_rotation(target_frames: object, record: object, *arguments: object, **keywords: object) -> list:
        """Note the record the rotation step was given.

        Returns
        -------
        rows : `list`
            What the real step gives.
        """
        seen.append(record)
        return real_rotation(target_frames, record, *arguments, **keywords)

    def spy_pointing(target_frames: object, record: object, *arguments: object, **keywords: object) -> list:
        """Note the record the pointing step was given.

        Returns
        -------
        rows : `list`
            What the real step gives.
        """
        seen.append(record)
        return real_pointing(target_frames, record, *arguments, **keywords)

    monkeypatch.setattr(record_quality, "build_registration_record", counting_build)
    monkeypatch.setattr(record_quality, "field_rotation_measurements", spy_rotation)
    monkeypatch.setattr(record_quality, "pointing_measurements", spy_pointing)
    record_quality.frame_geometry_measurements(fixture_summary(), CONTEXT, frames, diagnostics, "S.fits")

    assert len(built) == 1
    assert seen[0] is built[0]
    assert seen[1] is built[0]


def test_the_rotation_row_uses_the_one_frame_subject_builder() -> None:
    """The rotation row's subject is the one `frame_subject` builds."""
    frame = _frames(1)[0]
    rows = field_rotation_measurements(
        [frame], _record_of(_diagnostics([frame], [_turned(0.004)])), CONTEXT, is_spectral=False
    )
    assert rows[0].subject == frame_subject(frame.path, "M 99", frame.timestamp)
