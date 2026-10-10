"""Purpose: Serve the equipment set-up and the control-mode calls over RPC.

Description: The wayfinding library's `control.equipment` knows the camera
profiles and the active telescope and camera. Its `control.safety` moves
every observatory capability between "monitoring" (the app only watches)
and "controller" (the app drives the hardware). This service passes the
RPC calls on to those two and turns their replies into plain data.
"""

from typing import Any

from wayfindinglib import ObservatoryControl


class EquipmentService:
    """Pass equipment and control-mode calls to the observatory control."""

    def __init__(self, observatory_api: ObservatoryControl) -> None:
        """Keep the observatory control.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`.
        """
        self._observatory = observatory_api

    def list_camera_profiles(self) -> Any:
        """List the camera profiles the configuration knows.

        Returns
        -------
        profiles : `list`
            One profile per camera, with its pixel size and sensor size.
        """
        return self._observatory.equipment.status(include=["camera_profiles"]).camera_profiles

    def get_equipment_configuration(self) -> Any:
        """Report the active telescope and camera and what they can see.

        Returns
        -------
        configuration : `dict` or `None`
            The telescope, the camera, the plate scale and the field of
            view, or `None` when no equipment is set up.
        """
        return self._observatory.equipment.status(include=["configuration"]).configuration

    def set_active_camera(self, camera_name: str) -> bool:
        """Make a camera the active one.

        Parameters
        ----------
        camera_name : `str`
            The name of a camera profile.

        Returns
        -------
        changed : `bool`
            `True` if the camera is now active.
        """
        return self._observatory.equipment.set_active_camera(camera_name)

    def enter_monitoring_mode(self, evidence_note: str = "") -> dict[str, dict[str, str]]:
        """Hand every capability back to the outside software, to watch only.

        Parameters
        ----------
        evidence_note : `str`, optional
            Why the change was made, kept with the record.

        Returns
        -------
        outcome : `dict`
            ``{"applied": {capability: state}, "rejected": {capability:
            reason}}``.
        """
        return _outcome_as_dict(self._observatory.safety.enter_monitoring_mode(evidence_note=evidence_note))

    def enter_controller_mode(self, evidence_note: str = "") -> dict[str, dict[str, str]]:
        """Let this app drive every capability it is allowed to drive.

        Parameters
        ----------
        evidence_note : `str`, optional
            Why the change was made, kept with the record.

        Returns
        -------
        outcome : `dict`
            ``{"applied": {capability: state}, "rejected": {capability:
            reason}}``.
        """
        return _outcome_as_dict(self._observatory.safety.enter_controller_mode(evidence_note=evidence_note))


def _outcome_as_dict(outcome: Any) -> dict[str, dict[str, str]]:
    """Turn a bulk control-mode change into plain strings for the reply.

    The capability and state values are string enums. They are turned
    into plain strings here, so the reply does not depend on how those
    enums are written.

    Parameters
    ----------
    outcome : `BulkDelegationOutcome`
        The library's record of what changed and what was refused.

    Returns
    -------
    serialized : `dict`
        ``{"applied": {capability: state}, "rejected": {capability:
        reason}}``, both keyed by capability name.
    """
    return {
        "applied": {capability.value: state.value for capability, state in outcome.applied.items()},
        "rejected": {capability.value: reason for capability, reason in outcome.rejected.items()},
    }
