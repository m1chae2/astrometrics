"""Purpose: The `get_status` reads of the `control` children.

Description: `control.equipment`, `control.imaging`, `control.guiding` and
`control.safety` each offer one `get_status(include=[...])` read. These are
the functions behind them. Each one checks the asked-for sections, fills
only those, and leaves the others as `None`. A section that touches a
device reads it now; the others read saved records. `control.mount` has
one fixed reply, `hardware_operations.mount_status`.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.models.control_status import EquipmentStatus, GuidingStatus, ImagingStatus, SafetyStatus
from wayfindinglib.tasks.control_tasks import hardware_operations

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.policy.delegation import ObservatoryCapability

__all__ = [
    "EQUIPMENT_SECTIONS",
    "GUIDING_SECTIONS",
    "IMAGING_SECTIONS",
    "SAFETY_SECTIONS",
    "read_equipment_status",
    "read_guiding_status",
    "read_imaging_status",
    "read_safety_status",
]

EQUIPMENT_SECTIONS = (
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
"""Sections `control.equipment.get_status` can read."""

DEFAULT_EQUIPMENT_SECTIONS = EQUIPMENT_SECTIONS[:7]
"""Sections read when `include` is omitted: the profile and the site.
The commissioning records can be long, so they are read only on request."""

IMAGING_SECTIONS = ("filters", "current_filter", "focuser", "camera", "focus_model", "capture")
"""Sections `control.imaging.get_status` can read."""

GUIDING_SECTIONS = ("calibration", "spectrum_analysis", "plate_scale", "live")
"""Sections `control.guiding.get_status` can read."""

SAVED_GUIDING_SECTIONS = ("calibration", "spectrum_analysis", "plate_scale")
"""The guiding sections read when none are named. They need no device."""

SAFETY_SECTIONS = (
    "rule_set",
    "assessment",
    "enclosure",
    "enclosure_state",
    "delegation_policy",
    "divergence",
)
"""Sections `control.safety.get_status` can read."""


def read_equipment_status(
    context: ControlContext, include: list[str] | None = None, device_name: str | None = None
) -> EquipmentStatus:
    """Read the equipment profile, the site and the device connections.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration, the storage and the INDI session.
    include : `list` [`str`], optional
        Sections to read (`EQUIPMENT_SECTIONS`). When omitted, every
        section but ``commissioning_runs`` and the two INDI sections, plus
        ``indi_properties`` when `device_name` is given.
    device_name : `str`, optional
        The INDI device whose properties ``indi_properties`` reads.

    Returns
    -------
    status : `EquipmentStatus`
        The sections read. The others stay `None`. The INDI sections are
        empty when the active mount does not use INDI.

    Raises
    ------
    InvalidArgumentError
        If ``indi_properties`` is asked for without `device_name`, or
        `device_name` is given without ``indi_properties``.
    """
    from wayfindinglib.tasks.control_tasks import equipment_activation

    if include is None:
        include = [*DEFAULT_EQUIPMENT_SECTIONS, *(["indi_properties"] if device_name is not None else [])]
    sections = hardware_operations.check_sections(include, EQUIPMENT_SECTIONS)
    if ("indi_properties" in sections) != (device_name is not None):
        raise InvalidArgumentError('The "indi_properties" section and device_name go together.')
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
    diagnostics = context.indi_diagnostics if {"indi_devices", "indi_properties"} & set(sections) else None
    if "indi_devices" in sections:
        status.indi_devices = diagnostics.get_devices() if diagnostics else []
    if device_name is not None:
        status.indi_properties = diagnostics.get_properties(device_name) if diagnostics else {}
    return status


def read_imaging_status(context: ControlContext, include: list[str] | None = None) -> ImagingStatus:
    """Read the filter wheel, the focuser, the camera and the focus model.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers and the storage.
    include : `list` [`str`], optional
        Sections to read (`IMAGING_SECTIONS`): ``filters``,
        ``current_filter``, ``focuser`` and ``camera`` read the devices,
        ``focus_model`` the saved model and ``capture`` whether a capture
        run this app started is going. All of them when omitted.

    Returns
    -------
    status : `ImagingStatus`
        The sections read. The others stay `None`.
    """
    sections = hardware_operations.check_sections(include, IMAGING_SECTIONS)
    status = ImagingStatus(sections=sections)
    if "filters" in sections:
        status.filter_names = hardware_operations.filter_names(context)
    if "current_filter" in sections:
        status.current_filter = hardware_operations.current_filter(context)
    if "focuser" in sections:
        status.focuser_position = hardware_operations.focuser_position(context)
    if "camera" in sections:
        status.camera_temperature, status.camera_status = hardware_operations.camera_reading(context)
    if "focus_model" in sections:
        status.focus_model = context.active_focus_model()
    if "capture" in sections:
        status.capture_running = context.capture_runs.is_running()
    return status


def read_guiding_status(context: ControlContext, include: list[str] | None = None) -> GuidingStatus:
    """Read the saved guider models, and the guiding going on now.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage, the guiding driver and the live monitor.
    include : `list` [`str`], optional
        Sections to read (`GUIDING_SECTIONS`). The three saved ones when
        omitted. ``live`` first reads what the guider measured since the
        last read (`live_guiding.poll`) and records those samples.

    Returns
    -------
    status : `GuidingStatus`
        The sections read. The others stay `None`.
    """
    from wayfindinglib.tasks.control_tasks import live_guiding

    sections = hardware_operations.check_sections(
        include if include is not None else list(SAVED_GUIDING_SECTIONS), GUIDING_SECTIONS
    )
    status = GuidingStatus(sections=sections)
    if "calibration" in sections:
        status.calibration = context.active_guider_calibration()
    if "spectrum_analysis" in sections:
        status.spectrum_analysis = context.active_guiding_spectrum_analysis()
    if "plate_scale" in sections:
        status.plate_scale_arcsec_per_px = context.guider_plate_scale_arcsec_per_px()
    if "live" in sections:
        live_guiding.poll(context)
        status.live = context.live_guiding.status()
    return status


def read_safety_status(
    context: ControlContext,
    include: list[str] | None = None,
    capability: ObservatoryCapability | None = None,
) -> SafetyStatus:
    """Read the safety rules, weather verdict, enclosure and policy.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage and the drivers.
    include : `list` [`str`], optional
        Sections to read (`SAFETY_SECTIONS`). Every section but
        ``divergence`` when omitted, plus ``divergence`` when `capability`
        is given.
    capability : `ObservatoryCapability`, optional
        The capability whose agreement evidence ``divergence`` summarizes.

    Returns
    -------
    status : `SafetyStatus`
        The sections read. The others stay `None`.

    Raises
    ------
    InvalidArgumentError
        If ``divergence`` is asked for without `capability`, or
        `capability` is given without ``divergence``.
    """
    from wayfindinglib.data_access.safety_policy_reader import get_safety_rule_set
    from wayfindinglib.tasks.control_tasks.capability_promotion import summarize_divergence_evidence

    if include is None:
        include = [name for name in SAFETY_SECTIONS if name != "divergence" or capability is not None]
    sections = hardware_operations.check_sections(include, SAFETY_SECTIONS)
    if ("divergence" in sections) != (capability is not None):
        raise InvalidArgumentError('The "divergence" section and capability go together.')
    status = SafetyStatus(sections=sections)
    if "rule_set" in sections:
        status.rule_set = get_safety_rule_set(context.butler)
    if "assessment" in sections:
        status.assessment = hardware_operations.refresh_safety_assessment(context)
    if "enclosure" in sections:
        status.enclosure = context.active_enclosure()
    if "enclosure_state" in sections:
        status.enclosure_state = hardware_operations.enclosure_state(context)
    if "delegation_policy" in sections:
        status.delegation_policy = context.delegation_policy()
    if capability is not None:
        summary = summarize_divergence_evidence(capability, context.butler.get_all("divergence_record"))
        status.divergence = {**dataclasses.asdict(summary), "agreement_rate": summary.agreement_rate}
    return status
