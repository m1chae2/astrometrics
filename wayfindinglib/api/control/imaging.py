"""Purpose: `control.imaging`, the main camera, filter wheel and focuser.

Description: Takes exposures with the main camera (with a filter change
and dithering between frames), checks a capture before it runs, stops
the run the app started, turns the filter wheel and moves the focuser.
`get_status` reads the filter names and the filter in the beam, the
focuser position, the camera's temperature and activity, whether a run
is going and the saved focus model; focus models are records, read with
`query_focus_models` and stored with `create_focus_model`. A capture or
filter change needs `CAPTURE_ORCHESTRATION` to be `AUTHORITATIVE`; a
focuser move needs `AUTOFOCUS`.
"""

from astrometricslib.arguments import Page
from astrometricslib.jobs import background_job
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import ImagingStatus
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
from wayfindinglib.models.planning.observation_package import DitherConfig
from wayfindinglib.models.session.capture_result import CaptureResult

__all__ = ["ImagingControl"]


class ImagingControl(ControlChild):
    """Operate the main camera, the filter wheel and the focuser."""

    def get_status(self, include: list[str] | None = None) -> ImagingStatus:
        """Read the filter wheel, the focuser, the camera and the focus model.

        Owner:
            wayfindinglib.tasks.control_tasks.control_status.read_imaging_status

        Parameters
        ----------
        include : `list` [`str`], optional
            Sections to read: ``filters`` (slot names, from the device),
            ``current_filter`` (the filter in the beam), ``focuser``
            (position, from the device), ``camera`` (sensor temperature and
            what the camera is doing), ``focus_model`` (saved, no device
            needed) and ``capture`` (whether a capture run this app started
            is going). All of them when omitted.

        Returns
        -------
        status : `ImagingStatus`
            The sections read. The others stay `None`.
        """
        from wayfindinglib.tasks.control_tasks.control_status import read_imaging_status

        return read_imaging_status(self._context, include)

    def check_capture(
        self,
        exposure_s: float,
        count: int = 1,
        filter_name: str | None = None,
        dither: bool | DitherConfig = False,
        delay_s: float = 0.0,
    ) -> None:
        """Check that a capture may run, without touching a device.

        `capture` makes the same check first. A program that runs the
        capture on a thread of its own calls this before it starts the
        thread, so a refusal (a bad argument, or monitor mode) reaches the
        person at once.

        Owner:
            wayfindinglib.tasks.control_tasks.imaging_capture.check_capture

        Parameters
        ----------
        exposure_s : `float`
            Length of each exposure, in seconds.
        count : `int`, optional
            Number of exposures.
        filter_name : `str`, optional
            Filter to turn to first.
        dither : `bool` or `DitherConfig`, optional
            Dithering between frames, as for `capture`.
        delay_s : `float`, optional
            Pause between exposures, in seconds.

        Notes
        -----
        Raises `InvalidArgumentError` if `exposure_s` or `count` is not
        above zero or `delay_s` is negative, `PermissionDeniedError` unless
        `CAPTURE_ORCHESTRATION` (and, to dither, `AUTOGUIDING`) is
        `AUTHORITATIVE`, and `ConfigurationError` when a dither cannot be
        sized.
        """
        from wayfindinglib.tasks.control_tasks.imaging_capture import check_capture

        check_capture(self._context, exposure_s, count, filter_name, dither, delay_s)

    @background_job("capture", grace_period_seconds=5.0)
    def capture(
        self,
        exposure_s: float,
        count: int = 1,
        filter_name: str | None = None,
        dither: bool | DitherConfig = False,
        delay_s: float = 0.0,
    ) -> CaptureResult:
        """Take one or more exposures with the main camera.

        Runs `check_capture` first. Turns the filter wheel when
        `filter_name` is given, then takes the exposures one after another.
        With `dither`, the pointing shifts slightly between frames (one
        guide pulse on each axis), so a fixed sensor defect lands on
        different sky in each frame. The run is recorded as a ``capture``
        job, and each frame moves the job's progress.

        Owner:
            wayfindinglib.tasks.control_tasks.imaging_capture.capture_frames

        Parameters
        ----------
        exposure_s : `float`
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
        delay_s : `float`, optional
            Pause between exposures, for example to let the mount settle.

        Returns
        -------
        result : `CaptureResult`
            How many frames were taken, with which filter, and how many
            dithers. ``stopped`` is `True` if `stop` (or a slew or park)
            ended the run early.

        Notes
        -----
        Raises what `check_capture` raises, and `HardwareError` if the
        camera does not start an exposure or the filter wheel does not turn.
        """
        from wayfindinglib.tasks.control_tasks.imaging_capture import capture_frames

        return capture_frames(
            self._context, exposure_s, count=count, filter_name=filter_name, dither=dither, delay_s=delay_s
        )

    def stop(self) -> None:
        """Stop the capture run this app started, and abort its exposure.

        The run ends cleanly after the frame it is on is dropped; the frames
        already taken are kept, and the run's result has ``stopped`` set.
        Only a run started by this app is stopped. When this app has no run
        going, nothing is sent to the camera, so a run started by another
        program (Ekos, for example) is never touched. A slew or park does
        the same before it moves.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.stop_capture

        Notes
        -----
        Raises `PermissionDeniedError` unless `CAPTURE_ORCHESTRATION` is
        `AUTHORITATIVE`, even when no run is going.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.stop_capture(self._context)

    def set_filter(self, name: str) -> None:
        """Turn the filter wheel to a filter.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.set_filter

        Parameters
        ----------
        name : `str`
            The filter's name, or a name the wheel can match to one.

        Notes
        -----
        Raises `NotFoundError` if the wheel has no such filter and
        `HardwareError` if it does not turn.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.set_filter(self._context, name)

    def move_focuser(self, steps: int) -> None:
        """Move the focuser by a number of steps.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.move_focuser

        Parameters
        ----------
        steps : `int`
            Steps to move; the sign gives the direction.

        Notes
        -----
        Raises `HardwareError` if the focuser refuses the move.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.move_focuser(self._context, steps)

    def query_focus_models(self, limit: int | None = None, offset: int = 0) -> Page[FocusModel]:
        """List every saved focus model, oldest first.

        `get_status` reads only the focus model of the active equipment.
        This reads them all, for every telescope and camera pairing.

        Owner:
            wayfindinglib.tasks.control_tasks.control_records.query_focus_models

        Parameters
        ----------
        limit : `int`, optional
            The most records to return. Every one when omitted.
        offset : `int`, optional
            How many records to skip.

        Returns
        -------
        focus_models : `Page` [`FocusModel`]
            The saved records in order of ``calibrated_at``.
        """
        from wayfindinglib.tasks.control_tasks.control_records import query_focus_models

        return query_focus_models(self._context, limit=limit, offset=offset)

    def create_focus_model(self, focus_model: FocusModel, force: bool = False) -> FocusModel:
        """Save a focus model that was measured elsewhere.

        Use this when the numbers come from a focus run in another tool or
        from a copy of an earlier record. The delegation rules treat the
        record like one fitted here. Nothing is sent to a device.

        Owner:
            wayfindinglib.tasks.control_tasks.control_records.create_focus_model

        Parameters
        ----------
        focus_model : `FocusModel`
            The record. Its ``source_note`` says where the numbers came
            from; its id has at most 100 characters.
        force : `bool`, optional
            Replace a saved record that has the same id. `False` refuses
            instead.

        Returns
        -------
        focus_model : `FocusModel`
            The saved record.

        Notes
        -----
        Raises `InvalidArgumentError` if a value is out of range, the id
        or source note is empty or too long, or the id is already saved
        and `force` is false.
        """
        from wayfindinglib.tasks.control_tasks.control_records import create_focus_model

        return create_focus_model(self._context, focus_model, force=force)
