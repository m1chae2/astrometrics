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
 * See `astrometricslib.utilities.target_classification` for the
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
  saturatedPixelFractionLimitAdu?: number | null;
  measuredFwhmPx?: number | null;
  registrationFwhmXPx?: number | null;
  registrationFwhmYPx?: number | null;
  registrationWeightedFwhmPx?: number | null;
  registrationRoundness?: number | null;
  registrationQuality?: number | null;
  registrationBackgroundLevel?: number | null;
  registrationRotationDeg?: number | null;
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
  fileSize?: number | null;
  fileModifiedNs?: number | null;
  qualityMeasuredFileSize?: number | null;
  qualityMeasuredFileModifiedNs?: number | null;
  qualityMeasuredMetricSet?: string | null;
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
  positionUnverified?: boolean;
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
  gaiaDr3SourceId?: number | null;
  vsxVariabilityType?: string;
  vsxBrightMagnitude?: number | null;
  vsxFaintMagnitude?: number | null;
  vsxAmplitudeMag?: number | null;
  vsxPeriodDays?: number | null;
  vsxMagnitudeBand?: string;
  vsxRangeChecked?: boolean;
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
  intensityErrors?: number[] | null;
  quantumEfficiencyCorrectedIntensities?: number[] | null;
  responseCorrectedIntensities?: number[] | null;
  responseCorrectedIntensityErrors?: number[] | null;
  intensityNoiseModel?: SpectralNoiseModelRecord | null;
  selfDeterminedSpectralType?: string;
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
  measuredLineSpread?: MeasuredLineSpread | null;
  extractionRadius?: number | null;
  neighborWingFraction?: number[] | null;
  neighborWingStatus?: string | null;
  possibleNeighborContamination?: Record<string, number | null>[] | null;
  countsPerSecondFactor?: number | null;
  catalogComparison?: CatalogComparison | null;
  inputQuality?: InputQualityAssessment | null;
  outputQuality?: OutputQualityAssessment | null;
  stageQuality?: StageQualityCheckpoint[];
  wavelengthZeroPoint?: WavelengthZeroPointRecord | null;
  reddening?: ReddeningRecord | null;
  lineIndexClassification?: LineIndexClassification | null;
  extractionDiagnostics?: SpectralExtractionDiagnostics | null;
  extinctionCorrection?: ExtinctionCorrectionRecord | null;
  differentialRefraction?: DifferentialRefractionRecord | null;
  gaiaXpComparison?: GaiaXpComparison | null;
  generatedByJobId?: string | null;
  /** Check if even the closest reference spectrum fits badly. */
  isPoorMatch?: boolean;
  /** Measure how much closer the best reference is than the runner-up. */
  rmsGapToSecondBest?: number | null;
  /** Check if the best reference barely beats the runner-up. */
  isAmbiguous?: boolean | null;
  /** Measure how much closer the best reference is than the next class. */
  rmsGapToNextClass?: number | null;
  /** Check if another spectral class fits almost as well as the best. */
  isClassAmbiguous?: boolean | null;
}

/**
 * What the extractor did while it read one star's spectrum.
 *
 * A short summary of the extractor's `ExtractionDiagnostics`. It shows
 * whether the sky background was read cleanly and how wide the reading
 * box was, so a reader can judge the spectrum without re-running the
 * extraction.
 */
export interface SpectralExtractionDiagnostics {
  skyModeCounts?: Record<string, number>;
  dominantSkyMode?: string | null;
  contaminatedSkyFraction?: number;
  apertureHalfWidthMedianPx?: number | null;
  apertureHalfWidthSpreadPx?: number | null;
}

/**
 * What the refraction correction did to a spectrum's wavelengths.
 *
 * Mirrors `DifferentialRefraction` in the spectroscopy pipeline. Air bends
 * blue light toward the zenith (the point straight overhead) more than red
 * light. The zero-order image is a white-light image, so each wavelength of
 * the spectrum lands slightly off the place the wavelength scale gives it.
 * The part of that shift along the dispersion changes the wavelength. The
 * part across it only widens the trail. Every number is `None` when it
 * could not be computed.
 */
export interface DifferentialRefractionRecord {
  isComputed: boolean;
  isApplied: boolean;
  reason?: string | null;
  midExposureUtc?: string | null;
  altitudeDegrees?: number | null;
  parallacticAngleDegrees?: number | null;
  dispersionPositionAngleDegrees?: number | null;
  parallacticToDispersionAngleDegrees?: number | null;
  pixelScaleArcsec?: number | null;
  effectiveWavelengthAngstrom?: number | null;
  pressureHpa?: number | null;
  temperatureC?: number | null;
  relativeHumidityPercent?: number | null;
  atmosphereSource?: string | null;
  alongDispersionArcsecAt4200?: number | null;
  alongDispersionArcsecAt8000?: number | null;
  alongDispersionAngstromAt4200?: number | null;
  alongDispersionAngstromAt8000?: number | null;
  acrossDispersionArcsecAt4200?: number | null;
  acrossDispersionArcsecAt8000?: number | null;
  alongDispersionSpanAngstrom?: number | null;
  acrossDispersionSpanPx?: number | null;
}

/**
 * The blur of one spectrum, measured in wavelength bands.
 *
 * Every list has one entry per band, in order of increasing wavelength.
 * A band that had too few working trail-width fits is left out of all the
 * lists, so the lists always have the same length.
 */
export interface MeasuredLineSpread {
  wavelengthAngstrom?: number[];
  fwhmAngstrom?: number[];
  fwhmPx?: number[];
  scatterPx?: number[];
  sampleCount?: number[];
}

/**
 * The camera numbers behind a spectrum's per-sample errors.
 *
 * Mirrors `PixelNoiseModel` in the spectroscopy pipeline. The error of each
 * sample comes from the photon noise of its pixels (which needs the camera's
 * gain), the read noise, and the error of the sky level. When a number is
 * not known for the camera, the pipeline assumes one and says so here.
 */
export interface SpectralNoiseModelRecord {
  gainEPerAdu: number;
  readNoiseE: number;
  gainIsAssumed: boolean;
  readNoiseIsAssumed: boolean;
  aduPerStoredUnit?: number;
  framesAveraged?: number;
  framesAreAssumed?: boolean;
  rejectionMapUsed?: boolean;
}

/**
 * The reddening correction applied before a spectrum was classified.
 *
 * Dust between us and a star dims blue light more than red light and tilts
 * the spectrum toward the red. The colour excess E(B-V) measures how much,
 * in magnitudes. When a catalog gives E(B-V) for a star, the pipeline
 * removes the tilt (see `interstellar_extinction`) and classifies both the
 * observed and the dereddened spectrum. This record keeps both results.
 */
export interface ReddeningRecord {
  ebv: number;
  ebvSource: string;
  gaiaSourceId?: string;
  observedBestType?: string;
  dereddenedBestType?: string;
  typeShiftSteps?: number | null;
}

/**
 * A spectral type estimated from line strengths alone.
 *
 * Each index is the depth of one spectral line or band below the continuum
 * next to it, as a fraction of that continuum (0.1 means 10% dimmer). The
 * same indices are measured on every bundled reference spectrum after
 * blurring it to the instrument's resolution. The best type is the
 * reference whose indices are closest to the star's. It is a cross-check on
 * the template classifier, not a replacement for it.
 */
export interface LineIndexClassification {
  bestType: string;
  distance: number;
  indices?: Record<string, number>;
  templateFitType?: string;
  stepsFromTemplateFit?: number | null;
}

/**
 * How one wavelength band of a spectrum compares with the XP spectrum.
 *
 * Both spectra are scaled to match at 5400-5600 Angstroms first, so the
 * numbers show the change of shape across the spectrum, not its brightness.
 */
export interface GaiaXpBandResidual {
  startAngstrom: number;
  endAngstrom: number;
  medianRatio?: number | null;
  rmsFraction?: number | null;
  sampleCount?: number;
}

/**
 * The result of checking one spectrum against its Gaia XP spectrum.
 *
 * ``status`` is ``"compared"`` when the check ran, and ``"not_checked"``
 * when it could not (no Gaia id, no XP spectrum, no response-corrected
 * spectrum). A not-checked record keeps its reason and has no numbers.
 */
export interface GaiaXpComparison {
  status: any;
  notCheckedReason?: string;
  gaiaSourceId?: number | null;
  sampleCount?: number;
  residualRmsFraction?: number | null;
  slopePercentPer1000Angstrom?: number | null;
  slopeErrorPercentPer1000Angstrom?: number | null;
  wavelengthShiftAngstrom?: number | null;
  wavelengthShiftCorrelation?: number | null;
  bands?: GaiaXpBandResidual[];
  normalizationWindowAngstrom?: number[];
  observedNormalization?: number | null;
  xpNormalization?: number | null;
  xpWasBlurred?: boolean;
  observedWasBlurred?: boolean;
  usedObservedErrors?: boolean;
}

/**
 * The run's comparison with XP in one wavelength band, over all stars.
 *
 * The median ratio of observed to XP is the measured residual instrument
 * response in this band: dividing the response-corrected spectra by it would
 * make them agree with Gaia.
 */
export interface GaiaXpBandSummary {
  startAngstrom: number;
  endAngstrom: number;
  medianRatio?: number | null;
  scatter?: number | null;
  starCount?: number;
}

/**
 * How a run's spectra compare with Gaia XP, summed over its stars.
 *
 * Each star counts once, however many spectra (frames) it has: its numbers
 * are the median over its own spectra.
 */
export interface GaiaXpRunSummary {
  comparedStarCount?: number;
  bands?: GaiaXpBandSummary[];
  medianSlopePercentPer1000Angstrom?: number | null;
  medianAbsoluteSlopePercentPer1000Angstrom?: number | null;
}

/**
 * Whether the airmass correction was applied to a spectrum, and with what.
 *
 * Mirrors `ExtinctionCorrection` in the spectroscopy pipeline. Airmass is
 * how much air the light crossed (1.0 straight overhead, larger nearer the
 * horizon). The instrument response removes the air's dimming at the
 * standard star's airmass, and this correction rescales the spectrum for
 * the airmass of the target's frame.
 */
export interface ExtinctionCorrectionRecord {
  isApplied: boolean;
  targetAirmass?: number | null;
  referenceAirmass?: number | null;
  curveName: string;
  reason?: string | null;
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
  isPoorMatch?: boolean | null;
  isAmbiguous: boolean;
  isClassAmbiguous?: boolean | null;
  isSubtypeFinerThanResolution?: boolean | null;
  catalogAgrees?: boolean | null;
  isTrustworthy: boolean;
}

/**
 * One measured number at a quality checkpoint, with its limit and verdict.
 *
 * Every checkpoint reports its numbers in this one shape. A reader can
 * compare a metric with its limit without knowing which stage made it. Build
 * one with `metric`, which fills in `passed`.
 */
export interface StageQualityMetric {
  name: string;
  value?: number | null;
  unit?: string;
  limit?: number | null;
  passed?: boolean | null;
  note?: string;
}

/**
 * All the quality numbers measured at one point of the pipeline.
 *
 * A spectrum passes four checkpoints, in order: ``raw_frame``,
 * ``pre_processing``, ``processing`` and ``post_processing``. The
 * checkpoints let a reader see where in the pipeline quality was lost.
 */
export interface StageQualityCheckpoint {
  stage: any;
  metrics?: StageQualityMetric[];
  flags?: string[];
}

/**
 * How one checkpoint went across all the spectra of a run.
 *
 * Built from every spectrum's `StageQualityCheckpoint` for the same stage.
 * It tells a reader how many spectra had a problem at this stage and what a
 * typical spectrum measured.
 */
export interface StageQualityRollup {
  stage: any;
  spectrumCount?: number;
  failedSpectrumCount?: number;
  metricMedians?: Record<string, number>;
}

/**
 * One absorption line used to measure a spectrum's zero-point offset.
 *
 * The pipeline looks for the dip a known line makes and compares where it
 * found the dip with the line's known (rest) wavelength.
 */
export interface WavelengthZeroPointLine {
  name: string;
  restWavelengthAngstrom: number;
  measuredWavelengthAngstrom: number;
  offsetAngstrom: number;
  uncertaintyAngstrom: number;
  depth: number;
  significance: number;
}

/**
 * The zero-point offset measured for one spectrum, and what was done.
 *
 * The offset is the error-weighted mean of measured minus rest wavelength
 * over the lines found. The pipeline shifts the spectrum's wavelengths by it
 * only when at least two lines agree with each other within their errors.
 */
export interface WavelengthZeroPointRecord {
  offsetAngstrom?: number | null;
  uncertaintyAngstrom?: number | null;
  lineCount?: number;
  chiSquare?: number | null;
  agreementPValue?: number | null;
  isApplied?: boolean;
  zeroOrderSaturatedPixelFraction?: number | null;
  isZeroOrderSaturated?: boolean | null;
  lines?: WavelengthZeroPointLine[];
}

/**
 * How well the wavelength scale held across the spectra of a run.
 *
 * Built from the pre-processing checkpoint of each spectrum. It counts only
 * spectra with a measured offset.
 */
export interface WavelengthScaleSummary {
  spectrumCount: number;
  appliedCount: number;
  rmsBeforeCorrectionAngstrom: number;
  rmsAfterCorrectionAngstrom: number;
  offsetSaturationCorrelation?: number | null;
  correlationSpectrumCount?: number;
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
  shuffleCount?: number | null;
  nullBlockLength?: number | null;
  familyWiseFalseAlarmProbability?: number | null;
  searchesInFamily?: number | null;
  uncorrectedVerdict?: string;
  verdictChecks?: GateResult[];
  unconfirmed?: boolean;
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
  shuffleCount?: number | null;
  nullBlockLength?: number | null;
  familyWiseFalseAlarmProbability?: number | null;
  searchesInFamily?: number | null;
  uncorrectedVerdict?: string;
  verdictChecks?: GateResult[];
  unconfirmed?: boolean;
}

/**
 * What one observing session contributed to a merged light curve.
 *
 * A star seen on several nights has one merged light curve. Each night
 * is normalized against its own group of comparison stars, so the
 * levels of the nights are only comparable if those groups behave
 * alike. This record keeps what a reader needs to judge that: which
 * session, how many comparison stars it used, how bright their
 * typical member was, and where the star's own normalized level sat.
 */
export interface SessionPhotometrySummary {
  sessionId: string;
  pointCount?: number;
  medianNormalizedFlux?: number | null;
  normalizedFluxScatter?: number | null;
  comparisonStarCount?: number | null;
  ensembleMedianFlux?: number | null;
  comparisonStarIds?: string[];
  comparisonScatterMag?: number | null;
  comparisonRejectedCount?: number | null;
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
  fluxErrors?: number[];
  fluxesNormalizedErrors?: number[];
  fluxesDetrendedErrors?: number[];
  errorsAssumeUnitGain?: boolean | null;
  errorsAssumeZeroReadNoise?: boolean | null;
  timeBjdTdb?: number[];
  timeBasis?: string | null;
  periodogram?: PeriodogramResult | null;
  transitCandidate?: TransitCandidate | null;
  meanFlux?: number | null;
  coefficientOfVariation?: number | null;
  inputQuality?: InputQualityAssessment | null;
  outputQuality?: OutputQualityAssessment | null;
  generatedByJobId?: string | null;
  sessionSummaries?: SessionPhotometrySummary[];
  betweenSessionAmplitudeMag?: number | null;
  betweenSessionSignificance?: number | null;
  instrumentalMag?: number | null;
  rmsMag?: number | null;
  excessScatter?: number | null;
  reducedChiSquare?: number | null;
  stetsonJ?: number | null;
  variabilityScore?: number | null;
  falseAlarmProbability?: number | null;
  falseDiscoveryQValue?: number | null;
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
 *
 * `wayfindinglib.group_alignment_attempts` builds these from a list of
 * `AlignmentAttempt` records.
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
 * thresholds : `dict` [`str`, `QualityLimit`]
 * Every limit, by name. A limit whose value is `None` could not be
 * worked out; its derivation says why.
 * limit_inputs : `dict` [`str`, `dict` [`str`, `float` or `str` or `None`]]
 * For each limit, by name, the numbers it was worked out from, so
 * anyone can redo it. `QualityLimit` has no place for them.
 * tracking_risk : `TrackingRiskMap` or `None`
 * How risky each part of the sky is for tracking with this
 * equipment. Filled in by `control.history.get_performance_envelope`;
 * `None` in the envelopes the night analyses use internally.
 */
export interface PerformanceEnvelope {
  equipmentFingerprint: string;
  blurToleranceFraction: number;
  thresholds?: Record<string, QualityLimit>;
  limitInputs?: Record<string, Record<string, number | string | null>>;
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
 * (`Wayfinding_Library_Architecture.md` Section 6.7): as standing,
 * cross-night mount-mechanical Foundation state persisted via
 * `DiskButler`, refit cumulatively each time `guiding_log_ingestion.py`
 * processes a new guide log, the same status as
 * `GuiderCalibration`/`FocusModel` (`Wayfinding_Library_Architecture.md`
 * Section 3.2). All
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
  excessScatter?: number | null;
  reducedChiSquare?: number | null;
  stetsonJ?: number | null;
  variabilityScore?: number | null;
  falseAlarmProbability?: number | null;
  falseDiscoveryQValue?: number | null;
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
 *
 * Only the system health report uses it, so it lives here rather than in
 * wayfindinglib.
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
  binning?: string | null;
  temperatureC?: number | null;
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
 * One corner of a panel, as a point on the sky.
 */
export interface SkyCorner {
  /** Right ascension, in degrees. */
  ra_deg: number;
  /** Declination, in degrees. */
  dec_deg: number;
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
  /** How far the camera is turned, in degrees east of north (the way Ekos and KStars give it). 0 puts the grid rows along declination and the columns along right ascension. */
  rotation_deg?: number;
  /** What `rotation_deg` means, in words, for display. */
  rotation_convention?: string;
  /** The four corners of the panel's field of view, in drawing order: lower left, lower right, upper right, upper left of the camera image. Empty when the field of view is not known. */
  corners?: SkyCorner[];
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
  gainMidRange?: number | null;
  gainBrightEndRatio?: number | null;
  gainDisagreement?: number | null;
  gainApplied?: number | null;
  gainNonlinear?: boolean;
  leftOutReason?: string | null;
  brightestStarPeakFraction?: number | null;
  brightestStarPeakAboveSkyAdu?: number | null;
  peakSkyAdu?: number | null;
  peakClipCeilingAdu?: number | null;
  peakLinearityLimitAdu?: number | null;
  peakFramesWithPeak?: number;
  peakFramesEstimated?: number;
  peakFramesClipped?: number;
  peakFramesSampled?: number;
  peakFramesOnProfileCeiling?: number;
  peakLargestEstimateToRoomRatio?: number | null;
  peakClipCeilingProvenance?: string | null;
  peakLinearityLimitProvenance?: string | null;
  peakCheckedRangeRoomMultiples?: number;
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
  catalogMatchSeparationRmsArcsec?: number | null;
  plateSolveFitResidualRmsArcsec?: number | null;
  plateSolveMatchedStarCount?: number | null;
  gaiaFitRmsArcsec?: number | null;
  gaiaFitRmsBeforeArcsec?: number | null;
  gaiaFitStarCount?: number | null;
  wcsRefinementShiftArcsec?: number | null;
  astrometryFlags?: string[];
  plateScaleArcsecPerPixel?: number | null;
  starFwhmPx?: number | null;
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
 * One point of a photometry run's noise-model curve.
 *
 * The curve says how much scatter a constant star of each brightness shows
 * in the run's field. A plot of these points, with the stars' own
 * `instrumentalMag` and `rmsMag`, shows which stars sit above the curve.
 */
export interface NoiseModelPoint {
  instrumentalMag: number;
  rmsMag: number;
  starCount: number;
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
  noiseModelCurve?: NoiseModelPoint[];
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
  classificationRms?: number | null;
  rmsGapToNextClass?: number | null;
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
  poorMatchClassificationCount?: number;
  ambiguousClassificationCount?: number;
  flaggedSpectralClassifications?: SpectralClassificationConcern[];
  stageQualitySummary?: StageQualityRollup[] | null;
  gaiaXpSummary?: GaiaXpRunSummary | null;
  wavelengthScaleSummary?: WavelengthScaleSummary | null;
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
  astrometricErrorArcsec?: number | null;
  astrometricErrorRaArcsec?: number | null;
  astrometricErrorDecArcsec?: number | null;
  astrometricErrorCorrelation?: number | null;
  exposureSeconds?: number | null;
}

/**
 * The calculated path (speed, direction) of an object across pictures.
 */
export interface MovingObjectTrack {
  rightAscensionRateArcsecPerHour: number;
  declinationRateArcsecPerHour: number;
  totalRateArcsecPerHour: number;
  linearFitRSquared: number;
  residualRmsRightAscensionArcsec?: number | null;
  residualRmsDeclinationArcsec?: number | null;
  astrometricErrorArcsec?: number | null;
  astrometricErrorRaArcsec?: number | null;
  astrometricErrorDecArcsec?: number | null;
  residualLimitArcsec?: number | null;
  astrometricErrorAssumed?: boolean | null;
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
  REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE = "rejected_nonlinear_or_out_of_range_rate",
  REJECTED_NON_MONOTONIC = "rejected_non_monotonic"
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
  firstDetectionSeparationArcsec?: number | null;
  lastDetectionSeparationArcsec?: number | null;
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
  residualChainsTested?: number | null;
  residualChainsRejected?: number | null;
  residualChainsNonMonotonic?: number | null;
  residualAssumedErrorDetections?: number | null;
  residualWorstAcceptedRatio?: number | null;
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
 * What a measurement describes. There is no night or target level.
 */
export enum SubjectLevel {
  FRAME = "frame",
  STACK = "stack",
  RUN = "run"
}

/**
 * Whether a value was measured, assumed, or not measured at all.
 */
export enum MeasurementStatus {
  MEASURED = "measured",
  ASSUMED = "assumed",
  NOT_MEASURED = "not_measured"
}

/**
 * How a stored row came to exist.
 */
export enum MeasurementOrigin {
  MEASURED = "measured",
  BACKFILLED = "backfilled"
}

/**
 * How a limit was obtained, from most fixed to most data-driven.
 */
export enum LimitTier {
  GEOMETRY = "geometry",
  SENSOR_PROFILE = "sensor_profile",
  PHYSICAL_BUDGET = "physical_budget",
  DESIGN_ESTIMATE = "design_estimate",
  OWN_BASELINE = "own_baseline",
  VALIDATED_DATASET = "validated_dataset"
}

/**
 * The point in processing where a metric is measured.
 */
export enum Checkpoint {
  FRAME = "frame",
  CALIBRATION = "calibration",
  STACKING = "stacking",
  ASTROMETRY = "astrometry",
  PHOTOMETRY = "photometry",
  SPECTROSCOPY = "spectroscopy",
  ASTEROID_DETECTION = "asteroid_detection"
}

/**
 * Which direction of a metric is better.
 */
export enum Direction {
  LOWER = "lower",
  HIGHER = "higher",
  NONE = "none"
}

/**
 * What kind of uncertainty a metric reports.
 *
 * ``MODEL_DERIVED`` marks a value that adds a term from a model (such as the
 * blur of a square pixel) to a measured value. Its uncertainty carries only
 * the statistical error of the measured part. The error of the model term
 * is not included, so the value must be read as model-derived.
 */
export enum UncertaintyKind {
  STATISTICAL_1_SIGMA = "statistical_1_sigma",
  SYSTEMATIC_BOUND = "systematic_bound",
  MODEL_DERIVED = "model_derived",
  NONE = "none"
}

/**
 * Why a metric exists.
 */
export enum MetricPurpose {
  DECISION = "decision",
  CONTEXT = "context",
  EXPLORATORY = "exploratory"
}

/**
 * The thing a measurement is about.
 *
 * Parameters
 * ----------
 * level : `SubjectLevel`
 * Whether the subject is a frame, a stack or a run.
 * subject_id : `str`
 * A stable id: target plus relative frame path, stack id or run id.
 * Never a mount path.
 * target_id : `str` or `None`
 * The target the subject belongs to.
 * observing_night_id : `str` or `None`
 * A plain time label for the observing night.
 * capture_setting_id : `int` or `None`
 * Row in the capture-setting table.
 * channel : `str` or `None`
 * The color channel for a one-shot-color camera, else ``None``.
 * variant : `str` or `None`
 * ``None`` for a library stack (and for every frame and run). For a
 * subset stack made from some of a library stack's frames, the subset's
 * label. A reader filters on this field; it never has to parse the
 * subject id for ``_subsets/``.
 */
export interface QualitySubject {
  level: SubjectLevel;
  subjectId: string;
  targetId?: string | null;
  observingNightId?: string | null;
  captureSettingId?: number | null;
  channel?: string | null;
  variant?: string | null;
}

/**
 * One limit that a measurement can be compared with.
 *
 * Parameters
 * ----------
 * value : `float` or `None`
 * The limit itself. ``None`` when the limit could not be computed.
 * unit : `str`
 * The unit of `value`, such as ``"arcsec"``.
 * tier : `LimitTier`
 * How the limit was obtained.
 * derivation : `str`
 * A plain-language statement of how the value was found.
 * sample_count : `int` or `None`
 * The number of samples behind the limit, when it comes from data.
 * register_entry : `str` or `None`
 * The validation register entry that holds the evidence, such as
 * ``"STK-07"``. Required when `tier` is ``validated_dataset``.
 */
export interface QualityLimit {
  value: number | null;
  unit: string;
  tier: LimitTier;
  derivation: string;
  sampleCount?: number | null;
  registerEntry?: string | null;
}

/**
 * One combination of equipment and exposure settings.
 *
 * Parameters
 * ----------
 * camera : `str`
 * The camera model name.
 * telescope : `str` or `None`
 * The telescope or lens name.
 * focal_length_mm : `float` or `None`
 * The focal length in millimeters.
 * pixel_scale_arcsec : `float` or `None`
 * The size of one pixel on the sky, in arcseconds per pixel, rounded to
 * `PIXEL_SCALE_SIGNIFICANT_FIGURES` significant figures.
 * filter_name : `str` or `None`
 * The filter in the light path, such as ``"L"`` or ``"Ha"``.
 * exposure_seconds : `float` or `None`
 * The exposure time of one frame, in seconds.
 * gain_setting : `int` or `None`
 * The camera's gain setting (a unitless number in the FITS ``GAIN``
 * card, not electrons per ADU).
 * offset : `int` or `None`
 * The constant the camera adds to every pixel.
 * binning : `int` or `None`
 * The binning factor (1 means no binning).
 */
export interface CaptureSetting {
  camera: string;
  telescope?: string | null;
  focalLengthMm?: number | null;
  pixelScaleArcsec?: number | null;
  filterName?: string | null;
  exposureSeconds?: number | null;
  gainSetting?: number | null;
  offset?: number | null;
  binning?: number | null;
}

/**
 * Points at one per-star result, which has no row id of its own.
 *
 * Parameters
 * ----------
 * star_id : `str`
 * The star's identifier.
 * run_id : `str`
 * The run that produced the result.
 * metric : `str`
 * A registered metric name.
 */
export interface StarResultReference {
  starId: string;
  runId: string;
  metric: string;
}

/**
 * Points at the exact measurement a judgment or report used.
 *
 * Exactly one of `row_id` and `star_result` is set.
 *
 * Parameters
 * ----------
 * row_id : `int` or `None`
 * A row in the measurement store.
 * star_result : `StarResultReference` or `None`
 * A per-star result.
 */
export interface MeasurementReference {
  rowId?: number | null;
  starResult?: StarResultReference | null;
}

/**
 * One measured fact about a subject.
 *
 * Parameters
 * ----------
 * metric : `str`
 * A registered metric name, such as ``"frame.star_width_moffat_arcsec"``.
 * definition_version : `int`
 * The registered definition version of that metric.
 * subject : `QualitySubject`
 * What the measurement is about.
 * status : `MeasurementStatus`
 * Measured, assumed or not measured.
 * value : `float` or `None`
 * The value. ``None`` exactly when the status is ``not_measured``.
 * uncertainty : `float` or `None`
 * The uncertainty of the value, in the same unit.
 * sample_count : `int` or `None`
 * The number of samples behind the value, such as the number of stars.
 * reason : `str`
 * Why the value is missing or assumed. Required for ``not_measured``.
 * check_outcome : `GateStatus` or `None`
 * For a self-check, whether it passed, failed or was not checked.
 * check_limit : `QualityLimit` or `None`
 * For a self-check, the limit it used.
 * action_taken : `str` or `None`
 * For a processing decision, what was done, such as
 * ``"frame moved to _excluded/"``.
 * origin : `MeasurementOrigin`
 * Whether a pipeline run wrote the row or it was backfilled.
 * pipeline_version : `str` or `None`
 * The pipeline version. ``None`` when a backfilled row did not record it.
 * run_id : `str`
 * The job id, or a generated id when there was no job.
 * job_id : `str` or `None`
 * A soft link to the activity record of the job.
 * measured_at : `datetime`
 * When the measurement was made. It must carry a time zone.
 * row_id : `int` or `None`
 * The store's row id. Filled only when the measurement is read back.
 * unit : `str` or `None`
 * The metric's unit from its declaration. Not stored:
 * `QualityRecords.query` fills it on a read.
 */
export interface QualityMeasurement {
  metric: string;
  definitionVersion: number;
  subject: QualitySubject;
  status: MeasurementStatus;
  value?: number | null;
  uncertainty?: number | null;
  sampleCount?: number | null;
  reason?: string;
  checkOutcome?: GateStatus | null;
  checkLimit?: QualityLimit | null;
  actionTaken?: string | null;
  origin: MeasurementOrigin;
  pipelineVersion?: string | null;
  runId: string;
  jobId?: string | null;
  measuredAt: string;
  rowId?: number | null;
  unit?: string | null;
}

/**
 * The file behind one stored quality row.
 *
 * Parameters
 * ----------
 * row_id : `int`
 * The store's row id, as the caller gave it.
 * level : `str`
 * ``"frame"``, ``"stack"`` or ``"run"``.
 * target_id : `str` or `None`
 * The target the row belongs to.
 * subject_id : `str`
 * The row's stable subject id.
 * path : `str` or `None`
 * The picture's file path, in the form the app's image reads accept.
 * `None` when the row has no picture or it could not be found.
 * reason : `str`
 * Why `path` is `None`. Empty when a path is given.
 */
export interface QualityRowFile {
  rowId: number;
  level: string;
  targetId?: string | null;
  subjectId: string;
  path?: string | null;
  reason?: string;
}

/**
 * The answer to "which pictures do these rows describe?".
 *
 * Parameters
 * ----------
 * rows : `list` [`QualityRowFile`]
 * One entry for each row id that was found, in the order asked.
 * not_found_row_ids : `list` [`int`]
 * The ids that no stored row has, in the order asked.
 */
export interface QualityRowResolution {
  rows?: QualityRowFile[];
  notFoundRowIds?: number[];
}

/**
 * The declaration of one registered metric.
 *
 * Parameters
 * ----------
 * name : `str`
 * The metric name, such as ``"frame.star_width_moffat_arcsec"``.
 * definition_version : `int`
 * Raised whenever the estimator, sample or constants change.
 * unit : `str`
 * The unit every measurement of this metric must use.
 * checkpoint : `Checkpoint`
 * Where in processing the metric is measured.
 * levels : `tuple` of `SubjectLevel`
 * The subject levels the metric applies to. Stored sorted by value
 * with no repeats, so the JSON form is always the same.
 * better : `Direction`
 * Which direction is better.
 * meaning : `str`
 * One line in plain words.
 * decisions : `tuple` of `str`
 * The decisions the metric informs. Documentation only.
 * estimator : `str`
 * How the value is estimated.
 * sample_definition : `str`
 * What is sampled, such as which stars.
 * band : `str`
 * The filter or color channel the metric applies to.
 * computed_by : `str`
 * Dotted path of the function that computes it, relative to
 * ``astrometricslib``.
 * uncertainty_kind : `UncertaintyKind`
 * The kind of uncertainty reported.
 * uncertainty_method : `str`
 * How the uncertainty is found.
 * register_entry : `str` or `None`
 * The validation register entry an auditor checks.
 * purpose : `MetricPurpose`
 * Why the metric exists.
 * purpose_reason : `str`
 * Required for a decision or context metric.
 * exploratory_question : `str`
 * The question an exploratory metric answers. Required for exploratory.
 * review_date : `datetime.date` or `None`
 * When an exploratory metric must be promoted or removed. Required for
 * exploratory.
 */
export interface MetricSpec {
  name: string;
  definitionVersion: number;
  unit: string;
  checkpoint: Checkpoint;
  levels: SubjectLevel[];
  better: Direction;
  meaning: string;
  decisions?: string[];
  estimator: string;
  sampleDefinition: string;
  band: string;
  computedBy: string;
  uncertaintyKind: UncertaintyKind;
  uncertaintyMethod: string;
  registerEntry?: string | null;
  purpose: MetricPurpose;
  purposeReason?: string;
  exploratoryQuestion?: string;
  reviewDate?: string | null;
}

/**
 * The short form of a `MetricSpec`, for listings.
 *
 * A full `MetricSpec` is long. About 170 metrics are registered, and even a
 * one-line meaning for each makes a listing of all of them too long for an
 * MCP reply (the limit is 40,000 bytes). This record keeps only what a
 * reader needs to find a metric. Ask for the meaning and the rest with
 * ``detail="full"``, usually together with a ``checkpoint``.
 *
 * Parameters
 * ----------
 * name : `str`
 * The metric name.
 * unit : `str`
 * The unit every measurement of this metric uses.
 * checkpoint : `Checkpoint`
 * Where in processing the metric is measured.
 * levels : `tuple` of `SubjectLevel`
 * The subject levels the metric applies to, sorted by value.
 * purpose : `MetricPurpose`
 * Why the metric exists.
 */
export interface MetricSummary {
  name: string;
  unit: string;
  checkpoint: Checkpoint;
  levels: SubjectLevel[];
  purpose: MetricPurpose;
}

/**
 * Gain and read noise measured at one camera setting.
 *
 * Gain and read noise change with the camera's gain setting, offset and
 * binning, so a measurement belongs to exactly one setting. A measurement
 * that could not be made has the status ``not_measured`` and a reason, and
 * holds no numbers.
 *
 * Attributes
 * ----------
 * status : `MeasurementStatus`
 * ``measured`` or ``not_measured``.
 * reason : `str` or `None`
 * Why nothing was measured. `None` when ``status`` is ``measured``.
 * camera_id : `str`
 * The camera the frames came from.
 * gain_setting : `str`
 * The camera's gain setting (or the ISO of a DSLR), in standard form.
 * offset : `str`
 * The camera offset, in standard form.
 * binning : `str`
 * The binning, such as ``"1x1"``.
 * temperature_c : `float` or `None`
 * The mean sensor temperature of the frames, in degrees C.
 * exposure_seconds : `float` or `None`
 * The exposure of the flat frames, in seconds.
 * gain_e_per_adu : `float` or `None`
 * The conversion gain: the median over the flat pairs.
 * gain_spread_e_per_adu : `float` or `None`
 * The sample standard deviation of the gain over the flat pairs. It
 * says how steady the pairs agree, not how accurate the gain is.
 * read_noise_adu : `float` or `None`
 * The read noise in ADU (RMS, root mean square): the median over the
 * bias pairs. This is the figure the noise model uses.
 * read_noise_adu_robust : `float` or `None`
 * The same noise from the median absolute deviation, which ignores hot
 * pixels and cosmic-ray hits. Kept for the record only.
 * read_noise_e : `float` or `None`
 * The read noise in electrons: ``read_noise_adu`` times the gain.
 * signal_level_adu : `float` or `None`
 * The mean flat signal, bias removed, in ADU. A gain from one signal
 * level cannot show whether the sensor is linear (a full photon
 * transfer curve uses many levels), so the level is recorded.
 * region_shape_pixels : `list` [`int`] or `None`
 * The height and width, in pixels, of the central region measured
 * (two numbers).
 * flat_pair_count : `int`
 * Flat pairs that gave a gain.
 * bias_pair_count : `int`
 * Bias pairs that gave a read noise.
 * pair_gains_e_per_adu : `list` [`float`]
 * The gain of each flat pair.
 * pair_read_noises_adu : `list` [`float`]
 * The read noise of each bias pair, in ADU.
 * flat_frame_count : `int`
 * Flat frames read.
 * bias_frame_count : `int`
 * Bias frames read.
 * provenance : `str` or `None`
 * A sentence for the camera profile: the method, frames, region and
 * date. `None` when nothing was measured.
 * saved : `bool`
 * `True` when the values were written to the camera profile.
 * backup_path : `str` or `None`
 * The config backup made before the write, if one was made.
 * notes : `list` [`str`]
 * Plain sentences about choices the measurement made, such as other
 * flat groups that were not used.
 */
export interface SensorNoiseMeasurement {
  status: MeasurementStatus;
  reason?: string | null;
  cameraId: string;
  gainSetting: string;
  offset: string;
  binning: string;
  temperatureC?: number | null;
  exposureSeconds?: number | null;
  gainEPerAdu?: number | null;
  gainSpreadEPerAdu?: number | null;
  readNoiseAdu?: number | null;
  readNoiseAduRobust?: number | null;
  readNoiseE?: number | null;
  signalLevelAdu?: number | null;
  regionShapePixels?: number[] | null;
  flatPairCount?: number;
  biasPairCount?: number;
  pairGainsEPerAdu?: number[];
  pairReadNoisesAdu?: number[];
  flatFrameCount?: number;
  biasFrameCount?: number;
  provenance?: string | null;
  saved?: boolean;
  backupPath?: string | null;
  notes?: string[];
}

/**
 * One observing night's full record: plan, placement, and outcome.
 */
export interface ObservationSession {
  id: string;
  nightDate: string;
  status?: SessionStatus;
  statusDetail?: string;
  siteProfileId: string;
  telescopeId: string;
  cameraId: string;
  queue?: QueuedObservationPackage[];
  unplacedPackageDiagnostics?: InfeasibilityReason[];
  divergenceRecords?: DivergenceRecord[];
  faultRecords?: FaultRecord[];
  meridianFlips?: MeridianFlipOutcome[];
  targetSessionIds?: string[];
  guidingSamples?: GuidingSample[];
  weatherSamples?: WeatherSample[];
  createdAt?: string;
  closedAt?: string | null;
  checklist?: ChecklistItem[];
  framing?: Framing | null;
  /** Counts the plan writes. Starts at 0. */
  revision?: number;
}

/**
 * A stored session plus its progress, as `get_plan` returns it.
 *
 * The progress is worked out on each read and is not part of the stored
 * session. Do not write this object back.
 */
export interface ObservationSessionWithProgress {
  id: string;
  nightDate: string;
  status?: SessionStatus;
  statusDetail?: string;
  siteProfileId: string;
  telescopeId: string;
  cameraId: string;
  queue?: QueuedObservationPackage[];
  unplacedPackageDiagnostics?: InfeasibilityReason[];
  divergenceRecords?: DivergenceRecord[];
  faultRecords?: FaultRecord[];
  meridianFlips?: MeridianFlipOutcome[];
  targetSessionIds?: string[];
  guidingSamples?: GuidingSample[];
  weatherSamples?: WeatherSample[];
  createdAt?: string;
  closedAt?: string | null;
  checklist?: ChecklistItem[];
  framing?: Framing | null;
  /** Counts the plan writes. Starts at 0. */
  revision?: number;
  progress?: SessionProgress | null;
}

/**
 * A reusable imaging request for one target.
 *
 * `target_id` references an existing `astrometricslib.models.target.Target`
 * by identifier only, never embedded -- resolved live at placement
 * time, never cached (`Wayfinding_Library_Architecture.md` Section 3.3,
 * "Live target resolution").
 */
export interface ObservationPackage {
  id: string;
  name: string;
  target_id: string;
  exposure_requests?: ExposureRequest[];
  dither_config?: DitherConfig | null;
  minimum_altitude_deg?: number | null;
  priority?: number;
  quality_weighting_enabled?: boolean;
  notes?: string;
  created_at?: string;
  /** Planned, suggested by the library, or complete. */
  status?: PackageStatus;
  /** Counts the package writes. Starts at 0. A stale edit is refused. */
  revision?: number;
  /** Why the library suggested this package. None for one made by hand. */
  suggestion?: PackageSuggestion | null;
  /** Where the camera points for this package, with any mosaic panels. */
  framing?: Framing | null;
  /** When the library last found every light row captured. None: not complete. */
  completed_at?: string | null;
}

/**
 * What one suggestion refresh changed, as lists of package ids.
 *
 * Parameters
 * ----------
 * added : `list` [`str`]
 * Suggested packages created.
 * updated : `list` [`str`]
 * Suggested packages whose rows or reason changed.
 * deleted : `list` [`str`]
 * Suggested packages removed (goal met, the rule stopped asking, or
 * the night ended).
 * completed : `list` [`str`]
 * Planned packages found complete.
 * reverted : `list` [`str`]
 * Complete packages set back to planned because frames were set aside.
 * checklist_item_ids : `list` [`str`]
 * Checklist items added to tonight's session.
 * notes : `list` [`str`]
 * Plain sentences about anything skipped, such as a target whose report
 * could not be built. Missing data never deletes a suggestion.
 * as_of : `datetime`
 * When the refresh ran (UTC).
 */
export interface PackageChange {
  added?: string[];
  updated?: string[];
  deleted?: string[];
  completed?: string[];
  reverted?: string[];
  checklistItemIds?: string[];
  notes?: string[];
  asOf?: string;
}

/**
 * One line of the suggestion lifecycle log.
 *
 * Parameters
 * ----------
 * id : `str`
 * A unique id for the line.
 * time : `datetime`
 * When it happened (UTC).
 * event : `SuggestionEvent`
 * What happened.
 * package_id : `str`
 * The package it happened to.
 * target_id : `str`
 * The package's target.
 * rule_name : `str`
 * The rule behind the suggestion, or ``"ranking"``.
 * source_key : `str`
 * What the suggestion came from. Empty for the whole package.
 * cause : `str`
 * Why it happened, such as ``"goal met"`` or ``"night ended"``.
 * evidence_row_ids : `list` [`int`]
 * The stored measurement rows behind the suggestion.
 * values : `dict` [`str`, `float` or `str` or `None`]
 * The numbers that mattered, such as the noise, the goal and the
 * proposed frame count.
 */
export interface SuggestionLogEntry {
  id?: string;
  time?: string;
  event: SuggestionEvent;
  package_id: string;
  target_id?: string;
  rule_name?: string;
  source_key?: string;
  cause?: string;
  evidence_row_ids?: number[];
  values?: Record<string, number | string | null>;
}

/**
 * A physical imaging rig's optics and safe pointing envelope.
 *
 * More than one may be configured in an `EquipmentCatalog`; exactly
 * one is active. `flip_hour_angle_deg` is the single stored
 * meridian-flip threshold; `meridian_flip_delay_min` is derived from
 * it (`Wayfinding_Library_Architecture.md` Section 5.5, Equation (3)).
 */
export interface Telescope {
  id: string;
  name: string;
  /** Effective focal length in millimetres. */
  focal_length_mm: number;
  /** Focal ratio (f/number). */
  focal_ratio?: number;
  altitude_limits_enabled?: boolean;
  min_altitude_deg?: number;
  max_altitude_deg?: number;
  hour_angle_limits_enabled?: boolean;
  max_hour_angle_hours?: number;
  /** Hour angle past transit at which a meridian flip is triggered, in degrees. */
  flip_hour_angle_deg?: number;
  /** Hardware-control protocol driving this telescope's mount. */
  mount_protocol?: string;
  /** Hardware-control protocol driving this telescope's focuser. Lives here rather than on a standalone catalog entry because the focuser has no `EquipmentCatalog` entry of its own -- it is discovered heuristically off the same connection as the mount. */
  focuser_protocol?: string;
  /** Hardware-control protocol driving this telescope's filter wheel. Lives here rather than on a standalone catalog entry for the same reason as `focuser_protocol`. */
  filter_wheel_protocol?: string;
  /** Which guider guides this telescope: 'phd2' (PHD2), 'internal' (KStars/Ekos sends the mount its own guide pulses) or 'simulator' (a stand-in guide loop). */
  guiding_protocol?: string;
}

/**
 * What a plan write returns: the new revision and what changed.
 *
 * The backend sends the plan-changed event from this record.
 */
export interface PlanChange {
  sessionId: string;
  previousRevision: number;
  /** The revision now stored. */
  revision: number;
  changes?: PlanChangeItem[];
  /** Notes on accepted capture settings, such as 'setting not measured'. */
  settingNotes?: string[];
  /** The session as now stored. */
  session: ObservationSession;
}

/**
 * One thing a plan write changed.
 */
export interface PlanChangeItem {
  /** What changed, such as queue_added or checklist_ticked. */
  kind: string;
  detail?: string;
  entryId?: string | null;
}

/**
 * One fix the user means to try, kept with the night plan.
 *
 * The rule name and capture setting say what the fix is about. Ticking
 * the item records when the user did it. Whether a ticked item comes back
 * is never stored: the library works that out from the measurements taken
 * since the tick.
 */
export interface ChecklistItem {
  itemId?: string;
  /** The name of the quality rule that raised the item. */
  rule: string;
  /** The capture setting the item is about. */
  captureSetting?: CaptureSetting | null;
  createdAt?: string;
  /** When the user last ticked the item. None: not ticked. */
  tickedAt?: string | null;
  /** Every time the item was ticked, oldest first. The last one equals `ticked_at`. */
  tickHistory?: string[];
  note?: string;
  /** The item in plain words, for display. Filled from the rule's plain wording, and from its purpose for an item stored without one. */
  label?: string;
}

/**
 * Where the camera points and how it is turned, with any mosaic panels.
 *
 * The rotation is in degrees east of north, the way Ekos and KStars give
 * it. The first version shows the numbers only.
 */
export interface Framing {
  centerRaDeg: number;
  centerDecDeg: number;
  rotationDeg?: number;
  panels?: MosaicPanel[];
}

/**
 * One requested exposure -- science or calibration -- within a package.
 */
export interface ExposureRequest {
  frameType: FrameType;
  filter?: FilterType;
  exposureSec: number;
  count: number;
  delaySec?: number;
  /** The camera's gain setting (a unitless index, not electrons per ADU). None: not set. */
  gainSetting?: number | null;
  /** The constant the camera adds to every pixel. None: not set. */
  offset?: number | null;
  /** The binning factor (1 means no binning). None: not set. */
  binning?: number | null;
  /** For a row the library suggested: what it came from (rule, target, capture setting). */
  sourceKey?: string;
  /** For a row the library suggested: why, in plain words. */
  reason?: string;
  /** For a suggested row: a short code for its evidence. A dismissal lasts until it changes. */
  evidenceFingerprint?: string;
  /** The row's last update or promotion time. Only frames taken at or after it count toward the row. None: the package's creation time. */
  countedFrom?: string | null;
  /** Matching frames already counted when the package was reopened. They do not count again. */
  baselineCaptured?: number;
}

/**
 * The night plan as rows for the Ekos Capture module.
 */
export interface SequenceSummary {
  sessionId: string;
  /** The plan revision the rows were built from. */
  revision: number;
  rows?: SequenceRow[];
  ditherRows?: SequenceDitherRow[];
}

/**
 * One statement about the data of a target.
 *
 * Parameters
 * ----------
 * rule : `str`
 * The name of the rule that produced it.
 * kind : `StatementKind`
 * Warning, recommendation, constraint or insufficient data.
 * severity : `Severity` or `None`
 * How much attention it needs. Required for a warning.
 * action_class : `ActionClass` or `None`
 * The kind of change it asks for. Required for a warning or a
 * recommendation, and ``None`` otherwise.
 * capture_setting : `CaptureSetting` or `None`
 * The capture setting it is about, when it is about one.
 * message : `str`
 * The technical sentence, with the numbers.
 * plain_message : `str`
 * The same finding in everyday words, one or two short sentences. Empty
 * in a statement stored before this field existed; a reader then falls
 * back to ``message``.
 * reason : `str`
 * Why. For ``insufficient_data``, what is missing and how to get it.
 * proposed_action : `ProposedAction` or `None`
 * The values proposed. ``None`` for a constraint or missing data.
 * expected_effect : `ExpectedEffect` or `None`
 * What the action is expected to do.
 * evidence : `list` [`MeasurementReference`]
 * The exact measurements used.
 * evidence_omitted_count : `int`
 * How many references were left out of ``evidence`` to keep a long
 * reply short. Zero when none were left out.
 * limits : `list` [`QualityLimit`]
 * The limits they were compared with.
 * values : `dict` [`str`, `float` or `str` or `None`]
 * Numbers the statement rests on that are not rows of the measurement
 * store, such as guide-log values and counts. They stay readable by
 * machines so a reader need not parse the message. The keys are plain
 * names. Empty when every number is cited as evidence.
 * confidence : `Confidence`
 * How well the rule is backed.
 * checklist_return : `ChecklistReturn` or `None`
 * Set when a ticked checklist item came back.
 */
export interface TargetStatement {
  rule: string;
  kind: StatementKind;
  severity?: Severity | null;
  actionClass?: ActionClass | null;
  captureSetting?: CaptureSetting | null;
  message: string;
  plainMessage?: string;
  reason?: string;
  proposedAction?: ProposedAction | null;
  expectedEffect?: ExpectedEffect | null;
  evidence?: MeasurementReference[];
  evidenceOmittedCount?: number;
  limits?: QualityLimit[];
  values?: Record<string, number | string | null>;
  confidence: Confidence;
  checklistReturn?: ChecklistReturn | null;
}

/**
 * The count and worst severity of the warnings of a target or night.
 *
 * Parameters
 * ----------
 * warning_count : `int`
 * How many warnings there are.
 * worst_severity : `Severity` or `None`
 * The most severe of them. ``None`` exactly when there are none.
 */
export interface NeedsAttention {
  warningCount: number;
  worstSeverity?: Severity | null;
}

/**
 * What the library says about one target's data.
 *
 * Parameters
 * ----------
 * target_id : `str`
 * The target.
 * capture_settings : `list` [`CaptureSettingSummary`]
 * One summary for each capture setting.
 * statements : `list` [`TargetStatement`]
 * The statements, already in priority order.
 * needs_attention : `NeedsAttention`
 * The count and worst severity of the warnings.
 * starting_point : `StartingPoint` or `None`
 * A first setting, for a target with no frames.
 * notes : `list` [`str`]
 * Remarks on how the report was made: which efficiency reference each
 * setting uses (and the other one), where the observing site came from,
 * and whether the set-aside frames are recorded.
 * forecast_checks : `list` [`ForecastCheck`]
 * How earlier forecasts compare with what was measured.
 * science_outcomes : `ScienceOutcomeSummary`
 * Science results already made from the target's data.
 * context : `list` [`ContextReading`]
 * The newest value of each context metric the target has, for each
 * capture setting. Empty when the report would not fit the reply
 * budget; see ``context_omitted_reason``.
 * context_omitted_reason : `str` or `None`
 * Why ``context`` was left empty. ``None`` when it was not.
 * generated_at : `datetime`
 * When the report was computed.
 */
export interface TargetDatasetReport {
  targetId: string;
  captureSettings?: CaptureSettingSummary[];
  statements?: TargetStatement[];
  needsAttention: NeedsAttention;
  startingPoint?: StartingPoint | null;
  notes?: string[];
  forecastChecks?: ForecastCheck[];
  scienceOutcomes?: ScienceOutcomeSummary;
  context?: ContextReading[];
  contextOmittedReason?: string | null;
  generatedAt?: string;
}

/**
 * What to image at a chosen time, ranked by the gain from one clock hour.
 *
 * Parameters
 * ----------
 * at : `datetime`
 * The planning time the ranking is for.
 * generated_at : `datetime`
 * When the ranking was computed.
 * lists : `list` [`RankedList`]
 * One list for each use that has candidates.
 * sky : `TonightSkyModel`
 * The sky model the gains assume.
 * notes : `list` [`str`]
 * Remarks for the whole ranking, such as a thin dataset leading a list,
 * targets that were not visible, or kinds of target that are never
 * ranked.
 */
export interface TonightRanking {
  at: string;
  generatedAt?: string;
  lists?: RankedList[];
  sky: TonightSkyModel;
  notes?: string[];
}

/**
 * The quality of one observing night.
 *
 * Parameters
 * ----------
 * night_id : `str`
 * The night, named for the local date on which it began.
 * targets : `list` [`NightTargetQuality`]
 * Each target imaged that night, those that need attention first.
 * needs_attention : `NeedsAttention`
 * The count and worst severity of the warnings of every target.
 * guiding : `GuidingNightSummary`
 * The guiding summary.
 * night_statements : `list` [`TargetStatement`]
 * Statements about the whole night that belong to no one target, such
 * as the focus drift (one focuser serves every target), in priority
 * order. They count in `needs_attention`.
 * focus_drift : `NightFocusDrift` or `None`
 * How star width changed with focuser temperature over the night's
 * luminance frames, with the refocus interval. ``None`` when the night
 * has no frames to fit; `notes` says why.
 * polar_alignment : `NightPolarAlignment` or `None`
 * The polar-alignment error that the field rotation shows. Always
 * labeled exploratory: no rule uses it. ``None`` when there is no
 * rotation data.
 * notes : `list` [`str`]
 * Remarks for the whole night, such as set-aside frames that are not
 * recorded per frame.
 * generated_at : `datetime`
 * When the review was worked out.
 */
export interface NightQuality {
  nightId: string;
  targets?: NightTargetQuality[];
  needsAttention: NeedsAttention;
  guiding: GuidingNightSummary;
  nightStatements?: TargetStatement[];
  focusDrift?: NightFocusDrift | null;
  polarAlignment?: NightPolarAlignment | null;
  notes?: string[];
  generatedAt?: string;
}

/**
 * One recorded observing night and what data it has.
 *
 * Attributes
 * ----------
 * night_id : `str`
 * The night, named for the local date on which it began, such as
 * ``"2026-09-24"``. Pass it as ``session_id`` to the other history
 * questions.
 * night_date : `datetime.date`
 * The same date as a date.
 * targets_imaged : `list` [`str`]
 * Ids of the library targets with light frames from the active
 * equipment on this night, sorted. Empty when there are no frames.
 * frame_count : `int`
 * Light frames from the active equipment on this night.
 * has_frame_data : `bool`
 * Whether the night has light frames from the active equipment.
 * has_guiding_data : `bool`
 * Whether at least one guiding run is recorded for the night.
 * has_sky_data : `bool`
 * Whether at least one of the night's frames has a stored sky
 * background measurement. Sky brightness and transparency checks
 * need it.
 * has_ekos_record : `bool`
 * Whether an Ekos session record is stored for the night.
 */
export interface NightListEntry {
  nightId: string;
  nightDate: string;
  targetsImaged?: string[];
  frameCount?: number;
  hasFrameData?: boolean;
  hasGuidingData?: boolean;
  hasSkyData?: boolean;
  hasEkosRecord?: boolean;
}

/**
 * A slew that ended in tracking at a new place in the sky.
 *
 * Attributes
 * ----------
 * time : `float`
 * When the mount began tracking after the slew.
 * night_id : `str`
 * The observing night of the Ekos session it came from.
 * ra_deg, dec_deg : `float` or `None`
 * Where the mount pointed, in degrees. `None` when the position could
 * not be worked out; `note` says why.
 * target_id : `str` or `None`
 * The nearest library target inside the match radius. `None` when no
 * library target is that close.
 * separation_deg : `float` or `None`
 * How far the pointing is from `target_id`'s position, in degrees.
 * `None` when there is no matched target.
 * source : `str`
 * The id of the Ekos session record the change was read from.
 * note : `str` or `None`
 * Why a field is missing, in plain words. `None` when nothing is
 * missing.
 */
export interface TargetChange {
  time: number;
  nightId: string;
  raDeg?: number | null;
  decDeg?: number | null;
  targetId?: string | null;
  separationDeg?: number | null;
  source: string;
  note?: string | null;
}

/**
 * The state of the current observing session.
 *
 * Attributes
 * ----------
 * analyze_file, kstars_log_file : `str` or `None`
 * The files the status was read from.
 * as_of : `float`
 * Time of the latest record in the analyze file.
 * seconds_since_last_record : `float` or `None`
 * How long ago that was by the wall clock. A large value means Ekos
 * has stopped writing (or the file was not refreshed).
 * guider_state : `str` or `None`
 * The guider's latest state, such as ``"Guiding"``.
 * guider_state_since : `float` or `None`
 * When it entered that state.
 * declination_degrees : `float` or `None`
 * The mount's latest declination.
 * temperature_c : `float` or `None`
 * The latest camera sensor temperature Ekos logged, in degrees C.
 * pier_side : `str` or `None`
 * The side of the pier the mount was on at the latest position
 * record: ``"East"`` or ``"West"``.
 * captures_completed, captures_aborted : `int`
 * Exposures that finished and exposures that were cancelled, in the
 * session this analyze file records.
 * dither_settings : `DitherSettings`
 * How the session has been dithering, worked out from the dithers
 * in the KStars log. These are observed values, not the Ekos setting.
 * unavailable : `list` [`str`]
 * Things the app cannot report from these logs, so a client does not
 * have to guess.
 * details : `dict` [`str`, `Any`]
 * The Ekos record sections the caller asked for (autofocus runs,
 * aborted captures, state events and so on), empty if none were.
 * recent_guiding : `GuidingWindowSummary`
 * Guiding accuracy over the requested window.
 * exposures : `list` [`ExposureSummary`]
 * The most recent finished exposures, oldest first.
 * excursions : `list` [`GuidingExcursion`]
 * Every excursion in the file.
 * dithers : `list` [`DitherEvent`]
 * Every dither in the KStars log.
 * last_autofocus_at : `float` or `None`
 * When the latest successful autofocus ended.
 * last_autofocus_temperature_c : `float` or `None`
 * The temperature at that time.
 * flags : `list` [`str`]
 * The session-level problems, one sentence each, most recent first.
 * plain_flags : `list` [`str`]
 * The same problems in everyday words, one sentence each. Their order
 * and count can differ from ``flags``.
 * plain_verdict : `str`
 * How the session is going, in one or two everyday sentences built from
 * ``plain_flags``. It is a rough reading of Ekos' own records.
 */
export interface LiveSessionStatus {
  analyze_file: string;
  kstars_log_file?: string | null;
  as_of: number;
  seconds_since_last_record?: number | null;
  guider_state?: string | null;
  guider_state_since?: number | null;
  declination_degrees?: number | null;
  temperature_c?: number | null;
  pier_side?: string | null;
  captures_completed?: number;
  captures_aborted?: number;
  dither_settings?: DitherSettings;
  unavailable?: string[];
  details?: Record<string, any>;
  recent_guiding: GuidingWindowSummary;
  exposures?: ExposureSummary[];
  excursions?: GuidingExcursion[];
  dithers?: DitherEvent[];
  last_autofocus_at?: number | null;
  last_autofocus_temperature_c?: number | null;
  flags?: string[];
  plain_flags?: string[];
  plain_verdict?: string;
}

/**
 * The weather verdict, the enclosure and who may command the hardware.
 */
export interface SafetyStatus {
  /** The sections that were read. */
  sections?: string[];
  /** Saved safety rules. */
  rule_set?: SafetyRuleSet | null;
  /** Verdict from a fresh weather reading. */
  assessment?: SafetyAssessment | null;
  /** The configured enclosure. */
  enclosure?: Enclosure | null;
  /** The enclosure's live motion state. */
  enclosure_state?: EnclosureState | null;
  /** Which capabilities this app may command. */
  delegation_policy?: DelegationPolicy | null;
  /** Agreement between this app's advice and what the incumbent did. */
  divergence?: Record<string, any> | null;
}

/**
 * The gain and read noise of a camera at one setting.
 */
export interface CameraNoiseSetting {
  /** The camera's name in the configuration. */
  cameraName: string;
  /** The camera's gain setting (an index, not electrons per ADU) or a DSLR's ISO. */
  gainSetting: string;
  /** The camera offset. */
  offset: string;
  /** The binning, such as "1x1". */
  binning: string;
  /** Sensor temperature of the measurement, in degrees C. */
  temperatureC?: number | null;
  /** The conversion gain in electrons per ADU, or None when only read noise is recorded. */
  gain?: CameraNoiseValue | null;
  /** The read noise in electrons (RMS), or None when only the gain is recorded. */
  readNoise?: CameraNoiseValue | null;
  /** Measured when every number recorded here is measured. Partly measured when some are. Design estimate when none is. */
  status: NoiseEvidenceStatus;
}

/**
 * The answer of `CalibrationCatalog.query`.
 */
export interface CalibrationQueryResult {
  detail: string;
  kind?: string | null;
  target_id?: string | null;
  darks?: Record<string, any>[] | null;
  biases?: Record<string, any>[] | null;
  flats?: Record<string, any>[] | null;
  groups?: Record<string, any>[] | null;
  lights?: Record<string, any>[] | null;
  camera_totals?: Record<string, any>[] | null;
  matched_row_keys?: string[] | null;
}

/**
 * One observation session's overall lifecycle state.
 *
 * `SUSPENDED` means an unsafe verdict halted advancement without ending
 * the night, since conditions may clear.
 */
export enum SessionStatus {
  PLANNED = "PLANNED",
  RUNNING = "RUNNING",
  SUSPENDED = "SUSPENDED",
  COMPLETED = "COMPLETED",
  ABORTED = "ABORTED"
}

/**
 * One placed, self-contained package instance within a session's queue.
 */
export interface QueuedObservationPackage {
  id: string;
  observationPackageId: string;
  targetId: string;
  exposureRequests?: ExposureRequest[];
  ditherConfig?: DitherConfig | null;
  minimumAltitudeDeg?: number | null;
  priority?: number;
  appliedPriorityBoost?: number;
  startTimeMode: StartTimeMode;
  requestedStartTime?: string | null;
  computedStartTime?: string | null;
  computedEndTime?: string | null;
  actualStartTime?: string | null;
  actualEndTime?: string | null;
  framesCaptured?: number;
  status?: QueueEntryStatus;
  statusDetail?: string;
}

/**
 * One specific, non-boolean reason a package could not be placed.
 */
export interface InfeasibilityReason {
  observationPackageId: string;
  reasonCode: InfeasibilityReasonCode;
  detail?: string;
}

/**
 * One comparison between a computed action and the delegated system.
 */
export interface DivergenceRecord {
  id: string;
  observationSessionId: string;
  queuedObservationPackageId?: string | null;
  capability: ObservatoryCapability;
  comparisonInputId: string;
  observedAt?: string;
  intendedValue: number;
  observedValue: number;
  /** Signed: intended minus observed, so systematic bias is distinguishable from symmetric noise. */
  divergenceMagnitude: number;
  divergenceUnit: string;
  /** The tolerance this comparison was evaluated against, so a later aggregate report can recompute e.g. 'within half tolerance' without re-deriving a value this record already used. */
  tolerance: number;
  withinTolerance: boolean;
  /** For PLATE_SOLVE_ALIGNMENT comparisons only: whether the underlying PointingCorrection converged within its iteration limit. None for every other capability, and for alignment comparisons recorded before this field existed. */
  converged?: boolean | null;
  detail?: string;
}

/**
 * One device fault, its bounded recovery attempts, and its outcome.
 */
export interface FaultRecord {
  id: string;
  deviceId: string;
  faultedState: DeviceSummaryState;
  faultDetail?: string;
  attempts?: RecoveryAttempt[];
  recovered?: boolean;
  escalatedToSafeState?: boolean;
}

/**
 * The outcome of one meridian-flip sequence for one queue entry.
 */
export interface MeridianFlipOutcome {
  id: string;
  queuedObservationPackageId: string;
  triggeredAt?: string;
  hourAngleAtTriggerDeg: number;
  flipCompleted?: boolean;
  realignAttempts?: number;
  residualPointingErrorArcsec?: number | null;
  guideReacquireAttempts?: number;
  resumed?: boolean;
  failureDetail?: string | null;
}

/**
 * The progress of a whole session.
 *
 * Parameters
 * ----------
 * session_id : `str`
 * The session.
 * entries : `list` [`EntryProgress`]
 * One per queue entry, in queue order.
 * imaging_now_entry_id : `str` or `None`
 * The entry being imaged now, or `None`.
 * run_state : `RunState`
 * Where the session stands.
 * as_of : `datetime`
 * When the counts were made (UTC).
 */
export interface SessionProgress {
  sessionId: string;
  entries?: EntryProgress[];
  imagingNowEntryId?: string | null;
  runState: RunState;
  asOf?: string;
}

/**
 * A package-level dithering cadence, applied across a package's run.
 */
export interface DitherConfig {
  enabled?: boolean;
  everyNFrames?: number;
  pixels?: number;
}

/**
 * Where an observation package stands in the package library.
 */
export enum PackageStatus {
  PLANNED = "PLANNED",
  SUGGESTED = "SUGGESTED",
  COMPLETE = "COMPLETE"
}

/**
 * Why the library suggested a package, kept with the package.
 *
 * The suggestion refresh (`ObservationPlanning.refresh_suggestions`) fills
 * these fields in. A package the user promoted keeps them, so the refresh
 * does not suggest it again.
 */
export interface PackageSuggestion {
  /** "rule": a quality rule asked for more frames. "ranking": tonight's ranking picked it. */
  sourceKind: any;
  /** The rule that made the suggestion, if any. */
  ruleName?: string;
  /** Names what the suggestion came from, so a repeat is not added twice. */
  sourceKey?: string;
  /** The reason, in plain words. */
  reason?: string;
  /** How sure the library is, for example measured or design_estimate. */
  confidence?: string;
  /** A short code for the evidence. A dismissal lasts until it changes. */
  evidenceFingerprint?: string;
  /** For a ranking suggestion: the use it ranks for (imaging, photometry or spectroscopy). */
  rankingUse?: string;
  /** For a ranking suggestion: the observing night it is for. It expires after that night. */
  observingNightId?: string | null;
  /** The evidence fingerprint when the whole package was dismissed. Empty: not dismissed. */
  dismissedFingerprint?: string;
  /** Rows the user dismissed one at a time. */
  dismissedRows?: DismissedRow[];
  createdAt?: string;
  updatedAt?: string;
}

/**
 * What happened to a suggestion or to a package the refresh looked at.
 */
export enum SuggestionEvent {
  CREATED = "created",
  UPDATED = "updated",
  DELETED = "deleted",
  DISMISSED = "dismissed",
  UNDISMISSED = "undismissed",
  COMPLETED = "completed",
  REOPENED = "reopened",
  PROMOTED = "promoted"
}

/**
 * The role of a requested exposure within an observation package.
 */
export enum FrameType {
  LIGHT = "LIGHT",
  DARK = "DARK",
  FLAT = "FLAT",
  BIAS = "BIAS"
}

/**
 * One capture job of the plan, with its fields in Ekos order.
 */
export interface SequenceRow {
  /** The queue entry the job comes from. */
  entryId: string;
  targetId: string;
  /** LIGHT, DARK, FLAT or BIAS. */
  frameType: string;
  fields?: SequenceField[];
}

/**
 * The dithering of one queue entry, as the two Ekos settings it needs.
 */
export interface SequenceDitherRow {
  entryId: string;
  sizePixels: number;
  sizeEkosScreen?: string;
  everyNFrames: number;
  frequencyEkosScreen?: string;
}

/**
 * What a statement is.
 *
 * `WARNING` is about data already taken. `RECOMMENDATION` is about the
 * next session. `CONSTRAINT` states a limit of the equipment and gives no
 * advice. `INSUFFICIENT_DATA` says what is missing and how to get it.
 * `RESULT` states what a star's science result is (for example its
 * spectral type) with the number behind it, and asks for nothing.
 */
export enum StatementKind {
  WARNING = "warning",
  RESULT = "result",
  RECOMMENDATION = "recommendation",
  CONSTRAINT = "constraint",
  INSUFFICIENT_DATA = "insufficient_data"
}

/**
 * How much attention a warning needs.
 *
 * `INFO` states a finding with nothing to do. `ADVICE` suggests a change
 * that would improve results. `WARNING` names a problem that makes the
 * data unreliable or the equipment misconfigured. The per-night analyses
 * use the same scale for their statements.
 */
export enum Severity {
  INFO = "info",
  ADVICE = "advice",
  WARNING = "warning"
}

/**
 * The kind of change a warning or recommendation asks for.
 *
 * There is no hardware class: the library never advises a purchase.
 */
export enum ActionClass {
  CAPTURE_SETTINGS = "capture_settings",
  SCHEDULING = "scheduling",
  CALIBRATION = "calibration",
  DATA_SELECTION = "data_selection",
  SESSION_PROCEDURE = "session_procedure",
  ADJUSTMENT = "adjustment"
}

/**
 * The typed values a recommendation proposes.
 *
 * At least one field must be set. Each value must be one the equipment
 * allows; the code that builds the statement checks that, not this model.
 *
 * Parameters
 * ----------
 * exposure_seconds : `float` or `None`
 * Exposure time of one frame, in seconds.
 * frame_count : `int` or `None`
 * How many frames to take.
 * gain_setting : `int` or `None`
 * The camera's gain setting.
 * offset : `int` or `None`
 * The camera's offset.
 * binning : `int` or `None`
 * The binning factor.
 * filter_name : `str` or `None`
 * The filter to use.
 * dither_every_frames : `int` or `None`
 * Dither after this many frames.
 * refocus_every_degc : `float` or `None`
 * Refocus after the focuser temperature changes by this many degrees
 * Celsius.
 * window_start : `datetime` or `None`
 * Start of a good observing window.
 * window_end : `datetime` or `None`
 * End of a good observing window.
 * calibration_frame_type : `str` or `None`
 * ``"dark"``, ``"flat"`` or ``"bias"`` for a calibration action.
 * note : `str` or `None`
 * A short plain-language detail that fits no other field.
 */
export interface ProposedAction {
  exposureSeconds?: number | null;
  frameCount?: number | null;
  gainSetting?: number | null;
  offset?: number | null;
  binning?: number | null;
  filterName?: string | null;
  ditherEveryFrames?: number | null;
  refocusEveryDegc?: number | null;
  windowStart?: string | null;
  windowEnd?: string | null;
  calibrationFrameType?: string | null;
  note?: string | null;
}

/**
 * What a proposed action is expected to do.
 *
 * Parameters
 * ----------
 * noise_change_fraction : `float` or `None`
 * Expected change in stack noise as a fraction. A fall of 29% is -0.29.
 * efficiency_change : `float` or `None`
 * Expected change in efficiency (a number from 0 to 1).
 * effective_time_added_seconds : `float` or `None`
 * Effective exposure time the action adds, in seconds.
 * wall_clock_seconds : `float` or `None`
 * Time on the clock the action takes, with downloads, dithers and
 * autofocus, in seconds.
 * description : `str`
 * The effect in one plain sentence.
 */
export interface ExpectedEffect {
  noiseChangeFraction?: number | null;
  efficiencyChange?: number | null;
  effectiveTimeAddedSeconds?: number | null;
  wallClockSeconds?: number | null;
  description: string;
}

/**
 * How well a rule is backed, in the validation register's status labels.
 *
 * A design estimate is shown to the user as tentative.
 */
export enum Confidence {
  MEASURED = "measured",
  PARTLY_MEASURED = "partly_measured",
  DESIGN_ESTIMATE = "design_estimate"
}

/**
 * Says that a ticked checklist item has come back.
 *
 * Parameters
 * ----------
 * item_id : `str`
 * The checklist item that was ticked.
 * ticked_at : `datetime`
 * When the user ticked it.
 * reason : `str`
 * Why it came back, such as "stars still 28% wider on Oct 15".
 */
export interface ChecklistReturn {
  itemId: string;
  tickedAt: string;
  reason: string;
}

/**
 * What was taken with one capture setting.
 *
 * Parameters
 * ----------
 * capture_setting : `CaptureSetting`
 * The setting.
 * frames_taken : `int`
 * How many frames were taken.
 * nights : `int`
 * On how many nights.
 * frames_set_aside : `int`
 * How many frames were set aside (for example, clouded or trailed).
 * captured_seconds : `float`
 * Exposure time captured, in seconds.
 * weight_effective_seconds : `float` or `None`
 * The weighted effective time the stacker reports, in seconds.
 * ``None`` when no stack has been made.
 * efficiency_effective_seconds : `float` or `None`
 * Efficiency times the captured time, in seconds. ``None`` when
 * efficiency cannot be found.
 * loss_account : `LossAccount` or `None`
 * Where the time went. ``None`` when it cannot be built.
 * science_goal : `ScienceGoal` or `None`
 * The goal this setting's efficiency is judged for (user decision U2,
 * 2026-10-24: the goal is chosen for each capture setting). ``None``
 * when the summary was built without one.
 */
export interface CaptureSettingSummary {
  captureSetting: CaptureSetting;
  framesTaken: number;
  nights: number;
  framesSetAside: number;
  capturedSeconds: number;
  weightEffectiveSeconds?: number | null;
  efficiencyEffectiveSeconds?: number | null;
  lossAccount?: LossAccount | null;
  scienceGoal?: ScienceGoal | null;
}

/**
 * A first capture setting for a target with no frames yet.
 *
 * It is always a starting point, never a result.
 *
 * Parameters
 * ----------
 * capture_setting : `CaptureSetting` or `None`
 * The setting the numbers are for, when it is known.
 * exposure_seconds : `float` or `None`
 * Exposure long enough that sky noise dominates read noise, in
 * seconds. ``None`` when it cannot be computed.
 * exposure_reason : `str`
 * How it was found, or why it was not computed.
 * clipping_cap_seconds : `float` or `None`
 * The longest exposure that keeps the target and bright stars from
 * clipping, in seconds. ``None`` is "not computed".
 * clipping_cap_reason : `str`
 * How it was found, or why it was not computed.
 * confidence : `Confidence`
 * How well the method is backed.
 */
export interface StartingPoint {
  captureSetting?: CaptureSetting | null;
  exposureSeconds?: number | null;
  exposureReason: string;
  clippingCapSeconds?: number | null;
  clippingCapReason: string;
  confidence: Confidence;
}

/**
 * One check of a forecast against the stack that followed it.
 *
 * Parameters
 * ----------
 * stack_run_id : `str` or `None`
 * The stack run the measured change comes from.
 * forecast_when_planned : `ForecastedChange`
 * The forecast made when the session was planned.
 * forecast_given_frames_taken : `ForecastedChange`
 * The forecast again, using the frames actually taken.
 * measured : `ForecastedChange`
 * The change that was measured.
 * weighting_mode : `str`
 * The frame weighting the stack run used, such as ``"equal"``.
 * paired : `bool`
 * Whether the tolerance is paired (the two stacks measured in the same
 * apertures) or unpaired.
 * outcome : `ForecastOutcome`
 * Whether the gain arrived as forecast.
 * reason : `str`
 * Why it fell short or could not be checked. Required unless the gain
 * arrived as forecast.
 * evidence : `list` [`MeasurementReference`]
 * The measurements the check used.
 * basis : `ForecastBasis`
 * Whether the check compared two library stacks (``later_stack``) or an
 * earlier-nights subset with the library stack
 * (``earlier_nights_subset``).
 */
export interface ForecastCheck {
  stackRunId?: string | null;
  forecastWhenPlanned: ForecastedChange;
  forecastGivenFramesTaken: ForecastedChange;
  measured: ForecastedChange;
  weightingMode: string;
  paired: boolean;
  outcome: ForecastOutcome;
  reason?: string;
  evidence?: MeasurementReference[];
  basis?: ForecastBasis;
}

/**
 * Notable science results already made from a target's data.
 *
 * Planning uses these to raise the priority of a target whose data has
 * already produced something worth following up.
 *
 * Parameters
 * ----------
 * variable_star_candidate_count : `int`
 * Variable star candidates the photometry run found. Read from the
 * stored measurement ``photometry.variable_candidate_count``.
 * asteroid_candidate_count : `int`
 * Asteroid candidates the detection run found.
 * confirmed_asteroid_candidate_count : `int`
 * Asteroid candidates that match a known orbit.
 * flagged_pipelines : `list` [`str`]
 * Names of the pipelines whose own quality summary is flagged.
 */
export interface ScienceOutcomeSummary {
  variableStarCandidateCount?: number;
  asteroidCandidateCount?: number;
  confirmedAsteroidCandidateCount?: number;
  flaggedPipelines?: string[];
}

/**
 * One context measurement of the target, shown beside the statements.
 *
 * A context metric explains other results (the sensor temperature, how a
 * stack was made, how many stars a solve matched). It drives no rule, so
 * the report shows it here instead.
 *
 * Parameters
 * ----------
 * metric : `str`
 * The registered metric name.
 * level : `str`
 * ``"frame"``, ``"stack"`` or ``"run"``.
 * value : `float` or `None`
 * The value of the newest row of this metric for the capture setting.
 * ``None`` when that row was not measured.
 * unit : `str`
 * The unit of the metric.
 * capture_setting_id : `int` or `None`
 * The capture setting the value belongs to. ``None`` when the rows
 * belong to no single setting.
 * row_count : `int`
 * How many stored rows of this metric and setting there are.
 * row_id : `int`
 * The row the value comes from, so the reading is also a reference.
 */
export interface ContextReading {
  metric: string;
  level: string;
  value?: number | null;
  unit?: string;
  captureSettingId?: number | null;
  rowCount?: number;
  rowId?: number | null;
}

/**
 * The ranked candidates of one use.
 *
 * Parameters
 * ----------
 * use : `RankedUse`
 * The use.
 * gain_description : `str`
 * What the gain means for this list, in one sentence.
 * candidates : `list` [`RankedCandidate`]
 * Ranked targets first (best first), then stopped ones at zero, then new
 * targets and those with no forecast, which are not ranked.
 */
export interface RankedList {
  use: RankedUse;
  gainDescription: string;
  candidates?: RankedCandidate[];
}

/**
 * The sky the ranking assumed, labeled as a model.
 *
 * Parameters
 * ----------
 * at : `datetime`
 * The time the sky is for.
 * moon_altitude_deg : `float` or `None`
 * The Moon's altitude at the site, in degrees (negative is below the
 * horizon). ``None`` when it could not be worked out.
 * moon_illumination_fraction : `float` or `None`
 * The lit fraction of the Moon's disc, from 0 to 1.
 * settings : `list` [`SkySettingSummary`]
 * The models that were fitted, one for each equipment setting.
 * unavailable : `list` [`str`]
 * One sentence for each equipment setting that has no model, with the
 * reason. A target of such a setting is ranked from its own frames' sky
 * and says so.
 * model_derived : `bool`
 * Always true: the values come from a model, not a measurement.
 * extinction_note : `str`
 * Says that atmospheric extinction is not modeled separately.
 * confidence : `Confidence`
 * Always ``design_estimate`` (shown as tentative).
 * register_entry : `str`
 * The validation register entry behind the model.
 */
export interface TonightSkyModel {
  at: string;
  moonAltitudeDeg?: number | null;
  moonIlluminationFraction?: number | null;
  settings?: SkySettingSummary[];
  unavailable?: string[];
  modelDerived?: boolean;
  extinctionNote?: string;
  confidence?: Confidence;
  registerEntry?: string;
}

/**
 * What one target's frames of a night were worth.
 *
 * Parameters
 * ----------
 * target_id : `str`
 * The target.
 * goal : `ScienceGoal`
 * The goal that holds most of the target's captured time. The goal is
 * chosen for each capture setting (decision U2), so when the settings
 * differ, each ``capture_settings`` entry states its own goal.
 * frames_taken : `int`
 * Frames of the night, kept and set aside.
 * frames_set_aside : `int`
 * Frames the stacker set aside.
 * captured_seconds : `float`
 * Exposure time of all frames taken, in seconds.
 * efficiency_effective_seconds : `float` or `None`
 * The effective time of the kept frames, in seconds. ``None`` when no
 * frame could be assessed.
 * capture_settings : `list` [`CaptureSettingSummary`]
 * One summary for each capture setting used that night.
 * loss_account : `LossAccount` or `None`
 * Where the night's time went. ``None`` when no reference was
 * available; `notes` says why.
 * statements : `list` [`TargetStatement`]
 * Warnings and notes from the night's measurements, in priority order.
 * needs_attention : `NeedsAttention`
 * The count and worst severity of the warnings.
 * notes : `list` [`str`]
 * Remarks, such as a setting with no reference or frames that could not
 * be assessed.
 */
export interface NightTargetQuality {
  targetId: string;
  goal: ScienceGoal;
  framesTaken: number;
  framesSetAside: number;
  capturedSeconds: number;
  efficiencyEffectiveSeconds?: number | null;
  captureSettings?: CaptureSettingSummary[];
  lossAccount?: LossAccount | null;
  statements?: TargetStatement[];
  needsAttention: NeedsAttention;
  notes?: string[];
}

/**
 * How the night's guiding went, in a few numbers.
 *
 * Parameters
 * ----------
 * available : `bool`
 * Whether the night has guiding data to summarize.
 * reason : `str`
 * Why it is not available. Empty when it is.
 * flagged : `bool` or `None`
 * True when the guiding analysis raised a warning.
 * flag_reasons : `list` [`str`]
 * The kinds of those warnings.
 * rms_per_axis_arcsec : `float` or `None`
 * The robust guiding error per axis, in arcseconds.
 * expected_star_widening_fraction : `float` or `None`
 * How much that error widens a star, as a fraction of its width.
 * lost_fraction : `float` or `None`
 * The share of guide frames lost.
 * excursion_count : `int` or `None`
 * Samples with an error far beyond the limit.
 * runs : `list` [`GuidingRunPerformance`]
 * The guiding runs of the night, with the error of each, so the stretches
 * where guiding failed can be seen.
 */
export interface GuidingNightSummary {
  available: boolean;
  reason?: string;
  flagged?: boolean | null;
  flagReasons?: string[];
  rmsPerAxisArcsec?: number | null;
  expectedStarWideningFraction?: number | null;
  lostFraction?: number | null;
  excursionCount?: number | null;
  runs?: GuidingRunPerformance[];
}

/**
 * How star width followed the focuser temperature over a night.
 *
 * This is the plain form of the focus drift fit (`fit_focus_drift`), kept
 * here so the model layer imports no analytics code.
 *
 * Parameters
 * ----------
 * status : `str`
 * ``"drift_found"``, ``"no_drift_detected"`` or ``"insufficient_data"``.
 * reason : `str`
 * Why, in one sentence.
 * slope_px_per_degc, slope_error_px_per_degc : `float` or `None`
 * Pixels of width added per degree Celsius once the star is well out of
 * focus, and its error (see `FocusDriftResult` for how the fit finds it).
 * best_focus_width_px : `float` or `None`
 * The star width at best focus, in pixels.
 * allowed_width_increase_fraction : `float`
 * How much widening the rule accepts before a refocus.
 * refocus_interval_degc, interval_tolerance_degc : `float` or `None`
 * Refocus after this much temperature change, and its tolerance.
 * interval_resolvable : `bool` or `None`
 * Whether one frame's width error is small enough to see the limit.
 * frames_used, segments_used : `int`
 * Frames and autofocus segments in the fit.
 * temperature_change_degc : `float` or `None`
 * The temperature range the fit covers.
 * evidence : `list` [`MeasurementReference`]
 * The rows the fit used.
 */
export interface NightFocusDrift {
  status: string;
  reason: string;
  slopePxPerDegc?: number | null;
  slopeErrorPxPerDegc?: number | null;
  bestFocusWidthPx?: number | null;
  allowedWidthIncreaseFraction?: number | null;
  refocusIntervalDegc?: number | null;
  intervalToleranceDegc?: number | null;
  intervalResolvable?: boolean | null;
  framesUsed?: number;
  segmentsUsed?: number;
  temperatureChangeDegc?: number | null;
  evidence?: MeasurementReference[];
}

/**
 * The polar-alignment error that the field rotation of a night shows.
 *
 * This is the plain form of `PolarAlignmentEstimate`. It is exploratory:
 * its input metric is exploratory and no rule uses it.
 *
 * Parameters
 * ----------
 * exploratory : `bool`
 * Always true.
 * kind : `str`
 * ``"fit"``, ``"lower_bound"`` or ``"insufficient_data"``.
 * total_error_arcmin : `float` or `None`
 * The angle between the mount's polar axis and the pole, in arcminutes
 * (the least it can be, for a lower bound).
 * altitude_error_arcmin, azimuth_error_arcmin : `float` or `None`
 * The two parts, only for a fit.
 * uncertainty_arcmin : `float` or `None`
 * One standard error of the total, only for a fit.
 * hour_angle_span_deg : `float` or `None`
 * The widest hour-angle span of any guiding run.
 * sample_count : `int`
 * Frames used.
 * reason : `str`
 * Why the kind is what it is.
 */
export interface NightPolarAlignment {
  exploratory?: boolean;
  kind: string;
  totalErrorArcmin?: number | null;
  altitudeErrorArcmin?: number | null;
  azimuthErrorArcmin?: number | null;
  uncertaintyArcmin?: number | null;
  hourAngleSpanDeg?: number | null;
  sampleCount?: number;
  reason: string;
}

/**
 * How a session has been dithering, as seen in the KStars log.
 *
 * Attributes
 * ----------
 * dither_count, failed_count : `int`
 * Dithers started, and how many gave up.
 * last_amplitude_px, median_amplitude_px : `float` or `None`
 * The latest and the typical requested move, in guide-camera pixels.
 * exposures_per_dither : `float` or `None`
 * Finished exposures divided by dithers: roughly how many frames
 * between dithers. `None` with no dithers.
 */
export interface DitherSettings {
  dither_count?: number;
  failed_count?: number;
  last_amplitude_px?: number | null;
  median_amplitude_px?: number | null;
  exposures_per_dither?: number | null;
}

/**
 * Guiding accuracy over a recent stretch of time.
 *
 * Attributes
 * ----------
 * window_seconds : `float`
 * The length of the stretch, ending at the latest record.
 * sample_count : `int`
 * Guiding measurements inside it.
 * ra_rms_arcsec, dec_rms_arcsec, total_rms_arcsec : `float` or `None`
 * Root-mean-square guide error along RA, along Dec, and combined.
 * `None` when there are no samples.
 */
export interface GuidingWindowSummary {
  window_seconds: number;
  sample_count: number;
  ra_rms_arcsec?: number | null;
  dec_rms_arcsec?: number | null;
  total_rms_arcsec?: number | null;
}

/**
 * One finished exposure and how guiding behaved during it.
 *
 * Attributes
 * ----------
 * frame_name : `str`
 * The saved file's name.
 * completed_at : `float`
 * When the exposure finished.
 * exposure_seconds : `float`
 * Its length.
 * star_count : `int`
 * Stars Ekos found in the frame.
 * half_flux_radius_px : `float` or `None`
 * Median star half-flux radius, in pixels.
 * eccentricity : `float` or `None`
 * Median star eccentricity (0 is round).
 * guide_rms_first_seconds_arcsec : `float` or `None`
 * Guide error (total, root-mean-square) in the first stretch of the
 * exposure, where a dither's after-effects show.
 * guide_rms_rest_arcsec : `float` or `None`
 * Guide error over the rest of the exposure.
 * follows_dither : `bool`
 * Whether a dither finished shortly before this exposure began.
 * flags : `list` [`str`]
 * One sentence per problem found. Empty for a clean frame.
 * plain_flags : `list` [`str`]
 * The same problems in everyday words, one sentence each, in the same
 * order as ``flags``.
 */
export interface ExposureSummary {
  frame_name: string;
  completed_at: number;
  exposure_seconds: number;
  star_count: number;
  half_flux_radius_px?: number | null;
  eccentricity?: number | null;
  guide_rms_first_seconds_arcsec?: number | null;
  guide_rms_rest_arcsec?: number | null;
  follows_dither?: boolean;
  flags?: string[];
  plain_flags?: string[];
}

/**
 * A stretch where the guide error ran far above its normal level.
 *
 * Attributes
 * ----------
 * started_at, ended_at : `float`
 * First and last measurement of the excursion.
 * peak_error_arcsec : `float`
 * The largest total guide error inside it.
 * axis : `str`
 * ``"RA"`` or ``"Dec"``, whichever carried the larger error.
 * kind : `str`
 * ``"jump"`` if the error arrived at full size in one step (the
 * field moved suddenly), ``"drift"`` if it built up over time.
 * drift_rate_arcsec_per_second : `float` or `None`
 * How fast the error grew along that axis, from a straight-line fit.
 * `None` with fewer than three measurements.
 * matches_stopped_ra_axis : `bool` or `None`
 * For an RA excursion: whether the drift rate is within 25% of the
 * rate a motionless mount drifts at the current declination
 * (15.04 arcsec per second times the cosine of the declination).
 * `None` for Dec excursions or when the declination is unknown.
 * reacquire_count : `int`
 * How many times the guider re-locked during the excursion.
 */
export interface GuidingExcursion {
  started_at: number;
  ended_at: number;
  peak_error_arcsec: number;
  axis: string;
  kind?: string;
  drift_rate_arcsec_per_second?: number | null;
  matches_stopped_ra_axis?: boolean | null;
  reacquire_count?: number;
}

/**
 * One dither, read from the KStars log.
 *
 * Attributes
 * ----------
 * timestamp : `float`
 * When the dither began.
 * amplitude_px : `float`
 * The requested move, in guide-camera pixels.
 * succeeded : `bool` or `None`
 * `True` if the guide star reached its new position, `False` if the
 * guider gave up after its allowed tries, `None` if the log ends
 * before the dither does.
 * settle_seconds : `float` or `None`
 * The wait the guider applied after the move, before the next
 * exposure could start.
 * duration_seconds : `float` or `None`
 * Time from the start of the dither to its completion message.
 */
export interface DitherEvent {
  timestamp: number;
  amplitude_px: number;
  succeeded?: boolean | null;
  settle_seconds?: number | null;
  duration_seconds?: number | null;
}

/**
 * The full configured set of environmental safety rules.
 */
export interface SafetyRuleSet {
  id: string;
  rules?: SafetyRule[];
  /** Duration conditions must remain continuously safe before the enclosure may reopen. */
  settling_period_sec?: number;
}

/**
 * The current verdict, which rule produced it, and its freshness.
 */
export interface SafetyAssessment {
  verdict: SafetyVerdict;
  triggering_rule_id?: string | null;
  assessed_at?: string;
  oldest_reading_at?: string | null;
}

/**
 * The observatory's roof or dome and its mechanical interlock envelope.
 *
 * `present=False` distinguishes an observatory with no enclosure from
 * one whose enclosure is offline -- a distinction that matters because
 * the first permits unattended operation under the remaining safety
 * actions while the second is a fault.
 */
export interface Enclosure {
  id: string;
  enclosure_type: EnclosureType;
  present?: boolean;
  park_azimuth_deg: number;
  park_altitude_deg: number;
  clearance_tolerance_deg?: number;
  motion_timeout_sec?: number;
  /** Hardware-control protocol driving this enclosure. */
  protocol?: string;
}

/**
 * A device's motion state, published uniformly for the interlock.
 *
 * `UNKNOWN` is treated as unsafe for both mount motion and enclosure
 * motion, per `Wayfinding_Library_Architecture.md` Section 6.6 (an
 * unknown enclosure state blocks mount motion; see also Section 6.5,
 * "Absence of information is not safety").
 */
export enum EnclosureState {
  OPEN = "open",
  OPENING = "opening",
  CLOSED = "closed",
  CLOSING = "closing",
  UNKNOWN = "unknown",
  FAULT = "fault"
}

/**
 * The delegation state of every capability -- the observatory phase.
 */
export interface DelegationPolicy {
  id: string;
  capability_delegations?: CapabilityDelegation[];
  updated_at?: string;
}

/**
 * One gain or read noise number with where it came from.
 */
export interface CameraNoiseValue {
  /** The number: electrons per ADU for a gain, electrons for a read noise. */
  value: number;
  /** Measured when someone measured it on frames. Design estimate when it comes from a datasheet or an assumption. A single number is never Partly measured. */
  status: NoiseEvidenceStatus;
  /** How the profile records its origin: "measured", "datasheet" or "assumed". */
  kind: string;
  /** Enough detail to find the origin again: a file, a paper or the frames. */
  source: string;
}

/**
 * How well a gain or read noise is backed, in the register's words.
 */
export enum NoiseEvidenceStatus {
  MEASURED = "measured",
  PARTLY_MEASURED = "partly_measured",
  DESIGN_ESTIMATE = "design_estimate"
}

/**
 * A package placement request's starting disposition.
 */
export enum StartTimeMode {
  SOONEST = "SOONEST",
  FIXED = "FIXED"
}

/**
 * One queue entry's execution status.
 *
 * No `INFEASIBLE` value -- unplaced packages never enter the queue.
 */
export enum QueueEntryStatus {
  PENDING = "PENDING",
  RUNNING = "RUNNING",
  COMPLETED = "COMPLETED",
  FAILED = "FAILED",
  SKIPPED = "SKIPPED"
}

/**
 * A closed, named reason an observation package could not be placed.
 *
 * A closed enumeration, not free text, so a client can branch on the reason
 * and a test can assert one specific diagnosis rather than "some
 * failure."
 */
export enum InfeasibilityReasonCode {
  NEVER_CLEARS_ALTITUDE = "NEVER_CLEARS_ALTITUDE",
  BLOCKED_BY_AVOIDANCE_ZONE = "BLOCKED_BY_AVOIDANCE_ZONE",
  WINDOW_TOO_SHORT = "WINDOW_TOO_SHORT",
  NIGHT_FULLY_COMMITTED = "NIGHT_FULLY_COMMITTED",
  FIXED_TIME_CONFLICT = "FIXED_TIME_CONFLICT"
}

/**
 * One hardware-facing capability whose delegation state is tracked.
 *
 * Corresponds one-to-one with the functional modules of the incumbent
 * software this architecture is designed to replace
 * (`Wayfinding_Library_Architecture.md` Section 2.3).
 */
export enum ObservatoryCapability {
  MOUNT_CONTROL = "MOUNT_CONTROL",
  PLATE_SOLVE_ALIGNMENT = "PLATE_SOLVE_ALIGNMENT",
  AUTOGUIDING = "AUTOGUIDING",
  AUTOFOCUS = "AUTOFOCUS",
  CAPTURE_ORCHESTRATION = "CAPTURE_ORCHESTRATION",
  OBSERVATORY_SAFETY = "OBSERVATORY_SAFETY"
}

/**
 * A device's lifecycle state, published in one uniform vocabulary.
 *
 * `FAULT` is what gives Observation Execution something to recover
 * from, and what gives an operator a place to see that a device has
 * reported a specific failure rather than merely gone quiet.
 */
export enum DeviceSummaryState {
  OFFLINE = "OFFLINE",
  STANDBY = "STANDBY",
  DISABLED = "DISABLED",
  ENABLED = "ENABLED",
  FAULT = "FAULT"
}

/**
 * One attempt to return a faulted device to standby and re-enable it.
 */
export interface RecoveryAttempt {
  attemptedAt?: string;
  outcome: DeviceSummaryState;
  detail?: string;
}

/**
 * The progress of one queue entry.
 *
 * Parameters
 * ----------
 * entry_id : `str`
 * The queue entry id.
 * target_id : `str`
 * The entry's target.
 * requested : `int`
 * Light frames asked for, over all light rows.
 * captured : `int`
 * Light frames captured, each row capped at its request.
 * flagged : `int`
 * Flagged frames among them.
 * not_judged : `int`
 * Frames not judged yet.
 * rows : `list` [`RowProgress`]
 * The light rows, in order.
 * newest_frame_time : `datetime` or `None`
 * When the newest counted frame was taken. `None` when none was.
 * state : `RunState`
 * Where the entry stands.
 * imaging_now : `bool`
 * `True` when this is the entry being imaged now.
 * note : `str`
 * A plain sentence when the frames could not be read, else empty.
 */
export interface EntryProgress {
  entryId: string;
  targetId: string;
  requested: number;
  captured: number;
  flagged: number;
  notJudged: number;
  rows?: RowProgress[];
  newestFrameTime?: string | null;
  state: RunState;
  imagingNow?: boolean;
  note?: string;
}

/**
 * How far a session or one of its entries has got, from the frames.
 *
 * The library works it out from the frames that arrived (see
 * `wayfindinglib.analytics.session_progress`). The value is typed so a
 * screen never has to decide it.
 *
 * * `UPCOMING`: the night has not begun, or nothing has been captured yet.
 * * `IMAGING`: a frame arrived a short while ago, so it is imaged now.
 * * `IN_PROGRESS`: tonight, some frames are in, and nothing arrived lately.
 * * `COMPLETE`: every light frame requested has been captured and judged.
 * * `PARTLY_DONE`: the night is over and some, but not all, frames came in.
 * * `NOT_RUN`: the night is over and no frame came in.
 * * `UNKNOWN`: the frames could not be read, so the library cannot say.
 */
export enum RunState {
  UPCOMING = "upcoming",
  IMAGING = "imaging",
  IN_PROGRESS = "in_progress",
  COMPLETE = "complete",
  PARTLY_DONE = "partly_done",
  NOT_RUN = "not_run",
  UNKNOWN = "unknown"
}

/**
 * A suggested row the user dismissed, kept so the dismissal can be undone.
 *
 * The row is taken out of the package's exposure list. The refresh does
 * not add it again while its fingerprint stays the same.
 */
export interface DismissedRow {
  /** The row's source key. */
  sourceKey: string;
  /** The row's evidence fingerprint when dismissed. */
  fingerprint: string;
  /** The row as it was when dismissed. */
  request: ExposureRequest;
}

/**
 * One value of a capture job and the Ekos screen it goes on.
 */
export interface SequenceField {
  /** The field, one of `CAPTURE_FIELD_ORDER`. */
  name: string;
  /** The value to enter. None: leave the camera's own value. */
  value?: string | number | null;
  /** Where to enter the value in Ekos. */
  ekosScreen: string;
}

/**
 * Where the time of a night or a target went.
 *
 * Two parts are kept apart and never added into one number without saying
 * so: the frames set aside, and the efficiency deficit of the frames kept.
 *
 * Parameters
 * ----------
 * goal : `ScienceGoal`
 * The science goal the account is stated for.
 * reference_name : `str`
 * The name of the reference efficiency is measured against.
 * set_aside_seconds : `float`
 * Total exposure time of the frames set aside, in seconds.
 * set_aside : `list` [`LossShare`]
 * The same time split by the reason frames were set aside.
 * kept_frame_deficit : `list` [`LossShare`]
 * Captured minus effective time of the kept frames, split by cause.
 * cause_unknown_seconds : `float` or `None`
 * The part of the deficit that no listed cause explains. ``None``
 * means "not measured": it exists only where efficiency is measured
 * independently of the causes.
 * cause_unknown_reason : `str`
 * Why the part is unknown, or why it was not measured.
 * transparency_included : `bool`
 * Whether cloud and haze are part of the account.
 * transparency_reason : `str`
 * Why transparency is or is not included.
 */
export interface LossAccount {
  goal: ScienceGoal;
  referenceName: string;
  setAsideSeconds: number;
  setAside?: LossShare[];
  keptFrameDeficit?: LossShare[];
  causeUnknownSeconds?: number | null;
  causeUnknownReason: string;
  transparencyIncluded: boolean;
  transparencyReason: string;
}

/**
 * What the data is for. Efficiency depends on the goal.
 */
export enum ScienceGoal {
  FAINT_POINT_SOURCE = "faint_point_source",
  ASTROMETRIC_PRECISION = "astrometric_precision",
  EXTENDED_OBJECT = "extended_object",
  BRIGHT_STAR_PHOTOMETRY = "bright_star_photometry",
  SLITLESS_SPECTRAL_RESOLUTION = "slitless_spectral_resolution",
  PERIOD_DETECTION = "period_detection"
}

/**
 * One predicted or measured change in stack noise, with its tolerance.
 *
 * Parameters
 * ----------
 * noise_change_fraction : `float` or `None`
 * The change in noise as a fraction. A fall of 29% is -0.29.
 * ``None`` when it could not be found.
 * tolerance : `float` or `None`
 * How far the number may be off and still agree, as a fraction. It
 * comes from the uncertainty of the aperture noise.
 */
export interface ForecastedChange {
  noiseChangeFraction?: number | null;
  tolerance?: number | null;
}

/**
 * How a forecast compares with what happened.
 */
export enum ForecastOutcome {
  AS_FORECAST = "as_forecast",
  SHORT_KNOWN_REASON = "short_known_reason",
  SHORT_UNKNOWN_REASON = "short_unknown_reason",
  NOT_CHECKABLE = "not_checkable"
}

/**
 * What the later stack of a forecast check is compared with.
 *
 * ``later_stack`` is a check of two library stacks, the later one holding
 * new frames. ``earlier_nights_subset`` is a check made from frames already
 * taken: the stack of all observing nights but the last against the library
 * stack of all nights.
 */
export enum ForecastBasis {
  LATER_STACK = "later_stack",
  EARLIER_NIGHTS_SUBSET = "earlier_nights_subset"
}

/**
 * What a target is observed for. Each use has its own ranked list.
 */
export enum RankedUse {
  IMAGING = "imaging",
  PHOTOMETRY = "photometry",
  SPECTROSCOPY = "spectroscopy"
}

/**
 * One target in a ranked list.
 *
 * Parameters
 * ----------
 * target_id : `str`
 * The target.
 * use : `RankedUse`
 * The list it belongs to.
 * status : `CandidateStatus`
 * Ranked, stopped, new or no forecast.
 * rank : `int` or `None`
 * Its place in the list, starting at 1. ``None`` for a new target or one
 * with no forecast, because those are not ranked.
 * gain_per_hour_fraction : `float` or `None`
 * How much one clock hour of exposures cuts the stack's noise, as a
 * fraction (0.18 is a fall of 18%). 0 for a stopped target. ``None``
 * when it could not be found.
 * altitude_deg : `float` or `None`
 * The target's altitude at the planning time, in degrees.
 * capture_setting : `CaptureSetting` or `None`
 * The setting the forecast assumes (the one most used so far).
 * frames_in_hour : `int` or `None`
 * How many frames of that setting fit in one clock hour.
 * exposure_seconds : `float` or `None`
 * The exposure of one of those frames, in seconds.
 * overhead_fraction : `float` or `None`
 * Downloads, dithers and autofocus over exposure time (0.2 means the
 * hour holds 20% less exposure than the clock says).
 * effective_hours_so_far : `float` or `None`
 * Exposure time of the frames already taken, in hours.
 * sky_rate_e_per_s_per_px : `float` or `None`
 * The sky rate the forecast assumed for tonight.
 * sky_source : `str`
 * ``"model"`` when the rate came from the sky model, ``"history"`` when
 * it was the target's own past frames, or ``"none"``.
 * thin_dataset : `bool`
 * True when the target has little data, so a large gain per hour is
 * partly just a short history.
 * reason : `str`
 * Why a target is stopped, new or without a forecast, or a remark on a
 * ranked one. Empty when there is nothing to add.
 * confidence : `Confidence`
 * Always ``design_estimate`` today.
 * forecast_gate : `ForecastGate`
 * Whether the target's "take N more" count is confirmed by a check of two
 * library stacks, exploratory (only a check made from frames already
 * taken agreed), or closed (no check agreed). An exploratory target
 * ranks below a confirmed or closed one of about the same gain.
 * register_entry : `str`
 * The validation register entry behind the ranking.
 * evidence : `list` [`MeasurementReference`]
 * The stored rows the forecast rests on.
 */
export interface RankedCandidate {
  targetId: string;
  use: RankedUse;
  status: CandidateStatus;
  rank?: number | null;
  gainPerHourFraction?: number | null;
  altitudeDeg?: number | null;
  captureSetting?: CaptureSetting | null;
  framesInHour?: number | null;
  exposureSeconds?: number | null;
  overheadFraction?: number | null;
  effectiveHoursSoFar?: number | null;
  skyRateEPerSPerPx?: number | null;
  skySource?: string;
  thinDataset?: boolean;
  reason?: string;
  confidence?: Confidence;
  forecastGate?: ForecastGate;
  registerEntry?: string;
  evidence?: MeasurementReference[];
}

/**
 * How well the sky model is known for one equipment setting.
 *
 * Parameters
 * ----------
 * setting_key : `str`
 * The equipment the model was fitted for (camera, telescope, filter,
 * gain setting, offset and binning).
 * nights_used : `int`
 * Nights of the user's own frames behind the fit.
 * frames_used : `int`
 * Frames behind the fit.
 * held_out_relative_error : `float`
 * The error to expect on a new night, as a fraction of the sky rate.
 * moon_fitted : `bool`
 * Whether enough moonlit nights existed to fit the Moon.
 */
export interface SkySettingSummary {
  settingKey: string;
  nightsUsed: number;
  framesUsed: number;
  heldOutRelativeError: number;
  moonFitted: boolean;
}

/**
 * Processing: the guiding error of one run.
 *
 * Attributes
 * ----------
 * run_id : `str`
 * The run's identifier.
 * started_at : `float`
 * When the run began, in seconds since the Unix epoch.
 * duration_seconds : `float` or `None`
 * Length of the run.
 * samples : `int`
 * Samples in the run that were not excursions.
 * rms_per_axis_arcsec : `float` or `None`
 * Robust guiding error per axis, in arcseconds.
 * lost_fraction : `float` or `None`
 * Share of the run's frames that were lost.
 * net_dec_correction_arcsec_per_minute : `float` or `None`
 * The run's net Dec correction rate (see `GuidingPerformance`).
 * altitude_degrees : `float` or `None`
 * Altitude when the run began.
 * azimuth_degrees : `float` or `None`
 * Azimuth when the run began.
 * declination_degrees : `float` or `None`
 * Declination when the run began.
 * pier_side : `str` or `None`
 * Side of the pier the telescope was on.
 */
export interface GuidingRunPerformance {
  runId: string;
  startedAt: number;
  durationSeconds?: number | null;
  samples?: number;
  rmsPerAxisArcsec?: number | null;
  lostFraction?: number | null;
  netDecCorrectionArcsecPerMinute?: number | null;
  altitudeDegrees?: number | null;
  azimuthDegrees?: number | null;
  declinationDegrees?: number | null;
  pierSide?: string | null;
}

/**
 * One environmental threshold the safety monitor evaluates.
 *
 * `comparison` names how `unsafe_threshold` is applied to the named
 * measurement -- e.g. ``"greater_than"``, ``"less_than"`` -- rather
 * than being hardcoded per measurement, so a new sensor is
 * configuration, not a new evaluation path
 * (`Wayfinding_Library_Architecture.md` Section 6.5).
 */
export interface SafetyRule {
  id: string;
  measurement: string;
  comparison: string;
  unsafe_threshold: number;
  marginal_threshold?: number | null;
  staleness_bound_sec?: number;
}

/**
 * The current environmental judgment.
 *
 * `UNKNOWN` is not a lesser-severity middle ground between `SAFE` and
 * `UNSAFE` -- it is treated identically to `UNSAFE` by
 * `permits_observing()`.
 */
export enum SafetyVerdict {
  SAFE = "SAFE",
  MARGINAL = "MARGINAL",
  UNSAFE = "UNSAFE",
  UNKNOWN = "UNKNOWN"
}

/**
 * The physical form of the observatory's protective structure.
 */
export enum EnclosureType {
  ROLL_OFF_ROOF = "roll_off_roof",
  DOME = "dome"
}

/**
 * The delegation state of one capability, and since when.
 */
export interface CapabilityDelegation {
  capability: ObservatoryCapability;
  state: DelegationState;
  effective_since?: string;
  promotion_evidence_note?: string;
}

/**
 * The frames captured for one light row of an entry.
 *
 * Parameters
 * ----------
 * source_key : `str`
 * The row's source key, when the library suggested it.
 * filter_name : `str`
 * The row's filter.
 * exposure_sec : `float`
 * The row's frame length in seconds.
 * requested : `int`
 * How many frames the row asks for.
 * captured : `int`
 * Matching frames that arrived, judged or not, and not set aside.
 * judged : `int`
 * How many of them have a stored quality judgment.
 * flagged : `int`
 * How many of the judged frames carry a quality flag. They still count.
 * not_judged : `int`
 * How many have no judgment yet. They do not complete the row.
 * met : `bool`
 * `True` when the judged frames reach the requested count.
 */
export interface RowProgress {
  sourceKey?: string;
  filterName?: string;
  exposureSec: number;
  requested: number;
  captured: number;
  judged: number;
  flagged: number;
  notJudged: number;
  met: boolean;
}

/**
 * The time lost to one cause.
 *
 * Parameters
 * ----------
 * cause : `str`
 * The cause, or for a frame set aside, the reason it was set aside.
 * seconds : `float`
 * Time lost, in seconds.
 * recoverable_with_time : `bool`
 * Whether more exposure time repairs this loss.
 * model_derived : `bool`
 * Whether the share comes from a model instead of a measurement.
 */
export interface LossShare {
  cause: string;
  seconds: number;
  recoverableWithTime: boolean;
  modelDerived: boolean;
}

/**
 * What the ranking could do with a target.
 *
 * `RANKED` has a gain and a rank. `STOPPED` is ranked at zero because its
 * noise has stopped falling. `NEW` has no data yet and is not ranked.
 * `NO_FORECAST` has data, but something needed for the forecast is missing,
 * and the reason says what.
 */
export enum CandidateStatus {
  RANKED = "ranked",
  STOPPED = "stopped",
  NEW = "new",
  NO_FORECAST = "no_forecast"
}

/**
 * Whether "take N more" may be shown for a target, and on what footing.
 *
 * ``closed`` hides the count. ``exploratory`` shows it after a check made
 * from frames already taken agreed; the next real stack confirms or closes
 * it. ``confirmed`` shows it after a check of two library stacks agreed.
 */
export enum ForecastGate {
  CLOSED = "closed",
  EXPLORATORY = "exploratory",
  CONFIRMED = "confirmed"
}

/**
 * Which system currently performs a capability.
 *
 * `SHADOWED` is not a valid state for `OBSERVATORY_SAFETY`
 * (`Wayfinding_Library_Architecture.md` Section 2.3, "Safety is never
 * shadowed") -- enforced by the delegation policy reader, not by this
 * enum, since the restriction is per-capability rather than global.
 */
export enum DelegationState {
  DELEGATED = "DELEGATED",
  SHADOWED = "SHADOWED",
  AUTHORITATIVE = "AUTHORITATIVE"
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
  "observatory:get_active_telescope",
  "observatory:set_active_telescope",
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
  "processing:record_frame_measurements",
  "processing:measure_half_difference_noise",
  "processing:measure_noise_growth",
  "processing:measure_night_contributions",
  "quality:query",
  "quality:metrics",
  "quality:resolve_rows",
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
  "calibration:measure_sensor_noise",
  "mosaic:preview",
  "mosaic:create",
  "execution:list_sessions",
  "execution:get_session",
  "execution:abort_session",
  "execution:reconcile_session",
  "execution:record_divergence",
  "plan:get",
  "plan:current_session",
  "plan:list_packages",
  "plan:package_for_target",
  "plan:create",
  "plan:create_package",
  "plan:edit",
  "plan:edit_package",
  "plan:refresh_suggestions",
  "plan:tick_checklist_item",
  "advisory:target_report",
  "advisory:tonight",
  "advisory:result",
  "history:nights",
  "history:night_quality",
  "history:target_changes",
  "history:live_session_status",
  "observatory:safety_status",
  "calibration:query",
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
 * Every event the backend pushes to the app over the events WebSocket,
 * from backend/public_interface.py.
 */
export const UI_EVENTS = {
  /** The stellar catalog was written to somewhere, so cached star lists are stale. */
  "catalog:changed": "catalog:changed",
  /** A frame measurement job stored new quality rows. The payload is {targetId, framesMeasured, rowsWritten, observingNightIds}. */
  "quality:measured": "quality:measured",
  /** A write changed the night plan. The payload is {sessionId, revision, changed}, where revision is the plan revision now stored and changed lists the kind of each change. */
  "plan:changed": "plan:changed",
  /** A write created, edited, reopened, dismissed, brought back, re-framed, deleted or promoted an observation package, or a suggestion refresh ran. The payload is {packageId, targetId, kind, revision}; kind says which, and a promotion has no revision. After a refresh, kind is "refreshed", packageId and targetId are null, and the payload adds the counts added, updated, deleted, completed, reverted and checklistItemsAdded, and the ids deletedPackageIds (suggestions removed) and completedPackageIds. */
  "packages:changed": "packages:changed",
  /** The mount, processing jobs or health status changed. */
  "system_state_update": "system_state_update",
  /** A backend log line, for the app's log panel. */
  "log": "log",
  /** The shared workspace state changed, such as the selected target. */
  "handoff": "handoff",
  /** Another client asked the app to switch to a screen. */
  "navigate-mode": "navigate-mode",
  /** Another client asked the terminal to show a variable. */
  "inspect-variable": "inspect-variable",
  /** A device needs attention; shown on a paired phone. */
  "device_alert": "device_alert",
  /** An AI agent ran code in the terminal. */
  "terminal:agent_exec": "terminal:agent_exec",
  /** The terminal made a plot to show. */
  "terminal:plot_generated": "terminal:plot_generated",
  /** The script editor's text was replaced by another client. */
  "editor:sync": "editor:sync",
} as const;

/** The name of one event the backend pushes to the app. */
export type UiEvent = (typeof UI_EVENTS)[keyof typeof UI_EVENTS];

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
