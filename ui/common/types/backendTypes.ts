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
 * Lists the different categories of images we can process.
 */
export enum ImageType {
  STAR_FIELD = "star_field",
  TARGET_IMAGE = "target_image"
}

/**
 * A single raw photograph and its settings (like ISO, exposure).
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
  pierSide?: string | null;
  airmass?: number | null;
  altitudeDegrees?: number | null;
  azimuthDegrees?: number | null;
  pixelScaleArcsec?: number | null;
  focalLengthMm?: number | null;
  binning?: number | null;
  sensorTemperatureC?: number | null;
  focuserPosition?: number | null;
  focuserTemperatureC?: number | null;
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
 * Details about a multi-panel picture (mosaic) created for this target.
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
 * The final stacked image for a specific telescope/camera setup.
 *
 * If a target was shot with two different telescopes, it will produce
 * two different stacked images. This structure tracks one of them.
 */
export interface StackConfigurationResult {
  configurationKey: string;
  camera?: string;
  focalLengthMm?: number | null;
  framesStacked?: number;
  stackedImage?: string;
  isPreferred?: boolean;
}

/**
 * The main record for an astronomical target (like a galaxy or nebula).
 *
 * This class only stores data. If you want to stack images or analyze
 * the target, use the tools in the `TargetCatalog`.
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
  stacksByConfiguration?: Record<string, StackConfigurationResult>;
  stackedSpectralTarget?: string;
  stackQualitySummary?: StackQualitySummary | null;
  spectralStackQualitySummary?: StackQualitySummary | null;
  astrometryQualitySummary?: AstrometryQualitySummary | null;
  photometryQualitySummary?: PhotometryQualitySummary | null;
  spectroscopyQualitySummary?: SpectroscopyQualitySummary | null;
  asteroidCandidates?: AsteroidRecoveryCandidate[];
  asteroidRecoveryQualitySummary?: AsteroidRecoveryQualitySummary | null;
  trackingQualitySummary?: TrackingQualitySummary | null;
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
 * A single image file ready to be shown in a UI list.
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
 * All the files and summary statistics that belong to a single target.
 */
export interface TargetFilesResponse {
  files?: FileItem[];
  stackedImage?: string | null;
  stackedSpectralTarget?: string | null;
  totalExposure?: number;
  exposureCounts?: Record<string, number>;
}

/**
 * A count of how many images share the same filter and exposure time.
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
 * Holds the X and Y coordinates needed to draw a spectrum graph.
 */
export interface PlotData {
  wavelengths?: number[];
  intensities?: number[];
}

/**
 * The main record for an individual star found in an image.
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
 * Tracks when a star was detected during a specific observing session.
 *
 * If we observe a star on 5 different nights, it will have 5 of these records
 * combined into its final light curve.
 */
export interface StellarSessionMatch {
  sessionId: string;
  angularSeparationArcsec: number;
}

/**
 * A measurement of a star's light split into its component colors.
 */
export interface SpectralObservation {
  timestamp: string;
  wavelengths?: number[];
  intensities?: number[];
}

/**
 * The result of searching a star's brightness for repeating cycles.
 */
export interface PeriodogramResult {
  bestPeriodDays?: number;
  power?: number;
  falseAlarmProbability?: number;
}

/**
 * Data for when a star dims, possibly because a planet passed in front.
 */
export interface ExoplanetTransitCandidate {
  periodDays?: number;
  transitDepthMag?: number;
  transitDurationHours?: number;
  epochT0?: number;
  transitSnr?: number;
}

/**
 * A record of how a star's brightness changes over time.
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
 * A summary of what happened when we ran a processing job.
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
 * A star we think might be changing brightness over time.
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
 * A single piece of metadata (key/value pair) from a FITS image file.
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
 * A finished PNG image ready to display, plus brightness stats.
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
 * A record of a single picture that was skipped, and the reason why.
 */
export interface ExcludedFrame {
  path: string;
  reason: string;
}

/**
 * Tracks how many pictures from a single observing session were used.
 */
export interface TargetSessionContribution {
  session_id: string;
  frames_contributed: number;
  frames_clipped: number;
}

/**
 * Measurements recorded when combining (stacking) multiple images.
 *
 * This tracks how many images were successfully combined and records details
 * like the final image sharpness (FWHM) or if the background was uneven.
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
  stacking_duration_seconds?: number | null;
  timed_out?: boolean;
  debayer_applied?: boolean | null;
  registration_reference_frame?: string | null;
  registration_reference_star_count?: number | null;
}

/**
 * The final saved report for an image stacking job.
 *
 * It combines the basic pipeline information with the specific stacking
 * metrics.
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
 * Measurements recorded when figuring out where an image is pointing.
 *
 * This tracks how many stars were found and whether the image's coordinates
 * could be successfully calculated (plate solving).
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
  remote_catalog_queries_attempted?: number;
  remote_catalog_queries_failed?: number;
  remote_catalog_circuit_breaker_tripped?: boolean;
  plate_solve_attempts?: number;
}

/**
 * The final saved report for an astrometry (coordinate-finding) job.
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
 * Tracks which known stars were the brightness reference used.
 */
export interface FrameEnsembleComposition {
  frame_path: string;
  ensemble_size: number;
  excluded_comparison_star_ids?: string[];
}

/**
 * Measurements recorded when measuring the brightness of stars.
 *
 * This tracks how many stars were processed and if any variable stars
 * were found.
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
 * The final saved report for a photometry (brightness-measuring) job.
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
 * Measurements recorded when analyzing a star's light spectrum.
 *
 * This tracks details about the spectral lines, like how wide they are
 * and whether any parts of the spectrum were too bright (saturated).
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
 * The final saved report for a spectroscopy (light-spectrum) job.
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
 * Measurements that describe how well the telescope tracked the sky.
 *
 * This looks for problems with the telescope mount (like drifting) or
 * changes in the sky conditions (like the background getting brighter).
 * It records the worst-case values across all observing sessions.
 */
export interface TrackingPipelineQualityMetrics {
  sessions_found: number;
  sessions_analyzed: number;
  usable_frames: number;
  span_hours?: number | null;
  drift_rate_x_px_per_hour?: number | null;
  drift_rate_y_px_per_hour?: number | null;
  max_excursion_px?: number | null;
  meridian_flips?: number;
  periodic_error_period_seconds?: number | null;
  periodic_error_strength?: number;
  periodic_error_corroborated?: boolean;
  trailed_frame_count?: number;
  median_fwhm_px?: number | null;
  fwhm_spread_px?: number | null;
  median_roundness?: number | null;
  median_background?: number | null;
  background_spread?: number | null;
}

/**
 * The final saved report for a telescope tracking analysis job.
 */
export interface TrackingQualitySummary {
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
  tracking_metrics: TrackingPipelineQualityMetrics;
}

/**
 * A dot of light in one picture, which might be an asteroid.
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
 * The calculated path (speed, direction) of an object across pictures.
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
 * Tracks how far a possible asteroid made it through our checking process.
 *
 * We run several tests to see if a moving dot is really an asteroid.
 * This shows if it passed all tests, or at which step it was rejected
 * (e.g., it was just a dead pixel).
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
 * A match between our detected object and a real, known asteroid.
 *
 * We compare our object's speed and location against databases (like SkyBoT)
 * that predict where known asteroids should be.
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
 * A potential asteroid we tracked across several pictures.
 *
 * It holds all the individual detections, its calculated path, and
 * whether it matched any known asteroids.
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
 * Measurements for the process that searches for moving asteroids.
 *
 * This tracks how many candidates were found and how many passed each
 * successive check (e.g., did it move in a straight line? did it match a
 * known asteroid?).
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
 * The final saved report for an asteroid-hunting job.
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
