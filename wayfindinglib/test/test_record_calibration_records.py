"""Purpose: Tests for recording a guider calibration and a focus model.

Description: The two ``record_*`` calls save numbers measured elsewhere. They
must refuse an id that is already saved unless ``replace`` is true, and they
cap the id, the note and the numbers. The tests use a dictionary in place of
the library's butler, so nothing is written to a real library.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.api.control.guiding import GuidingControl
from wayfindinglib.api.control.imaging import ImagingControl


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


def _calibration_arguments(**changes: Any) -> dict[str, Any]:
    """Give valid arguments for `GuidingControl.record_calibration`.

    Returns
    -------
    arguments : `dict`
        The arguments, with `changes` applied.
    """
    arguments: dict[str, Any] = {
        "calibration_id": "cal-1",
        "camera_id": "guide-cam",
        "telescope_id": "scope",
        "arcsec_per_pixel": 3.8,
        "camera_angle_deg": 10.0,
        "ra_rate_arcsec_per_sec": 15.0,
        "dec_rate_arcsec_per_sec": 15.0,
        "source_note": "PHD2 calibration, copied by hand",
    }
    arguments.update(changes)
    return arguments


def _focus_arguments(**changes: Any) -> dict[str, Any]:
    """Give valid arguments for `ImagingControl.record_focus_model`.

    Returns
    -------
    arguments : `dict`
        The arguments, with `changes` applied.
    """
    arguments: dict[str, Any] = {
        "focus_model_id": "focus-1",
        "camera_id": "cam",
        "telescope_id": "scope",
        "backlash_steps": 40,
        "approach_direction": "inward",
        "source_note": "Fitted in another tool",
    }
    arguments.update(changes)
    return arguments


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


def test_a_saved_guider_calibration_id_is_refused_unless_replace_is_true(guiding: GuidingControl) -> None:
    """A second record with the same id needs ``replace=True``."""
    guiding.record_calibration(**_calibration_arguments())

    with pytest.raises(InvalidArgumentError, match="already saved"):
        guiding.record_calibration(**_calibration_arguments(arcsec_per_pixel=4.0))
    assert guiding._context.butler.get(
        "guider_calibration", {"id": "cal-1"}
    ).arcsec_per_pixel == pytest.approx(3.8)

    replaced = guiding.record_calibration(**_calibration_arguments(arcsec_per_pixel=4.0), replace=True)
    assert replaced.arcsec_per_pixel == pytest.approx(4.0)


def test_a_saved_focus_model_id_is_refused_unless_replace_is_true(imaging: ImagingControl) -> None:
    """A second focus model with the same id needs ``replace=True``."""
    imaging.record_focus_model(**_focus_arguments())

    with pytest.raises(InvalidArgumentError, match="already saved"):
        imaging.record_focus_model(**_focus_arguments(backlash_steps=60))

    replaced = imaging.record_focus_model(**_focus_arguments(backlash_steps=60), replace=True)
    assert replaced.backlash_steps == 60


@pytest.mark.parametrize(
    "changes",
    [
        {"calibration_id": "x" * 101},
        {"source_note": "x" * 501},
        {"arcsec_per_pixel": 101.0},
        {"ra_rate_arcsec_per_sec": 1001.0},
        {"dec_rate_arcsec_per_sec": 1001.0},
    ],
)
def test_guider_calibration_limits(guiding: GuidingControl, changes: dict[str, Any]) -> None:
    """Too long an id or note, or an absurd number, is refused."""
    with pytest.raises(InvalidArgumentError):
        guiding.record_calibration(**_calibration_arguments(**changes))
    assert guiding._context.butler.records == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"focus_model_id": "x" * 101},
        {"source_note": "x" * 501},
        {"backlash_steps": 100001},
        {"thermal_coefficient_steps_per_c": 10001.0},
        {"filter_offsets": {"Ha": 100001}},
    ],
)
def test_focus_model_limits(imaging: ImagingControl, changes: dict[str, Any]) -> None:
    """Too long an id or note, or an absurd number, is refused."""
    with pytest.raises(InvalidArgumentError):
        imaging.record_focus_model(**_focus_arguments(**changes))
    assert imaging._context.butler.records == {}
