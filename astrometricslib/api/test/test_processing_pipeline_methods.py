"""Purpose: Unit tests for the analysis pipelines' ``run`` methods.

Description: Each analysis pipeline has its own object under
``Astrometrics.processing`` (``astrometry``, ``photometry``, ``spectroscopy``,
``asteroids``), and each ``run`` takes its own options. These tests verify the
options reach the underlying pipeline dispatch, that photometry measures the
frames of the analyzed stack's camera and optic (or the frames given, refused
when they mix cameras), that spectroscopy is skipped gracefully when a target
has no spectral data, and that ``astrometry.solve`` describes a solution.
Prerequisite rules are tested in `pipelines/test/test_stage_prerequisites.py`;
these tests mark every result current, so a run adds no prerequisite.
"""

from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.observatory_setups import ObservatorySetups, OpticConfig, SetupConfig
from astrometricslib.models.target import FrameRecord, StackConfigurationResult, Target
from astrometricslib.pipelines import pipeline_runs, tasks


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


def _quiet_runs(monkeypatch: pytest.MonkeyPatch, recorder: _RecordingPipelineMatch) -> None:
    """Record the dispatch, take no real job slot, and add no prerequisite.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The test's patcher.
    recorder : `_RecordingPipelineMatch`
        The stub that records every dispatched call.
    """
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", recorder)
    monkeypatch.setattr(tasks, "stage_staleness_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline_runs, "acquire_resource_slot", lambda *_args, **_kwargs: nullcontext())


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
    _quiet_runs(monkeypatch, recorder)
    return ProcessingPipelines(AppConfiguration(), MagicMock()), recorder


def test_astrometry_solves_the_main_stack_or_the_image_given(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the stack to solve reaches the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="AstrometryParamsTarget", stacked_image="/fake/stack.fits")

    pipelines.astrometry.run(target)
    pipelines.astrometry.run(target, stack="/override.fits")

    assert [call["pipeline_type"] for call in recorder.calls] == ["astrometry", "astrometry"]
    assert recorder.calls[0]["path"] == "/fake/stack.fits"
    assert recorder.calls[1]["path"] == "/override.fits"


def test_photometry_measures_the_frames_given(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the frames given reach the photometry dispatch."""
    pipelines, recorder = recording_pipelines
    frame = FrameRecord(path="/frame1.fits", filter="Luminance", camera="Cam")
    target = Target(id="PhotometryParamsTarget", frames=[frame])

    result = pipelines.photometry.run(target, frames=[frame])

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pipeline_type"] == "photometry"
    assert call["frames"] == [frame]
    assert result.stages_run == ["photometry"]


def test_spectroscopy_takes_a_star_limit(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify ``max_stars`` reaches the spectroscopy dispatch as its limit."""
    pipelines, recorder = recording_pipelines
    target = Target(id="SpectroscopyParamsTarget", stacked_spectral_target="/fake/spec.fits")

    pipelines.spectroscopy.run(target, max_stars=5)

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pipeline_type"] == "spectroscopy"
    assert call["limit"] == 5


def test_a_star_limit_below_one_is_refused(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify a bad limit raises before any work."""
    pipelines, recorder = recording_pipelines
    with pytest.raises(InvalidArgumentError, match="max_stars"):
        pipelines.spectroscopy.run(Target(id="T"), max_stars=0)
    assert recorder.calls == []


def test_spectroscopy_is_skipped_without_spectral_data(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify spectroscopy is skipped on a target with no spectral input."""
    pipelines, recorder = recording_pipelines
    target = Target(id="NoSpectraTarget", stacked_image="/fake/stack.fits")

    results = pipelines.spectroscopy.run(target)

    assert recorder.calls == []
    assert results.results["spectroscopy"] == {"status": "skipped", "reason": "target has no spectral data"}


def test_the_asteroid_search_dispatches_its_pipeline(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify ``asteroids.run`` runs the asteroid detection pipeline."""
    pipelines, recorder = recording_pipelines
    pipelines.asteroids.run(Target(id="AsteroidTarget", stacked_image="/fake/stack.fits"))
    assert [call["pipeline_type"] for call in recorder.calls] == ["asteroid_detection"]


def test_a_pipeline_refuses_a_target_name(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the subject is a `Target`, not its id."""
    pipelines, _ = recording_pipelines
    with pytest.raises(InvalidArgumentError, match="Target itself"):
        pipelines.photometry.run("M 13")


def test_stack_summary_reports_a_target_with_no_stack() -> None:
    """A target without a saved stack summary raises NotFoundError."""
    target = Target(id="Nothing_Yet", name="Nothing Yet")
    with pytest.raises(NotFoundError, match="has no saved summary"):
        ProcessingPipelines(AppConfiguration(), MagicMock()).stacking.get_summary(target)


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
    _quiet_runs(monkeypatch, recorder)
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


def test_photometry_defaults_to_the_batch_camera_and_primary_optic(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify photometry gets the batch's camera and the primary optic."""
    pipelines, recorder = setups_pipelines

    pipelines.photometry.run(_mixed_target())

    assert _photometry_paths(recorder) == ["/asi405.fits", "/asi_spec.fits"]


def _two_stack_target() -> Target:
    """Build the mixed target with an ASI533 stack and a Nikon 300 mm stack.

    Returns
    -------
    target : `Target`
        The target. Its recorded (main) stack is the ASI533 one.
    """
    target = _mixed_target()
    target.stacking.stacked_image = "/no/such/asi405_Stacked.fits"
    target.stacking.stacks_by_configuration = {
        "asi@405mm": StackConfigurationResult(
            configuration_key="asi@405mm",
            camera=_ASI,
            focal_length_mm=405.0,
            stacked_image="/no/such/asi405_Stacked.fits",
            is_preferred=True,
        ),
        "nikon@300mm": StackConfigurationResult(
            configuration_key="nikon@300mm",
            camera=_NIKON,
            focal_length_mm=300.0,
            stacked_image="/no/such/nikon300_Stacked.fits",
        ),
    }
    return target


def test_a_run_analyzes_the_stack_of_the_configuration_given(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify another configuration's stack is solved and its frames measured.

    The target's recorded stack is the ASI533 one. Passing the Nikon 300 mm
    stack solves it and measures only its frames; the recorded stack does
    not put them aside as another camera's frames.
    """
    pipelines, recorder = setups_pipelines
    target = _two_stack_target()
    nikon_stack = target.stacking.stacks_by_configuration["nikon@300mm"]

    pipelines.astrometry.run(target, nikon_stack)
    pipelines.photometry.run(target, stack=nikon_stack)

    astrometry_call = next(call for call in recorder.calls if call["pipeline_type"] == "astrometry")
    assert astrometry_call["path"] == "/no/such/nikon300_Stacked.fits"
    assert [path for path in _photometry_paths(recorder) if "spec" not in path] == ["/nikon300.fits"]


def test_photometry_refuses_frames_of_two_cameras_with_their_counts(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify a frame list from two cameras is refused before any work."""
    pipelines, recorder = setups_pipelines
    target = _mixed_target()

    with pytest.raises(InvalidArgumentError, match="more than one camera") as raised:
        pipelines.photometry.run(target, frames=target.frames[:3])
    assert _NIKON in str(raised.value)
    assert _ASI in str(raised.value)
    assert recorder.calls == []


def test_spectroscopy_runs_on_the_spectra_of_the_analyzed_camera(
    setups_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify spectroscopy runs for the ASI533 spectra."""
    pipelines, _ = setups_pipelines

    ran = pipelines.spectroscopy.run(_mixed_target())

    assert ran.results["spectroscopy"]["pipeline_type"] == "spectroscopy"


def test_solve_returns_the_center_scale_and_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """`astrometry.solve` asks the star identifier to solve and describes."""
    from astropy.wcs import WCS

    from astrometricslib.pipelines.astrometry.processing import star_identifier

    calls: list[tuple[str, bool]] = []
    solved = WCS(naxis=2)
    solved.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    solved.wcs.crval = [150.0, 30.0]
    solved.wcs.crpix = [50.5, 40.5]
    solved.wcs.cdelt = [-2 / 3600, 2 / 3600]
    solved.pixel_shape = (100, 80)

    class FakeIdentifier:
        """Stands in for `StarIdentifier` and records each request."""

        def __init__(self, config: object, drivers: object = None) -> None:
            """Accept the configuration and drivers the catalog passes."""

        def process_image(self, path: str, attempt_plate_solving: bool) -> tuple[list, object]:
            """Record the call and return a made-up solution.

            Returns
            -------
            result : `tuple`
                No stars, and the made-up solution.
            """
            calls.append((path, attempt_plate_solving))
            return [], solved

    monkeypatch.setattr(star_identifier, "StarIdentifier", FakeIdentifier)

    solution = ProcessingPipelines(AppConfiguration(), MagicMock()).astrometry.solve("/frames/one.fits")

    assert (solution.ra_deg, solution.dec_deg) == pytest.approx((150.0, 30.0))
    assert solution.pixel_scale_arcsec == pytest.approx(2.0)
    assert solution.rotation_deg == pytest.approx(0.0)
    assert (solution.width, solution.height) == (100, 80)
    assert calls == [("/frames/one.fits", True)]
