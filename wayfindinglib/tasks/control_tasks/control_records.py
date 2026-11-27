"""Purpose: Read and store guider calibrations and focus models.

Description: A guider calibration says how far a guide pulse moves the
guide star; a focus model says how the focuser behaves (backlash, the
direction every move ends in, the shift per degree and per filter). Both
are records kept per telescope and camera pairing. These functions are
the work behind `control.guiding.query_guider_calibrations`,
`create_guider_calibration`, `control.imaging.query_focus_models` and
`create_focus_model`. Creating a record checks its sizes, so a typing slip
or a pasted value from another tool never becomes a saved record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrometricslib.arguments import Page
from astrometricslib.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
    from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration

__all__ = [
    "create_focus_model",
    "create_guider_calibration",
    "query_focus_models",
    "query_guider_calibrations",
]

#: Longest id a stored record may have.
MAXIMUM_RECORD_ID_LENGTH = 100

#: Longest source note a stored record may have.
MAXIMUM_SOURCE_NOTE_LENGTH = 500

#: Largest backlash or filter offset accepted, in focuser steps. Real
#: focusers travel tens of thousands of steps at most.
MAXIMUM_BACKLASH_STEPS = 100000

#: Largest focus shift per degree Celsius accepted, in steps.
MAXIMUM_THERMAL_COEFFICIENT_STEPS_PER_C = 10000.0

#: Largest plate scale a guide camera could have (arcsec per pixel). Real
#: guide trains are about 1 to 10; 100 only catches a typing slip.
MAXIMUM_PLATE_SCALE_ARCSEC_PER_PIXEL = 100.0

#: Largest guide pulse speed accepted (arcsec per second). The sidereal rate
#: is about 15; PHD2 reports a few times that at most.
MAXIMUM_GUIDE_RATE_ARCSEC_PER_SEC = 1000.0


def _check_identity(kind: str, record_id: str, source_note: str) -> None:
    """Check a record's id and source note.

    Parameters
    ----------
    kind : `str`
        What the record is, for the message.
    record_id : `str`
        The record's id.
    source_note : `str`
        Where the numbers came from.

    Raises
    ------
    InvalidArgumentError
        If either is empty or too long.
    """
    if not record_id.strip() or not source_note.strip():
        raise InvalidArgumentError(f"A {kind} needs an id and a source_note.")
    if len(record_id) > MAXIMUM_RECORD_ID_LENGTH or len(source_note) > MAXIMUM_SOURCE_NOTE_LENGTH:
        raise InvalidArgumentError(
            f"A {kind} id may have at most {MAXIMUM_RECORD_ID_LENGTH} characters and its source_note "
            f"at most {MAXIMUM_SOURCE_NOTE_LENGTH}."
        )


def _refuse_existing(context: ControlContext, kind: str, record_id: str, force: bool) -> None:
    """Refuse to replace a stored record unless asked to.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage.
    kind : `str`
        The storage kind, such as ``"focus_model"``.
    record_id : `str`
        The record's id.
    force : `bool`
        Replace a stored record with the same id.

    Raises
    ------
    InvalidArgumentError
        If a record with the id is stored and `force` is false.
    """
    if not force and context.butler.get(kind, {"id": record_id}) is not None:
        raise InvalidArgumentError(
            f"A {kind.replace('_', ' ')} with id {record_id!r} is already saved. "
            "Pass force=True to replace it."
        )


def query_focus_models(
    context: ControlContext, limit: int | None = None, offset: int = 0
) -> Page[FocusModel]:
    """List every saved focus model, oldest first.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage.
    limit : `int`, optional
        The most records to return. Every one when omitted.
    offset : `int`, optional
        How many records to skip.

    Returns
    -------
    focus_models : `Page` [`FocusModel`]
        The records in order of ``calibrated_at``.
    """
    records = sorted(context.butler.get_all("focus_model"), key=lambda record: record.calibrated_at)
    return Page.of(records, offset=offset, limit=limit)


def create_focus_model(context: ControlContext, focus_model: FocusModel, force: bool = False) -> FocusModel:
    """Check and save a focus model that was measured elsewhere.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage.
    focus_model : `FocusModel`
        The record, keyed by its own id.
    force : `bool`, optional
        Replace a saved record that has the same id.

    Returns
    -------
    focus_model : `FocusModel`
        The saved record.

    Raises
    ------
    InvalidArgumentError
        If a value is out of range, the id or the source note is empty or
        too long, or the id is already saved and `force` is false.
    """
    _check_identity("focus model", focus_model.id, focus_model.source_note)
    if focus_model.backlash_steps > MAXIMUM_BACKLASH_STEPS:
        raise InvalidArgumentError(f"backlash_steps must be at most {MAXIMUM_BACKLASH_STEPS}.")
    if abs(focus_model.thermal_coefficient_steps_per_c) > MAXIMUM_THERMAL_COEFFICIENT_STEPS_PER_C:
        raise InvalidArgumentError(
            f"thermal_coefficient_steps_per_c must be within {MAXIMUM_THERMAL_COEFFICIENT_STEPS_PER_C}."
        )
    if any(abs(offset.offset_steps) > MAXIMUM_BACKLASH_STEPS for offset in focus_model.filter_offsets):
        raise InvalidArgumentError(f"Each filter offset must be within {MAXIMUM_BACKLASH_STEPS} steps.")
    _refuse_existing(context, "focus_model", focus_model.id, force)
    context.butler.put(focus_model, "focus_model", {"id": focus_model.id})
    return focus_model


def query_guider_calibrations(
    context: ControlContext, limit: int | None = None, offset: int = 0
) -> Page[GuiderCalibration]:
    """List every saved guider calibration, oldest first.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage.
    limit : `int`, optional
        The most records to return. Every one when omitted.
    offset : `int`, optional
        How many records to skip.

    Returns
    -------
    calibrations : `Page` [`GuiderCalibration`]
        The records in order of ``calibrated_at``.
    """
    records = sorted(context.butler.get_all("guider_calibration"), key=lambda record: record.calibrated_at)
    return Page.of(records, offset=offset, limit=limit)


def create_guider_calibration(
    context: ControlContext, calibration: GuiderCalibration, force: bool = False
) -> GuiderCalibration:
    """Check and save a guider calibration that was measured elsewhere.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage.
    calibration : `GuiderCalibration`
        The record, keyed by its own id.
    force : `bool`, optional
        Replace a saved record that has the same id.

    Returns
    -------
    calibration : `GuiderCalibration`
        The saved record.

    Raises
    ------
    InvalidArgumentError
        If a number is out of range, the id or the source note is empty or
        too long, or the id is already saved and `force` is false.
    """
    _check_identity("guider calibration", calibration.id, calibration.source_note)
    if calibration.arcsec_per_pixel > MAXIMUM_PLATE_SCALE_ARCSEC_PER_PIXEL:
        raise InvalidArgumentError(
            f"arcsec_per_pixel must be at most {MAXIMUM_PLATE_SCALE_ARCSEC_PER_PIXEL}."
        )
    if max(calibration.ra_rate_arcsec_per_sec, calibration.dec_rate_arcsec_per_sec) > (
        MAXIMUM_GUIDE_RATE_ARCSEC_PER_SEC
    ):
        raise InvalidArgumentError(
            f"The guide rates must be at most {MAXIMUM_GUIDE_RATE_ARCSEC_PER_SEC} arcsec/s."
        )
    _refuse_existing(context, "guider_calibration", calibration.id, force)
    context.butler.put(calibration, "guider_calibration", {"id": calibration.id})
    return calibration
