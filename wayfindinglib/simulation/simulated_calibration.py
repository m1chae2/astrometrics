"""Purpose: Give the simulator a guider calibration and a focus model.

Description: The library's delegation rules let autoguiding leave
``DELEGATED`` only when a guider calibration exists, autofocus only when a
focus model exists, and capture only when both corrections are
``AUTHORITATIVE``. A real night earns those records by calibrating the
guider and running a focus curve. The simulated observatory has no sky to
calibrate on, so it records ones that follow how the user's real telescope
performs, and the same rules then run unchanged.

`record_simulated_calibrations` records both into the simulator's own
wayfinding library, for the active telescope and camera, at every
simulator start. The simulator's library is a copy of the real one, made at
the same start, so new real data is picked up by itself. Each value comes
from the first source that has it:

1. the most recent real `GuiderCalibration` or `FocusModel` in the library
   (copied, with the equipment names changed to the active ones);
2. for the guider, the real guide-log runs: the guide camera's plate scale
   and the middle (median) calibrated RA and Dec speeds of the most recent
   runs that look sensible, or else the plate scale of the active
   equipment;
3. the INDI simulators' own behavior: the guide rate the simulated mount
   accepts (half the sidereal rate), a camera angle of 0 degrees, and a
   focuser with no backlash and no thermal model.

Each record carries a ``source_note`` that says it is for the simulator and
names the source. The function refuses to run unless the wayfinding library
it would write to is inside the simulator's folder and is not one of the
user's real folders. It only reads the library it is given.
"""

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from wayfindinglib.analytics.guide_pulses import GUIDE_RATE_ARCSEC_PER_S
from wayfindinglib.exceptions import GuardError
from wayfindinglib.models.equipment_and_site.focus_model import ApproachDirection, FocusModel
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.simulation.observatory_guard import real_protected_paths

if TYPE_CHECKING:
    from wayfindinglib.api.control.control import ObservatoryControl

__all__ = ["SimulatedCalibrationReport", "record_simulated_calibrations"]

#: The guide simulator's frames are not rotated against the mount axes.
SIMULATED_CAMERA_ANGLE_DEG = 0.0


#: The speeds of one guide-log run count only between these (arcsec/s). Below
#: is a failed calibration; above is faster than the sky moves (sidereal rate
#: is 15.04 arcsec/s), which a guide pulse cannot do.
PLAUSIBLE_RATE_RANGE_ARCSEC_PER_SEC = (1.5, 15.0)
#: How many of the most recent sensible guide-log runs set the speeds.
RECENT_RUN_COUNT = 20


@dataclass(frozen=True)
class SimulatedCalibrationReport:
    """What `record_simulated_calibrations` recorded."""

    guider_calibration: GuiderCalibration
    focus_model: FocusModel
    guider_source: str
    focus_source: str


def _check_library_is_simulators(
    control: ObservatoryControl, simulator_root: Path, protected: Iterable[Path] | None
) -> None:
    """Refuse a wayfinding library that is not the simulator's own.

    Parameters
    ----------
    control : `ObservatoryControl`
        The control whose library would be written.
    simulator_root : `pathlib.Path`
        The simulator's folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`] or `None`
        The user's real folders. Default: `real_protected_paths`.

    Raises
    ------
    GuardError
        If the library folder is outside `simulator_root`, or is, holds, or
        is inside a real folder.
    """
    library = Path(control._context.butler.library_path).resolve()
    root = simulator_root.resolve()
    if not library.is_relative_to(root):
        raise GuardError(f"The wayfinding library {library} is not inside the simulator folder {root}.")
    for real in real_protected_paths() if protected is None else protected:
        if library.is_relative_to(real) or real.is_relative_to(library):
            raise GuardError(
                f"The wayfinding library {library} is, holds, or is inside the real folder {real}."
            )


def _latest_real(records: list) -> object | None:
    """Pick the newest record that did not come from a simulator.

    Parameters
    ----------
    records : `list`
        `GuiderCalibration` or `FocusModel` records.

    Returns
    -------
    record : `GuiderCalibration` or `FocusModel` or `None`
        The record with the latest ``calibrated_at``, or `None`.
    """
    real = [record for record in records if not record.source_note.startswith("Simulator")]
    return max(real, key=lambda record: record.calibrated_at, default=None)


def _day(moment: datetime) -> str:
    """Write a time as a UTC date for a note.

    Returns
    -------
    day : `str`
        For example ``"2026-10-03"``.
    """
    return moment.astimezone(UTC).date().isoformat()


def _guider_from_history(
    control: ObservatoryControl, plate_scale_from_equipment: float | None
) -> tuple[float, float, float, str]:
    """Work out the guider numbers from the real guide-log runs.

    Parameters
    ----------
    control : `ObservatoryControl`
        Supplies the recorded runs.
    plate_scale_from_equipment : `float` or `None`
        The active equipment's guide plate scale, used when no run has one.

    Returns
    -------
    plate_scale, ra_rate, dec_rate : `float`
        Arcseconds per pixel, and arcseconds per second along each axis.
    source : `str`
        Where the numbers came from, for the note.

    Raises
    ------
    ConfigurationError
        If no plate scale is known from the runs or the equipment.
    """
    from astrometricslib.exceptions import ConfigurationError

    low, high = PLAUSIBLE_RATE_RANGE_ARCSEC_PER_SEC
    runs = sorted(control._context.butler.get_all("guiding_run"), key=lambda run: run.started_at)
    sensible = [
        run
        for run in runs
        if run.ra_rate_arcsec_per_second is not None
        and run.dec_rate_arcsec_per_second is not None
        and low <= run.ra_rate_arcsec_per_second <= high
        and low <= run.dec_rate_arcsec_per_second <= high
    ][-RECENT_RUN_COUNT:]
    scales = [run.pixel_scale_arcsec_per_px for run in runs if run.pixel_scale_arcsec_per_px]
    if scales:
        plate_scale = statistics.median(scales[-RECENT_RUN_COUNT:])
        scale_source = "guide-log plate scale"
    elif plate_scale_from_equipment is not None:
        plate_scale = plate_scale_from_equipment
        scale_source = "equipment plate scale"
    else:
        raise ConfigurationError(
            "The guide camera's plate scale is not known, so no calibration can be made."
        )
    if sensible:
        ra_rate = statistics.median(run.ra_rate_arcsec_per_second for run in sensible)
        dec_rate = statistics.median(run.dec_rate_arcsec_per_second for run in sensible)
        rate_source = f"median speeds of the last {len(sensible)} sensible real guide-log runs"
    else:
        ra_rate = dec_rate = GUIDE_RATE_ARCSEC_PER_S
        rate_source = "simulator guide rate (no sensible real runs)"
    return plate_scale, ra_rate, dec_rate, f"{scale_source}, {rate_source}"


def record_simulated_calibrations(
    control: ObservatoryControl,
    simulator_root: Path,
    protected: Iterable[Path] | None = None,
) -> SimulatedCalibrationReport:
    """Record a guider calibration and focus model for the simulator.

    The values follow the user's real telescope (see the module
    description). The records are saved in the simulator's own library only.

    Parameters
    ----------
    control : `ObservatoryControl`
        The simulator's control. Its wayfinding library must be inside
        `simulator_root`.
    simulator_root : `pathlib.Path`
        The simulator's folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`], optional
        The user's real folders. Default: `real_protected_paths`.

    Returns
    -------
    report : `SimulatedCalibrationReport`
        The two records, now saved, and where each came from.

    Raises
    ------
    ConfigurationError
        If no telescope and camera are active, or the guide camera's plate
        scale is not known.

    Notes
    -----
    `GuardError` is raised first if the wayfinding library is not the
    simulator's own.
    """
    from astrometricslib.exceptions import ConfigurationError

    _check_library_is_simulators(control, simulator_root, protected)
    equipment = control.equipment.status(include=["telescope", "camera"])
    if equipment.telescope is None or equipment.camera is None:
        raise ConfigurationError(
            "No active telescope and camera: there is nothing to calibrate in the simulator."
        )
    names = {"telescope_id": equipment.telescope.id, "camera_id": equipment.camera.id}
    identifier = f"simulator-{equipment.telescope.id}-{equipment.camera.id}"

    real_calibration = _latest_real(control._context.butler.get_all("guider_calibration"))
    if real_calibration is not None:
        guider_source = f"real calibration of {_day(real_calibration.calibrated_at)}"
        calibration = real_calibration.model_copy(
            update={**names, "id": identifier, "source_note": f"Simulator: copied from {guider_source}."}
        )
    else:
        plate_scale, ra_rate, dec_rate, guider_source = _guider_from_history(
            control, control.guiding.status(include=["plate_scale"]).plate_scale_arcsec_per_px
        )
        calibration = GuiderCalibration(
            id=identifier,
            arcsec_per_pixel=plate_scale,
            camera_angle_deg=SIMULATED_CAMERA_ANGLE_DEG,
            ra_rate_arcsec_per_sec=ra_rate,
            dec_rate_arcsec_per_sec=dec_rate,
            source_note=f"Simulator: derived from the {guider_source}; camera angle 0 (not recorded).",
            **names,
        )

    real_focus = _latest_real(control._context.butler.get_all("focus_model"))
    if real_focus is not None:
        focus_source = f"real focus model of {_day(real_focus.calibrated_at)}"
        focus_model = real_focus.model_copy(
            update={**names, "id": identifier, "source_note": f"Simulator: copied from {focus_source}."}
        )
    else:
        focus_source = "simulator focuser defaults"
        focus_model = FocusModel(
            id=identifier,
            backlash_steps=0,
            approach_direction=ApproachDirection.INWARD,
            thermal_coefficient_steps_per_c=0.0,
            source_note=(
                "Simulator: made-up focus model from the INDI focuser simulator's behavior "
                "(no backlash, no thermal model); no real focus model is recorded."
            ),
            **names,
        )
    control.guiding._save_calibration(calibration)
    control.imaging._save_focus_model(focus_model)
    return SimulatedCalibrationReport(
        guider_calibration=calibration,
        focus_model=focus_model,
        guider_source=guider_source,
        focus_source=focus_source,
    )
