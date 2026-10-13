"""Purpose: Unit tests for `ProcessingPipelines.process_target`'s stages.

Description: Each stage takes its options in its own dictionary, so an
option meant for one stage never reaches another. These tests verify each
stage's options reach the underlying pipeline dispatch correctly, that an
unknown option is refused, and that `process_target` runs the stages in the
right fixed order, respects a `stages` subset, skips spectroscopy gracefully
when a target has no spectral data, and threads photometry's result into
the spectroscopy call.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.observatory_setups import ObservatorySetups, OpticConfig, SetupConfig
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines import tasks


class _RecordingPipelineMatch:
    """A `tasks._run_analysis_pipeline_match` stub that records every call.

    Returns a distinct, recognizable dict per `pipeline_type` so a test
    can confirm which stage's result reached a later stage.
    """

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[dict] = []

    def __call__(
        self,
        target: object,
        frames: object,
        pipeline_type: str,
        filter_type: object,
        catalog_access: object,
        path: object,
        **kwargs: object,
    ) -> dict:
        """Record the call and return a stage-recognizable stub result.

        Returns
        -------
        result : `dict`
            A minimal stub result carrying `pipeline_type` back.
        """
        self.calls.append({
            "pipeline_type": pipeline_type,
            "frames": frames,
            "filter_type": filter_type,
            "catalog_access": catalog_access,
            "path": path,
            **kwargs,
        })
        return {"status": "finished", "pipeline_type": pipeline_type}


@pytest.fixture
def recording_pipelines(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProcessingPipelines, _RecordingPipelineMatch]:
    """Build a `ProcessingPipelines` whose dispatch is recorded, not run.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        A pipelines instance backed by a fresh, in-memory-only config.
    recorder : `_RecordingPipelineMatch`
        The stub recording every dispatched call.
    """
    recorder = _RecordingPipelineMatch()
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", recorder)
    return ProcessingPipelines(AppConfiguration(), MagicMock()), recorder


def test_the_astrometry_stage_forwards_its_options(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the astrometry options reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="AstrometryParamsTarget", stacked_image="/fake/stack.fits")

    pipelines.process_target(
        target, stages=["astrometry"], astrometry={"path": "/override.fits"}, register_job=False
    )

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["pipeline_type"] == "astrometry"
    assert recorder.calls[0]["path"] == "/override.fits"


def test_the_photometry_stage_forwards_its_options(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the photometry options reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="PhotometryParamsTarget")
    frame = FrameRecord(path="/frame1.fits", filter="Luminance")

    pipelines.process_target(
        target,
        stages=["photometry"],
        photometry={"frames": [frame], "filter_type": "L", "use_astrometry_seed": False, "max_workers": 3},
        register_job=False,
    )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pipeline_type"] == "photometry"
    assert call["frames"] == [frame]
    assert call["filter_type"] == "L"
    assert call["use_astrometry_seed"] is False
    assert call["max_workers"] == 3


def test_the_spectroscopy_stage_forwards_its_options(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the spectroscopy options reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="SpectroscopyParamsTarget", stacked_spectral_target="/fake/spec.fits")

    pipelines.process_target(target, stages=["spectroscopy"], spectroscopy={"limit": 5}, register_job=False)

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pipeline_type"] == "spectroscopy"
    assert call["limit"] == 5
    assert call["photometry_result"] is None


def test_an_option_a_stage_does_not_know_is_refused(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify a misspelt or misplaced option raises, not being dropped."""
    pipelines, recorder = recording_pipelines
    target = Target(id="UnknownOptionTarget", stacked_image="/fake/stack.fits")

    with pytest.raises(InvalidArgumentError, match="Unknown astrometry option"):
        pipelines.process_target(target, stages=["astrometry"], astrometry={"limit": 5}, register_job=False)
    with pytest.raises(InvalidArgumentError, match="does not use: on_item_complete"):
        pipelines.process_target(target, on_item_complete=lambda *_: None, register_job=False)
    assert recorder.calls == []


def test_process_target_runs_all_three_stages_in_order(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the default run order is astrometry, photometry, spectroscopy."""
    pipelines, recorder = recording_pipelines
    target = Target(
        id="ProcessTargetOrderTarget",
        stacked_image="/fake/stack.fits",
        stacked_spectral_target="/fake/spec.fits",
    )

    results = pipelines.process_target(target, register_job=False)

    assert [call["pipeline_type"] for call in recorder.calls] == [
        "astrometry",
        "photometry",
        "spectroscopy",
    ]
    assert results.stages_run == ["astrometry", "photometry", "spectroscopy"]


def test_process_target_threads_photometry_result_into_spectroscopy(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify spectroscopy receives photometry's result forwarded to it."""
    pipelines, recorder = recording_pipelines
    target = Target(
        id="ProcessTargetThreadingTarget",
        stacked_image="/fake/stack.fits",
        stacked_spectral_target="/fake/spec.fits",
    )

    results = pipelines.process_target(target, register_job=False)

    spectroscopy_call = next(call for call in recorder.calls if call["pipeline_type"] == "spectroscopy")
    assert spectroscopy_call["photometry_result"] == results.results["photometry"]


def test_process_target_stages_subset_runs_only_the_requested_stages(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify a `stages` subset skips whatever stage isn't listed."""
    pipelines, recorder = recording_pipelines
    target = Target(
        id="ProcessTargetSubsetTarget",
        stacked_image="/fake/stack.fits",
        stacked_spectral_target="/fake/spec.fits",
    )

    results = pipelines.process_target(target, stages=["spectroscopy", "photometry"], register_job=False)

    assert [call["pipeline_type"] for call in recorder.calls] == ["photometry", "spectroscopy"]
    assert set(results.results) == {"photometry", "spectroscopy"}


def test_process_target_skips_spectroscopy_without_spectral_data(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify spectroscopy is skipped on a target with no spectral input."""
    pipelines, recorder = recording_pipelines
    target = Target(id="ProcessTargetNoSpectraTarget", stacked_image="/fake/stack.fits")

    results = pipelines.process_target(target, register_job=False)

    assert "spectroscopy" not in [call["pipeline_type"] for call in recorder.calls]
    assert results.results["spectroscopy"] == {"status": "skipped", "reason": "target has no spectral data"}


def test_stack_summary_reports_a_target_with_no_stack() -> None:
    """A target without a saved stack summary raises NotFoundError."""
    target = Target(id="Nothing_Yet", name="Nothing Yet")
    with pytest.raises(NotFoundError, match="has no saved summary"):
        ProcessingPipelines(AppConfiguration(), MagicMock()).stack_summary(target)


_ASI = "ZWO ASI 533MM Pro"
_NIKON = "Nikon DSLR DSC D5300"


class _SetupsConfig(AppConfiguration):
    """A config with the ASI533 and the D5300 on a 405 mm primary optic."""

    def get_observatory_setups(self) -> ObservatorySetups:
        """Give two cameras on one optic.

        Returns
        -------
        setups : `ObservatorySetups`
            The ASI533 and the D5300, both on the 405 mm optic.
        """
        return ObservatorySetups(
            optics=(OpticConfig(name="Apertura", focal_length_mm=405.0),),
            setups=(
                SetupConfig(name="ASI", camera_name=_ASI, optic_name="Apertura"),
                SetupConfig(name="Nikon", camera_name="Nikon D5300", optic_name="Apertura"),
            ),
        )

    def get_primary_camera_name(self) -> str:
        """Give the primary camera.

        Returns
        -------
        camera_name : `str`
            The ASI533.
        """
        return _ASI

    def get_primary_focal_length_mm(self) -> float:
        """Give the primary optic.

        Returns
        -------
        focal_length_mm : `float`
            405.
        """
        return 405.0


def _mixed_target() -> Target:
    """Build a target with Nikon frames at two optics and ASI frames.

    Returns
    -------
    target : `Target`
        The target. Its frames are named for their camera, optic and kind.
    """
    frames = [
        FrameRecord(path="/nikon405.fits", camera=_NIKON, filter="Luminance", focalLengthMm=405.0),
        FrameRecord(path="/nikon300.fits", camera=_NIKON, filter="Luminance", focalLengthMm=300.0),
        FrameRecord(path="/asi405.fits", camera=_ASI, filter="Luminance", focalLengthMm=405.0),
        FrameRecord(path="/asi_spec.fits", camera=_ASI, filter="SPEC", focalLengthMm=405.0),
    ]
    return Target(id="MixedOpticsTarget", frames=frames)


@pytest.fixture
def setups_pipelines(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProcessingPipelines, _RecordingPipelineMatch]:
    """Build pipelines with a recorded dispatch and two configured cameras.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        The pipelines.
    recorder : `_RecordingPipelineMatch`
        The stub recording every dispatched call.
    """
    recorder = _RecordingPipelineMatch()
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", recorder)
    return ProcessingPipelines(_SetupsConfig(), MagicMock()), recorder


def _photometry_paths(recorder: _RecordingPipelineMatch) -> list[str]:
    """List the paths of the frames the photometry stage was given.

    Returns
    -------
    paths : `list` [`str`]
        The frame paths, in order.
    """
    call = next(call for call in recorder.calls if call["pipeline_type"] == "photometry")
    return [frame.path for frame in call["frames"]]


def test_one_target_photometry_defaults_to_the_batch_camera_and_primary_optic(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify photometry gets the batch's camera and the primary optic."""
    pipelines, recorder = setups_pipelines

    pipelines.process_target(_mixed_target(), stages=["photometry"], register_job=False)

    assert _photometry_paths(recorder) == ["/asi405.fits", "/asi_spec.fits"]


def test_one_target_photometry_uses_the_camera_and_optic_asked_for(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify ``camera_id`` and ``focal_length_mm`` replace the defaults."""
    pipelines, recorder = setups_pipelines

    pipelines.process_target(
        _mixed_target(), stages=["photometry"], camera_id="Nikon", focal_length_mm=300.0, register_job=False
    )

    assert _photometry_paths(recorder) == ["/nikon300.fits"]


def test_one_target_photometry_option_frames_still_win(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify frames in the photometry options beat the selection."""
    pipelines, recorder = setups_pipelines
    own_frame = FrameRecord(path="/own.fits", filter="Luminance")

    pipelines.process_target(
        _mixed_target(), stages=["photometry"], photometry={"frames": [own_frame]}, register_job=False
    )

    assert _photometry_paths(recorder) == ["/own.fits"]


def test_one_target_spectroscopy_needs_spectral_frames_in_the_selection(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify spectroscopy runs for ASI533 spectra, not for the D5300."""
    pipelines, recorder = setups_pipelines
    target = _mixed_target()

    ran = pipelines.process_target(target, stages=["spectroscopy"], register_job=False)
    assert ran.results["spectroscopy"]["pipeline_type"] == "spectroscopy"

    skipped = pipelines.process_target(target, stages=["spectroscopy"], camera_id="Nikon", register_job=False)
    assert skipped.results["spectroscopy"]["status"] == "skipped"
    assert len(recorder.calls) == 1


def test_one_target_still_refuses_the_batch_only_callback(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify ``on_item_complete`` is refused for one target."""
    pipelines, recorder = setups_pipelines

    with pytest.raises(InvalidArgumentError, match="does not use: on_item_complete"):
        pipelines.process_target(_mixed_target(), on_item_complete=lambda *_: None, register_job=False)
    pipelines.process_target(_mixed_target(), stages=["astrometry"], camera_id="ZWO", register_job=False)
    assert len(recorder.calls) == 1
