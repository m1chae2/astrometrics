"""Purpose: `control.imaging`, the main camera, filter wheel and focuser.

Description: Takes exposures with the main camera, turns the filter
wheel, moves the focuser, and fits a focus curve. `status` reads the
filter names, the focuser position and the saved focus model. A capture
or filter change needs `CAPTURE_ORCHESTRATION` to be `AUTHORITATIVE`; a
focuser move needs `AUTOFOCUS`.
"""

from typing import Any

from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import ImagingStatus
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
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

    def capture_image(self, exposure_seconds: float) -> Any:
        """Take one exposure with the main camera.

        Parameters
        ----------
        exposure_seconds : `float`
            Exposure length in seconds.

        Returns
        -------
        result : `Any`
            The camera driver's exposure result.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.capture_image(self._context, exposure_seconds)

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
