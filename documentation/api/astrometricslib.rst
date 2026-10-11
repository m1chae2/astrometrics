.. ==============================================================================
.. Purpose: API Reference documentation for astrometricslib using automodapi.
.. Documents the top-level facade and primary domain models. Low-level internal
.. storage and helper functions are skipped to keep the public surface clear.
.. ==============================================================================

Astrometrics Library (``astrometricslib``)
===========================================

Overview
--------

``astrometricslib`` provides astronomical image processing, calibration, plate
solving, photometry, spectroscopy, and moving object detection pipelines.

The primary entry point is the :class:`~astrometricslib.Astrometrics` façade class.
Instantiate it with an :class:`~astrometricslib.AppConfiguration` to access all
sub-APIs:

.. code-block:: python

   from astrometricslib import Astrometrics, get_configuration

   config = get_configuration()
   astrometrics = Astrometrics(config)

Compositional Sub-APIs
----------------------

The :class:`~astrometricslib.Astrometrics` instance provides access to five specialized sub-APIs:

* :attr:`~astrometricslib.Astrometrics.targets` (:class:`~astrometricslib.TargetCatalog`):
  Query, inspect, and manage celestial observation targets and their associated FITS frames.
* :attr:`~astrometricslib.Astrometrics.stars` (:class:`~astrometricslib.StellarCatalog`):
  Query SIMBAD and Gaia DR3/XP stellar databases, cross-match field stars, and analyze light curves.
* :attr:`~astrometricslib.Astrometrics.processing` (:class:`~astrometricslib.ProcessingPipelines`):
  Image stacking, master calibration frame matching (:attr:`~astrometricslib.ProcessingPipelines.calibration`),
  and automated pipeline quality verification (:attr:`~astrometricslib.ProcessingPipelines.diagnostics`).
* :attr:`~astrometricslib.Astrometrics.visualization` (:class:`~astrometricslib.Visualization`):
  Render high-dynamic-range astronomical previews, apply stretching algorithms, and compute coordinate overlays.
* :attr:`~astrometricslib.Astrometrics.jobs` (:class:`~astrometricslib.Jobs`):
  Monitor, inspect, and query asynchronous pipeline execution jobs.

Core Domain Models
------------------

The sub-APIs operate on unified observational domain models:

* :class:`~astrometricslib.Target`: Central entity representing a celestial target, its exposure frames, and stack results.
* :class:`~astrometricslib.StellarObject`: Astrometric, photometric, and spectroscopic measurements for a single star.
* :class:`~astrometricslib.FrameRecord`: Metadata and measured quality metrics for an individual FITS exposure.

API Specification
-----------------

.. automodapi:: astrometricslib
   :no-inheritance-diagram:
   :skip: AbstractButler
   :skip: AbstractCatalogAccess
   :skip: ActivityDescription
   :skip: Agent
   :skip: AgentType
   :skip: AnalysisResult
   :skip: AppConfiguration
   :skip: AsteroidDetectionCandidate
   :skip: AstrometryPipelineQualityMetrics
   :skip: AstrometryQualitySummary
   :skip: BatchRunSummary
   :skip: Butler
   :skip: CatalogAccess
   :skip: Collection
   :skip: ConfigFile
   :skip: ConfigFileDescription
   :skip: DatasetDescription
   :skip: DatasetEntity
   :skip: DatasetSpec
   :skip: DbLogHandler
   :skip: DeviceInUseError
   :skip: EntityDescription
   :skip: ErrorInfo
   :skip: FileItem
   :skip: FilterType
   :skip: FitsHeaderEntry
   :skip: FrameSelection
   :skip: GenerationDescription
   :skip: GroupedFrameStat
   :skip: ImageProcessing
   :skip: JobStore
   :skip: MovingObjectConfig
   :skip: NumpyEncoder
   :skip: Parameter
   :skip: ParameterDescription
   :skip: PhotometryResult
   :skip: PlotData
   :skip: ProcessingJob
   :skip: ProvenanceStore
   :skip: RenderedImage
   :skip: SpectroscopyResult
   :skip: StorageError
   :skip: StorageNotMountedError
   :skip: TargetFilesResponse
   :skip: TargetSessionContribution
   :skip: UsageDescription
   :skip: Used
   :skip: ValueDescription
   :skip: ValueEntity
   :skip: VariableCandidate
   :skip: WasAssociatedWith
   :skip: WasAttributedTo
   :skip: WasConfiguredBy
   :skip: WasGeneratedBy
   :skip: acquire_resource_slot
   :skip: background_job
   :skip: capture_job_logs
   :skip: check_choice
   :skip: check_include
   :skip: classify_and_sort_fits_files
   :skip: close_interrupted_jobs
   :skip: configure_logging
   :skip: configure_offline_iers
   :skip: connect_db
   :skip: derive_target_sessions
   :skip: error_from_info
   :skip: export_target_lineage_as_prov_xml
   :skip: file_lock
   :skip: frame_is_spectral
   :skip: get_configuration
   :skip: get_current_job
   :skip: get_log_context
   :skip: has_catalog_magnitude
   :skip: is_path_inside
   :skip: log_context
   :skip: new_request_id
   :skip: observing_night_id
   :skip: parse_coordinate_string
   :skip: parse_iso_time
   :skip: preview_path_for
   :skip: registered_job
   :skip: reject_unused_arguments
   :skip: require_mounted_storage
   :skip: resolve_camera_profile
   :skip: resolve_mounted_path
   :skip: resolve_target
   :skip: run_as_background_job
   :skip: safe_json_dumps
   :skip: select_library_frames
   :skip: to_epoch_seconds
   :skip: to_error_info
   :skip: warm_earth_orientation_data
