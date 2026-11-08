"""Purpose: `control.imaging`, the main camera, filter wheel and focuser.

Description: Takes exposures with the main camera (with a filter change
and dithering between frames), turns the filter wheel, moves the
focuser, and fits a focus curve. `status` reads the
filter names and the filter in the beam, the focuser position, the
camera's temperature and activity, and the saved focus model. A capture
or filter change needs `CAPTURE_ORCHESTRATION` to be `AUTHORITATIVE`; a
focuser move needs `AUTOFOCUS`.
"""

from datetime import datetime

from astrometricslib.jobs import background_job, get_current_job, registered_job
from wayfindinglib.api.control.context import ControlChild, require_text
from wayfindinglib.models.control_status import ImagingStatus
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
from wayfindinglib.models.planning.observation_package import DitherConfig
from wayfindinglib.models.session.capture_result import CaptureResult

__all__ = ["ImagingControl"]

STATUS_SECTIONS = ("filters", "current_filter", "focuser", "camera", "focus_model")
"""Sections `ImagingControl.status` can read."""


#: Longest id a recorded focus model may have.
MAXIMUM_RECORD_ID_LENGTH = 100

#: Longest source note a recorded focus model may have.
MAXIMUM_SOURCE_NOTE_LENGTH = 500

#: Largest backlash or filter offset accepted, in focuser steps. Real
#: focusers travel tens of thousands of steps at most.
MAXIMUM_BACKLASH_STEPS = 100000

#: Largest focus shift per degree Celsius accepted, in steps.
MAXIMUM_THERMAL_COEFFICIENT_STEPS_PER_C = 10000.0


class ImagingControl(ControlChild):
    """Operate the main camera, the filter wheel and the focuser."""

    def status(self, include: list[str] | None = None) -> ImagingStatus:
        """Read the filter wheel, the focuser, the camera and the focus model.

        Parameters
        ----------
        include : `list` [`str`], optional
            Sections to read: ``filters`` (slot names, from the device),
            ``current_filter`` (the filter in the beam), ``focuser``
            (position, from the device), ``camera`` (sensor temperature and
            what the camera is doing) and ``focus_model`` (saved, no device
            needed). All of them when omitted.

        Returns
        -------
        status : `ImagingStatus`
            The sections read. The others stay `None`.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        sections = hardware_operations.check_sections(include, STATUS_SECTIONS)
        status = ImagingStatus(sections=sections)
        if "filters" in sections:
            status.filter_names = hardware_operations.filter_names(self._context)
        if "current_filter" in sections:
            status.current_filter = hardware_operations.current_filter(self._context)
        if "focuser" in sections:
            status.focuser_position = hardware_operations.focuser_position(self._context)
        if "camera" in sections:
            status.camera_temperature, status.camera_status = hardware_operations.camera_reading(
                self._context
            )
        if "focus_model" in sections:
            status.focus_model = self._context.active_focus_model()
        return status

    @background_job("capture", grace_period_seconds=5.0)
    def capture_image(
        self,
        exposure_seconds: float,
        count: int = 1,
        filter_name: str | None = None,
        dither: bool | DitherConfig = False,
        delay_seconds: float = 0.0,
        register_job: bool = True,
    ) -> CaptureResult:
        """Take one or more exposures with the main camera.

        Turns the filter wheel first when `filter_name` is given, then
        takes the exposures one after another. With `dither`, the
        pointing shifts slightly between frames (one guide pulse on each
        axis), so a fixed sensor defect lands on different sky in each
        frame. Each frame waits for its exposure plus a short readout.

        Parameters
        ----------
        exposure_seconds : `float`
            Length of each exposure, in seconds.
        count : `int`, optional
            Number of exposures. Defaults to 1.
        filter_name : `str`, optional
            Filter to turn to first, by name or a name the wheel can match.
            `None` leaves the wheel where it is.
        dither : `bool` or `DitherConfig`, optional
            `True` dithers by 3 pixels every 3 frames. A `DitherConfig`
            sets the size and how often. Needs an active telescope and
            camera, and `AUTOGUIDING` to be `AUTHORITATIVE`.
        delay_seconds : `float`, optional
            Pause between exposures, for example to let the mount settle.
        register_job : `bool`, optional
            Record the run as a ``capture`` job with its progress. A call
            made inside a running job reports to that job instead.

        Returns
        -------
        result : `CaptureResult`
            How many frames were taken, with which filter, and how many
            dithers.

        Notes
        -----
        The capture raises `InvalidArgumentError` if `exposure_seconds` or
        `count` is not above zero or `delay_seconds` is negative, and
        `HardwareError` if the camera does not start an exposure or the
        filter wheel does not turn.
        """
        from wayfindinglib.tasks.control_tasks.imaging_capture import capture_frames

        with registered_job(
            enabled=register_job and get_current_job() is None,
            job_type="capture",
            target_id=filter_name or "capture",
        ):
            return capture_frames(
                self._context,
                exposure_seconds,
                count=count,
                filter_name=filter_name,
                dither=dither,
                delay_seconds=delay_seconds,
            )

    def set_filter(self, filter_name: str) -> bool:
        """Turn the filter wheel to a filter.

        Parameters
        ----------
        filter_name : `str`
            The filter's name, or a name the wheel can match to one.

        Returns
        -------
        success : `bool`
            `True` once the wheel reports the new filter.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.set_filter(self._context, filter_name)

    def focus_move(self, steps: int) -> bool:
        """Move the focuser by a number of steps.

        Parameters
        ----------
        steps : `int`
            Steps to move; the sign gives the direction.

        Returns
        -------
        success : `bool`
            Whether the move command was accepted.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.focus_move(self._context, steps)

    def query_focus_models(self) -> list[FocusModel]:
        """List every saved focus model, oldest first.

        `status` reads only the focus model of the active equipment. This
        reads them all, for every telescope and camera pairing, so a script
        can compare models or pick the latest one. It only reads.

        Returns
        -------
        focus_models : `list` [`FocusModel`]
            The saved records in order of ``calibrated_at``. Empty when none
            is saved.
        """
        records = self._context.butler.get_all("focus_model")
        return sorted(records, key=lambda record: record.calibrated_at)

    def record_focus_model(
        self,
        *,
        focus_model_id: str,
        camera_id: str,
        telescope_id: str,
        backlash_steps: int,
        approach_direction: str,
        source_note: str,
        thermal_coefficient_steps_per_c: float = 0.0,
        filter_offsets: dict[str, int] | None = None,
        calibrated_at: datetime | None = None,
        replace: bool = False,
    ) -> FocusModel:
        """Save a focus model that was measured elsewhere.

        Use this when the numbers come from a focus run in another tool or
        from a copy of an earlier record. A record with an id that is already
        saved is refused unless `replace` is true. The delegation rules treat
        the record like one fitted here. Nothing is sent to a device.

        Parameters
        ----------
        focus_model_id : `str`
            Id of the record, at most 100 characters.
        camera_id, telescope_id : `str`
            The equipment pairing the model belongs to.
        backlash_steps : `int`
            Focuser backlash in steps. Zero or more, at most 100000.
        approach_direction : `str`
            ``"inward"`` or ``"outward"``: the one direction every focus
            move ends in.
        source_note : `str`
            Where the numbers came from. Required, so a record never has an
            unknown origin. At most 500 characters.
        thermal_coefficient_steps_per_c : `float`, optional
            Focus shift in steps for each degree Celsius, from -10000 to
            10000. Default 0.
        filter_offsets : `dict` [`str`, `int`], optional
            Focus offset in steps for each filter name.
        calibrated_at : `~datetime.datetime`, optional
            When the model was made. Now when omitted.
        replace : `bool`, optional
            Replace a saved record that has the same id. Defaults to
            `False`, which refuses instead.

        Returns
        -------
        focus_model : `FocusModel`
            The saved record.

        Raises
        ------
        InvalidArgumentError
            If a value is out of range or not recognized, an id or
            `source_note` is empty or too long, or the id is already saved
            and `replace` is false.
        """
        from pydantic import ValidationError

        from astrometricslib.exceptions import InvalidArgumentError
        from wayfindinglib.models.equipment_and_site.focus_model import FilterFocusOffset

        require_text(source_note=source_note, focus_model_id=focus_model_id)
        if len(focus_model_id) > MAXIMUM_RECORD_ID_LENGTH or len(source_note) > MAXIMUM_SOURCE_NOTE_LENGTH:
            raise InvalidArgumentError(
                f"focus_model_id may have at most {MAXIMUM_RECORD_ID_LENGTH} characters and source_note "
                f"at most {MAXIMUM_SOURCE_NOTE_LENGTH}."
            )
        if backlash_steps > MAXIMUM_BACKLASH_STEPS:
            raise InvalidArgumentError(f"backlash_steps must be at most {MAXIMUM_BACKLASH_STEPS}.")
        if abs(thermal_coefficient_steps_per_c) > MAXIMUM_THERMAL_COEFFICIENT_STEPS_PER_C:
            raise InvalidArgumentError(
                f"thermal_coefficient_steps_per_c must be within {MAXIMUM_THERMAL_COEFFICIENT_STEPS_PER_C}."
            )
        if any(abs(offset) > MAXIMUM_BACKLASH_STEPS for offset in (filter_offsets or {}).values()):
            raise InvalidArgumentError(f"Each filter offset must be within {MAXIMUM_BACKLASH_STEPS} steps.")
        if not replace and self._context.butler.get("focus_model", {"id": focus_model_id}) is not None:
            raise InvalidArgumentError(
                f"A focus model with id {focus_model_id!r} is already saved. Pass replace=True to replace it."
            )
        fields = {
            "id": focus_model_id,
            "camera_id": camera_id,
            "telescope_id": telescope_id,
            "backlash_steps": backlash_steps,
            "approach_direction": approach_direction,
            "thermal_coefficient_steps_per_c": thermal_coefficient_steps_per_c,
            "filter_offsets": [
                FilterFocusOffset(filter=name, offset_steps=offset)
                for name, offset in (filter_offsets or {}).items()
            ],
            "source_note": source_note,
        }
        if calibrated_at is not None:
            fields["calibrated_at"] = calibrated_at
        try:
            focus_model = FocusModel(**fields)
        except ValidationError as error:
            raise InvalidArgumentError(f"The focus model is not valid: {error}") from error
        self._save_focus_model(focus_model)
        return focus_model

    def _save_focus_model(self, focus_model: FocusModel) -> None:
        """Save a measured focus model.

        Parameters
        ----------
        focus_model : `FocusModel`
            The model, keyed by its own id.
        """
        self._context.butler.put(focus_model, "focus_model", {"id": focus_model.id})
