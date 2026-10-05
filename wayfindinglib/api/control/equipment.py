"""Purpose: `control.equipment`, the equipment profile and device connections.

Description: Reads the active telescope, cameras and guide scope, the
camera profiles, the site location and the commissioning records;
connects and disconnects the devices; chooses the active telescope and
camera; and gives raw access to INDI device properties for diagnosis.
It also holds two small calculations about the equipment: the camera
cooling ramp rate and a device's state from its raw signals.
"""

from typing import Any

from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import EquipmentStatus
from wayfindinglib.models.equipment_and_site.equipment import CoolingPolicy
from wayfindinglib.models.policy.commissioning import CommissioningRun
from wayfindinglib.models.policy.device_state import DeviceRole, DeviceState

__all__ = ["EquipmentControl"]

STATUS_SECTIONS = (
    "telescope",
    "camera",
    "guide_scope",
    "guide_camera",
    "camera_profiles",
    "configuration",
    "observer_location",
    "commissioning_runs",
    "indi_devices",
    "indi_properties",
)
"""Sections `EquipmentControl.status` can read."""

DEFAULT_STATUS_SECTIONS = STATUS_SECTIONS[:7]
"""Sections read when `include` is omitted: the profile and the site.
The commissioning records can be long, so they are read only on request."""


class EquipmentControl(ControlChild):
    """Manage the equipment profile and the device connections."""

    def status(self, include: list[str] | None = None, device_name: str | None = None) -> EquipmentStatus:
        """Read the equipment profile, the site and the device connections.

        Parameters
        ----------
        include : `list` [`str`], optional
            Sections to read: ``telescope``, ``camera``, ``guide_scope``
            and ``guide_camera`` (the active equipment),
            ``camera_profiles`` (every profile in the configuration),
            ``configuration`` (the active pairing with its field of view),
            ``observer_location`` (from the mount, else the configuration),
            ``commissioning_runs`` (recorded drills), ``indi_devices``
            (connected INDI devices) and ``indi_properties`` (every property
            of `device_name`). When omitted, every section but
            ``commissioning_runs`` and the two INDI sections, plus
            ``indi_properties`` when `device_name` is given.
        device_name : `str`, optional
            The INDI device to inspect. Used only by ``indi_properties``,
            which needs it.

        Returns
        -------
        status : `EquipmentStatus`
            The sections read. The others stay `None`. The INDI sections
            are empty when the active mount does not use INDI.

        Raises
        ------
        InvalidArgumentError
            If ``indi_properties`` is asked for without `device_name`, or
            `device_name` is given without ``indi_properties``.
        """
        from astrometricslib import InvalidArgumentError
        from wayfindinglib.tasks.control_tasks import equipment_activation, hardware_operations

        if include is None:
            include = [*DEFAULT_STATUS_SECTIONS, *(["indi_properties"] if device_name is not None else [])]
        sections = hardware_operations.check_sections(include, STATUS_SECTIONS)
        if ("indi_properties" in sections) != (device_name is not None):
            raise InvalidArgumentError('The "indi_properties" section and device_name go together.')
        context = self._context
        status = EquipmentStatus(sections=sections)
        if "telescope" in sections:
            status.telescope = context.active_telescope()
        if "camera" in sections:
            status.camera = context.active_camera()
        if "guide_scope" in sections:
            status.guide_scope = context.active_guide_scope()
        if "guide_camera" in sections:
            status.guide_camera = context.active_guide_camera()
        if "camera_profiles" in sections:
            status.camera_profiles = equipment_activation.list_camera_profiles(context.config)
        if "configuration" in sections:
            status.configuration = equipment_activation.get_equipment_configuration(context.config)
        if "observer_location" in sections:
            status.observer_location = context.observer_location()
        if "commissioning_runs" in sections:
            status.commissioning_runs = context.butler.get_all("commissioning_run")
        diagnostics = (
            context.indi_diagnostics if {"indi_devices", "indi_properties"} & set(sections) else None
        )
        if "indi_devices" in sections:
            status.indi_devices = diagnostics.get_devices() if diagnostics else []
        if device_name is not None:
            status.indi_properties = diagnostics.get_properties(device_name) if diagnostics else {}
        return status

    def connect(self) -> bool:
        """Connect every device.

        Returns
        -------
        success : `bool`
            Always `True`; each driver connects on a best-effort basis.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.connect(self._context)

    def disconnect(self) -> bool:
        """Disconnect every device, the safe alternative to unplugging it.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.disconnect(self._context)

    def set_active_telescope(self, telescope_id: str) -> bool:
        """Choose the active telescope and save the choice.

        Parameters
        ----------
        telescope_id : `str`
            The telescope's id in the configuration.

        Returns
        -------
        success : `bool`
            Whether the choice was saved.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import set_active_telescope

        return set_active_telescope(self._context.config, telescope_id)

    def set_active_camera(self, camera_id: str) -> bool:
        """Choose the active main camera and save the choice.

        Parameters
        ----------
        camera_id : `str`
            The camera's id in the configuration.

        Returns
        -------
        success : `bool`
            Whether the choice was saved.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import set_active_camera

        return set_active_camera(self._context.config, camera_id)

    def set_device_property(
        self, device_name: str, property_name: str, value: Any, element: str | None = None
    ) -> bool:
        """Set one raw INDI property element on a device.

        For diagnosis only. Does nothing when the active mount does not
        use INDI.

        Parameters
        ----------
        device_name : `str`
            The INDI device.
        property_name : `str`
            The property to change.
        value : `Any`
            The new value.
        element : `str`, optional
            The element of the property to change.

        Returns
        -------
        success : `bool`
            `True` if the element was set, `False` off INDI.
        """
        diagnostics = self._context.indi_diagnostics
        if diagnostics is None:
            return False
        return diagnostics.set_property(device_name, property_name, value, element)

    def cooling_ramp_rate(self, policy: CoolingPolicy) -> float:
        """Return the active camera's cooling ramp rate, capped at its maximum.

        Parameters
        ----------
        policy : `CoolingPolicy`
            The requested cooling policy.

        Returns
        -------
        ramp_rate_c_per_min : `float`
            The ramp rate in degrees Celsius per minute.
        """
        from wayfindinglib.tasks.control_tasks.cooling_control import effective_ramp_rate_c_per_min

        return effective_ramp_rate_c_per_min(policy, self._context.active_camera())

    def summarize_device(
        self,
        device_id: str,
        device_role: DeviceRole,
        is_present: bool,
        is_connected: bool,
        allow_commands: bool,
        has_alert: bool,
        fault_detail: str | None = None,
    ) -> DeviceState:
        """Classify one device's raw signals into a `DeviceState`.

        Parameters
        ----------
        device_id : `str`
            The device.
        device_role : `DeviceRole`
            What the device does.
        is_present, is_connected, allow_commands, has_alert : `bool`
            The device's raw signals.
        fault_detail : `str`, optional
            The device's own fault message.

        Returns
        -------
        state : `DeviceState`
            The classified state.
        """
        from wayfindinglib.tasks.control_tasks.device_state_tasks import summarize_device

        return summarize_device(
            device_id, device_role, is_present, is_connected, allow_commands, has_alert, fault_detail
        )

    def save_commissioning_run(self, run: CommissioningRun) -> None:
        """Save one commissioning drill.

        Saving again under the same id replaces the record, so give each
        drill a new id.

        Parameters
        ----------
        run : `CommissioningRun`
            The drill and its results.
        """
        self._context.butler.put(run, "commissioning_run", {"id": run.id})
