"""Purpose: Serve the equipment set-up and the control-mode calls over RPC.

Description: The wayfinding library's `control.equipment` knows the camera
profiles and the active observatory configuration (the stored record of the
telescope, camera and other equipment used together). Its `control.safety`
moves every observatory capability between "monitoring" (the app only
watches) and "controller" (the app drives the hardware). This service passes
the RPC calls on to those two and turns their replies into plain data.

The screens still choose a camera or a telescope on its own
(``observatory:set_active_camera``, ``observatory:set_active_telescope``).
The library chooses a whole configuration, so this service finds the stored
configuration that pairs the chosen camera or telescope with the other half
of the active one, and makes it active.
"""

from datetime import date
from typing import Any

from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from wayfindinglib import ObservatoryControl


def _name_key(name: str) -> str:
    """Reduce an equipment name to lowercase letters and digits.

    Parameters
    ----------
    name : `str`
        A camera or telescope name in any spelling.

    Returns
    -------
    key : `str`
        The name without spaces, punctuation or case.
    """
    return "".join(character for character in name.lower() if character.isalnum())


def _has_camera(configuration: Any, camera_name: str) -> bool:
    """Say whether a configuration's main camera has a name.

    Parameters
    ----------
    configuration : `ObservatoryConfiguration`
        A stored configuration.
    camera_name : `str`
        The camera's name in any spelling.

    Returns
    -------
    has_camera : `bool`
        `True` when the camera's name or an alias matches.
    """
    wanted = _name_key(camera_name)
    return any(
        _name_key(name) == wanted for name in (configuration.camera.name, *configuration.camera.aliases)
    )


class EquipmentService:
    """Pass equipment and control-mode calls to the observatory control."""

    def __init__(self, observatory_api: ObservatoryControl, configurations: Any) -> None:
        """Keep the observatory control and the stored configurations.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`.
        configurations : `ObservatoryConfigurations`
            ``astrometrics.observatories.configurations``, every stored set
            of equipment.
        """
        self._observatory = observatory_api
        self._configurations = configurations

    def _choose(self, *, camera_name: str | None = None, telescope_id: str | None = None) -> Any:
        """Find the stored configuration for a new camera or telescope choice.

        Exactly one of the two is given. A configuration in use today that
        pairs it with the other half of the active configuration wins;
        otherwise the newest one in use today that has it.

        Parameters
        ----------
        camera_name : `str`, optional
            The chosen camera.
        telescope_id : `str`, optional
            The chosen telescope.

        Returns
        -------
        configuration : `ObservatoryConfiguration` or `None`
            The configuration, or `None` when no stored one has the choice.
        """
        active = self._observatory.equipment.get_active()
        candidates = [
            configuration
            for configuration in self._configurations.query(time=date.today())
            if (camera_name is not None and _has_camera(configuration, camera_name))
            or (telescope_id is not None and configuration.telescope.id == telescope_id)
        ]
        if active is not None:
            keep = [
                configuration
                for configuration in candidates
                if (camera_name is not None and configuration.telescope.id == active.telescope.id)
                or (telescope_id is not None and _has_camera(configuration, active.camera.name))
            ]
            candidates = keep or candidates
        if not candidates:
            return None
        return max(
            candidates, key=lambda configuration: (configuration.valid_from or date.min, configuration.id)
        )

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
            `True` if the camera is now active; `False` when no stored
            configuration has it or the settings do not list it.
        """
        configuration = self._choose(camera_name=camera_name)
        if configuration is None:
            return False
        try:
            self._observatory.equipment.set_active(configuration)
        except InvalidArgumentError:
            return False
        return True

    def get_active_telescope(self) -> Any:
        """Read the active telescope with its safety limits.

        Returns
        -------
        telescope : `Telescope` or `None`
            The active telescope (its altitude and hour angle limits and
            flip threshold are fields of it), or `None` when none is set up.
        """
        return self._observatory.equipment.status(include=["telescope"]).telescope

    def set_active_telescope(self, telescope_id: str, limits: dict[str, float | bool] | None = None) -> bool:
        """Choose the active telescope, and optionally save its safety limits.

        Parameters
        ----------
        telescope_id : `str`
            The telescope's id in the configuration.
        limits : `dict`, optional
            New safety limits, such as ``min_altitude_deg`` or
            ``max_hour_angle_hours``. The library checks them and writes them
            only to an existing config section: the mount section of the
            telescope's setup, or the telescope's own section.

        Returns
        -------
        saved : `bool`
            Whether the choice was saved; `False` when no stored
            configuration has the telescope.

        Raises
        ------
        ValueError
            If the limits are not valid (not a limit, a minimum above the
            maximum, a value out of range) or have no section to go to.
        """
        active = self._observatory.equipment.get_active()
        if active is None or active.telescope.id != telescope_id:
            configuration = self._choose(telescope_id=telescope_id)
            if configuration is None:
                return False
            try:
                self._observatory.equipment.set_active(configuration)
            except InvalidArgumentError:
                return False
        if limits:
            try:
                self._observatory.equipment.set_pointing_limits(**limits)
            except (InvalidArgumentError, NotFoundError, TypeError) as problem:
                raise ValueError(str(problem)) from None
        return True

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

    def enter_controller_mode(
        self, evidence_note: str = "", shadow_first: bool = False
    ) -> dict[str, dict[str, str]]:
        """Let this app drive every capability it is allowed to drive.

        Parameters
        ----------
        evidence_note : `str`, optional
            Why the change was made, kept with the record.
        shadow_first : `bool`, optional
            Move the capabilities that need it to the shadowed state first,
            so they can reach the authoritative one in the same call. The
            rules for that step still apply. Defaults to `False`.

        Returns
        -------
        outcome : `dict`
            ``{"applied": {capability: state}, "rejected": {capability:
            reason}}``.
        """
        return _outcome_as_dict(
            self._observatory.safety.enter_controller_mode(
                evidence_note=evidence_note, shadow_first=shadow_first
            )
        )


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
