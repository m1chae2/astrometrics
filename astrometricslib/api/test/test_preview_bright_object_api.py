"""Tests for the two public calls that choose the masked stretch's object.

``list_preview_bright_objects`` lists what SIMBAD places inside a stack and
``set_preview_bright_object`` saves the user's choice on the target. A
stand-in SIMBAD driver and a stand-in target catalog keep the tests offline.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.processing_results import BrightObjectChoice, BrightObjectChoices
from astrometricslib.models.target import Target


def make_pipelines(simbad: MagicMock | None = None) -> tuple[ProcessingPipelines, MagicMock]:
    """Build pipelines with a stand-in target catalog.

    Parameters
    ----------
    simbad : `unittest.mock.MagicMock`, optional
        The SIMBAD driver to use.

    Returns
    -------
    pipelines, targets : `tuple`
        The pipelines and the stand-in catalog they save through.
    """
    targets = MagicMock()
    return ProcessingPipelines(
        AppConfiguration(), MagicMock(), targets=targets, drivers=Drivers(simbad=simbad)
    ), targets


def test_the_choice_is_saved_on_the_target_and_the_catalog_is_saved() -> None:
    """An object choice keeps the name, position and size."""
    pipelines, targets = make_pipelines()
    target = Target(id="M 52")

    saved = pipelines.set_preview_bright_object(
        target, "object", simbad_id="NGC  7635", ra_deg=350.2, dec_deg=61.2, major_axis_arcmin=9.0
    )

    assert target.preview_bright_object == saved
    assert (saved.mode, saved.simbad_id, saved.ra_deg, saved.dec_deg, saved.major_axis_arcmin) == (
        "object",
        "NGC  7635",
        350.2,
        61.2,
        9.0,
    )
    targets.add.assert_called_once_with(target)


def test_off_and_automatic_clear_the_object() -> None:
    """Going back to Off or Automatic drops the saved object."""
    pipelines, _ = make_pipelines()
    target = Target(id="M 52")
    pipelines.set_preview_bright_object(target, "object", simbad_id="X", ra_deg=1.0, dec_deg=2.0)

    pipelines.set_preview_bright_object(target, "off")
    assert (target.preview_bright_object.mode, target.preview_bright_object.simbad_id) == ("off", None)
    pipelines.set_preview_bright_object(target, "auto")
    assert target.preview_bright_object.mode == "auto"


@pytest.mark.parametrize(
    "arguments",
    [
        {"mode": "sometimes"},
        {"mode": "object"},
        {"mode": "object", "simbad_id": "X", "ra_deg": 400.0, "dec_deg": 0.0},
        {"mode": "object", "simbad_id": "X", "ra_deg": 10.0, "dec_deg": 0.0, "major_axis_arcmin": -1.0},
    ],
)
def test_a_bad_choice_is_refused_and_nothing_is_saved(arguments: dict) -> None:
    """A bad mode, a missing position or a bad size raises."""
    pipelines, targets = make_pipelines()

    with pytest.raises(InvalidArgumentError):
        pipelines.set_preview_bright_object(Target(id="M 52"), **arguments)
    targets.add.assert_not_called()


def test_a_target_with_no_wcs_in_its_stack_is_told_to_run_astrometry(tmp_path: Path) -> None:
    """The list call gives the message and does not ask SIMBAD."""
    stack = tmp_path / "stack.fits"
    fits.PrimaryHDU(np.zeros((20, 20), dtype=np.float32)).writeto(stack)
    simbad = MagicMock()
    pipelines, _ = make_pipelines(simbad)
    target = Target(id="M 52")
    target.stacking.stacked_image = str(stack)

    result = pipelines.list_preview_bright_objects(target)

    assert result.objects == []
    assert result.message == "Run Astrometry first to list objects in the frame"
    simbad.query_tap.assert_not_called()


def test_the_list_carries_the_saved_choice(tmp_path: Path) -> None:
    """The list call says which choice the target has saved."""
    stack = tmp_path / "stack.fits"
    fits.PrimaryHDU(np.zeros((20, 20), dtype=np.float32)).writeto(stack)
    pipelines, _ = make_pipelines(MagicMock())
    target = Target(id="M 52")
    target.stacking.stacked_image = str(stack)
    pipelines.set_preview_bright_object(target, "off")

    result = pipelines.list_preview_bright_objects(target)

    assert result.current is not None
    assert result.current.mode == "off"


def listing_with_nebula() -> BrightObjectChoices:
    """Build a stack's list holding one nebula.

    Returns
    -------
    listing : `BrightObjectChoices`
        The list, as ``list_preview_bright_objects`` gives it.
    """
    nebula = BrightObjectChoice(
        simbad_id="NGC 7635",
        object_type="HII",
        category="nebula",
        ra_deg=350.2,
        dec_deg=61.2,
        major_axis_arcmin=15.0,
    )
    return BrightObjectChoices(target_id="M 52", objects=[nebula])


@pytest.mark.parametrize("name", ["ngc7635", "NGC  7635", "NAME NGC_7635"])
def test_an_object_name_in_any_spelling_saves_the_listed_object(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A name is found in the stack's list; its position and size are saved."""
    pipelines, _ = make_pipelines()
    monkeypatch.setattr(pipelines, "list_preview_bright_objects", lambda target: listing_with_nebula())
    target = Target(id="M 52")

    saved = pipelines.set_preview_bright_object(target, "object", name=name)

    assert (saved.simbad_id, saved.ra_deg, saved.dec_deg, saved.major_axis_arcmin) == (
        "NGC 7635",
        350.2,
        61.2,
        15.0,
    )


def test_a_name_not_in_the_list_is_refused_with_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown name raises, names the listed objects, and saves nothing."""
    pipelines, targets = make_pipelines()
    monkeypatch.setattr(pipelines, "list_preview_bright_objects", lambda target: listing_with_nebula())

    with pytest.raises(InvalidArgumentError, match="NGC 7635"):
        pipelines.set_preview_bright_object(Target(id="M 52"), "object", name="M 31")
    targets.add.assert_not_called()
