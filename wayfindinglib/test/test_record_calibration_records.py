"""Purpose: Tests for storing a guider calibration and a focus model.

Description: ``control.guiding.create_guider_calibration`` and
``control.imaging.create_focus_model`` save records measured elsewhere.
They must refuse an id that is already saved unless ``force`` is true, and
they cap the id, the note and the numbers. The tests use a dictionary in
place of the library's butler, so nothing is written to a real library.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib import FocusModel, GuiderCalibration
from wayfindinglib.api.control.guiding import GuidingControl
from wayfindinglib.api.control.imaging import ImagingControl
from wayfindinglib.models.equipment_and_site.focus_model import FilterFocusOffset


class _Butler:
    """A stand-in butler that keeps records in a dictionary."""

    def __init__(self) -> None:
        """Start with no records."""
        self.records: dict[tuple[str, str], Any] = {}

    def get(self, dataset_type: str, selector: dict[str, Any]) -> Any:
        """Return the record or `None`.

        Returns
        -------
        record : `~typing.Any`
            The saved record, or `None`.
        """
        return self.records.get((dataset_type, selector["id"]))

    def put(self, record: Any, dataset_type: str, selector: dict[str, Any]) -> None:
        """Keep a record under its dataset type and id."""
        self.records[dataset_type, selector["id"]] = record


def _calibration(**changes: Any) -> GuiderCalibration:
    """Give a valid guider calibration record.

    Returns
    -------
    calibration : `GuiderCalibration`
        The record, with `changes` applied.
    """
    fields: dict[str, Any] = {
        "id": "cal-1",
        "camera_id": "guide-cam",
        "telescope_id": "scope",
        "arcsec_per_pixel": 3.8,
        "camera_angle_deg": 10.0,
        "ra_rate_arcsec_per_sec": 15.0,
        "dec_rate_arcsec_per_sec": 15.0,
        "source_note": "PHD2 calibration, copied by hand",
    }
    fields.update(changes)
    return GuiderCalibration(**fields)


def _focus_model(**changes: Any) -> FocusModel:
    """Give a valid focus model record.

    Returns
    -------
    focus_model : `FocusModel`
        The record, with `changes` applied.
    """
    fields: dict[str, Any] = {
        "id": "focus-1",
        "camera_id": "cam",
        "telescope_id": "scope",
        "backlash_steps": 40,
        "approach_direction": "inward",
        "source_note": "Fitted in another tool",
    }
    fields.update(changes)
    return FocusModel(**fields)


@pytest.fixture
def guiding() -> GuidingControl:
    """Make a guiding child over a dictionary butler.

    Returns
    -------
    guiding : `GuidingControl`
        The child under test.
    """
    return GuidingControl(SimpleNamespace(butler=_Butler()))


@pytest.fixture
def imaging() -> ImagingControl:
    """Make an imaging child over a dictionary butler.

    Returns
    -------
    imaging : `ImagingControl`
        The child under test.
    """
    return ImagingControl(SimpleNamespace(butler=_Butler()))


def test_a_saved_guider_calibration_id_is_refused_unless_forced(guiding: GuidingControl) -> None:
    """A second record with the same id needs ``force=True``."""
    guiding.create_guider_calibration(_calibration())

    with pytest.raises(InvalidArgumentError, match="already saved"):
        guiding.create_guider_calibration(_calibration(arcsec_per_pixel=4.0))
    assert guiding._context.butler.get(
        "guider_calibration", {"id": "cal-1"}
    ).arcsec_per_pixel == pytest.approx(3.8)

    replaced = guiding.create_guider_calibration(_calibration(arcsec_per_pixel=4.0), force=True)
    assert replaced.arcsec_per_pixel == pytest.approx(4.0)


def test_a_saved_focus_model_id_is_refused_unless_forced(imaging: ImagingControl) -> None:
    """A second focus model with the same id needs ``force=True``."""
    imaging.create_focus_model(_focus_model())

    with pytest.raises(InvalidArgumentError, match="already saved"):
        imaging.create_focus_model(_focus_model(backlash_steps=60))

    replaced = imaging.create_focus_model(_focus_model(backlash_steps=60), force=True)
    assert replaced.backlash_steps == 60


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "x" * 101},
        {"source_note": "x" * 501},
        {"arcsec_per_pixel": 101.0},
        {"ra_rate_arcsec_per_sec": 1001.0},
        {"dec_rate_arcsec_per_sec": 1001.0},
    ],
)
def test_guider_calibration_limits(guiding: GuidingControl, changes: dict[str, Any]) -> None:
    """Too long an id or note, or an absurd number, is refused."""
    with pytest.raises(InvalidArgumentError):
        guiding.create_guider_calibration(_calibration(**changes))
    assert guiding._context.butler.records == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "x" * 101},
        {"source_note": "x" * 501},
        {"backlash_steps": 100001},
        {"thermal_coefficient_steps_per_c": 10001.0},
        {"filter_offsets": [FilterFocusOffset(filter="Ha", offset_steps=100001)]},
    ],
)
def test_focus_model_limits(imaging: ImagingControl, changes: dict[str, Any]) -> None:
    """Too long an id or note, or an absurd number, is refused."""
    with pytest.raises(InvalidArgumentError):
        imaging.create_focus_model(_focus_model(**changes))
    assert imaging._context.butler.records == {}
