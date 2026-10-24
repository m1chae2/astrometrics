.. ==============================================================================
.. Purpose: API Reference documentation for astrometricslib using automodapi.
.. Documents every name in astrometricslib.__all__. The summary tables group the
.. names by purpose; the automodapi block below renders each one.
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

Compositional sub-APIs
----------------------

The :class:`~astrometricslib.Astrometrics` instance provides access to six specialized sub-APIs:

* :attr:`~astrometricslib.Astrometrics.targets` (:class:`~astrometricslib.TargetCatalog`):
  Query, inspect, and manage celestial observation targets and their associated FITS frames.
* :attr:`~astrometricslib.Astrometrics.stars` (:class:`~astrometricslib.StellarCatalog`):
  Query SIMBAD and Gaia DR3/XP stellar databases, cross-match field stars, and analyze light curves.
* :attr:`~astrometricslib.Astrometrics.processing` (:class:`~astrometricslib.ProcessingPipelines`):
  Image stacking and master calibration frame matching (:attr:`~astrometricslib.ProcessingPipelines.calibration`).
* :attr:`~astrometricslib.Astrometrics.quality` (:class:`~astrometricslib.QualityRecords`):
  Stored quality measurements, the list of quality metrics, and live checks of raw frames and stacks.
* :attr:`~astrometricslib.Astrometrics.visualization` (:class:`~astrometricslib.Visualization`):
  Render high-dynamic-range astronomical previews, apply stretching algorithms, and compute coordinate overlays.
* :attr:`~astrometricslib.Astrometrics.jobs` (:class:`~astrometricslib.Jobs`):
  Monitor, inspect, and query asynchronous pipeline execution jobs.

Core domain models
------------------

The sub-APIs operate on unified observational domain models:

* :class:`~astrometricslib.Target`: Central entity representing a celestial target, its exposure frames, and stack results.
* :class:`~astrometricslib.StellarObject`: Astrometric, photometric, and spectroscopic measurements for a single star.
* :class:`~astrometricslib.FrameRecord`: Metadata and measured quality metrics for an individual FITS exposure.

Names by purpose
----------------

Every public name belongs to one group below. The "Advanced" groups are for programmers who extend the storage, provenance and job layers; most users never need them.

.. currentmodule:: astrometricslib

Entry points and sub-APIs
^^^^^^^^^^^^^^^^^^^^^^^^^

.. autosummary::

   Astrometrics
   TargetCatalog
   StellarCatalog
   ProcessingPipelines
   Visualization
   Jobs
   QualityRecords
   CalibrationCatalog
   ImageProcessing

Configuration and setup
^^^^^^^^^^^^^^^^^^^^^^^

.. autosummary::

   AppConfiguration
   get_configuration
   ConfigFile
   ConfigFileDescription
   resolve_camera_profile
   AppliedCameraProfile
   configure_logging
   configure_offline_iers
   warm_earth_orientation_data
   locks_directory
   scratch_directory
   DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
   SATURATED_BLOB_MINIMUM_PIXELS
   SATURATED_FRAME_FRACTION

Domain models and helpers
^^^^^^^^^^^^^^^^^^^^^^^^^

.. autosummary::

   Target
   StellarObject
   FrameRecord
   FrameSelection
   FilterType
   TargetFilesResponse
   TargetSessionContribution
   TargetStarCount
   ExposureGroupSummary
   FitsHeaderEntry
   FileItem
   OverlayStar
   ViewableImage
   RenderedImage
   PlotData
   GroupedFrameStat
   derive_target_sessions
   select_library_frames
   resolve_target
   frame_is_spectral
   has_catalog_magnitude
   classify_and_sort_fits_files
   observing_night_id
   preview_path_for
   parse_coordinate_string

Pipeline results and quality reports
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autosummary::

   AnalysisResult
   PhotometryResult
   SpectroscopyResult
   AsteroidDetectionCandidate
   MovingObjectConfig
   VariableCandidate
   StackResult
   StackRunResult
   StackSettings
   StackSummary
   StackQualityReport
   InputQualityReport
   RawFrameCheckReport
   SpectralFrameCheckReport
   AstrometryPipelineQualityMetrics
   AstrometryQualitySummary
   BatchRunSummary
   ProcessTargetResult
   PreviewRemakeResult
   QuarantinePreview
   SetAsideFrame
   CalibrationQueryResult
   TargetQueryResult
   StarQueryResult
   ReindexReport
   RestoreReport
   TargetReindexChange
   ReddeningEstimate

Drivers and catalog access
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autosummary::

   GaiaXpDriver
   PlateSolveDriver
   PlateSolveHeader
   ReddeningDriver
   SimbadDriver
   StackingDriver
   CatalogAccess
   AbstractCatalogAccess

Errors and argument checks
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autosummary::

   AstrometricsError
   ConfigurationError
   ConflictError
   ExternalServiceError
   HardwareError
   InvalidArgumentError
   NotFoundError
   PermissionDeniedError
   PlateSolveFailedError
   ProcessingError
   DeviceInUseError
   StorageError
   StorageNotMountedError
   ErrorInfo
   error_from_info
   to_error_info
   DATA_ERRORS
   FITS_READ_ERRORS
   ONLINE_QUERY_ERRORS
   RPC_CODES
   check_choice
   check_include
   reject_unused_arguments

Advanced: storage and provenance
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autosummary::

   AbstractButler
   Butler
   Collection
   DatasetDescription
   DatasetEntity
   DatasetSpec
   EntityDescription
   Entity
   ValueDescription
   ValueEntity
   Parameter
   ParameterDescription
   Activity
   ActivityDescription
   Agent
   AgentType
   Used
   WasAssociatedWith
   WasAttributedTo
   WasConfiguredBy
   WasGeneratedBy
   GenerationDescription
   UsageDescription
   ProvenanceStore
   export_target_lineage_as_prov_xml
   connect_db
   DbLogHandler

Advanced: background jobs and utilities
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autosummary::

   JobStore
   ProcessingJob
   acquire_resource_slot
   background_job
   capture_job_logs
   close_interrupted_jobs
   get_current_job
   get_log_context
   log_context
   new_request_id
   registered_job
   run_as_background_job
   file_lock
   is_path_inside
   require_mounted_storage
   resolve_mounted_path
   safe_json_dumps
   NumpyEncoder
   parse_iso_time
   to_epoch_seconds

API specification
-----------------

.. automodapi:: astrometricslib
   :no-inheritance-diagram:
   :include-all-objects:
