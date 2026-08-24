"""Tests for never stacking frames from two different optics together.

Optics of different focal length image at different scales -- this
library's 300mm lens and 405mm telescope differ by 1.35x -- so a stack
blending them has no single pixel scale, cannot be plate solved
accurately, and yields fluxes that are not comparable between frames.

Seven targets were being stacked that way before this existed, worst
NGC 7023 with 424 frames at 300mm mixed with 111 at 405mm. The symptoms
matched: astrometric residuals near the 5.77 arcsec that random matching
would give, and light curves scattering at 0.4 mag where the one
single-optic target managed 0.031.
"""

import pytest

from astrometricslib.tasks.target_tasks.pipeline_tasks import (
    frame_configuration_key,
    frames_missing_focal_length,
    group_frames_by_configuration,
    select_frames_for_configuration,
)


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
        self.id = "OpticTestTarget"
        self.frames = frames


def test_a_configuration_names_both_camera_and_optic():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Either alone is insufficient to decide what may be stacked."""
    assert frame_configuration_key(_Frame("Nikon D5300", 300.0)) == "Nikon D5300@300mm"


def test_focal_length_is_keyed_to_whole_millimetres():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """405 and 405.0 are one optic, not two."""
    assert frame_configuration_key(_Frame("ASI533", 405.0)) == frame_configuration_key(_Frame("ASI533", 405))


def test_a_frame_without_a_focal_length_has_no_configuration():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Guessing an optic is exactly what must not happen."""
    assert frame_configuration_key(_Frame("Nikon D5300", None)) is None


def test_a_nonsense_focal_length_has_no_configuration():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Zero is a missing value written badly, not a real optic."""
    assert frame_configuration_key(_Frame("Nikon D5300", 0.0)) is None


def test_two_optics_form_two_groups():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The regression: NGC 7023's two optics must not share a stack."""
    frames = [_Frame(focal_length_mm=300.0) for _ in range(424)]
    frames += [_Frame(focal_length_mm=405.0) for _ in range(111)]

    grouped = group_frames_by_configuration(_Target(frames))

    assert len(grouped) == 2
    assert len(grouped["Nikon D5300@300mm"]) == 424
    assert len(grouped["Nikon D5300@405mm"]) == 111


def test_the_same_optic_on_two_cameras_stays_separate():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A shared focal length does not make two cameras interchangeable."""
    frames = [_Frame("ASI533", 405.0), _Frame("Nikon D5300", 405.0)]

    assert len(group_frames_by_configuration(_Target(frames))) == 2


def test_groups_are_ordered_largest_first():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The biggest group is the most useful default to reach for."""
    frames = [_Frame(focal_length_mm=405.0) for _ in range(3)]
    frames += [_Frame(focal_length_mm=300.0) for _ in range(9)]

    assert next(iter(group_frames_by_configuration(_Target(frames)))) == "Nikon D5300@300mm"


def test_grouping_can_be_restricted_to_one_camera():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The camera filter still applies on top of the optic split."""
    frames = [_Frame("ASI533", 405.0), _Frame("Nikon D5300", 405.0), _Frame("Nikon D5300", 300.0)]

    grouped = group_frames_by_configuration(_Target(frames), camera_name="Nikon")

    assert set(grouped) == {"Nikon D5300@405mm", "Nikon D5300@300mm"}


def test_unassignable_frames_are_reported_not_hidden():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Frames with no FOCALLEN must be reported, never lost silently."""
    frames = [_Frame(focal_length_mm=300.0), _Frame(focal_length_mm=None)]

    grouped = group_frames_by_configuration(_Target(frames))

    assert sum(len(group) for group in grouped.values()) == 1
    assert len(frames_missing_focal_length(_Target(frames))) == 1


def test_selecting_one_configuration_excludes_the_other():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A stack must receive one optic's frames and no others."""
    frames = [
        _Frame(focal_length_mm=300.0, path="/a.fits"),
        _Frame(focal_length_mm=405.0, path="/b.fits"),
        _Frame(focal_length_mm=300.0, path="/c.fits"),
    ]

    selected = select_frames_for_configuration(_Target(frames), "Nikon D5300@300mm")

    assert [frame.path for frame in selected] == ["/a.fits", "/c.fits"]


def test_a_single_optic_target_forms_one_group():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Most targets are unaffected and must not be split."""
    frames = [_Frame(focal_length_mm=405.0) for _ in range(20)]

    assert len(group_frames_by_configuration(_Target(frames))) == 1


def test_a_target_with_no_frames_groups_to_nothing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An empty target is not an error."""
    assert group_frames_by_configuration(_Target([])) == {}


def test_scale_difference_between_this_librarys_optics():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Documents why blending is wrong, not merely untidy.

    206.265 * pixel_size / focal_length gives 2.675 arcsec/px at 300mm
    and 1.981 at 405mm on this camera's 3.89 micron pixels.
    """
    scale_at_300 = 206.265 * 3.89 / 300
    scale_at_405 = 206.265 * 3.89 / 405

    assert scale_at_300 / scale_at_405 == pytest.approx(1.35, abs=0.01)
