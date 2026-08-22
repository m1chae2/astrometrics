.. ==============================================================================
.. Purpose: API Reference documentation for astrometricslib using automodapi.
.. Documents the top-level facade only -- astrometricslib.api and every
.. other subpackage are internal; import everything from the top-level
.. `astrometricslib` namespace instead. A single automodapi block avoids
.. generating a duplicate stub page per class (one from this block, one
.. from a submodule-level block) for names that live under `api/`.
.. ==============================================================================

Astrometrics Library (`astrometricslib`)
=========================================

.. automodapi:: astrometricslib
   :no-inheritance-diagram:
   :skip: AbstractButler
   :skip: AnalysisResult
   :skip: AppConfiguration
   :skip: AsteroidRecoveryCandidate
   :skip: AstrometryPipeline
   :skip: AstrometryPipelineQualityMetrics
   :skip: AstrometryQualitySummary
   :skip: BatchRunSummary
   :skip: DbLogHandler
   :skip: DiskButler
   :skip: FileItem
   :skip: FilterType
   :skip: FitsHeaderEntry
   :skip: FrameRecord
   :skip: GroupedFrameStat
   :skip: ImageProcessing
   :skip: LightCurve
   :skip: LoggerInterface
   :skip: MosaicInfo
   :skip: MovingObjectConfig
   :skip: PlotData
   :skip: ProcessingJob
   :skip: RenderedImage
   :skip: SpectralObservation
   :skip: StarIdentifier
   :skip: StellarObject
   :skip: Target
   :skip: TargetFilesResponse
   :skip: TargetSessionContribution
   :skip: VariableCandidate
   :skip: classify_and_sort_fits_files
   :skip: derive_target_sessions
   :skip: get_configuration
   :skip: parse_coordinate_string
   :skip: resolve_worker_counts
   :skip: run_parallel_batch
