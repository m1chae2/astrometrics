/**
 * @fileoverview Auto-generated TypeScript interfaces from Pydantic models.
 */

/**
 * Optical filter or spectroscopy accessory used to capture a frame.
 *
 * Notes
 * -----
 * ``LUMINANCE``, ``RED``, ``GREEN``, and ``BLUE`` are separate
 * members (not true Python Enum aliases, since their values differ
 * from ``L``, ``R``, ``G``, and ``B``) kept so that FITS headers
 * written by capture software using the full color-name spelling
 * still resolve to a matching filter.
 */
export enum FilterType {
  L = "L",
  R = "R",
  G = "G",
  B = "B",
  Ha = "Ha",
  OIII = "OIII",
  SII = "SII",
  SPEC = "SPEC",
  NONE = "None"
}

/**
 * Enum representing different types of target images.
 */
export enum ImageType {
  STAR_FIELD = "star_field",
  TARGET_IMAGE = "target_image"
}

/**
 * Represents a single image frame on disk with its associated metadata.
 */
export interface FrameRecord {
  path: string;
  filter?: FilterType;
  role?: string;
  iso?: string;
  offset?: string;
  exposure?: string;
  timestamp?: number | null;
  camera?: string;
  telescope?: string;
  date?: string;
  registrationFwhmXPx?: number | null;
  registrationFwhmYPx?: number | null;
  registrationRoundness?: number | null;
  registrationRmse?: number | null;
  registrationStarCount?: number | null;
  registrationDxPx?: number | null;
  registrationDyPx?: number | null;
  backgroundLevel?: number | null;
  saturatedPixelFraction?: number | null;
  measuredFwhmPx?: number | null;
}

/**
 * Stores information about a mosaic configuration created for a target.
 */
export interface MosaicInfo {
  /** UUID for the mosaic group */
  groupId: string;
  /** Name of the mosaic configuration */
  name: string;
  /** Timestamp of creation */
  createdAt: number;
  panels?: string[];
}

/**
 * Represents the current status and telemetry of the telescope.
 *
 * Uses aliases to provide camelCase names for the frontend.
 */
export interface TelescopeStatus {
  ra: string;
  dec: string;
  altitude: string;
  azimuth: string;
  temperature: string;
  humidity: string;
  trackingStatus: string;
  connectionStatus?: string;
  focuserPosition?: number;
  filter?: string;
  guidingHistory?: any;
  /** Flexible index to accommodate additional data from the backend. */
  [key: string]: any;
}

/**
 * Pure data schema for astronomical targets.
 *
 * Algorithmic operations (stacking, analysis, pipelines) are free
 * functions in `astrometricslib.tasks.target_tasks.pipeline_tasks`
 * taking a `Target` as their first argument, and are exposed to
 * external callers via `astrometricslib.api.targets.TargetCatalog`.
 */
export interface TargetObject {
  id?: string;
  commonName?: string;
  imageType?: ImageType;
  ra?: string;
  dec?: string;
  fieldOfView?: string;
  mainCamera?: string;
  guideCamera?: string;
  mainScope?: string;
  guideScope?: string;
  mount?: string;
  processedImage?: string;
  stackedImage?: string;
  stackedSpectralTarget?: string;
  stackQualitySummary?: StackQualitySummary | null;
  spectralStackQualitySummary?: StackQualitySummary | null;
  astrometryQualitySummary?: AstrometryQualitySummary | null;
  photometryQualitySummary?: PhotometryQualitySummary | null;
  spectroscopyQualitySummary?: SpectroscopyQualitySummary | null;
  asteroidCandidates?: AsteroidRecoveryCandidate[];
  asteroidRecoveryQualitySummary?: AsteroidRecoveryQualitySummary | null;
  exposureTime?: number;
  numberOfStars?: number;
  frames?: FrameRecord[];
  mosaicGroups?: MosaicInfo[];
  parentGroupId?: string | null;
  panelName?: string;
  /** Flexible index to accommodate additional data from the backend. */
  [key: string]: any;
}

/**
 * Represent a single file item for display in the file browser.
 */
export interface FileItem {
  path: string;
  name: string;
  camera?: string;
  iso?: string;
  exposure?: string;
  filter?: string;
  date?: string;
}

/**
 * Represent the full file response for a target.
 */
export interface TargetFilesResponse {
  files?: FileItem[];
  stackedImage?: string | null;
  stackedSpectralTarget?: string | null;
  totalExposure?: number;
  exposureCounts?: Record<string, number>;
}

/**
 * Represent frame statistics grouped by filter and exposure.
 */
export interface GroupedFrameStat {
  filter: string;
  iso: string;
  exposure: string;
  count: number;
  darks?: string | null;
  camera?: string | null;
}

/**
 * Hold wavelengths and flux intensities for interactive plotting.
 */
export interface PlotData {
  wavelengths?: number[];
  intensities?: number[];
}

/**
 * Define a stellar object with coordinates, flux, and spectra.
 */
export interface Spectrum {
  id?: string;
  name?: string;
  ra?: any;
  dec?: any;
  flux?: any;
  magnitude?: any;
  spectralType?: string;
  lightCurve?: LightCurve | null;
  spectraHistory?: SpectralObservation[];
  spectrumData?: any[];
  starData?: any;
  data?: any[];
  spectrumDataProcessed?: Record<string, any> | null;
  rect?: any | null;
  rectangle?: any | null;
  detectedAngle?: number | null;
  dispersionAngle?: number | null;
  trailCenterlinePx?: number[] | null;
  trailWidthPx?: number[] | null;
  stellarSpectralType?: string;
  camera?: string | null;
  targetIds?: string[];
  extractionRadius?: number | null;
  meanFlux?: number | null;
  coefficientOfVariation?: number | null;
  variabilityScore?: number | null;
  sessionMatches?: StellarSessionMatch[];
  isCatalogIdentified?: boolean;
  /** Flexible index to accommodate additional data from the backend. */
  [key: string]: any;
}

/**
 * Record one observing session detection folded into a merged curve.
 *
 * One instance per contributing session (a merged star observed across
 * N sessions carries N of these), mirroring how `EphemerisMatch` records
 * a single cross-match rather than a bare boolean.
 */
export interface StellarSessionMatch {
  sessionId: string;
  angularSeparationArcsec: number;
}

/**
 * Represent a single spectral extraction at a specific timestamp.
 */
export interface SpectralObservation {
  timestamp: string;
  wavelengths?: number[];
  intensities?: number[];
}

/**
 * Hold Lomb-Scargle periodogram analysis results for a light curve.
 */
export interface PeriodogramResult {
  bestPeriodDays?: number;
  power?: number;
  falseAlarmProbability?: number;
}

/**
 * Hold Box-fitting Least Squares (BLS) transit candidate parameters.
 */
export interface ExoplanetTransitCandidate {
  periodDays?: number;
  transitDepthMag?: number;
  transitDurationHours?: number;
  epochT0?: number;
  transitSnr?: number;
}

/**
 * Represent a light curve of flux variations over time for a star.
 */
export interface LightCurve {
  timestamps?: string[];
  fluxes?: number[];
  fluxesNormalized?: number[];
  fluxesDetrended?: number[];
  airmasses?: number[];
  magnitudes?: number[];
  isSaturated?: boolean[];
  periodogram?: PeriodogramResult | null;
  transitCandidate?: ExoplanetTransitCandidate | null;
}

/**
 * Lightweight snapshot of current telescope state for polling.
 */
export interface TelescopePulse {
  ra?: string;
  dec?: string;
  altitude?: string;
  azimuth?: string;
  trackingStatus?: string;
  connectionStatus?: string;
  temperature?: string;
  humidity?: string;
  filter?: string;
  focuserPosition?: number;
  guidingHistory?: Record<string, any>[];
  alignmentAttempts?: any[];
  alignmentActive?: boolean;
}

/**
 * Lightweight snapshot of a single background processing job.
 */
export interface ProcessingJobPulse {
  target_id: string;
  job_id: string;
  status: string;
}

/**
 * Aggregated lightweight system status for frequent polling.
 */
export interface SystemPulse {
  telescope: TelescopePulse;
  processing?: ProcessingJobPulse[];
}

/**
 * Represents a single telemetry point from a guiding run.
 */
export interface GuidingSample {
  time: number;
  dra: number;
  ddec: number;
  pulseRa: number;
  pulseDec: number;
  snr?: number | null;
  rmsRa?: number | null;
  rmsDec?: number | null;
}

/**
 * Represents the result of a plate-solving alignment attempt.
 */
export interface AlignmentAttempt {
  status: string;
  deltaRaArcsec?: number | null;
  deltaDecArcsec?: number | null;
}

/**
 * High-level execution status of a processing pipeline run.
 *
 * Includes the active job identifier and references to generated
 * logs or outputs.
 *
 * Attributes
 * ----------
 * status : `str`
 * Current execution status of the pipeline run.
 * target_id : `str` or `None`
 * Identifier of the target being processed, by default `None`.
 * job_id : `str` or `None`
 * Identifier of the active `ProcessingJob`, by default `None`.
 * expected_output : `str` or `None`
 * Path where the run's output is expected to be written, by
 * default `None`.
 * log_file : `str` or `None`
 * Filesystem path to the run's log file, by default `None`.
 */
export interface ProcessStatus {
  status: string;
  targetId?: string | null;
  jobId?: string | null;
  expectedOutput?: string | null;
  logFile?: string | null;
}

/**
 * Represents a background scientific processing or ingestion task.
 *
 * Enforces validation on the job's lifecycle status, progress
 * metrics, and associated metadata.
 *
 * Attributes
 * ----------
 * id : `str`
 * Unique identifier for this processing job.
 * target_id : `str`
 * Identifier of the target this job operates on.
 * job_type : `str`
 * Type of job being tracked (e.g. stacking, analysis).
 * status : `str`
 * Current lifecycle status of the job.
 * progress_current : `int`
 * Current progress count toward `progress_total`, by default 0.
 * progress_total : `int`
 * Total expected progress count, by default 0.
 * message : `str` or `None`
 * Optional human-readable status message, by default `None`.
 * log_file_path : `str` or `None`
 * Filesystem path to the job's log file, by default `None`.
 * created_at : `str` or `None`
 * Timestamp the job was created, by default `None`.
 * updated_at : `str` or `None`
 * Timestamp the job was last updated, by default `None`.
 * completed_at : `str` or `None`
 * Timestamp the job completed, by default `None`.
 */
export interface ProcessingJob {
  id: string;
  targetId: string;
  jobType: string;
  status: string;
  progressCurrent?: number;
  progressTotal?: number;
  message?: string | null;
  logFilePath?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  completedAt?: string | null;
}

/**
 * Summarize an analysis run of the spectroscopy/variability pipeline.
 */
export interface AnalysisResult {
  status: string;
  targetId: string;
  jobId?: string | null;
  totalImages?: number;
  analysisMode: string;
  starsProcessed?: number;
  spectraExtracted?: number;
  starsFound?: number;
  framesProcessed?: number;
  rejectedCount?: number;
  rejectedFiles?: string[];
  variableCandidates?: VariableCandidate[];
  error?: string | null;
  message?: string | null;
}

/**
 * Represent a stellar object flagged as a variable star candidate.
 */
export interface VariableCandidate {
  id: string;
  meanFlux: number;
  coefficientOfVariation: number;
  score: number;
  ra: number;
  dec: number;
}

/**
 * Represents a single card entry in a FITS file header.
 */
export interface FitsHeaderEntry {
  key: string;
  value: string;
  comment?: string;
}

/**
 * Status of the connected INDI hardware driver layer.
 */
export interface IndiStatus {
  status?: string;
}

/**
 * Aggregates health and resource status of the hardware bus and system.
 */
export interface SystemHealth {
  resources?: Record<string, any>;
  indi?: IndiStatus;
}

/**
 * Metadata for a callable method exposed to scripting.
 */
export interface IntrospectionMethod {
  name: string;
  doc?: string;
  args?: string[];
}

/**
 * Expose a service class and its methods for RPC discovery.
 */
export interface IntrospectionEndpoint {
  name: string;
  type: string;
  doc?: string;
  methods?: IntrospectionMethod[];
}

/**
 * Statistical RMS errors and tracking SNR for active guiding.
 */
export interface GuidingStats {
  rms_ra?: number;
  rms_dec?: number;
  rms_total?: number;
  star_mass?: number;
  snr?: number;
}

/**
 * Live feedback of guiding loop state and correction sample history.
 */
export interface GuidingStatus {
  is_guiding?: boolean;
  stats?: GuidingStats;
  history?: Record<string, any>[];
  exposure?: number;
  gain?: number;
}

/**
 * Lightweight schema holding scaled visualization PNG data and stats.
 */
export interface RenderedImage {
  id: string;
  min: number;
  max: number;
  imageData: string;
}

/**
 * A single instruction block inside an imaging sequence plan.
 */
export interface SequenceItem {
  count: number;
  exposure: number;
  filter: string;
  duration: number;
}

/**
 * A full structured session queue of planned exposure sequences.
 */
export interface SequencePlan {
  id: string;
  target_name: string;
  items?: SequenceItem[];
  total_duration?: number;
  created_at: string;
  status?: string;
}

/**
 * Metadata for a single registered calibration frame.
 */
export interface CalibrationEntry {
  camera: string;
  iso: string;
  exposure?: number | null;
  filter?: string | null;
  count: number;
}

/**
 * Registered dark, flat, and bias frame statistics for the library.
 */
export interface CalibrationStats {
  darks?: CalibrationEntry[];
  biases?: CalibrationEntry[];
  flats?: CalibrationEntry[];
}

/**
 * Row/column coordinates for a single pane in a mosaic layout.
 */
export interface MosaicPanel {
  row: number;
  col: number;
  ra_str: string;
  dec_str: string;
  ra_deg: number;
  dec_deg: number;
  panel_id: string;
}

/**
 * A single frame excluded from a pipeline run, and why.
 */
export interface ExcludedFrame {
  path: string;
  reason: string;
}

/**
 * One TargetSession's contribution of frames to a single pipeline run.
 */
export interface TargetSessionContribution {
  session_id: string;
  frames_contributed: number;
  frames_clipped: number;
}

/**
 * Quality metrics specific to the stacking pipeline.
 *
 * Not shared with other pipelines. Standard and spectral stacks populate
 * different subsets of the optional fields:
 * stacked_fwhm_px/median_input_fwhm_px/fwhm_degraded for standard stacks
 * (whole-field FWHM is meaningful there); spectral stacks populate
 * spectral_registration_flags instead (zero-order-star-tracking based, see
 * tasks/target_tasks/spectral_registration_quality.py) -- a whole-field
 * FWHM doesn't capture what matters for a spectral trace.
 */
export interface StackingPipelineQualityMetrics {
  is_spectral: boolean;
  frames_submitted: number;
  frames_stacked: number;
  excluded_frames?: ExcludedFrame[];
  rejected_pixel_fraction?: number | null;
  rejected_fraction_flagged?: boolean;
  background_split_detected?: boolean;
  background_split_detail?: string | null;
  calibration_mismatch_flags?: string[];
  saturated_pixel_fraction?: number | null;
  saturation_flagged?: boolean;
  stacked_fwhm_px?: number | null;
  median_input_fwhm_px?: number | null;
  fwhm_degraded?: boolean;
  spectral_registration_flags?: ExcludedFrame[];
}

/**
 * Persisted per-stack quality record for the stacking pipeline.
 *
 * Subclasses PipelineQualitySummaryBase. rejection_sigma_low/high/mode and
 * filter_wfwhm_requested/effective/loosened live in the inherited
 * resolved_parameters dict rather than as dedicated fields here -- they're
 * resolved-parameter values in the sense the shared base defines, not
 * stacking-specific quality metrics. upstream_quality_summary_reference is
 * always None for stacking: it's the only pipeline with primary,
 * non-inherited input quality (see the architecture discussion this
 * implements).
 */
export interface StackQualitySummary {
  pipeline_name?: string;
  pipeline_version?: string;
  target_id: string;
  target_session_ids?: string[];
  target_session_breakdown?: TargetSessionContribution[];
  upstream_quality_summary_reference?: string | null;
  resolved_parameters?: Record<string, any>;
  quality_processing_applied?: boolean;
  flagged?: boolean;
  flag_reasons?: string[];
  created_at?: string;
  stacking_metrics: StackingPipelineQualityMetrics;
}

/**
 * Astrometry-pipeline-specific quality metrics.
 *
 * Not shared with other pipelines.
 *
 * catalog_matched_star_count/position_only_star_count/unresolved_star_count
 * are a strict breakdown of every star this run identified against:
 * matched to SIMBAD or Gaia (a real name), matched to neither but
 * still given a stable position-derived id (`FIELD_J...`), or
 * matched to neither and never even given a sky position (dropped
 * before persistence -- see `pipeline_tasks._drop_unresolved_stars`).
 * A high position_only/unresolved rate relative to
 * catalog_matched_star_count is worth investigating, though it is
 * not proof by itself of spurious detections -- SIMBAD and Gaia are
 * both incomplete at the faint end.
 */
export interface AstrometryPipelineQualityMetrics {
  sources_detected: number;
  solve_attempted: boolean;
  plate_solve_succeeded: boolean;
  simbad_matched_count: number;
  astrometric_residual_rms_arcsec?: number | null;
  catalog_matched_star_count?: number;
  position_only_star_count?: number;
  unresolved_star_count?: number;
}

/**
 * Persisted per-solve quality record.
 *
 * Astrometry pipeline's subclass of `PipelineQualitySummaryBase`.
 *
 * upstream_quality_summary_reference is always "stacking": astrometry
 * always solves Target.stacked_image, never individual frames, so
 * stacking's StackQualitySummary is its upstream input quality.
 * target_session_ids/target_session_breakdown stay empty: astrometry
 * consumes one already-stacked image rather than individual frames, so a
 * per-session breakdown isn't meaningful here -- session provenance for
 * the underlying stack is reachable via upstream_quality_summary_reference
 * instead.
 */
export interface AstrometryQualitySummary {
  pipeline_name?: string;
  pipeline_version?: string;
  target_id: string;
  target_session_ids?: string[];
  target_session_breakdown?: TargetSessionContribution[];
  upstream_quality_summary_reference?: string | null;
  resolved_parameters?: Record<string, any>;
  quality_processing_applied?: boolean;
  flagged?: boolean;
  flag_reasons?: string[];
  created_at?: string;
  astrometry_metrics: AstrometryPipelineQualityMetrics;
}

/**
 * Per-frame comparison-star ensemble composition for normalization.
 *
 * ensemble_size is deliberately per-frame rather than a single run-level
 * constant: a saturated comparison star is excluded from the ensemble
 * median only in the frames where it's actually saturated, remaining
 * eligible in every other frame.
 */
export interface FrameEnsembleComposition {
  frame_path: string;
  ensemble_size: number;
  excluded_comparison_star_ids?: string[];
}

/**
 * Photometry-pipeline-specific quality metrics.
 *
 * Not shared with other pipelines.
 */
export interface PhotometryPipelineQualityMetrics {
  stars_processed: number;
  stars_found: number;
  frames_processed: number;
  rejected_frames?: ExcludedFrame[];
  frame_ensemble_composition?: FrameEnsembleComposition[];
  variable_candidate_count: number;
  light_curve_scatter_rms_mag?: number | null;
  cross_session_match_count?: number;
  sessions_missing_wcs?: string[];
  long_term_variable_candidate_count?: number;
  astrometry_identified_star_count?: number;
  sessions_with_reused_header_wcs?: string[];
  sessions_with_replaced_header_wcs?: string[];
  catalog_matched_star_count?: number;
  position_only_star_count?: number;
  unresolved_star_count?: number;
}

/**
 * Persisted per-run quality record.
 *
 * Photometry pipeline's subclass of `PipelineQualitySummaryBase`.
 *
 * upstream_quality_summary_reference is always None: photometry runs on
 * raw per-session frames rather than a stacked image, so it has no single
 * upstream pipeline run to reference (unlike astrometry, which always
 * solves one stack).
 */
export interface PhotometryQualitySummary {
  pipeline_name?: string;
  pipeline_version?: string;
  target_id: string;
  target_session_ids?: string[];
  target_session_breakdown?: TargetSessionContribution[];
  upstream_quality_summary_reference?: string | null;
  resolved_parameters?: Record<string, any>;
  quality_processing_applied?: boolean;
  flagged?: boolean;
  flag_reasons?: string[];
  created_at?: string;
  photometry_metrics: PhotometryPipelineQualityMetrics;
}

/**
 * Spectroscopy-pipeline-specific quality metrics.
 *
 * Not shared with other pipelines.
 *
 * trail_width_profile_available/median_trail_width_px are populated once
 * the rung-3 traced-extraction path (SpectrumExtractor.extract_line_traced/
 * extract_with_flare_mask_traced) actually runs for at least one star that
 * run; they stay at their defaults (False/None) when extraction_method is
 * "fixed" (rung 1), since there's no per-position width data to summarize.
 */
export interface SpectroscopyPipelineQualityMetrics {
  zero_order_saturated_pixel_fraction?: number | null;
  zero_order_saturation_flagged?: boolean;
  dispersion_angle_deg?: number | null;
  trail_width_profile_available?: boolean;
  median_trail_width_px?: number | null;
  wavelength_calibration_rms_nm?: number | null;
  catalog_matched_star_count?: number;
  position_only_star_count?: number;
  unresolved_star_count?: number;
}

/**
 * Persisted per-extraction quality record.
 *
 * Spectroscopy pipeline's subclass of `PipelineQualitySummaryBase`.
 *
 * Two distinct pipelines populate this model. The single-stacked-
 * frame path (`pipeline_tasks.analyze_target(pipeline_type="spectroscopy")`,
 * no `frames=` argument) always extracts from
 * `Target.stacked_spectral_target` and defaults
 * `upstream_quality_summary_reference` to "stacking", since
 * stacking's spectral_registration_flags is the directly relevant
 * upstream signal there. The session-grouped, per-frame interactive
 * "Analyze Target" path (`AnalysisOrchestrator._run_spectroscopy_analysis`
 * via `process_spectroscopy_frames_by_session`) instead extracts
 * directly from a target's raw, unstacked frames and sets
 * `upstream_quality_summary_reference` to "raw_frames" -- it also
 * populates `target_session_ids`/`target_session_breakdown`, which
 * the single-stacked-frame path never does (it has no session
 * concept by construction).
 */
export interface SpectroscopyQualitySummary {
  pipeline_name?: string;
  pipeline_version?: string;
  target_id: string;
  target_session_ids?: string[];
  target_session_breakdown?: TargetSessionContribution[];
  upstream_quality_summary_reference?: string | null;
  resolved_parameters?: Record<string, any>;
  quality_processing_applied?: boolean;
  flagged?: boolean;
  flag_reasons?: string[];
  created_at?: string;
  spectroscopy_metrics: SpectroscopyPipelineQualityMetrics;
}

/**
 * A single point-source detection on one individual frame.
 *
 * En route to being chained into a moving-object candidate track.
 */
export interface FrameDetection {
  framePath: string;
  timestamp: number;
  pixelX: number;
  pixelY: number;
  rightAscensionDeg: number;
  declinationDeg: number;
  flux: number;
  sharpness: number;
  photutilsRoundness1: number;
}

/**
 * A fitted linear sky-motion track across a chain of frame detections.
 */
export interface MovingObjectTrack {
  rightAscensionRateArcsecPerHour: number;
  declinationRateArcsecPerHour: number;
  totalRateArcsecPerHour: number;
  linearFitRSquared: number;
  fitStartTimestamp: number;
  fitEndTimestamp: number;
}

/**
 * The discrimination-cascade stage an `AsteroidRecoveryCandidate` reached.
 *
 * Ordered stages (a real mover progresses through these in sequence)
 * are followed by the rejection stages (a candidate exits the
 * cascade at exactly one of these).
 */
export enum CascadeStage {
  MORPHOLOGY_DETECTED = "morphology_detected",
  PERSISTENCE_CONFIRMED = "persistence_confirmed",
  REFERENCE_FRAME_CONFIRMED = "reference_frame_confirmed",
  RATE_LINEARITY_CONFIRMED = "rate_linearity_confirmed",
  EPHEMERIS_MATCHED = "ephemeris_matched",
  REJECTED_SINGLE_FRAME = "rejected_single_frame",
  REJECTED_STATIONARY_SKY = "rejected_stationary_sky",
  REJECTED_STATIONARY_PIXEL = "rejected_stationary_pixel",
  REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE = "rejected_nonlinear_or_out_of_range_rate"
}

/**
 * A cross-match against a known solar-system body's predicted motion.
 *
 * From a SkyBoT (or similar) ephemeris query, matched against an
 * `AsteroidRecoveryCandidate`.
 */
export interface EphemerisMatch {
  provider?: string;
  designation: string;
  mpcNumber?: number | null;
  predictedVisualMagnitude?: number | null;
  predictedRightAscensionRateArcsecPerHour?: number | null;
  predictedDeclinationRateArcsecPerHour?: number | null;
  angularSeparationArcsec: number;
}

/**
 * A candidate moving object tracked across a target's frames.
 *
 * Also records how far it progressed through the discrimination
 * cascade.
 */
export interface AsteroidRecoveryCandidate {
  id: string;
  targetId: string;
  frameDetections?: FrameDetection[];
  track?: MovingObjectTrack | null;
  cascadeStage: CascadeStage;
  ephemerisMatch?: EphemerisMatch | null;
}

/**
 * Asteroid-recovery-specific quality metrics; not pipeline-shared.
 *
 * Each count is a strict funnel: frames_with_wcs_estimate <= total
 * light frames; candidates_detected >=
 * candidates_persistence_confirmed >=
 * candidates_rate_linearity_confirmed >= candidates_ephemeris_matched.
 */
export interface AsteroidRecoveryPipelineQualityMetrics {
  frames_with_wcs_estimate: number;
  frames_excluded_missing_pointing_metadata: number;
  candidates_detected: number;
  candidates_persistence_confirmed: number;
  candidates_rate_linearity_confirmed: number;
  candidates_ephemeris_matched: number;
  trajectory_fit_residual_rms_arcsec?: number | null;
}

/**
 * Persisted per-run quality record for the asteroid-recovery pipeline.
 *
 * upstream_quality_summary_reference defaults to "astrometry": this
 * pipeline's per-frame WCS estimates depend on the stack's
 * plate-solve WCS having already succeeded
 * (`analyze_target(pipeline_type="astrometry")` must run first).
 */
export interface AsteroidRecoveryQualitySummary {
  pipeline_name?: string;
  pipeline_version?: string;
  target_id: string;
  target_session_ids?: string[];
  target_session_breakdown?: TargetSessionContribution[];
  upstream_quality_summary_reference?: string | null;
  resolved_parameters?: Record<string, any>;
  quality_processing_applied?: boolean;
  flagged?: boolean;
  flag_reasons?: string[];
  created_at?: string;
  asteroid_recovery_metrics: AsteroidRecoveryPipelineQualityMetrics;
}

/**
 * A single weather observation.
 *
 * No populator exists yet -- schema-ready, empty until an actual
 * weather-station integration is built. Recorded for context in the
 * session's telemetry, distinct from the safety monitor's environmental
 * verdict (`SafetyAssessment`), which must not be best-effort
 * (`Wayfinding_Library_Architecture.md` §2.4.7).
 */
export interface WeatherSample {
  time: number;
  ambientTemperatureC?: number | null;
  humidityPercent?: number | null;
  dewPointC?: number | null;
}

/**
 * Observatory-side context for one observing night.
 *
 * Composition over TargetSession (astrometricslib) by ID reference --
 * target_session_id is nullable and linked post-hoc, since
 * ObservationSession is recorded live during the night while TargetSession
 * is only derivable afterward, once frames exist. The ID reference also
 * serves as the quality-data conduit: session_operations.py's
 * find_quality_contributions_for_session follows it to read
 * astrometricslib's quality records directly, with no separate API.
 */
export interface ObservationSession {
  id: string;
  targetSessionId?: string | null;
  sequencePlanId?: string | null;
  guidingSamples?: GuidingSample[];
  weatherSamples?: WeatherSample[];
  createdAt: string;
}
