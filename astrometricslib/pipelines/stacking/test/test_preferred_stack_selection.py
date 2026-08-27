"""Tests for which configuration a target's `stacked_image` points at.

A target imaged through more than one camera or optic produces a stack
per configuration, but `stacked_image` names exactly one so that every
existing reader and the UI keep working. Which one it names has to be
deterministic: assigning it on every successful stack made it whichever
pass happened to run last, so a run over both optics left it pointing at
the non-primary one.
"""

import pytest

from astrometricslib.pipelines.stacking import stage as stacking_tasks
from astrometricslib.pipelines.stacking.stage import (
    _camera_names_match,
    _record_configuration_stack,
)

PRIMARY_CAMERA = "ZWO ASI533MM Pro"
PRIMARY_FOCAL_MM = 405.0


class _Frame:
    """A frame record stand-in carrying camera and optic."""

    def __init__(self, camera=PRIMARY_CAMERA, focal_length_mm=PRIMARY_FOCAL_MM):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.camera = camera
        self.focal_length_mm = focal_length_mm


class _Target:
    """A target stand-in holding per-configuration stacks."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        self.id = "PreferenceTestTarget"
        self.stacks_by_configuration = {}
        self.stacked_image = ""


@pytest.fixture(autouse=True)
def configured_primary(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Pin the primary camera and optic for every test here."""

    class _Configuration:
        def get_primary_camera_name(self) -> str:
            """Return the pinned primary camera.

            Returns
            -------
            camera_name : `str`
                The primary camera name.
            """
            return PRIMARY_CAMERA

        def get_primary_focal_length_mm(self) -> float:
            """Return the pinned primary focal length.

            Returns
            -------
            focal_length_mm : `float`
                The primary focal length in millimetres.
            """
            return PRIMARY_FOCAL_MM

    monkeypatch.setattr("astrometricslib.utilities.config_loader.get_configuration", lambda: _Configuration())


def test_the_primary_configuration_becomes_the_stacked_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The observatory's own camera and optic is what a reader should see."""
    target = _Target()

    assert _record_configuration_stack(target, [_Frame()], "/primary.fits") is True
    assert target.stacks_by_configuration["ZWO ASI533MM Pro@405mm"].is_preferred is True


def test_a_non_primary_optic_does_not_displace_the_primary():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The regression: a later pass must not overwrite the preferred stack.

    Running both optics assigned stacked_image on every success, so the
    second pass won regardless of preference.
    """
    target = _Target()
    _record_configuration_stack(target, [_Frame()], "/primary.fits")

    displaced = _record_configuration_stack(target, [_Frame(focal_length_mm=300.0)], "/secondary.fits")

    assert displaced is False
    assert len(target.stacks_by_configuration) == 2


def test_a_non_primary_camera_does_not_displace_the_primary():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Same optic on another camera is still a different configuration."""
    target = _Target()
    _record_configuration_stack(target, [_Frame()], "/primary.fits")

    displaced = _record_configuration_stack(
        target, [_Frame(camera="Nikon DSLR DSC D5300")], "/other_camera.fits"
    )

    assert displaced is False


def test_the_only_stack_wins_when_nothing_is_preferred():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A target never imaged with the primary rig still needs a stack.

    Most of this catalog's DSLR targets are in exactly this position.
    """
    target = _Target()

    assert (
        _record_configuration_stack(
            target, [_Frame(camera="Nikon DSLR DSC D5300", focal_length_mm=300.0)], "/only.fits"
        )
        is True
    )


def test_the_primary_claims_it_back_when_it_arrives_later():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Pass order must not decide the outcome."""
    target = _Target()
    _record_configuration_stack(target, [_Frame(focal_length_mm=300.0)], "/secondary.fits")

    assert _record_configuration_stack(target, [_Frame()], "/primary.fits") is True


def test_a_second_non_primary_still_sets_it_when_none_is_preferred():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """With no primary present, any stack is better than none."""
    target = _Target()
    _record_configuration_stack(target, [_Frame(camera="Nikon", focal_length_mm=300.0)], "/a.fits")

    assert (
        _record_configuration_stack(target, [_Frame(camera="Nikon", focal_length_mm=200.0)], "/b.fits")
        is True
    )


def test_a_blended_stack_is_not_recorded_but_still_sets_the_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Recording a mixed-optic stack under one key would assert a falsehood.

    It is still the only stack there is, so it must not leave the target
    with nothing.
    """
    target = _Target()
    frames = [_Frame(focal_length_mm=405.0), _Frame(focal_length_mm=300.0)]

    assert _record_configuration_stack(target, frames, "/blended.fits") is True
    assert target.stacks_by_configuration == {}


def test_frames_without_a_focal_length_still_set_the_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An unlabelled optic cannot be keyed, but the stack still exists."""
    target = _Target()

    assert _record_configuration_stack(target, [_Frame(focal_length_mm=None)], "/unknown.fits") is True


def test_camera_names_match_across_spelling_differences():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Configuration and FITS headers spell the same camera differently."""
    assert _camera_names_match("ZWO ASI533MM Pro", "ZWO ASI 533MM Pro")
    assert _camera_names_match("zwo asi533mm pro", "ZWO ASI533MM Pro")
    assert not _camera_names_match("ZWO ASI533MM Pro", "Nikon DSLR DSC D5300")


def test_the_recorded_entry_carries_its_frame_count():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Frame count is how a reader judges which stack is worth using."""
    target = _Target()
    _record_configuration_stack(target, [_Frame() for _ in range(21)], "/primary.fits")

    assert target.stacks_by_configuration["ZWO ASI533MM Pro@405mm"].frames_stacked == 21


def test_module_exposes_the_recorder():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Guards against the helper being renamed out from under these tests."""
    assert hasattr(stacking_tasks, "_record_configuration_stack")
