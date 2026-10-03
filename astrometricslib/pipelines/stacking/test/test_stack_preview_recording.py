"""Tests for showing a stack's preview as its target's processed image.

The image viewer shows a target's processed image first. These tests check
that an automatic preview fills that slot, replaces a picture a person
attached unless told to keep it, and never touches one that belongs to
another stack.
"""

from pathlib import Path
from types import SimpleNamespace

from astrometricslib.pipelines.shared.stack_preview_path import (
    is_preview_path,
    is_processed_fits_path,
    processed_fits_path_for,
)
from astrometricslib.pipelines.stacking.post_processing.stack_preview import record_preview_as_processed_image

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


def test_a_picture_a_person_attached_is_replaced() -> None:
    """Verify the new picture replaces an attached one, by default."""
    target = _target(processed="/home/me/Pictures/M13_final.jpg")

    assert record_preview_as_processed_image(target, False, _STACK, _PREVIEW)
    assert target.stacking.processed_image == _PREVIEW


def test_a_picture_a_person_attached_is_kept_only_when_asked() -> None:
    """Verify the explicit option keeps an attached picture."""
    target = _target(processed="/home/me/Pictures/M13_final.jpg")

    assert not record_preview_as_processed_image(target, False, _STACK, _PREVIEW, keep_attached=True)
    assert target.stacking.processed_image == "/home/me/Pictures/M13_final.jpg"


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


def test_the_stretched_fits_is_recorded_when_it_exists(tmp_path: Path) -> None:
    """Verify the FITS picture is preferred over the JPEG."""
    stack = str(tmp_path / "M_13_L_Stacked.fits")
    preview = str(tmp_path / "M_13_L_Stacked_preview.jpg")
    processed = Path(processed_fits_path_for(stack))
    processed.write_bytes(b"fits")
    target = _target(stacked=stack)

    assert record_preview_as_processed_image(target, False, stack, preview)
    assert target.stacking.processed_image == str(processed)


def test_an_earlier_stretched_fits_is_replaced_but_a_persons_picture_is_not(tmp_path: Path) -> None:
    """Verify automatic FITS pictures count as automatic."""
    stack = str(tmp_path / "M_13_L_Stacked.fits")
    preview = str(tmp_path / "M_13_L_Stacked_preview.jpg")
    target = _target(stacked=stack, processed=str(tmp_path / "M_13_L_Stacked_OLD_processed.fits"))

    assert record_preview_as_processed_image(target, False, stack, preview)
    assert target.stacking.processed_image == preview


def test_only_stretched_fits_names_are_recognised() -> None:
    """Verify the FITS name check matches the automatic file only."""
    assert processed_fits_path_for("/x/M_13_L_Stacked.fits") == "/x/M_13_L_Stacked_processed.fits"
    assert is_processed_fits_path("/x/M_13_L_Stacked_processed.fits")
    assert not is_processed_fits_path("/x/M_13_L_Stacked.fits")
    assert not is_processed_fits_path("/x/M_13_L_Stacked_processed.jpg")
