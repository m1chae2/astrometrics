"""Purpose: `control.equipment`, the equipment profile and device connections.

Description: Reads the active telescope, cameras and guide scope, the
camera profiles, the site location and the commissioning records;
connects and disconnects the devices; reads and chooses the active
observatory configuration and saves the telescope's safety limits; and gives
raw access to INDI device properties for diagnosis.
It also holds two small calculations about the equipment: the camera
cooling ramp rate and a device's state from its raw signals.
"""

from typing import Any

from astrometricslib import ObservatoryConfiguration
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import EquipmentStatus
from wayfindinglib.models.policy.commissioning import CommissioningRun

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
        from astrometricslib.exceptions import InvalidArgumentError
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
            status.camera_profiles = equipment_activation.list_camera_profiles(
                context.config, context.astrometrics
            )
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

    def apply_configuration(self) -> None:
        """Make the INDI session read the telescope hostname again.

        Call it after the configuration changed, so a new hostname is used
        on the next connection. It points the session at the host and does
        not reconnect or move anything.
        """
        self._context.driver.sync_configuration()

    def get_active(self) -> ObservatoryConfiguration | None:
        """Read the observatory configuration that is active now.

        One configuration is active at a time. The core library keeps every
        past configuration (``astrometrics.observatories.configurations``);
        when the active equipment has no stored configuration yet, one is
        created from the settings.

        Owner:
            wayfindinglib.tasks.control_tasks.equipment_activation.get_active_configuration

        Returns
        -------
        configuration : `ObservatoryConfiguration` or `None`
            The active configuration, or `None` when the settings make no
            telescope or no camera active. Its telescope's pointing limits
            are those saved with the configuration; `status` gives the
            current ones.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import get_active_configuration

        return get_active_configuration(self._context.config, self._context.astrometrics)

    def set_active(self, configuration: ObservatoryConfiguration) -> None:
        """Make an observatory configuration the active one, and save it.

        Its telescope, camera, guide scope and guide camera become the active
        ones; with setups in the settings, the setup that pairs the camera
        with the telescope becomes active.

        Owner:
            wayfindinglib.tasks.control_tasks.equipment_activation.set_active_configuration

        Parameters
        ----------
        configuration : `ObservatoryConfiguration`
            A stored configuration, from
            ``astrometrics.observatories.configurations``.

        Notes
        -----
        Raises `InvalidArgumentError` and saves nothing when the
        configuration is not stored or the settings do not list its
        equipment.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import set_active_configuration

        set_active_configuration(self._context.config, configuration)

    def set_pointing_limits(
        self,
        *,
        altitude_limits_enabled: bool | None = None,
        min_altitude_deg: float | None = None,
        max_altitude_deg: float | None = None,
        hour_angle_limits_enabled: bool | None = None,
        max_hour_angle_hours: float | None = None,
        flip_hour_angle_deg: float | None = None,
    ) -> None:
        """Save new safety limits for the active telescope.

        Each limit left as `None` keeps its value. The limits are checked,
        then written only to the settings section that already holds the
        telescope's settings (a section is never created). A per-telescope
        minimum or maximum altitude overrides the global
        ``[Observatory.Constraints]`` section. The current limits are read
        back with `status` (the ``telescope`` section).

        Owner:
            wayfindinglib.tasks.control_tasks.equipment_activation.set_pointing_limits

        Parameters
        ----------
        altitude_limits_enabled : `bool`, optional
            Whether the altitude limits apply.
        min_altitude_deg, max_altitude_deg : `float`, optional
            The lowest and highest altitude to point at, in degrees.
        hour_angle_limits_enabled : `bool`, optional
            Whether the hour angle limit applies.
        max_hour_angle_hours : `float`, optional
            The largest hour angle to point at, in hours.
        flip_hour_angle_deg : `float`, optional
            The hour angle past the meridian at which the mount flips, in
            degrees.

        Notes
        -----
        Raises `InvalidArgumentError` and saves nothing when no telescope is
        active, the values fail the checks (such as a minimum above the
        maximum), or the telescope has no settings section.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import set_pointing_limits

        limits = {
            "altitude_limits_enabled": altitude_limits_enabled,
            "min_altitude_deg": min_altitude_deg,
            "max_altitude_deg": max_altitude_deg,
            "hour_angle_limits_enabled": hour_angle_limits_enabled,
            "max_hour_angle_hours": max_hour_angle_hours,
            "flip_hour_angle_deg": flip_hour_angle_deg,
        }
        set_pointing_limits(
            self._context.config, {key: value for key, value in limits.items() if value is not None}
        )

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

    def _save_commissioning_run(self, run: CommissioningRun) -> None:
        """Save one commissioning drill.

        Saving again under the same id replaces the record, so give each
        drill a new id.

        Parameters
        ----------
        run : `CommissioningRun`
            The drill and its results.
        """
        self._context.butler.put(run, "commissioning_run", {"id": run.id})
