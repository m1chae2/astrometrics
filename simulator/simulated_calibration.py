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
names the source. They are saved through the public
``record_calibration`` and ``record_focus_model``. The function refuses to
run unless the wayfinding library it would write to is inside the simulator's
folder and is not one of the user's real folders. It only reads the library it
is given.

The launcher (`simulator.launch`) cannot call the library's methods on the
running backend's own objects, and it must not write to the databases while
the backend runs. So it works the values out first, with
`build_simulated_calibrations` in a short-lived process before the backend
starts (``python -m simulator.simulated_calibration --root R``), and then
sends them to the running backend with the public RPC methods
(`rpc_calls`).
"""

import argparse
import json
import statistics
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from simulator.observatory_guard import real_protected_paths
from wayfindinglib.exceptions import GuardError

if TYPE_CHECKING:
    from wayfindinglib import ObservatoryControl

__all__ = [
    "FocusModelValues",
    "GuiderCalibrationValues",
    "SimulatedCalibrationReport",
    "build_simulated_calibrations",
    "record_simulated_calibrations",
    "rpc_calls",
]

#: The guide simulator's frames are not rotated against the mount axes.
SIMULATED_CAMERA_ANGLE_DEG = 0.0
#: The guide rate the simulated mount accepts: half the sidereal rate of
#: 15.04 arcsec/s. The library's own guide rate is the same number (a test
#: checks that they agree).
SIMULATED_GUIDE_RATE_ARCSEC_PER_SEC = 7.52


#: The speeds of one guide-log run count only between these (arcsec/s). Below
#: is a failed calibration; above is faster than the sky moves (sidereal rate
#: is 15.04 arcsec/s), which a guide pulse cannot do.
PLAUSIBLE_RATE_RANGE_ARCSEC_PER_SEC = (1.5, 15.0)
#: How many of the most recent sensible guide-log runs set the speeds.
RECENT_RUN_COUNT = 20


@dataclass(frozen=True)
class GuiderCalibrationValues:
    """The numbers of one guider calibration, as plain values.

    These are the arguments of ``control.guiding.record_calibration``, so
    the simulator needs no library model class.
    """

    id: str
    camera_id: str
    telescope_id: str
    arcsec_per_pixel: float
    camera_angle_deg: float
    ra_rate_arcsec_per_sec: float
    dec_rate_arcsec_per_sec: float
    source_note: str
    calibrated_at: datetime


@dataclass(frozen=True)
class FocusModelValues:
    """The numbers of one focus model, as plain values.

    These are the arguments of ``control.imaging.record_focus_model``.
    """

    id: str
    camera_id: str
    telescope_id: str
    backlash_steps: int
    approach_direction: str
    thermal_coefficient_steps_per_c: float
    filter_offsets: dict[str, int]
    source_note: str
    calibrated_at: datetime


@dataclass(frozen=True)
class SimulatedCalibrationReport:
    """What `record_simulated_calibrations` recorded."""

    guider_calibration: GuiderCalibrationValues
    focus_model: FocusModelValues
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
    library = Path(control.library_path).resolve()
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


def _calibration_values(record: object, **changes: object) -> GuiderCalibrationValues:
    """Read the values of a saved guider calibration.

    Parameters
    ----------
    record : `object`
        A calibration from ``control.guiding.query_calibrations`` or
        ``record_calibration``. Its fields are read, not its class.
    **changes
        Values to use instead of the record's.

    Returns
    -------
    values : `GuiderCalibrationValues`
        The record's values, with `changes` applied.
    """
    values = {
        "id": record.id,
        "camera_id": record.camera_id,
        "telescope_id": record.telescope_id,
        "arcsec_per_pixel": record.arcsec_per_pixel,
        "camera_angle_deg": record.camera_angle_deg,
        "ra_rate_arcsec_per_sec": record.ra_rate_arcsec_per_sec,
        "dec_rate_arcsec_per_sec": record.dec_rate_arcsec_per_sec,
        "source_note": record.source_note,
        "calibrated_at": record.calibrated_at,
    }
    return GuiderCalibrationValues(**{**values, **changes})


def _focus_model_values(record: object, **changes: object) -> FocusModelValues:
    """Read the values of a saved focus model.

    Parameters
    ----------
    record : `object`
        A model from ``control.imaging.query_focus_models`` or
        ``record_focus_model``. Its fields are read, not its class.
    **changes
        Values to use instead of the record's.

    Returns
    -------
    values : `FocusModelValues`
        The record's values, with `changes` applied.
    """
    values = {
        "id": record.id,
        "camera_id": record.camera_id,
        "telescope_id": record.telescope_id,
        "backlash_steps": record.backlash_steps,
        "approach_direction": str(getattr(record.approach_direction, "value", record.approach_direction)),
        "thermal_coefficient_steps_per_c": record.thermal_coefficient_steps_per_c,
        "filter_offsets": {offset.filter: offset.offset_steps for offset in record.filter_offsets},
        "source_note": record.source_note,
        "calibrated_at": record.calibrated_at,
    }
    return FocusModelValues(**{**values, **changes})


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
    runs = sorted(control.guiding.query_runs(), key=lambda run: run.started_at)
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
        ra_rate = dec_rate = SIMULATED_GUIDE_RATE_ARCSEC_PER_SEC
        rate_source = "simulator guide rate (no sensible real runs)"
    return plate_scale, ra_rate, dec_rate, f"{scale_source}, {rate_source}"


def build_simulated_calibrations(
    control: ObservatoryControl,
    simulator_root: Path,
    protected: Iterable[Path] | None = None,
) -> SimulatedCalibrationReport:
    """Work out the guider calibration and focus model, and save nothing.

    The values follow the user's real telescope (see the module
    description). This only reads the library, so the launcher can call it
    before the backend starts and send the result to the running backend
    through its RPC methods (`rpc_calls`).

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
        The two records, not saved, and where each came from.

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

    real_calibration = _latest_real(control.guiding.query_calibrations())
    if real_calibration is not None:
        guider_source = f"real calibration of {_day(real_calibration.calibrated_at)}"
        calibration = _calibration_values(
            real_calibration, **names, id=identifier, source_note=f"Simulator: copied from {guider_source}."
        )
    else:
        plate_scale, ra_rate, dec_rate, guider_source = _guider_from_history(
            control, control.guiding.status(include=["plate_scale"]).plate_scale_arcsec_per_px
        )
        calibration = GuiderCalibrationValues(
            id=identifier,
            arcsec_per_pixel=plate_scale,
            camera_angle_deg=SIMULATED_CAMERA_ANGLE_DEG,
            ra_rate_arcsec_per_sec=ra_rate,
            dec_rate_arcsec_per_sec=dec_rate,
            source_note=f"Simulator: derived from the {guider_source}; camera angle 0 (not recorded).",
            calibrated_at=datetime.now(UTC),
            **names,
        )

    real_focus = _latest_real(control.imaging.query_focus_models())
    if real_focus is not None:
        focus_source = f"real focus model of {_day(real_focus.calibrated_at)}"
        focus_model = _focus_model_values(
            real_focus, **names, id=identifier, source_note=f"Simulator: copied from {focus_source}."
        )
    else:
        focus_source = "simulator focuser defaults"
        focus_model = FocusModelValues(
            id=identifier,
            backlash_steps=0,
            approach_direction="inward",
            thermal_coefficient_steps_per_c=0.0,
            filter_offsets={},
            source_note=(
                "Simulator: made-up focus model from the INDI focuser simulator's behavior "
                "(no backlash, no thermal model); no real focus model is recorded."
            ),
            calibrated_at=datetime.now(UTC),
            **names,
        )
    return SimulatedCalibrationReport(
        guider_calibration=calibration,
        focus_model=focus_model,
        guider_source=guider_source,
        focus_source=focus_source,
    )


def rpc_calls(report: SimulatedCalibrationReport) -> list[tuple[str, dict]]:
    """Turn a report into the backend calls that record it.

    Parameters
    ----------
    report : `SimulatedCalibrationReport`
        From `build_simulated_calibrations`.

    Returns
    -------
    calls : `list` [`tuple` [`str`, `dict`]]
        ``(method, parameters)`` for ``observatory:record_guider_calibration``
        and then ``observatory:record_focus_model``. Both values are plain
        JSON. The record time is left out, so each record is dated when
        it is saved; the real record's date is in its source note.
    """
    calibration = report.guider_calibration
    focus_model = report.focus_model
    return [
        (
            "observatory:record_guider_calibration",
            {
                "calibration_id": calibration.id,
                "camera_id": calibration.camera_id,
                "telescope_id": calibration.telescope_id,
                "arcsec_per_pixel": calibration.arcsec_per_pixel,
                "camera_angle_deg": calibration.camera_angle_deg,
                "ra_rate_arcsec_per_sec": calibration.ra_rate_arcsec_per_sec,
                "dec_rate_arcsec_per_sec": calibration.dec_rate_arcsec_per_sec,
                "source_note": calibration.source_note,
            },
        ),
        (
            "observatory:record_focus_model",
            {
                "focus_model_id": focus_model.id,
                "camera_id": focus_model.camera_id,
                "telescope_id": focus_model.telescope_id,
                "backlash_steps": focus_model.backlash_steps,
                "approach_direction": focus_model.approach_direction,
                "source_note": focus_model.source_note,
                "thermal_coefficient_steps_per_c": focus_model.thermal_coefficient_steps_per_c,
                "filter_offsets": dict(focus_model.filter_offsets),
            },
        ),
    ]


def record_simulated_calibrations(
    control: ObservatoryControl,
    simulator_root: Path,
    protected: Iterable[Path] | None = None,
) -> SimulatedCalibrationReport:
    """Record a guider calibration and focus model for the simulator.

    The values come from `build_simulated_calibrations`. The records are
    saved in the simulator's own library only, through the public
    ``record_calibration`` and ``record_focus_model``. The backend's own
    start-up path uses this; the launcher uses `rpc_calls` instead.

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

    Notes
    -----
    Raises what `build_simulated_calibrations` raises.
    """
    report = build_simulated_calibrations(control, simulator_root, protected)
    calibration = report.guider_calibration
    focus_model = report.focus_model
    saved_calibration = control.guiding.record_calibration(
        calibration_id=calibration.id,
        camera_id=calibration.camera_id,
        telescope_id=calibration.telescope_id,
        arcsec_per_pixel=calibration.arcsec_per_pixel,
        camera_angle_deg=calibration.camera_angle_deg,
        ra_rate_arcsec_per_sec=calibration.ra_rate_arcsec_per_sec,
        dec_rate_arcsec_per_sec=calibration.dec_rate_arcsec_per_sec,
        source_note=calibration.source_note,
        calibrated_at=calibration.calibrated_at,
    )
    saved_focus_model = control.imaging.record_focus_model(
        focus_model_id=focus_model.id,
        camera_id=focus_model.camera_id,
        telescope_id=focus_model.telescope_id,
        backlash_steps=focus_model.backlash_steps,
        approach_direction=focus_model.approach_direction,
        source_note=focus_model.source_note,
        thermal_coefficient_steps_per_c=focus_model.thermal_coefficient_steps_per_c,
        filter_offsets=dict(focus_model.filter_offsets),
        calibrated_at=focus_model.calibrated_at,
    )
    return SimulatedCalibrationReport(
        guider_calibration=_calibration_values(saved_calibration),
        focus_model=_focus_model_values(saved_focus_model),
        guider_source=report.guider_source,
        focus_source=report.focus_source,
    )


def main(argv: list[str] | None = None) -> int:
    """Print the calls that record the calibrations, for the launcher.

    The launcher runs this in a short-lived process with the world's
    settings in its environment, before the backend starts, so the library
    copy is only read while nothing else uses it.

    Parameters
    ----------
    argv : `list` [`str`], optional
        The command line. Default: ``sys.argv[1:]``.

    Returns
    -------
    status : `int`
        0 and one JSON line on standard output: ``{"calls": [[method,
        params], ...], "guider_source": ..., "focus_source": ...}``. 1 and
        a message on standard error when the values cannot be worked out.
    """
    parser = argparse.ArgumentParser(description="Work out the simulator's calibration records.")
    parser.add_argument("--root", type=Path, required=True, help="The simulator's folder.")
    arguments = parser.parse_args(argv)
    from astrometricslib import get_configuration
    from astrometricslib.exceptions import ConfigurationError
    from wayfindinglib import Wayfinder

    wayfinder = Wayfinder(get_configuration())
    try:
        report = build_simulated_calibrations(wayfinder.control, arguments.root)
    except (ConfigurationError, GuardError, OSError, ValueError) as error:
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        return 1
    finally:
        wayfinder.control.close()
    sys.stdout.write(
        json.dumps({
            "calls": rpc_calls(report),
            "guider_source": report.guider_source,
            "focus_source": report.focus_source,
        })
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
