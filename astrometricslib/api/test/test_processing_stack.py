"""Tests for `ProcessingPipelines.stack`, which chooses frames and stacks them.

The stacking stage itself is faked. These tests check which frames are
chosen (imaging against spectral, filter, file range, camera), that
`plan_only` changes nothing, that a finished stack is saved, and that a
request that cannot be met raises instead of returning an error.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from astrometricslib import FrameRecord, InvalidArgumentError, NotFoundError, ProcessingPipelines, Target
from astrometricslib.foundation.config import AppConfiguration
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
    record = SimpleNamespace(stacked_with=None, saved=0, forced=None)

    def stack_frames(target: Target, frames_to_stack: list, force: bool = False, **_options: object) -> str:
        """Pretend to stack.

        Returns
        -------
        path : `str`
            A made-up output path.
        """
        record.stacked_with = [frame.path for frame in frames_to_stack]
        record.forced = force
        return "/stacks/T.fits"

    def save() -> None:
        """Count a save."""
        record.saved += 1

    monkeypatch.setattr(stacking_stage, "stack_frames", stack_frames)
    pipelines = ProcessingPipelines(
        AppConfiguration(), None, targets=SimpleNamespace(save=save, get=lambda *_: None)
    )
    monkeypatch.setattr(pipelines, "acquire_stacking_slot", nullcontext)
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


def test_imaging_stack_uses_only_imaging_frames_in_range(library: tuple) -> None:
    """The spectrum and the frames outside the range are left out."""
    pipelines, record = library
    result = pipelines.stack(
        make_target(), filter_name="L", first_file="002", last_file="003", register_job=False
    )
    assert record.stacked_with == ["/lights/T_Light_Luminance_002.fits", "/lights/T_Light_Luminance_003.fits"]
    assert result.stacked_path == "/stacks/T.fits"
    assert record.saved == 1


def test_spectral_stack_uses_only_spectra(library: tuple) -> None:
    """Asking for spectral frames picks the spectrum and nothing else."""
    pipelines, record = library
    pipelines.stack(make_target(), kind="spectral", register_job=False)
    assert record.stacked_with == ["/lights/T_Light_Spectroscopy_001.fits"]


def test_plan_only_stacks_and_saves_nothing(library: tuple) -> None:
    """The plan lists the frames and leaves everything else alone."""
    pipelines, record = library
    result = pipelines.stack(make_target(), plan_only=True, register_job=False)
    assert result.frames_selected == 4
    assert result.plan_only
    assert record.stacked_with is None
    assert record.saved == 0


def test_no_matching_frames_is_an_error_not_a_stack(library: tuple) -> None:
    """A selection that matches nothing raises and does not stack."""
    pipelines, record = library
    with pytest.raises(NotFoundError, match="No imaging light frames"):
        pipelines.stack(make_target(), filter_name="Ha", register_job=False)
    assert record.stacked_with is None


def test_a_wrong_frame_type_is_refused(library: tuple) -> None:
    """Only imaging and spectral are accepted."""
    pipelines, _ = library
    with pytest.raises(InvalidArgumentError, match="kind"):
        pipelines.stack(make_target(), kind="all", register_job=False)


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


def test_frames_from_two_cameras_are_never_stacked_together(library: tuple) -> None:
    """Without a camera named, a mixed target is refused and says why."""
    pipelines, record = library
    with pytest.raises(InvalidArgumentError, match="more than one camera") as raised:
        pipelines.stack(make_two_camera_target(), kind="spectral", register_job=False)
    assert "Nikon DSLR DSC D5300" in str(raised.value)
    assert "ZWO ASI 533MM Pro" in str(raised.value)
    assert record.stacked_with is None


def test_naming_the_camera_stacks_only_its_frames_even_when_it_has_fewer(library: tuple) -> None:
    """The two ASI frames are stacked, though the Nikon has more."""
    pipelines, record = library
    result = pipelines.stack(
        make_two_camera_target(), kind="spectral", camera_id="ASI 533MM", register_job=False
    )
    assert record.stacked_with == ["/lights/T_Asi_1.fits", "/lights/T_Asi_2.fits"]
    assert result.camera == "ZWO ASI 533MM Pro"


def test_a_camera_that_took_none_of_the_frames_is_an_error(library: tuple) -> None:
    """Naming a camera that is not there lists the cameras that are."""
    pipelines, record = library
    with pytest.raises(NotFoundError, match="No frames from a camera matching") as raised:
        pipelines.stack(make_two_camera_target(), kind="spectral", camera_id="QHY", register_job=False)
    assert "Nikon DSLR DSC D5300" in str(raised.value)
    assert record.stacked_with is None


def test_exact_frames_cannot_be_combined_with_a_frame_choice(library: tuple) -> None:
    """Passing ``frames`` and a filter at once is refused."""
    pipelines, record = library
    target = make_target()
    with pytest.raises(InvalidArgumentError, match="not both"):
        pipelines.stack(target, frames=target.frames[:2], filter_name="L", register_job=False)
    assert record.stacked_with is None


def test_exact_frames_are_stacked_as_given(library: tuple) -> None:
    """Passing ``frames`` stacks exactly those frames."""
    pipelines, record = library
    target = make_target()
    pipelines.stack(target, frames=target.frames[:2], register_job=False)
    assert record.stacked_with == [frame.path for frame in target.frames[:2]]
