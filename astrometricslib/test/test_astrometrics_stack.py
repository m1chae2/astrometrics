"""Tests for `Astrometrics.stack`, the AI-callable stack of chosen frames.

The stacking stage itself is faked. These tests check which frames are
chosen (imaging against spectral, filter, file range), that `plan_only`
changes nothing, and that a finished stack is saved.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from astrometricslib import Astrometrics, FrameRecord, Target


def make_frame(name: str, filter_name: str = "Luminance") -> FrameRecord:
    """Make a 60 second light frame.

    Returns
    -------
    frame : `FrameRecord`
        The frame, with the file name given.
    """
    return FrameRecord(
        path=f"/lights/{name}", role="LIGHT", filter=filter_name, exposure="60", timestamp=1000.0
    )


@pytest.fixture
def library() -> tuple[Astrometrics, SimpleNamespace]:
    """Build an `Astrometrics` whose stacker and catalog are fakes.

    Returns
    -------
    astrometrics, record : `Astrometrics`, `types.SimpleNamespace`
        The object under test and a record of what the fakes were asked.
    """
    record = SimpleNamespace(stacked_with=None, saved=0, forced=None)

    def run_stacking(target: Target, frames_to_stack: list, force: bool = False) -> str:
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

    astrometrics = Astrometrics.__new__(Astrometrics)
    astrometrics.processing = SimpleNamespace(
        acquire_stacking_slot=lambda: nullcontext(), run_stacking=run_stacking
    )
    astrometrics.targets = SimpleNamespace(save=save)
    return astrometrics, record


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
    astrometrics, record = library
    result = astrometrics.stack(make_target(), filter_name="L", first_file="002", last_file="003")
    assert record.stacked_with == ["/lights/T_Light_Luminance_002.fits", "/lights/T_Light_Luminance_003.fits"]
    assert result["stacked_path"] == "/stacks/T.fits"
    assert record.saved == 1


def test_spectral_stack_uses_only_spectra(library: tuple) -> None:
    """Asking for spectral frames picks the spectrum and nothing else."""
    astrometrics, record = library
    astrometrics.stack(make_target(), frame_type="spectral")
    assert record.stacked_with == ["/lights/T_Light_Spectroscopy_001.fits"]


def test_plan_only_stacks_and_saves_nothing(library: tuple) -> None:
    """The plan lists the frames and leaves everything else alone."""
    astrometrics, record = library
    result = astrometrics.stack(make_target(), plan_only=True)
    assert result["frames_selected"] == 4
    assert record.stacked_with is None
    assert record.saved == 0


def test_no_matching_frames_is_an_error_not_a_stack(library: tuple) -> None:
    """A selection that matches nothing reports it and does not stack."""
    astrometrics, record = library
    result = astrometrics.stack(make_target(), filter_name="Ha")
    assert "No imaging light frames" in result["error"]
    assert record.stacked_with is None


def test_a_wrong_frame_type_is_refused(library: tuple) -> None:
    """Only imaging and spectral are accepted."""
    astrometrics, _ = library
    assert "frame_type" in astrometrics.stack(make_target(), frame_type="all")["error"]
