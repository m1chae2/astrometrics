"""Dependency injection container for the high-level interface backend.

Constructs and holds the single `Container` instance (`container`) that
`backend/routers/` reach through `backend.container.get_container()` to
obtain fully wired service instances.
"""

import os

from backend.services.analysis.analysis_orchestrator import AnalysisOrchestrator
from backend.services.data.image_service import ImageService
from backend.services.data.stellar_service import StellarService
from backend.services.data.target_service import TargetService
from backend.services.infrastructure.maintenance_service import MaintenanceService
from backend.services.infrastructure.notification_service import NotificationService
from backend.services.infrastructure.sync_service import SyncService
from backend.services.observatory.observatory_service import ObservatoryService
from backend.services.observatory.target_imaging_executor import TargetImagingExecutor
from backend.services.observatory.target_imaging_planner import TargetImagingPlanner
from backend.services.observatory.telescope_service import TelescopeService
from backend.services.processing.image_processing_service import ImageProcessingService
from backend.services.processing.job_service import JobService


class Container:
    """Dependency Injection Container.

    Centralizes the initialization of singletons and services to ensure
    dependencies are properly wired and shared across the application.
    """

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        # Core Infrastructure
        self.config_service = None
        self.indi_driver = None
        self.calibration_library = None

        # Domain Services
        self.astrometrics = None
        self.target_service = None
        self.stellar_object_service = None
        self.image_service = None
        self.image_processing_service = None
        self.sync_service = None
        self.remote_service = None
        self.socket_manager = None
        self.telescope_service = None
        self.notification_service = None
        self.scripting_service = None
        self.ingestion_service = None
        self.system_status_service = None
        self.guiding_service = None
        self.alignment_service = None
        self.mosaic_service = None
        self.analysis_orchestrator = None
        self.job_repository = None
        self.job_service = None
        self.maintenance_service = None
        self.target_imaging_planner = None
        self.target_imaging_executor = None
        self.execution_service = None
        self.stellar_service = None

        self.initialized = False

    def init_resources(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Initialize all resources.

        Safe to call multiple times; subsequent calls are no-ops once
        `initialized` is `True`.
        """
        if self.initialized:
            return

        # 1. Initialize Configuration
        from astrometricslib import get_configuration

        self.config_service = get_configuration()

        # 2. Initialize Data Services & Calibration
        from astrometricslib import Astrometrics
        from wayfindinglib import Wayfinder

        self.astrometrics = Astrometrics(self.config_service)
        self.wayfinder = Wayfinder(self.config_service)
        self.wayfinder.control.driver = self.indi_driver

        self.target_service = TargetService(self.config_service, astrometrics=self.astrometrics)
        self.stellar_object_service = StellarService(
            self.config_service, astrometrics=self.astrometrics, wayfinder=self.wayfinder
        )
        self.stellar_service = self.stellar_object_service
        self.image_service = ImageService(target_service=self.target_service)
        from astrometricslib import LoggerInterface

        self.job_repository = LoggerInterface(self.config_service.get_logs_db_path())
        self.job_service = JobService(self.job_repository)
        self.maintenance_service = MaintenanceService(self.job_service)
        # Reuse the calibration namespace's single CalibrationLibrary
        # instance rather than constructing a second one.
        self.calibration_library = self.astrometrics.processing.calibration.library

        # Load data from disk
        self.target_service.load_targets()
        self.stellar_object_service.load_stellar_objects()
        self.calibration_library.load_library()

        # 3. Initialize Hardware Drivers
        if os.getenv("ASTROMETRICS_TESTING"):
            from wayfindinglib import SimulatorIndiInterface

            self.indi_driver = SimulatorIndiInterface(config=self.config_service)
        else:
            from wayfindinglib import IndiInterface

            self.indi_driver = IndiInterface(config=self.config_service)

        self.wayfinder.control.driver = self.indi_driver

        # 4. Initialize Infrastructure Services
        from backend.services.infrastructure.remote_service import RemoteService

        self.remote_service = RemoteService(config_service=self.config_service)

        from backend.services.infrastructure.socket_manager import SocketManager

        self.socket_manager = SocketManager()

        from backend.services.infrastructure.astrometrics_service import AstrometricsService

        self.astrometrics_service = AstrometricsService(self.socket_manager)

        notification_path = os.path.join(os.path.dirname(__file__), "notifications.json")
        self.notification_service = NotificationService(storage_path=notification_path)

        from backend.services.observatory.guiding_service import GuidingService

        self.guiding_service = GuidingService(observatory_api=self.wayfinder.control)
        self.wayfinder.control.guiding_service = self.guiding_service

        # 5. Initialize Domain Services with proper DI
        self.telescope_service = TelescopeService(
            driver=self.indi_driver,
            guiding_service=self.guiding_service,
            target_service=self.target_service,
            wayfinder=self.wayfinder,
            astrometrics_service=self.astrometrics_service,
        )

        from astrometricslib import ImageProcessing

        siril_driver = ImageProcessing(
            config=self.config_service,
            calibration_library=self.calibration_library,
            job_repository=self.job_repository,
        )
        self.image_processing_service = ImageProcessingService(
            siril_driver=siril_driver,
            target_service=self.target_service,
            calibration_library=self.calibration_library,
            config_service=self.config_service,
            notification_service=self.notification_service,
            job_service=self.job_service,
            astrometrics_service=self.astrometrics_service,
        )

        self.analysis_orchestrator = AnalysisOrchestrator(
            config_service=self.config_service,
            stellar_service=self.stellar_object_service,
            target_service=self.target_service,
            notification_service=self.notification_service,
            job_service=self.job_service,
            astrometrics=self.astrometrics,
        )

        self.sync_service = SyncService(remote_service=self.remote_service)

        from backend.services.observatory.imaging_service import ImagingService

        self.imaging_service = ImagingService(indi_interface=self.indi_driver, job_service=self.job_service)

        self.target_imaging_planner = TargetImagingPlanner()
        self.target_imaging_executor = TargetImagingExecutor(
            telescope_service=self.telescope_service, imaging_service=self.imaging_service
        )

        # Adapter over wayfindinglib's Observation Execution astrometrics,
        # which had no route into the application at all before this.
        from backend.services.observatory.execution_service import ExecutionService

        self.execution_service = ExecutionService(
            wayfinder=self.wayfinder, astrometrics=self.astrometrics, config=self.config_service
        )

        from astrometricslib import StarIdentifier
        from backend.services.observatory.alignment_service import AlignmentService

        star_identifier = StarIdentifier(config=self.config_service)
        self.alignment_service = AlignmentService(
            indi_interface=self.indi_driver,
            imaging_service=self.imaging_service,
            star_identifier=star_identifier,
        )
        self.telescope_service._alignment_service = self.alignment_service

        from backend.services.observatory.mosaic_service import MosaicService

        self.mosaic_service = MosaicService(target_manager=self.target_service, wayfinder=self.wayfinder)

        from backend.services.processing.ingestion_service import IngestionService

        self.ingestion_service = IngestionService(
            target_service=self.target_service,
            config_service=self.config_service,
            calibration_library=self.calibration_library,
            stellar_service=self.stellar_object_service,
            job_service=self.job_service,
            image_processing_service=self.image_processing_service,
            wayfinder=self.wayfinder,
        )

        from backend.services.infrastructure.system_status_service import SystemStatusService

        self.system_status_service = SystemStatusService(
            telescope_service=self.telescope_service,
            image_processing_service=self.image_processing_service,
            astrometrics_service=self.astrometrics_service,
        )

        from backend.services.infrastructure.scripting_service import ScriptingService

        self.scripting_service = ScriptingService(self)

        # Wire the optional injected services into the Wayfinder domain
        # high-level interface
        self.wayfinder.control.sync_service = self.sync_service

        # Initialize ObservatoryService (peripheral state)
        self.observatory_service = ObservatoryService(self.indi_driver)

        # 7. Start Background Maintenance
        self.maintenance_service.system_status_service = self.system_status_service
        self.maintenance_service.telescope_service = self.telescope_service
        self.maintenance_service.start()

        self.initialized = True


# Global Container Instance
container = Container()


def get_container() -> Container:
    """Dependency Injection helper for FastAPI.

    Returns
    -------
    container : `Container`
        The module-level singleton `Container` instance.
    """
    return container
