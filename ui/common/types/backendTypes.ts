/**
 * @fileoverview Auto-generated TypeScript interfaces from Pydantic models,
 * and the backend's public interface (its RPC methods and routes).
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
 * The kind of sky object a target is, read from its name.
 *
 * See `astrometricslib.pipelines.shared.target_classification` for the
 * rules. `STAR` is every name that fits no other kind.
 */
export enum TargetObjectType {
  SOLAR_SYSTEM = "solar_system",
  MESSIER = "messier",
  NGC = "ngc",
  IC = "ic",
  COMET = "comet",
  CALIBRATION = "calibration",
  STAR = "star"
}

/**
 * The serializable form of an error.
 *
 * Every adapter sends this shape: the JSON-RPC ``error.data`` field, an MCP
 * error result, a failed job record, and the failed items of a batch.
 *
 * Attributes
 * ----------
 * code : `str`
 * The error code, one of the keys of `RPC_CODES`.
 * message : `str`
 * A sentence a user can read.
 * details : `dict` [`str`, `Any`]
 * Facts about the error.
 * retryable : `bool`
 * Whether the same call may succeed if tried again later.
 * request_id : `str` or `None`
 * The id of the call that failed. It also appears on every log line of
 * that call.
 */
export interface ErrorInfo {
  code: string;
  message: string;
  details?: Record<string, any>;
  retryable?: boolean;
  requestId?: string | null;
}

/**
 * Per-frame statistics our own code computed from the pixels.
 *
 * Unlike `FrameRecord`'s other fields, which are recorded straight
 * from the camera at capture time with zero analysis, every field
 * here is the output of some pipeline stage (frame scanning,
 * registration) running our own code against the pixels. Split out so
 * "what the instrument wrote down" and "what we calculated" are two
 * distinct, separately named things rather than fields interleaved in
 * one flat model.
 */
export interface FrameMeasurements {
  backgroundLevel?: number | null;
  saturatedPixelFraction?: number | null;
  measuredFwhmPx?: number | null;
  registrationFwhmXPx?: number | null;
  registrationFwhmYPx?: number | null;
  registrationRoundness?: number | null;
  registrationRmse?: number | null;
  registrationStarCount?: number | null;
  registrationDxPx?: number | null;
  registrationDyPx?: number | null;
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
  measurements?: FrameMeasurements;
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
  cameraTemperature?: string;
  cameraStatus?: string;
  targetName?: string | null;
  pierSide?: string | null;
  parked?: boolean | null;
  trackMode?: string | null;
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
 * One stacking pass's output and quality assessment, nested together.
 *
 * `Target` has two of these -- `stacking` for the ordinary imaging
 * stack, `spectral_stacking` for the spectroscopy stack -- rather than
 * the previous six loosely related flat fields (three of them
 * ambiguously named around which stack they belonged to).
 */
export interface TargetStackingResult {
  stackedImage?: string;
  processedImage?: string;
  stacksByConfiguration?: Record<string, StackConfigurationResult>;
  qualitySummary?: StackQualitySummary | null;
}

/**
 * One asteroid-detection run's candidates and quality assessment.
 */
export interface AsteroidDetectionResult {
  candidates?: AsteroidDetectionCandidate[];
  qualitySummary?: AsteroidDetectionQualitySummary | null;
}

/**
 * The three per-pipeline quality summaries `Target` keeps by itself.
 *
 * Astrometry, photometry, and spectroscopy each write their per-star
 * findings onto `StellarObject`, not `Target` -- they don't own a
 * result the way stacking and asteroid detection do -- so `Target`
 * only needs to keep each pipeline's run-level summary, grouped here
 * instead of as three flat sibling fields.
 */
export interface TargetQualitySummaries {
  astrometry?: AstrometryQualitySummary | null;
  photometry?: PhotometryQualitySummary | null;
  spectroscopy?: SpectroscopyQualitySummary | null;
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
  stacking?: TargetStackingResult;
  spectralStacking?: TargetStackingResult;
  asteroidDetection?: AsteroidDetectionResult;
  quality?: TargetQualitySummaries;
  exposureTime?: number;
  numberOfStars?: number;
  frames?: FrameRecord[];
  /** The kind of sky object this is, read from the target's id. */
  objectType?: TargetObjectType;
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
  bMinusV?: any;
  spectralType?: string;
  photometry?: PhotometryResult | null;
  starData?: any;
  radiusPx?: number | null;
  spectroscopy?: SpectroscopyResult | null;
  stellarSpectralType?: string;
  targetIds?: string[];
  sessionMatches?: StellarSessionMatch[];
  isCatalogIdentified?: boolean;
  simbadObjectTypes?: string;
  gaiaVariableFlag?: string;
  vsxVariabilityType?: string;
  catalogMatchQuality?: CatalogMatchQuality | null;
  /** How much this star's brightness jumps around, on a display scale. */
  variabilityScore?: number | null;
  /** Check if this star's light spectrum has been measured. */
  hasSpectra?: boolean;
  /** Check if the star's magnitude is a real catalog magnitude. */
  hasCatalogMagnitude?: boolean;
  /** Check if the spectrum's matched type disagrees with the catalog. */
  differsFromCatalog?: boolean | null;
  /** Check if the light curve has enough points for the cycle search. */
  canRunPeriodSearch?: boolean;
  /** Check if the light curve has enough points for the dip search. */
  canRunTransitSearch?: boolean;
  /** Check if this star's brightness has been tracked over time. */
  hasPhotometry?: boolean;
  /** The star's spectrum, formatted so it's easy to draw on a graph. */
  plotData?: Record<string, number[]>;
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
 * How confidently one star was matched to a catalog entry.
 *
 * `None` fields mean there was no catalog match to judge at all -- a
 * star that only got a position-based ``FIELD_J...`` id was never
 * compared against SIMBAD or Gaia in the first place.
 */
export interface CatalogMatchQuality {
  matchedVia?: string | null;
  separationArcsec?: number | null;
  isAmbiguous?: boolean;
  generatedByJobId?: string | null;
}

/**
 * One star placed on a target's image, for drawing labels over it.
 *
 * The positions are pixels of the target's reference image (its stacked
 * image, or its processed image when there is no stack). The field names
 * turn into the camelCase keys the app reads.
 */
export interface OverlayStar {
  id: string;
  name: string;
  x: number;
  y: number;
  spectralType?: string;
  isCatalogIdentified?: boolean;
  referenceWidth?: number | null;
  referenceHeight?: number | null;
  radiusPx?: number | null;
}

/**
 * How many stars belong to one target, and what data they have.
 */
export interface TargetStarCount {
  starCount?: number;
  hasSpectra?: boolean;
  hasPhotometry?: boolean;
}

/**
 * How clearly a spectrum's best reference type beats the next one.
 *
 * Attributes
 * ----------
 * runner_up_type : `str`
 * The second-closest reference type.
 * gap_points : `float`
 * How much worse the runner-up fits, in percentage points of the
 * root-mean-square (RMS) difference.
 * is_well_separated : `bool`
 * `True` when the gap is at least `WELL_SEPARATED_POINTS`; otherwise
 * the match is a close call.
 */
export interface CandidateSeparation {
  runnerUpType: string;
  gapPoints: number;
  isWellSeparated: boolean;
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
  responseCorrectedIntensities?: number[] | null;
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
  neighborWingFraction?: number[] | null;
  neighborWingStatus?: string | null;
  possibleNeighborContamination?: Record<string, number | null>[] | null;
  countsPerSecondFactor?: number | null;
  catalogComparison?: CatalogComparison | null;
  inputQuality?: InputQualityAssessment | null;
  outputQuality?: OutputQualityAssessment | null;
  generatedByJobId?: string | null;
  /** Check if even the closest reference spectrum fits badly. */
  isPoorMatch?: boolean;
  /** How clearly the best reference type beats the runner-up. */
  candidateSeparation?: CandidateSeparation | null;
}

/**
 * How a star's self-determined spectrum compares with its catalog entry.
 *
 * The catalog (SIMBAD/Gaia) already has a spectral type and a B-V colour
 * for most stars, measured a different way. This is not used to help the
 * classification along -- it is a check done afterward, to catch a
 * spectrum that probably is not this star's at all (a bright neighbour's
 * light, glare from a nearby bright star, or a name given to the wrong
 * object).
 */
export interface CatalogComparison {
  spectralTypeAgrees?: boolean | null;
  spectralTypeNote?: string;
  isLuminosityClassUncertain?: boolean;
  luminosityClassNote?: string;
  closestGiantType?: string | null;
  closestGiantRms?: number | null;
  colourAgrees?: boolean | null;
  colourNote?: string;
}

/**
 * How good the raw data behind a spectrum was, before anything was found.
 *
 * Answers "was this spectrum even worth analyzing?" using only signals
 * that come from the extraction itself -- the instrument's resolution,
 * how much of the frame was saturated, how much of the requested
 * spectrum actually landed on the image, and how far the signal stood
 * out from noise. None of this depends on what the classifier or the
 * feature tests concluded.
 */
export interface InputQualityAssessment {
  resolutionElementAngstrom: number;
  isResolutionMeasured: boolean;
  zeroOrderSaturatedPixelFraction?: number | null;
  validFraction?: number | null;
  signalToNoise?: number | null;
}

/**
 * How much to trust a spectrum's classification, given everything found.
 *
 * Combines the classifier's own match statistics (is the winning type a
 * weak match, or nearly tied with the runner-up) with the catalog
 * comparison above, into one overall verdict. Unlike
 * `InputQualityAssessment`, this depends on what classifying the
 * spectrum actually produced.
 */
export interface OutputQualityAssessment {
  isLowConfidence: boolean;
  isAmbiguous: boolean;
  isSubtypeFinerThanResolution?: boolean | null;
  catalogAgrees?: boolean | null;
  isTrustworthy: boolean;
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
  inputQuality?: InputQualityAssessment | null;
  outputQuality?: OutputQualityAssessment | null;
  generatedByJobId?: string | null;
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
  alignmentTargets?: AlignmentTargetSession[];
  alignmentActive?: boolean;
  polarAlignment?: PolarAlignmentStatus | null;
  cameraTemperature?: string | null;
  cameraStatus?: string | null;
  targetName?: string | null;
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
  starMass?: number | null;
}

/**
 * Represents the result of a plate-solving alignment attempt.
 */
export interface AlignmentAttempt {
  status: string;
  deltaRaArcsec?: number | null;
  deltaDecArcsec?: number | null;
  ra?: number | null;
  dec?: number | null;
  pointingErrorArcsec?: number | null;
  timestamp?: number | null;
  targetName?: string | null;
  sessionId?: string | null;
}

/**
 * Status and measurement metrics from Polar Alignment Assistant (PAA).
 */
export interface PolarAlignmentStatus {
  status?: string;
  totalErrorArcsec?: number | null;
  altErrorArcsec?: number | null;
  azErrorArcsec?: number | null;
  poleRa?: number | null;
  poleDec?: number | null;
  paaPoints?: Record<string, any>[];
  timestamp?: number | null;
}

/**
 * Summary of a past observing session's alignment and polar telemetry.
 */
export interface AlignmentSessionSummary {
  sessionId: string;
  sessionDate: string;
  syncCount?: number;
  targetCount?: number | null;
  startTime?: number | null;
  endTime?: number | null;
  avgErrorArcsec?: number | null;
  polarErrorArcsec?: number | null;
  polarAltErrorArcsec?: number | null;
  polarAzErrorArcsec?: number | null;
  /** Average right ascension of the night's solves, wrapped at 0/360 degrees. */
  meanRaDeg?: number | null;
  /** Average declination of the night's solves. */
  meanDecDeg?: number | null;
  /** Tracking jitter over the night's targets, weighted by their solve counts. */
  rmsJitterArcsec?: number | null;
}

/**
 * One plate solve in a target's run, timed from the run's first solve.
 */
export interface AlignmentTrackPoint {
  elapsedSeconds: number;
  deltaRaArcsec: number;
  deltaDecArcsec: number;
  totalErrorArcsec: number;
  timestamp: number;
}

/**
 * The plate solves on one target, with their tracking statistics.
 *
 * Solves are grouped by target name, or, without a name, by being within
 * half a degree of each other. A group can hold several runs: a gap of
 * more than two hours starts a new run.
 *
 * Jitter is the root-mean-square (RMS) scatter of the solves around the
 * run's own average offset, so a deliberate framing offset is not counted
 * as tracking error. Drift is the slope of a straight-line fit of the
 * offsets against time.
 */
export interface AlignmentTargetSession {
  id: string;
  targetName: string;
  /** Average right ascension, wrapped at 0/360 degrees. */
  meanRaDeg: number;
  meanDecDeg: number;
  frameCount: number;
  /** Pointing error of the first solve, after the slew. */
  initialErrorArcsec: number;
  rmsRaArcsec: number;
  rmsDecArcsec: number;
  /** Tracking jitter: RMS scatter around each run's mean offset. */
  rmsTotalArcsec: number;
  driftRaArcsecPerMin: number;
  driftDecArcsecPerMin: number;
  startTime?: number | null;
  endTime?: number | null;
  /** Time spent tracking, summed over runs (gaps excluded). */
  elapsedSeconds: number;
  timeSeries?: AlignmentTrackPoint[];
  attempts?: AlignmentAttempt[];
}

/**
 * Decomposed geometric mount pointing model terms from plate solves.
 */
export interface MountPointingModel {
  sampleCount: number;
  rawRmsArcsec: number;
  residualRmsArcsec: number;
  improvementPercent?: number;
  ihArcsec?: number;
  idArcsec?: number;
  meArcsec?: number;
  maArcsec?: number;
  chArcsec?: number;
  tfArcsec?: number;
  totalPolarErrorArcsec?: number;
  confidence?: string;
  message?: string;
}

/**
 * How a limit was obtained, from most to least direct.
 *
 * `GEOMETRY` limits follow from the equipment's dimensions or from
 * physics alone. `SENSOR_PROFILE` limits come from the camera's stored
 * profile. `PHYSICAL_BUDGET` limits are the amount of an error the
 * equipment's own measured image quality can absorb. `OWN_BASELINE` limits
 * come from how this same equipment has behaved in the past.
 */
export enum ThresholdTier {
  GEOMETRY = "geometry",
  SENSOR_PROFILE = "sensor_profile",
  PHYSICAL_BUDGET = "physical_budget",
  OWN_BASELINE = "own_baseline"
}

/**
 * Whether a limit could be worked out.
 */
export enum ThresholdStatus {
  DERIVED = "derived",
  INSUFFICIENT_DATA = "insufficient_data"
}

/**
 * One limit, with the record of how it was obtained.
 *
 * Attributes
 * ----------
 * name : `str`
 * The limit's name.
 * value : `float` or `None`
 * The limit. `None` when `status` is `INSUFFICIENT_DATA`.
 * unit : `str`
 * Unit of `value`, for example ``"arcsec"``.
 * tier : `ThresholdTier`
 * How the limit was obtained.
 * status : `ThresholdStatus`
 * Whether it could be worked out.
 * derivation : `str`
 * Plain-language statement of how the value follows from the inputs,
 * or, for a limit with insufficient data, what is missing.
 * inputs : `dict` [`str`, `float` or `str` or `None`]
 * The numbers the value was worked out from, so anyone can redo it.
 * sample_count : `int` or `None`
 * How many measurements a measured input rests on, if any.
 */
export interface PerformanceThreshold {
  name: string;
  value?: number | null;
  unit: string;
  tier: ThresholdTier;
  status: ThresholdStatus;
  derivation: string;
  inputs?: Record<string, number | string | null>;
  sampleCount?: number | null;
}

/**
 * How risky each part of the sky is for the mount's tracking.
 *
 * A grid of scores from 0 (safe) to 1 (likely to trail) over hour angle
 * (HA, west of the meridian positive) and declination. It is fixed to the
 * mount, so it holds all night. Scores combine a geometric prior (west of
 * the meridian, near the pole, low altitude) with the tracking jitter
 * measured on earlier targets near each point (see
 * `wayfindinglib.analytics.tracking_risk`).
 *
 * Attributes
 * ----------
 * latitude_deg : `float`
 * Observer latitude the altitudes were worked out for.
 * ha_deg : `list` [`float`]
 * Grid hour angles, -180 to 180 degrees, evenly spaced.
 * dec_deg : `list` [`float`]
 * Grid declinations, -90 to 90 degrees, evenly spaced.
 * scores : `list` [`list` [`float`]]
 * ``scores[i][j]`` is the score at ``dec_deg[i]`` and ``ha_deg[j]``.
 * caution_score : `float`
 * Scores from here up are a caution.
 * high_score : `float`
 * Scores from here up are a high risk.
 * ideal_rms_arcsec : `float`
 * Jitter up to this keeps stars round (0.75 pixel).
 * trailing_rms_arcsec : `float`
 * Jitter above this trails stars (1.25 pixels).
 * plate_scale_arcsec_per_px : `float` or `None`
 * The camera plate scale the jitter limits came from, if known.
 * measured_target_count : `int`
 * Earlier targets whose measured jitter shaped the scores.
 * solve_count : `int`
 * Plate solves those targets were measured from.
 */
export interface TrackingRiskMap {
  latitudeDeg: number;
  haDeg: number[];
  decDeg: number[];
  scores: number[][];
  cautionScore: number;
  highScore: number;
  idealRmsArcsec: number;
  trailingRmsArcsec: number;
  plateScaleArcsecPerPx?: number | null;
  measuredTargetCount?: number;
  solveCount?: number;
}

/**
 * Every limit for the equipment in use now.
 *
 * Attributes
 * ----------
 * equipment_fingerprint : `str`
 * Identifies the equipment the limits were worked out for (see
 * `build_equipment_fingerprint`).
 * blur_tolerance_fraction : `float`
 * The one policy choice behind the physical-budget limits: the most
 * that guiding error and trailing may widen a star image, as a
 * fraction of its width.
 * thresholds : `dict` [`str`, `PerformanceThreshold`]
 * Every limit, by name.
 * tracking_risk : `TrackingRiskMap` or `None`
 * How risky each part of the sky is for tracking with this
 * equipment. Filled in by `control.history.get_performance_envelope`;
 * `None` in the envelopes the night analyses use internally.
 */
export interface PerformanceEnvelope {
  equipmentFingerprint: string;
  blurToleranceFraction: number;
  thresholds?: Record<string, PerformanceThreshold>;
  trackingRisk?: TrackingRiskMap | null;
}

/**
 * A dominant harmonic frequency identified in guiding telemetry.
 */
export interface GuidingSpectrumPeak {
  periodSeconds: number;
  amplitudeArcsec: number;
  power: number;
  probableSource?: string;
}

/**
 * Periodic error, worm harmonic spectrum, and backlash diagnostics.
 *
 * `id`/`telescope_id`/`schema_version` support this model's other use
 * (M7a): as standing, cross-night mount-mechanical Foundation state
 * persisted via `DiskButler`, refit cumulatively each time
 * `guiding_log_ingestion.py` processes a new guide log, the same
 * status as `GuiderCalibration`/`FocusModel`
 * (`Wayfinding_Library_Architecture.md`). All
 * three default so existing ad hoc, non-persisted analysis results
 * (e.g. `GuidingService.analyze_guiding_spectrum`'s live RPC response)
 * are unaffected.
 */
export interface GuidingSpectrumAnalysis {
  id?: string;
  telescopeId?: string;
  schemaVersion?: number;
  sampleCount: number;
  durationSeconds: number;
  periodicErrorPeakToPeakArcsec?: number;
  dominantPeriodSeconds?: number | null;
  decBacklashEstimateMs?: number | null;
  peaks?: GuidingSpectrumPeak[];
  psdCurve?: Record<string, number>[];
  message?: string;
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
 * owner_pid : `int` or `None`
 * Number of the program running the job, by default `None` (jobs
 * recorded before this was kept).
 * owner_started_at : `str` or `None`
 * When that program started. With `owner_pid` it tells whether the
 * program is still the one that took the job (see
 * `astrometricslib.foundation.jobs.process_identity`).
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
  ownerPid?: number | null;
  ownerStartedAt?: string | null;
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
  knownVariability?: string;
  knownVariabilityNote?: string;
  /** How confident the code is that this star is truly variable. */
  score?: number;
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
 * The guiding now: whether it runs, its RMS and its newest samples.
 *
 * RMS (root-mean-square) error is the usual measure of guiding accuracy,
 * in arcseconds.
 */
export interface LiveGuidingStatus {
  is_guiding?: boolean;
  stats?: GuidingStats;
  history?: GuidingSample[];
  exposure?: number;
  gain?: number;
}

/**
 * The automatic stretch used to draw a picture, so a viewer can redo it.
 *
 * The stretch maps a pixel value v to ``(v - black_point) / (white_point
 * - black_point)``, clipped to 0..1, and then applies the midtones
 * transfer function (MTF) with the balance ``midtones``. The black point
 * sits 2.8 noise levels below the sky (the median), with the noise taken
 * from the median absolute deviation (MAD); the white point is the
 * brightest pixel; and the balance puts the sky at 25% brightness. These
 * are the PixInsight and Siril defaults.
 *
 * Attributes
 * ----------
 * black_point : `float`
 * The pixel value drawn black.
 * white_point : `float`
 * The pixel value drawn white.
 * midtones : `float`
 * The midtones balance, between 0 and 1. 0.5 leaves values unchanged.
 */
export interface StretchParameters {
  blackPoint: number;
  whitePoint: number;
  midtones: number;
}

/**
 * A finished picture ready to display in the app, plus brightness stats.
 *
 * `Visualization.render_fits(kind="data_url")` returns it.
 */
export interface RenderedImage {
  id?: string;
  min: number;
  max: number;
  imageData: string;
  headers?: FitsHeaderEntry[];
  path?: string | null;
  stretchParameters?: StretchParameters | null;
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
  offset?: number | null;
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
 * One panel of a mosaic grid and where its center is.
 */
export interface MosaicPanel {
  /** Grid row, counted from 0. */
  row: number;
  /** Grid column, counted from 0. */
  col: number;
  /** Right ascension of the center, as hours, minutes and seconds. */
  ra_str: string;
  /** Declination of the center, as degrees, minutes and seconds. */
  dec_str: string;
  /** Right ascension of the center, in degrees. */
  ra_deg: number;
  /** Declination of the center, in degrees. */
  dec_deg: number;
  /** Short panel name such as "P1_2" (row 1, column 2). */
  panel_id: string;
}

/**
 * One stretch of time, such as a span when an object is usable.
 */
export interface TimeSpan {
  /** When the stretch begins, as ISO 8601 text. */
  start: string;
  /** When the stretch ends, as ISO 8601 text. */
  end: string;
  /** Length of the stretch, in hours. */
  hours: number;
}

/**
 * Where an object stands relative to the meridian at one moment.
 */
export interface MeridianStatus {
  /** Hour angle, from -12 to 12 hours. Negative means east of the meridian. */
  hour_angle_hours: number;
  /** True once the object is past the meridian by more than the flip delay. */
  flip_required: boolean;
  /** Seconds until the object crosses the meridian. Negative once it has crossed. */
  time_to_flip_seconds: number;
}

/**
 * One meridian crossing inside the span, and when the flip is due.
 */
export interface MeridianCrossing {
  /** When the object crosses the meridian, as ISO 8601 text. */
  crossing: string;
  /** When the meridian flip is due, after the configured delay. */
  flip_due: string;
}

/**
 * The smallest and largest angle between an object and the Moon.
 */
export interface SeparationRange {
  /** Smallest separation over the span, in degrees. */
  minimum_deg: number;
  /** Largest separation over the span, in degrees. */
  maximum_deg: number;
}

/**
 * One row of the time table for one object.
 */
export interface VisibilitySample {
  /** The moment, as ISO 8601 text. */
  time: string;
  /** Altitude of the object. */
  altitude_deg: number;
  /** Azimuth of the object, from north through east. */
  azimuth_deg: number;
  /** True when the object is above the horizon limit at its azimuth. */
  clear: boolean;
  /** Altitude of the Sun. */
  sun_altitude_deg: number;
  /** Altitude of the Moon. */
  moon_altitude_deg: number;
  /** Angle between the object and the Moon. */
  moon_separation_deg: number;
}

/**
 * How one object moves across the sky over the requested span.
 */
export interface VisibilitySpan {
  /** The highest altitude the object reaches in the span. */
  highest_altitude_deg: number;
  /** When it is highest, as ISO 8601 text. */
  highest_altitude_at: string;
  /** Each meridian crossing inside the span. */
  meridian_crossings?: MeridianCrossing[];
  /** When the object is above the horizon limit. */
  clear_of_horizon?: TimeSpan[];
  /** When the object is above the horizon limit and the Sun is below -18 degrees. */
  usable?: TimeSpan[];
  /** How close the Moon comes. */
  moon_separation: SeparationRange;
  /** The time table, one row per step. Filled with include=["samples"]. */
  samples?: VisibilitySample[] | null;
}

/**
 * Where one object is at the start moment, and over the span if asked.
 */
export interface ObjectVisibility {
  /** The object's id. */
  id: string;
  /** The object's common name, when it has one. */
  name?: string | null;
  /** Right ascension (J2000), in degrees. */
  ra_deg: number;
  /** Declination (J2000), in degrees. */
  dec_deg: number;
  /** Altitude at the start moment. */
  altitude_deg: number;
  /** Azimuth at the start moment, from north through east. */
  azimuth_deg: number;
  /** True when the altitude is above 0 degrees. */
  above_horizon: boolean;
  /** True when the object is above the horizon limit at its azimuth. */
  clear: boolean;
  /** UTC time of day it rises, or "Circumpolar" or "Never Rises". */
  rise_utc: string;
  /** UTC time of day it sets, or "Circumpolar" or "Never Rises". */
  set_utc: string;
  /** UTC time of day it crosses the meridian. */
  transit_utc: string;
  /** Hour angle and flip status. Filled with include=["meridian"]. */
  meridian?: MeridianStatus | null;
  /** Movement over the span. Filled when an end time is given. */
  span?: VisibilitySpan | null;
}

/**
 * A star or target placed on the sky, ready for the Planetarium.
 */
export interface SkySource {
  /** The star's or target's id. */
  id: string;
  /** Right ascension, in degrees. */
  ra: number;
  /** Declination, in degrees. */
  dec: number;
  /** The name to show. The id when there is no other name. */
  name: string;
  /** The common name, or the id. */
  commonName: string;
  /** A star's catalog spectral type. None for a target. */
  spectralType?: string | null;
  /** A star's magnitude. None when unknown. */
  magnitude?: number | null;
  /** True when the library holds a spectrum for it. */
  hasSpectra?: boolean;
  /** A star: true when it has a light curve. A target: true when it has a stacked or processed image. */
  hasPhotometry?: boolean;
  /** Whether this is a star or a target. */
  type: any;
  /** True when it came from an online catalog, not from the user's own library. */
  global?: boolean;
  /** The online catalog driver that found it, such as 'deep_stars'. None for the library. */
  catalogSource?: string | null;
  /** A target's stacked image, or its longest light frame when it has no stack. */
  stackedImage?: string | null;
  /** A target's field of view, as saved on the target. */
  fieldOfView?: string | null;
  /** Check if the magnitude is a real catalog magnitude. */
  hasCatalogMagnitude?: boolean;
}

/**
 * The three possible answers of a quality check.
 */
export enum GateStatus {
  PASSED = "passed",
  FAILED = "failed",
  NOT_CHECKED = "not_checked"
}

/**
 * What one quality check found on one run.
 *
 * Parameters
 * ----------
 * name : `str`
 * A short, stable name for the gate, such as ``"flat_noise"``. Tests and
 * the UI match on it, so it must not be reworded once in use.
 * status : `GateStatus`
 * Whether the check passed, failed, or could not be run.
 * measured_value : `float` or `None`
 * The number the check measured, if it measures one.
 * limit : `float` or `None`
 * The limit the measured value was compared with, if there is one.
 * limit_source : `str` or `None`
 * Where the limit came from, such as ``"camera profile (measured)"`` or
 * ``"validated on M 57 only"``.
 * detail : `str`
 * One plain sentence. For a failed gate it says what is wrong. For a
 * ``not_checked`` gate it says why the check could not run.
 */
export interface GateResult {
  name: string;
  status: GateStatus;
  measuredValue?: number | null;
  limit?: number | null;
  limitSource?: string | null;
  detail?: string;
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
 * Which camera profile a pipeline run used, and the numbers from it.
 *
 * A camera profile holds facts about one camera model (see
 * `astrometricslib.models.camera_profile`). Recording it on each summary
 * lets a reader see which assumptions a result rests on, in particular
 * whether the camera was recognised at all.
 */
export interface AppliedCameraProfile {
  cameraName?: string | null;
  profileName: string;
  isGenericFallback: boolean;
  clipCeilingAdu: number;
  clipCeilingSource: string;
  saturationThresholdAdu: number;
  saturationThresholdSource: string;
  saturationThresholdCanBeReached: boolean;
  hasQuantumEfficiencyCurve: boolean;
}

/**
 * Whether the frames and calibration data going into a stack were sound.
 *
 * Answers "was this stack worth running?" using only facts known before
 * the stacking engine started: how many frames survived the pre-checks,
 * whether the sky background changed during the session, and whether the
 * flat frames could be trusted.
 */
export interface StackingInputQuality {
  framesSubmitted: number;
  framesAccepted: number;
  framesExcludedForGain?: number;
  framesExcludedForBackground?: number;
  framesQuarantined?: number;
  backgroundSplitDetected?: boolean;
  backgroundSplitDetail?: string | null;
  flatFrameCount?: number | null;
  flatNoiseFraction?: number | null;
  flatSmoothingSigmaPx?: number | null;
  flatCalibrationIssues?: string[];
  calibrationMismatchFlags?: string[];
  isFlagged?: boolean;
  flagReasons?: string[];
}

/**
 * Whether a finished stack came out well.
 *
 * Answers "can this stacked image be trusted?" using only measurements of
 * the stacked file and of the per-frame results the engine reported.
 * Each measurement is `None` when it does not apply or could not be made
 * (for example the sharpness comparison is only made for images, not for
 * spectra).
 */
export interface StackingOutputQuality {
  rejectedPixelFraction?: number | null;
  saturatedPixelFraction?: number | null;
  zeroPixelFraction?: number | null;
  negativePixelMaxPercent?: number | null;
  stackedFwhmPx?: number | null;
  medianInputFwhmPx?: number | null;
  expectedStackFwhmPx?: number | null;
  spectralRegistrationConcernCount?: number;
  isFlagged?: boolean;
  flagReasons?: string[];
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
  flatFrameCount?: number | null;
  flatNoiseFraction?: number | null;
  flatSmoothingSigmaPx?: number | null;
  flatCalibrationIssues?: string[];
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
  expectedStackFwhmPx?: number | null;
  fwhmDegraded?: boolean;
  spectralRegistrationFlags?: ExcludedFrame[];
  spectralRegistrationChecked?: boolean;
  stackingDurationSeconds?: number | null;
  timedOut?: boolean;
  debayerApplied?: boolean | null;
  stackingEngine?: string | null;
  stackingEngineVersion?: string | null;
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
  cameraProfile?: AppliedCameraProfile | null;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  gates?: GateResult[];
  createdAt?: string;
  provenanceActivityId?: string | null;
  upstreamEntityId?: string | null;
  stackingMetrics: StackingPipelineQualityMetrics;
  inputQuality?: StackingInputQuality | null;
  outputQuality?: StackingOutputQuality | null;
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
  cameraProfile?: AppliedCameraProfile | null;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  gates?: GateResult[];
  createdAt?: string;
  provenanceActivityId?: string | null;
  upstreamEntityId?: string | null;
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
  cameraProfile?: AppliedCameraProfile | null;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  gates?: GateResult[];
  createdAt?: string;
  provenanceActivityId?: string | null;
  upstreamEntityId?: string | null;
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
  cameraProfile?: AppliedCameraProfile | null;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  gates?: GateResult[];
  createdAt?: string;
  provenanceActivityId?: string | null;
  upstreamEntityId?: string | null;
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
  ephemerisQueriesAttempted?: number;
  ephemerisQueriesFailed?: number;
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
  cameraProfile?: AppliedCameraProfile | null;
  qualityProcessingApplied?: boolean;
  flagged?: boolean;
  flagReasons?: string[];
  gates?: GateResult[];
  createdAt?: string;
  provenanceActivityId?: string | null;
  upstreamEntityId?: string | null;
  asteroidDetectionMetrics: AsteroidDetectionPipelineQualityMetrics;
}

/**
 * A single weather observation.
 *
 * No populator exists yet -- schema-ready, empty until an actual
 * weather-station integration is built. Recorded for context in the
 * session's telemetry, distinct from the safety monitor's environmental
 * verdict (`SafetyAssessment`), which must not be best-effort.
 */
export interface WeatherSample {
  time: number;
  ambientTemperatureC?: number | null;
  humidityPercent?: number | null;
  dewPointC?: number | null;
}

/**
 * Every RPC method the backend serves, from backend/public_interface.py.
 */
export const RPC_METHODS = [
  "system:health",
  "system:frontend_log",
  "system:completions",
  "system:get_config",
  "system:save_config",
  "system:introspection",
  "system:cameras",
  "system:filters",
  "system:pulse",
  "system:notifications",
  "system:save",
  "terminal:execute",
  "terminal:get_workspace",
  "terminal:completions",
  "terminal:list_recipes",
  "terminal:get_recipe",
  "terminal:list_scripts",
  "terminal:read_script",
  "terminal:save_script",
  "terminal:reset_workspace",
  "docs:list_topics",
  "docs:search_topics",
  "docs:get_topic",
  "ui:editor_get",
  "ui:editor_set",
  "ui:navigate",
  "ui:inspect_variable",
  "handoff:get_state",
  "handoff:update_state",
  "handoff:beam",
  "handoff:list_devices",
  "handoff:send_alert",
  "handoff:share_file",
  "guiding:status",
  "guiding:start",
  "guiding:stop",
  "guiding:capture_frame",
  "telescope:connect",
  "telescope:abort_motion",
  "telescope:apply_promotion_decision",
  "telescope:focus_move",
  "telescope:get_focuser_position",
  "telescope:manual_move",
  "telescope:park",
  "telescope:set_filter",
  "telescope:set_slew_rate",
  "telescope:set_tracking",
  "telescope:slew_coordinates",
  "telescope:slew_target",
  "telescope:status",
  "telescope:sync",
  "telescope:is_syncing",
  "telescope:unpark",
  "telescope:indi_devices",
  "telescope:indi_properties",
  "telescope:set_indi_property",
  "telescope:alignment_start",
  "telescope:alignment_stop",
  "telescope:list_alignment_sessions",
  "telescope:get_session_alignment",
  "telescope:get_cumulative_tracking_data",
  "telescope:sync_logs",
  "telescope:get_pointing_model",
  "telescope:get_performance_envelope",
  "telescope:get_guiding_spectrum",
  "observatory:list_cameras",
  "observatory:get_equipment_configuration",
  "observatory:set_active_camera",
  "observatory:enter_monitoring_mode",
  "observatory:enter_controller_mode",
  "ingestion:start",
  "ingestion:status",
  "ingestion:scan",
  "ingestion:stats",
  "ingestion:list_files",
  "ingestion:reindex",
  "processing:stack",
  "processing:siril_open",
  "processing:cancel",
  "processing:status",
  "processing:list_jobs",
  "processing:active_jobs",
  "processing:get_job",
  "processing:delete_job",
  "processing:jobs_for_target",
  "processing:job_log_tail",
  "analysis:analyze_image",
  "analysis:get_results",
  "analysis:cancel",
  "target:list",
  "target:get",
  "target:get_targets",
  "target:create",
  "target:update",
  "target:delete",
  "target:add_data",
  "target:send_to_phone",
  "target:refresh",
  "target:get_files",
  "target:get_camera_index",
  "target:get_frames",
  "target:get_frames_grouped",
  "target:get_frame_header",
  "astronomy:list",
  "astronomy:count",
  "astronomy:target_data_availability",
  "astronomy:spectral_class_summary",
  "astronomy:stars_by_spectral_class",
  "astronomy:get",
  "astronomy:save",
  "astronomy:delete",
  "astronomy:analyze_periodicity",
  "astronomy:get_stellar_objects",
  "astronomy:get_overlay_stars",
  "astronomy:get_status",
  "astronomy:visible",
  "planetarium:get_sources",
  "planetarium:get_targets",
  "planetarium:get_visibility",
  "planetarium:get_observer_location",
  "planetarium:get_catalog_sources",
  "planetarium:list_catalog_drivers",
  "planetarium:get_deep_catalog_status",
  "planetarium:get_constellation_lines",
  "imaging:capture",
  "imaging:get_active_jobs",
  "images:get_target_frame",
  "images:get_light_frame_data",
  "images:convert_fits",
  "images:get_fits_header",
  "images:delete",
  "images:last",
  "calibration:get_stats",
  "mosaic:preview",
  "mosaic:create",
  "execution:list_sessions",
  "execution:get_session",
  "execution:abort_session",
  "execution:reconcile_session",
  "execution:record_divergence",
  "sequencer:get_queue",
  "sequencer:create_plan",
  "sequencer:add",
  "sequencer:remove",
  "sequencer:reorder",
  "sequencer:begin",
  "sequencer:modify",
] as const;

/** The name of one RPC method the backend serves. */
export type RpcMethod = (typeof RPC_METHODS)[number];

/**
 * The path of every other route the backend serves, by name, from
 * backend/public_interface.py.
 */
export const BACKEND_ROUTES = {
  /** Answers every RPC method in RPC_METHODS. */
  rpc: "/api/rpc",
  /** Says whether start-up warm-up has finished. */
  ready: "/api/ready",
  /** Gives the WebSocket token to the app. */
  sessionToken: "/api/session-token",
  /** Tells a companion phone how to connect. */
  pairingInfo: "/api/pairing-info",
  /** Says the backend is running. */
  root: "/",
  /** Streams live events and telemetry to the app. */
  eventsSocket: "/ws/events",
  /** Runs the plain Python terminal. */
  terminalSocket: "/ws/terminal",
  /** Drives one interactive Matplotlib figure. */
  figureSocket: "/ws/figure/{figure_id}",
  /** Serves the Matplotlib figure script. */
  figureScript: "/figure/mpl.js",
  /** Serves the page of one interactive figure. */
  figurePage: "/figure/{figure_id}",
  /** Downloads a figure as an image. */
  figureDownload: "/figure/{figure_id}/download.{fmt}",
  /** Serves raw frames, then stacks, by their path in the frames folder. */
  staticFrames: "/static/frames",
  /** Serves files by their path in the library folder. */
  staticLibrary: "/static",
  /** Serves Matplotlib's figure styles. */
  figureStatic: "/mpl_static",
  /** Serves Matplotlib's toolbar icons. */
  figureImages: "/_images",
  /** Serves Matplotlib's toolbar icons to a figure page. */
  figurePageImages: "/figure/_images",
} as const;
