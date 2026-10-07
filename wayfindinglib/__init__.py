"""Purpose: The Wayfinder, the entry point to the wayfinding library.

Description: `Wayfinder` composes the library's three sub-APIs:
`control` (operate the observatory), `planning` (decide what to
observe) and `execution` (run and recover an observing session).
`control` has seven children named by topic, such as `control.mount`
and `control.history`. The package root also offers the models those
sub-APIs take and return, and the library's own error class. The error
categories themselves (`NotFoundError`, `InvalidArgumentError` and the
rest) come from `astrometricslib`.

Every name below except `Wayfinder` is loaded on first use (module
`__getattr__`, PEP 562) from one lookup table. Importing a submodule
such as `wayfindinglib.api.planning` runs this file first, so an eager
import of the INDI drivers here would make every import of the library
load hardware code. The `TYPE_CHECKING` imports never run; they only let
type checkers see each name in `__all__`.
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import TYPE_CHECKING

from astrometricslib import configure_offline_iers

# A library only writes log messages. A program decides where they go, by
# calling `configure_logging`. The null handler stops Python from printing a
# "no handlers" warning when no program has done so.
logging.getLogger(__name__).addHandler(logging.NullHandler())

# Every program that uses the library works offline: astropy uses its bundled
# Earth-rotation table instead of downloading a new one.
configure_offline_iers()

if TYPE_CHECKING:
    from astrometricslib import AppConfiguration, Astrometrics
    from wayfindinglib.api.control import ObservatoryControl
    from wayfindinglib.api.execution import ObservationExecution
    from wayfindinglib.api.planning import ObservationPlanning
    from wayfindinglib.data_access.delegation_policy_reader import DelegationPolicyValidationError
    from wayfindinglib.drivers.indi_interface import IndiInterface
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.control_status import (
        EquipmentStatus,
        GuidingStatus,
        ImagingStatus,
        SafetyStatus,
    )
    from wayfindinglib.models.equipment_and_site.calibration import CalibrationAdvisory
    from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration, Telescope
    from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
    from wayfindinglib.models.planning.deep_catalog import DeepCatalogEstimate, DeepCatalogStatus
    from wayfindinglib.models.planning.mosaic import MosaicPanel, MosaicPlan
    from wayfindinglib.models.planning.observation_package import (
        DitherConfig,
        ExposureRequest,
        FrameType,
        ObservationPackage,
    )
    from wayfindinglib.models.planning.quality_advisory import TargetQualityAdvisory
    from wayfindinglib.models.planning.sequence_plan import SequenceItem, SequencePlan
    from wayfindinglib.models.planning.sky_source import SkySource
    from wayfindinglib.models.planning.visibility import (
        HorizonZone,
        MeridianStatus,
        NightConditions,
        ObjectVisibility,
        VisibilityReport,
        VisibilitySpan,
    )
    from wayfindinglib.models.session.capture_result import CaptureResult
    from wayfindinglib.models.session.observation_session import (
        ObservationSession,
        ObservationSessionSummary,
        QueueRequest,
        StartTimeMode,
    )
    from wayfindinglib.models.session.telemetry import (
        AlignmentAttempt,
        AlignmentTargetSession,
        LiveGuidingStatus,
        MountPointingModel,
    )
    from wayfindinglib.models.sky_position import SkyPosition

try:
    # Single source of truth is pyproject.toml; both libraries ship from the
    # same `astrometrics` distribution, so neither carries its own literal.
    __version__ = _distribution_version("astrometrics")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

_LAZY_EXPORTS = {
    # The sub-APIs and the drivers that tests and the backend put in.
    "ObservatoryControl": "wayfindinglib.api.control",
    "ObservationPlanning": "wayfindinglib.api.planning",
    "ObservationExecution": "wayfindinglib.api.execution",
    "IndiInterface": "wayfindinglib.drivers.indi_interface",
    "SimulatorIndiInterface": "wayfindinglib.drivers.simulators.indi_simulator",
    # Models that `control` takes and returns.
    "SkyPosition": "wayfindinglib.models.sky_position",
    "MountPointingModel": "wayfindinglib.models.session.telemetry",
    "LiveGuidingStatus": "wayfindinglib.models.session.telemetry",
    "AlignmentAttempt": "wayfindinglib.models.session.telemetry",
    "AlignmentTargetSession": "wayfindinglib.models.session.telemetry",
    "ImagingStatus": "wayfindinglib.models.control_status",
    "GuidingStatus": "wayfindinglib.models.control_status",
    "SafetyStatus": "wayfindinglib.models.control_status",
    "EquipmentStatus": "wayfindinglib.models.control_status",
    "CaptureResult": "wayfindinglib.models.session.capture_result",
    # Models that `planning` and `execution` take and return.
    "VisibilityReport": "wayfindinglib.models.planning.visibility",
    "ObjectVisibility": "wayfindinglib.models.planning.visibility",
    "VisibilitySpan": "wayfindinglib.models.planning.visibility",
    "MeridianStatus": "wayfindinglib.models.planning.visibility",
    "NightConditions": "wayfindinglib.models.planning.visibility",
    "HorizonZone": "wayfindinglib.models.planning.visibility",
    "TargetQualityAdvisory": "wayfindinglib.models.planning.quality_advisory",
    "CalibrationAdvisory": "wayfindinglib.models.equipment_and_site.calibration",
    "MosaicPanel": "wayfindinglib.models.planning.mosaic",
    "MosaicPlan": "wayfindinglib.models.planning.mosaic",
    "SequenceItem": "wayfindinglib.models.planning.sequence_plan",
    "SequencePlan": "wayfindinglib.models.planning.sequence_plan",
    "ObservationPackage": "wayfindinglib.models.planning.observation_package",
    "ExposureRequest": "wayfindinglib.models.planning.observation_package",
    "DitherConfig": "wayfindinglib.models.planning.observation_package",
    "FrameType": "wayfindinglib.models.planning.observation_package",
    "ObservationSession": "wayfindinglib.models.session.observation_session",
    "ObservationSessionSummary": "wayfindinglib.models.session.observation_session",
    "QueueRequest": "wayfindinglib.models.session.observation_session",
    "StartTimeMode": "wayfindinglib.models.session.observation_session",
    "DeepCatalogStatus": "wayfindinglib.models.planning.deep_catalog",
    "SkySource": "wayfindinglib.models.planning.sky_source",
    "DeepCatalogEstimate": "wayfindinglib.models.planning.deep_catalog",
    "SiteProfile": "wayfindinglib.models.equipment_and_site.site_profile",
    "Telescope": "wayfindinglib.models.equipment_and_site.equipment",
    "EquipmentConfiguration": "wayfindinglib.models.equipment_and_site.equipment",
    # The library's own error class. The categories come from astrometricslib.
    "DelegationPolicyValidationError": "wayfindinglib.data_access.delegation_policy_reader",
}
"""Export name -> the module that defines it."""

__all__ = sorted([*_LAZY_EXPORTS, "Wayfinder"])


def __getattr__(name: str) -> type:
    """Load an export on first use, so no hardware code loads early.

    Parameters
    ----------
    name : `str`
        The attribute asked for.

    Returns
    -------
    resolved : `type`
        The exported class.

    Raises
    ------
    AttributeError
        If `name` is not an export of this package.
    """
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(_LAZY_EXPORTS[name]), name)


class Wayfinder:
    """The entry point to the wayfinding library.

    Builds and holds the three sub-APIs, `control`, `planning` and
    `execution`, over one configuration, one `DiskButler` and one
    `Astrometrics` handle, so all three see the same records. It has no
    methods of its own.

    The watchdog (`wayfindinglib/watchdog/`) is not built here on
    purpose: it must not be importable by the code it watches.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        astrometrics: Astrometrics | None = None,
    ) -> None:
        """Build the three sub-APIs over a shared configuration and storage.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The application configuration. Loaded when omitted.
        astrometrics : `Astrometrics`, optional
            The science library handle every part of the Wayfinder uses, so
            all of them see the same target catalog. A new one is built over
            the configuration when omitted.
        """
        from astrometricslib import Astrometrics, get_configuration
        from wayfindinglib.api.control import ObservatoryControl
        from wayfindinglib.api.execution import ObservationExecution
        from wayfindinglib.api.planning import ObservationPlanning
        from wayfindinglib.drivers.butler import DiskButler

        self.config = config or get_configuration()

        self.astrometrics = astrometrics or Astrometrics(self.config)

        butler = DiskButler(app_config=self.config)
        self.control = ObservatoryControl(config=self.config, butler=butler, astrometrics=self.astrometrics)
        self.planning = ObservationPlanning(self.config, butler, astrometrics=self.astrometrics)
        self.execution = ObservationExecution(
            self.config, butler, astrometrics=self.astrometrics, control=self.control
        )
