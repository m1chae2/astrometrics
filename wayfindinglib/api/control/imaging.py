"""Purpose: `control.imaging`, the main camera, filter wheel and focuser.

Description: Takes exposures with the main camera (with a filter change
and dithering between frames), turns the filter wheel, moves the
focuser, and fits a focus curve. `status` reads the
filter names, the focuser position and the saved focus model. A capture
or filter change needs `CAPTURE_ORCHESTRATION` to be `AUTHORITATIVE`; a
focuser move needs `AUTOFOCUS`.
"""

from astrometricslib import background_job, get_current_job, registered_job
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import ImagingStatus
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
from wayfindinglib.models.planning.observation_package import DitherConfig
from wayfindinglib.models.session.capture_result import CaptureResult
from wayfindinglib.models.session.correction_result import FocusCorrection, FocusCurvePoint

__all__ = ["ImagingControl"]

STATUS_SECTIONS = ("filters", "focuser", "focus_model")
"""Sections `ImagingControl.status` can read."""


class ImagingControl(ControlChild):
    """Operate the main camera, the filter wheel and the focuser."""

    def status(self, include: list[str] | None = None) -> ImagingStatus:
        """Read the filter wheel, the focuser and the saved focus model.

        Parameters
        ----------
        include : `list` [`str`], optional
            Sections to read: ``filters`` (slot names, from the device),
            ``focuser`` (position, from the device) and ``focus_model``
            (saved, no device needed). All of them when omitted.

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
        if "focuser" in sections:
            status.focuser_position = hardware_operations.focuser_position(self._context)
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

    def compute_focus_correction(
        self,
        comparison_input_id: str,
        curve: list[FocusCurvePoint],
        starting_position: int,
        trigger_reason: str,
    ) -> FocusCorrection:
        """Fit a parabola to a sampled focus curve and pick its lowest point.

        Computes only; nothing is sent to the focuser.

        Parameters
        ----------
        comparison_input_id : `str`
            Id of the focus run this correction is for.
        curve : `list` [`FocusCurvePoint`]
            Star width measured at each focuser position.
        starting_position : `int`
            Focuser position before the run.
        trigger_reason : `str`
            Why focusing was started.

        Returns
        -------
        correction : `FocusCorrection`
            The best position and the move to reach it.
        """
        from wayfindinglib.tasks.control_tasks.focus_correction import compute_focus_correction

        return compute_focus_correction(
            comparison_input_id, curve, starting_position, trigger_reason, self._context.correction_config
        )

    def save_focus_model(self, focus_model: FocusModel) -> None:
        """Save a measured focus model.

        Parameters
        ----------
        focus_model : `FocusModel`
            The model, keyed by its own id.
        """
        self._context.butler.put(focus_model, "focus_model", {"id": focus_model.id})
