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
 * Lists the different categories of images that can be processed.
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
 * This class only stores data. If stacking images or analyzing
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
  mainScope?: string;
  processedImage?: string;
  stackedImage?: string;
  stacksByConfiguration?: Record<string, StackConfigurationResult>;
  stackedSpectralTarget?: string;
  stackQualitySummary?: StackQualitySummary | null;
  spectralStackQualitySummary?: StackQualitySummary | null;
  astrometryQualitySummary?: AstrometryQualitySummary | null;
  photometryQualitySummary?: PhotometryQualitySummary | null;
  spectroscopyQualitySummary?: SpectroscopyQualitySummary | null;
  asteroidCandidates?: AsteroidDetectionCandidate[];
  asteroidDetectionQualitySummary?: AsteroidDetectionQualitySummary | null;
  exposureTime?: number;
  numberOfStars?: number;
  frames?: FrameRecord[];
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
  photometry?: PhotometryResult | null;
  spectraHistory?: SpectralObservation[];
  starData?: any;
  radiusPx?: number | null;
  spectroscopy?: SpectroscopyResult | null;
  stellarSpectralType?: string;
  targetIds?: string[];
  sessionMatches?: StellarSessionMatch[];
  isCatalogIdentified?: boolean;
  /** Flexible index to accommodate additional data from the backend. */
  [key: string]: any;
}

/**
 * Tracks when a star was detected during a specific observing session.
 *
 * If a star is observed on 5 different nights, it will have 5 of
 * these records combined into its final light curve.
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
 * A star's own extracted spectrum, and what it suggests about the star.
 *
 * Bundles spectroscopy's results the same way `PhotometryResult` bundles
 * photometry's: the processed measurement itself alongside what was
 * derived from it, in one place on `StellarObject`, instead of as
 * several same-topic fields scattered directly on the star.
 */
export interface SpectroscopyResult {
  wavelengthsAngstrom?: number[];
  intensities?: number[];
  quantumEfficiencyCorrectedIntensities?: number[] | null;
  selfDeterminedSpectralType?: string;
  selfDeterminedSpectralTypeConfidence?: number | null;
  selfDeterminedSpectralTypeRms?: number | null;
  selfDeterminedSpectralTypeNote?: string;
  selfDeterminedSpectralTypeCandidates?: Record<string, any>[];
  probableSpectralFeatures?: Record<string, any>[];
  emissionLines?: Record<string, any>[];
  isEmissionLineSource?: boolean;
  starPositionPx?: number[] | null;
  requestedWavelengthRangeAngstrom?: number[] | null;
  validFraction?: number | null;
  rectangle?: any | null;
  detectedAngle?: number | null;
  dispersionAngle?: number | null;
  trailCenterlinePx?: number[] | null;
  trailWidthPx?: number[] | null;
  secondOrderBlueToRedRatio?: number[] | null;
  resolutionElementAngstrom?: number | null;
  extractionRadius?: number | null;
}

/**
 * The result of searching a star's brightness for repeating cycles.
 *
 * A "periodogram" tests many possible repeat lengths (periods) against
 * a star's brightness history and reports which one fits best -- the
 * way you might try different guesses for a song's beat until one
 * lines up.
 */
export interface PeriodogramResult {
  bestPeriodDays?: number;
  power?: number;
  falseAlarmProbability?: number;
  verdict?: string;
  note?: string;
  cyclesObserved?: number | null;
  searchedMinPeriodDays?: number | null;
  searchedMaxPeriodDays?: number | null;
}

/**
 * Data for a brief, repeating dip in a star's brightness.
 *
 * This "transit" pattern is how astronomers find planets around other
 * stars, but the same box-shaped dip also shows up when the "star" is
 * actually two stars and one passes in front of the other (an
 * eclipsing binary) -- the detection math (see
 * `VariabilityAnalyzer.run_bls_transit_search`) doesn't know which
 * caused it, so this model doesn't assume either.
 */
export interface TransitCandidate {
  periodDays?: number;
  transitDepthMag?: number;
  transitDurationHours?: number;
  epochT0?: number;
  transitSnr?: number;
  transitConfidence?: number;
  falseAlarmProbability?: number;
  transitCount?: number;
  pointsInTransit?: number;
  verdict?: string;
  note?: string;
  searchedMinPeriodDays?: number | null;
  searchedMaxPeriodDays?: number | null;
}

/**
 * A record of how a star's brightness changes over time: a light curve.
 */
export interface PhotometryResult {
  timestamps?: string[];
  fluxes?: number[];
  fluxesNormalized?: number[];
  fluxesDetrended?: number[];
  airmasses?: number[];
  magnitudes?: number[];
  isSaturated?: boolean[];
  periodogram?: PeriodogramResult | null;
  transitCandidate?: TransitCandidate | null;
  meanFlux?: number | null;
  coefficientOfVariation?: number | null;
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
  inputMetrics?: Record<string, any> | null;
  outputMetrics?: Record<string, any> | null;
}

/**
 * A summary of what happened when a processing job was run.
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
 * A star that might be changing brightness over time.
 */
export interface VariableCandidate {
  id: string;
  meanFlux: number;
  coefficientOfVariation: number;
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
  sessionId: string;
  framesContributed: number;
  framesClipped: number;
}

/**
 * What happened to the frames of one exposure length in a stack.
 *
 * Frames taken with different exposure lengths are stacked one length at a
 * time (each with the dark frames of its own length) and the results are
 * combined. This records, for each length, how it went. A group left out of
 * the combined image says why in `left_out_reason`.
 */
export interface ExposureGroupSummary {
  exposureSeconds: number;
  framesSubmitted: number;
  framesStacked: number;
  darkApplied: boolean;
  saturated?: boolean;
  clippedAtZero?: boolean;
  stackPath?: string | null;
  alignmentShiftPixels?: number[] | null;
  leftOutReason?: string | null;
}

/**
 * Measurements recorded when combining (stacking) multiple images.
 *
 * This tracks how many images were successfully combined and records details
 * like the final image sharpness (FWHM) or if the background was uneven.
 */
export interface StackingPipelineQualityMetrics {
  isSpectral: boolean;
  framesSubmitted: number;
  framesStacked: number;
  excludedFrames?: ExcludedFrame[];
  rejectedPixelFraction?: number | null;
  rejectedFractionFlagged?: boolean;
  backgroundSplitDetected?: boolean;
  backgroundSplitDetail?: string | null;
  calibrationMismatchFlags?: string[];
  saturatedPixelFraction?: number | null;
  saturationFlagged?: boolean;
  exposureGroups?: ExposureGroupSummary[];
  recommendedExposureSeconds?: number | null;
  zeroPixelFraction?: number | null;
  zeroFractionFlagged?: boolean;
  negativePixelMaxPercent?: number | null;
  negativePixelsFlagged?: boolean;
  stackedFwhmPx?: number | null;
  medianInputFwhmPx?: number | null;
  fwhmDegraded?: boolean;
  spectralRegistrationFlags?: ExcludedFrame[];
  stackingDurationSeconds?: number | null;
  timedOut?: boolean;
  debayerApplied?: boolean | null;
  registrationReferenceFrame?: string | null;
  registrationReferenceStarCount?: number | null;
}

/**
 * The final saved report for an image stacking job.
 *
 * It combines the basic pipeline information with the specific stacking
 * metrics.
 */
export interface StackQualitySummary {
  pipelineName?: string;
  pipelineVersion?: string;
  targetId: string;
  targetSessionIds?: string[];
  targetSessionBreakdown?: TargetSessionContribution[];
  upstreamQualitySummaryReference?: string | null;
  resolvedParameters?: Record<string, any>;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  createdAt?: string;
  stackingMetrics: StackingPipelineQualityMetrics;
}

/**
 * Measurements recorded when figuring out where an image is pointing.
 *
 * This tracks how many stars were found and whether the image's
 * coordinates could be successfully calculated ("plate solving" --
 * matching the stars in the picture to a star map to figure out
 * exactly where the telescope was pointed).
 */
export interface AstrometryPipelineQualityMetrics {
  catalogMatchedStarCount?: number;
  positionOnlyStarCount?: number;
  unresolvedStarCount?: number;
  sourcesDetected: number;
  solveAttempted: boolean;
  plateSolveSucceeded: boolean;
  simbadMatchedCount: number;
  astrometricResidualRmsArcsec?: number | null;
  remoteCatalogQueriesAttempted?: number;
  remoteCatalogQueriesFailed?: number;
  remoteCatalogCircuitBreakerTripped?: boolean;
  plateSolveAttempts?: number;
}

/**
 * The final saved report for an astrometry (coordinate-finding) job.
 */
export interface AstrometryQualitySummary {
  pipelineName?: string;
  pipelineVersion?: string;
  targetId: string;
  targetSessionIds?: string[];
  targetSessionBreakdown?: TargetSessionContribution[];
  upstreamQualitySummaryReference?: string | null;
  resolvedParameters?: Record<string, any>;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  createdAt?: string;
  astrometryMetrics: AstrometryPipelineQualityMetrics;
}

/**
 * Tracks which comparison stars a picture's brightness was measured with.
 *
 * To tell if a star got brighter or dimmer, its light is compared
 * against a group of other, steady stars in the same picture (called
 * the "ensemble"). This records which stars were in that group.
 */
export interface FrameEnsembleComposition {
  framePath: string;
  ensembleSize: number;
  excludedComparisonStarIds?: string[];
}

/**
 * Measurements recorded when measuring the brightness of stars.
 *
 * This tracks how many stars were processed and if any variable stars
 * were found.
 */
export interface PhotometryPipelineQualityMetrics {
  catalogMatchedStarCount?: number;
  positionOnlyStarCount?: number;
  unresolvedStarCount?: number;
  starsProcessed: number;
  starsFound: number;
  framesProcessed: number;
  rejectedFrames?: ExcludedFrame[];
  frameEnsembleComposition?: FrameEnsembleComposition[];
  variableCandidateCount: number;
  lightCurveScatterRmsMag?: number | null;
  crossSessionMatchCount?: number;
  sessionsMissingWcs?: string[];
  longTermVariableCandidateCount?: number;
  astrometryIdentifiedStarCount?: number;
  sessionsWithReusedHeaderWcs?: string[];
  sessionsWithReplacedHeaderWcs?: string[];
}

/**
 * The final saved report for a photometry (brightness-measuring) job.
 */
export interface PhotometryQualitySummary {
  pipelineName?: string;
  pipelineVersion?: string;
  targetId: string;
  targetSessionIds?: string[];
  targetSessionBreakdown?: TargetSessionContribution[];
  upstreamQualitySummaryReference?: string | null;
  resolvedParameters?: Record<string, any>;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  createdAt?: string;
  photometryMetrics: PhotometryPipelineQualityMetrics;
}

/**
 * One star whose self-determined spectral type shouldn't be trusted as-is.
 *
 * Names exactly which stars a spectroscopy run's own classification is
 * shaky for, and why, rather than only reporting how many -- so a user
 * building a personal catalog from self-determined types knows which
 * entries to double-check instead of taking every one at face value.
 */
export interface SpectralClassificationConcern {
  starId: string;
  reason: string;
  spectralType: string;
  confidence?: number | null;
}

/**
 * Measurements recorded when analyzing a star's light spectrum.
 *
 * A spectroscope splits a star's light into a rainbow-like streak (the
 * "trail") so its colors can be measured. This class tracks details
 * about that streak, like how wide it is and whether any part of it
 * was too bright (saturated).
 */
export interface SpectroscopyPipelineQualityMetrics {
  catalogMatchedStarCount?: number;
  positionOnlyStarCount?: number;
  unresolvedStarCount?: number;
  zeroOrderSaturatedPixelFraction?: number | null;
  zeroOrderSaturationFlagged?: boolean;
  dispersionAngleDeg?: number | null;
  trailWidthProfileAvailable?: boolean;
  medianTrailWidthPx?: number | null;
  lowConfidenceClassificationCount?: number;
  ambiguousClassificationCount?: number;
  flaggedSpectralClassifications?: SpectralClassificationConcern[];
}

/**
 * The final saved report for a spectroscopy (light-spectrum) job.
 */
export interface SpectroscopyQualitySummary {
  pipelineName?: string;
  pipelineVersion?: string;
  targetId: string;
  targetSessionIds?: string[];
  targetSessionBreakdown?: TargetSessionContribution[];
  upstreamQualitySummaryReference?: string | null;
  resolvedParameters?: Record<string, any>;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  createdAt?: string;
  spectroscopyMetrics: SpectroscopyPipelineQualityMetrics;
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
  brightness?: number | null;
  pictureBrightnessLevel?: number | null;
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
 * Tracks how far a possible asteroid made it through the checking process.
 *
 * Several tests are run to see if a moving dot is really an asteroid.
 * This shows if it passed all tests, or at which step it was rejected
 * (e.g., it was just a dead pixel).
 */
export enum CascadeStage {
  REFERENCE_FRAME_CONFIRMED = "reference_frame_confirmed",
  RATE_LINEARITY_CONFIRMED = "rate_linearity_confirmed",
  EPHEMERIS_MATCHED = "ephemeris_matched",
  REJECTED_SINGLE_FRAME = "rejected_single_frame",
  REJECTED_STATIONARY_SKY = "rejected_stationary_sky",
  REJECTED_STATIONARY_PIXEL = "rejected_stationary_pixel",
  REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE = "rejected_nonlinear_or_out_of_range_rate"
}

/**
 * A match between the detected object and a real, known asteroid.
 *
 * The object's speed and location are compared against databases
 * (like SkyBoT) that predict where known asteroids should be.
 */
export interface EphemerisMatch {
  designation: string;
  angularSeparationArcsec: number;
}

/**
 * A potential asteroid tracked across several pictures.
 *
 * It holds all the individual detections, its calculated path, and
 * whether it matched any known asteroids.
 */
export interface AsteroidDetectionCandidate {
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
export interface AsteroidDetectionPipelineQualityMetrics {
  framesWithWcsEstimate: number;
  framesExcludedMissingPointingMetadata: number;
  candidatesDetected: number;
  candidatesPersistenceConfirmed: number;
  candidatesRateLinearityConfirmed: number;
  candidatesEphemerisMatched: number;
}

/**
 * The final saved report for an asteroid-hunting job.
 */
export interface AsteroidDetectionQualitySummary {
  pipelineName?: string;
  pipelineVersion?: string;
  targetId: string;
  targetSessionIds?: string[];
  targetSessionBreakdown?: TargetSessionContribution[];
  upstreamQualitySummaryReference?: string | null;
  resolvedParameters?: Record<string, any>;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  createdAt?: string;
  asteroidDetectionMetrics: AsteroidDetectionPipelineQualityMetrics;
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
