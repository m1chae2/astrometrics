"""Tests for showing a stack's preview as its target's processed image.

The image viewer shows a target's processed image first. These tests check
that an automatic preview fills that slot when it is free, and that it never
replaces a picture a person attached or one that belongs to another stack.
"""

from types import SimpleNamespace

from astrometricslib.pipelines.shared.stack_preview_path import is_preview_path
from astrometricslib.pipelines.stacking.stack_preview import record_preview_as_processed_image

_STACK = "/lib/M 13/M_13_L_Stacked.fits"
_PREVIEW = "/lib/M 13/M_13_L_Stacked_preview.jpg"


def _target(stacked: str = _STACK, processed: str = "") -> SimpleNamespace:
    """Make a target stand-in with an imaging and a spectroscopy stack.

    Returns
    -------
    target : `types.SimpleNamespace`
        A target whose imaging stack is `stacked` and whose imaging processed
        image is `processed`.
    """
    return SimpleNamespace(
        stacking=SimpleNamespace(stacked_image=stacked, processed_image=processed),
        spectral_stacking=SimpleNamespace(stacked_image="/lib/M 13/M_13_SPEC.fits", processed_image=""),
    )


def test_an_empty_processed_image_takes_the_preview() -> None:
    """Verify the preview is recorded when nothing is attached."""
    target = _target()

    assert record_preview_as_processed_image(target, False, _STACK, _PREVIEW)
    assert target.stacking.processed_image == _PREVIEW


def test_a_picture_a_person_attached_is_left_alone() -> None:
    """Verify a hand-processed picture is never replaced by a preview."""
    target = _target(processed="/home/me/Pictures/M13_final.jpg")

    assert not record_preview_as_processed_image(target, False, _STACK, _PREVIEW)
    assert target.stacking.processed_image == "/home/me/Pictures/M13_final.jpg"


def test_a_picture_a_person_attached_is_replaced_only_when_asked() -> None:
    """Verify the explicit option replaces an attached picture."""
    target = _target(processed="/home/me/Pictures/M13_final.jpg")

    assert record_preview_as_processed_image(target, False, _STACK, _PREVIEW, replace_attached=True)
    assert target.stacking.processed_image == _PREVIEW


def test_an_earlier_automatic_preview_is_replaced() -> None:
    """Verify a stale preview of an older stack is updated."""
    target = _target(processed="/lib/M 13/M_13_L_Stacked_OLD_preview.jpg")

    assert record_preview_as_processed_image(target, False, _STACK, _PREVIEW)
    assert target.stacking.processed_image == _PREVIEW


def test_a_stack_the_target_does_not_show_is_left_alone() -> None:
    """Verify a preview of another telescope setup's stack is not recorded."""
    target = _target(stacked="/lib/M 13/M_13_L_Stacked_300mm.fits")

    assert not record_preview_as_processed_image(target, False, _STACK, _PREVIEW)
    assert target.stacking.processed_image == ""


def test_the_spectroscopy_stack_records_on_its_own_slot() -> None:
    """Verify a spectral preview goes to the spectroscopy processed image."""
    target = _target()
    spectral = "/lib/M 13/M_13_SPEC.fits"
    preview = "/lib/M 13/M_13_SPEC_preview.jpg"

    assert record_preview_as_processed_image(target, True, spectral, preview)
    assert target.spectral_stacking.processed_image == preview
    assert target.stacking.processed_image == ""


def test_only_preview_names_are_recognised() -> None:
    """Verify the name check matches previews, not a person's pictures."""
    assert is_preview_path(_PREVIEW)
    assert is_preview_path("/x/Y_preview.JPG")
    assert not is_preview_path("/home/me/Pictures/M13_final.jpg")
    assert not is_preview_path("/lib/M 13/M_13_L_Stacked_preview.png")
