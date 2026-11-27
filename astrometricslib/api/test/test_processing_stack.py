"""Tests for `processing.stacking.run` and `predict`: frames and stacking.

The stacking stage itself is faked. These tests check which frames are
stacked (every imaging frame by default, or the exact frames given), that a
frame list from two cameras, or of two kinds, is refused with its counts, that
a public run makes no preview picture, that a finished stack is saved, and
that `predict` chooses the same frames as `run`.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from astrometricslib import FrameRecord, ProcessingPipelines, Target
from astrometricslib.arguments import Page, check_frames
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.storage import process_locks
from astrometricslib.pipelines.stacking import stack_prediction
from astrometricslib.pipelines.stacking import stage as stacking_stage


def make_frame(name: str, filter_name: str = "Luminance", camera: str = "Unknown") -> FrameRecord:
    """Make a 60 second light frame.

    Returns
    -------
    frame : `FrameRecord`
        The frame, with the file name and camera given.
    """
    return FrameRecord(
        path=f"/lights/{name}",
        role="LIGHT",
        filter=filter_name,
        exposure="60",
        timestamp=1000.0,
        camera=camera,
    )


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch) -> tuple[ProcessingPipelines, SimpleNamespace]:
    """Build a `ProcessingPipelines` whose stacker and catalog are fakes.

    Returns
    -------
    pipelines, record : `ProcessingPipelines`, `types.SimpleNamespace`
        The object under test and a record of what the fakes were asked.
    """
    record = SimpleNamespace(stacked_with=None, saved=0, forced=None, made_preview=None)

    def stack_frames(
        target: Target,
        frames_to_stack: list,
        force: bool = False,
        make_preview: bool = True,
        **_options: object,
    ) -> str:
        """Pretend to stack.

        Returns
        -------
        path : `str`
            A made-up output path.
        """
        record.stacked_with = [frame.path for frame in frames_to_stack]
        record.forced = force
        record.made_preview = make_preview
        return "/stacks/T.fits"

    def save() -> None:
        """Count a save."""
        record.saved += 1

    monkeypatch.setattr(stacking_stage, "stack_frames", stack_frames)
    monkeypatch.setattr(process_locks, "acquire_resource_slot", lambda *_args, **_kwargs: nullcontext())
    config = AppConfiguration()
    pipelines = ProcessingPipelines(
        config, None, targets=SimpleNamespace(_save=save, _config=config, get=lambda *_: None)
    )
    return pipelines, record


def make_target() -> Target:
    """Make a target with imaging and spectral frames.

    Returns
    -------
    target : `Target`
        Frames L_001 to L_003, one red frame and one spectrum.
    """
    frames = [make_frame(f"T_Light_Luminance_{number:03d}.fits") for number in (1, 2, 3)]
    frames.append(make_frame("T_Light_Red_001.fits", "Red"))
    frames.append(make_frame("T_Light_Spectroscopy_001.fits", "Spectroscopy"))
    return Target(id="T 1", frames=frames)


def make_two_camera_target() -> Target:
    """Make a target with spectra from two cameras, more from the Nikon.

    Returns
    -------
    target : `Target`
        Three Nikon spectra and two ASI spectra.
    """
    frames = [
        make_frame(f"T_Nikon_{number}.fits", "Spectroscopy", "Nikon DSLR DSC D5300") for number in (1, 2, 3)
    ]
    frames += [make_frame(f"T_Asi_{number}.fits", "Spectroscopy", "ZWO ASI 533MM Pro") for number in (1, 2)]
    return Target(id="T 2", frames=frames)


def test_without_frames_every_imaging_frame_is_stacked_and_saved(library: tuple) -> None:
    """The spectrum is left out; the stack is saved and makes no picture."""
    pipelines, record = library
    result = pipelines.stacking.run(make_target())
    assert record.stacked_with == [
        "/lights/T_Light_Luminance_001.fits",
        "/lights/T_Light_Luminance_002.fits",
        "/lights/T_Light_Luminance_003.fits",
        "/lights/T_Light_Red_001.fits",
    ]
    assert result.stacked_path == "/stacks/T.fits"
    assert result.kind == "imaging"
    assert record.saved == 1
    assert record.made_preview is False


def test_exact_frames_are_stacked_as_given(library: tuple) -> None:
    """``frames`` stacks exactly those frames; ``force`` reaches the stage."""
    pipelines, record = library
    target = make_target()
    pipelines.stacking.run(target, frames=target.frames[:2], force=True)
    assert record.stacked_with == [frame.path for frame in target.frames[:2]]
    assert record.forced is True


def test_spectral_frames_make_a_spectral_stack(library: tuple) -> None:
    """A list of spectra is stacked as a spectral stack."""
    pipelines, record = library
    target = make_target()
    result = pipelines.stacking.run(target, frames=[target.frames[4]])
    assert record.stacked_with == ["/lights/T_Light_Spectroscopy_001.fits"]
    assert result.kind == "spectral"


def test_frames_given_as_paths_are_matched_to_the_target(library: tuple) -> None:
    """A list of paths is matched to the target's frame records."""
    pipelines, record = library
    target = make_target()
    pipelines.stacking.run(target, frames=[frame.path for frame in target.frames[1:3]])
    assert record.stacked_with == [frame.path for frame in target.frames[1:3]]


def test_a_target_with_no_imaging_frames_is_an_error_not_a_stack(library: tuple) -> None:
    """With no frames given and no imaging frame, nothing is stacked."""
    pipelines, record = library
    with pytest.raises(NotFoundError, match="No imaging light frames"):
        pipelines.stacking.run(make_two_camera_target())
    assert record.stacked_with is None


def test_frames_from_two_cameras_are_never_stacked_together(library: tuple) -> None:
    """A frame list from two cameras is refused, naming each camera."""
    pipelines, record = library
    target = make_two_camera_target()
    with pytest.raises(InvalidArgumentError, match="more than one camera") as raised:
        pipelines.stacking.run(target, frames=target.frames)
    assert "Nikon DSLR DSC D5300" in str(raised.value)
    assert "ZWO ASI 533MM Pro" in str(raised.value)
    assert record.stacked_with is None


def test_a_frame_list_that_mixes_imaging_and_spectral_frames_is_refused_with_counts(library: tuple) -> None:
    """A mixed list is refused before stacking, with counts of both kinds."""
    pipelines, record = library
    target = make_target()
    mixed = [*target.frames[:3], target.frames[4]]
    with pytest.raises(InvalidArgumentError, match=r"3 imaging, 1 spectral") as raised:
        pipelines.stacking.run(target, frames=mixed)
    assert raised.value.details == {"imaging_frames": 3, "spectral_frames": 1}
    assert record.stacked_with is None
    assert record.saved == 0


def test_a_page_cut_short_is_refused(library: tuple) -> None:
    """A page that holds only part of a query's matches is never stacked."""
    pipelines, record = library
    target = make_target()
    with pytest.raises(InvalidArgumentError, match="cut short"):
        pipelines.stacking.run(target, frames=Page.of(target.frames[:3], limit=2))
    assert record.stacked_with is None


def test_a_name_that_is_not_a_target_is_refused(library: tuple) -> None:
    """The subject is a `Target`; an id goes through ``targets.get`` first."""
    pipelines, _ = library
    with pytest.raises(InvalidArgumentError, match="Target itself"):
        pipelines.stacking.run("T 1")


def test_predict_chooses_the_frames_a_run_would_stack(
    library: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`predict` hands the same frames to the prediction and stacks nothing."""
    pipelines, record = library
    asked = {}

    def predict_stack(target: Target, chosen: list, kind: str, calibration: object) -> str:
        """Note what the prediction was given.

        Returns
        -------
        answer : `str`
            A stand-in prediction.
        """
        asked.update(paths=[frame.path for frame in chosen], kind=kind)
        return "predicted"

    monkeypatch.setattr(stack_prediction, "predict_stack", predict_stack)
    assert pipelines.stacking.predict(make_target()) == "predicted"
    assert asked == {
        "paths": [
            "/lights/T_Light_Luminance_001.fits",
            "/lights/T_Light_Luminance_002.fits",
            "/lights/T_Light_Luminance_003.fits",
            "/lights/T_Light_Red_001.fits",
        ],
        "kind": "imaging",
    }
    assert record.stacked_with is None


def test_check_frames_refuses_what_a_run_would_refuse_without_reading_a_frame() -> None:
    """The public frame check gives the records back, or raises with counts."""
    target = make_target()
    assert check_frames(target.frames[:2], target) == target.frames[:2]
    with pytest.raises(InvalidArgumentError, match=r"3 imaging, 1 spectral"):
        check_frames([*target.frames[:3], target.frames[4]], target)
    with pytest.raises(InvalidArgumentError, match="empty"):
        check_frames([], target)
