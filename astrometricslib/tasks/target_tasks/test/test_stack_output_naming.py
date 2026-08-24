"""Tests for giving each optic's stack its own file.

Splitting a target by optic runs the stacker once per configuration,
and every run wrote to the same ``<target>_<filter>_Stacked.fits``.
The second pass overwrote the first: M 27 finished with two recorded
configurations, 21 frames at 405mm and 105 at 300mm, both naming one
file that held only the 300mm result.
"""

from astrometricslib.tasks.target_tasks.stacking_tasks import _disambiguating_configuration_tag


class _Frame:
    """A frame record stand-in carrying camera, optic, and role."""

    def __init__(self, camera="Nikon D5300", focal_length_mm=300.0, role="LIGHT", path="/f.fits"):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.camera = camera
        self.focal_length_mm = focal_length_mm
        self.role = role
        self.path = path


class _Target:
    """A target stand-in exposing only the frames list."""

    def __init__(self, frames):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = "NamingTestTarget"
        self.frames = frames


def test_a_single_optic_target_keeps_its_existing_path():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Most targets must not be renamed, or their stacks are orphaned."""
    frames = [_Frame() for _ in range(20)]

    assert _disambiguating_configuration_tag(_Target(frames), frames) == ""


def test_each_optic_of_a_two_optic_target_gets_its_own_tag():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The regression: two stacks must not share one filename."""
    at_300 = [_Frame(focal_length_mm=300.0) for _ in range(105)]
    at_405 = [_Frame(focal_length_mm=405.0) for _ in range(21)]
    target = _Target(at_300 + at_405)

    tag_300 = _disambiguating_configuration_tag(target, at_300)
    tag_405 = _disambiguating_configuration_tag(target, at_405)

    assert tag_300 and tag_405
    assert tag_300 != tag_405


def test_a_tag_is_filename_safe():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Camera names carry spaces, and configuration keys carry an '@'."""
    at_300 = [_Frame(camera="Nikon DSLR DSC D5300", focal_length_mm=300.0)]
    at_405 = [_Frame(camera="Nikon DSLR DSC D5300", focal_length_mm=405.0)]

    tag = _disambiguating_configuration_tag(_Target(at_300 + at_405), at_405)

    assert tag == "Nikon_DSLR_DSC_D5300_405mm"
    assert all(character.isalnum() or character == "_" for character in tag)


def test_two_cameras_at_one_focal_length_still_differ():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Tagging by focal length alone would collide here."""
    asi = [_Frame(camera="ZWO ASI533MM Pro", focal_length_mm=405.0)]
    nikon = [_Frame(camera="Nikon DSLR DSC D5300", focal_length_mm=405.0)]
    target = _Target(asi + nikon)

    assert _disambiguating_configuration_tag(target, asi) != _disambiguating_configuration_tag(target, nikon)


def test_a_blended_stack_is_not_named_after_one_optic():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Naming a blend after either optic would assert something untrue."""
    at_300 = [_Frame(focal_length_mm=300.0)]
    at_405 = [_Frame(focal_length_mm=405.0)]

    assert _disambiguating_configuration_tag(_Target(at_300 + at_405), at_300 + at_405) == ""


def test_frames_without_a_focal_length_are_not_tagged():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An unlabelled optic has no configuration to name."""
    unlabelled = [_Frame(focal_length_mm=None)]
    at_405 = [_Frame(focal_length_mm=405.0)]
    at_300 = [_Frame(focal_length_mm=300.0)]

    assert _disambiguating_configuration_tag(_Target(unlabelled + at_405 + at_300), unlabelled) == ""
