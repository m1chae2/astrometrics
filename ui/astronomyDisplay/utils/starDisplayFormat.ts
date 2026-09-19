/**
 * @fileoverview Formatting helpers that turn a stellar object's raw fields
 * into the short text the Astronomy Manager shows: list labels, list
 * subtitles, coordinates, and the choice of light-curve series to plot.
 */

import { PhotometryResult, SpectroscopyResult } from '../../common/types/backendTypes';
import { SpectralFeatureResult } from '../../common/types/spectralFeatureTypes';

/** Prefix of an id given to a star found in an image but never matched to a catalog. */
const POSITION_ONLY_STAR_ID_PATTERN = /^FIELD_J(-?\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)$/;

/**
 * A magnitude below this is an instrument reading, not a catalog magnitude.
 * Must match _BRIGHTEST_CATALOG_MAGNITUDE in backend/services/data/stellar_service.py.
 */
const BRIGHTEST_CATALOG_MAGNITUDE = -2;

/** Longest run of digits shown whole; a longer catalog number is shortened to its last digits. */
const LONGEST_WHOLE_NUMBER_LENGTH = 8;

/** How many trailing digits of a long catalog number stay visible. */
const VISIBLE_TRAILING_DIGITS = 6;

/** The light curve values chosen for plotting, with the axis title that describes them. */
export interface LightCurveSeries {
    /** One value per timestamp, in the units named by `axisTitle`. */
    values: number[];
    /** Text for the plot's vertical axis. */
    axisTitle: string;
    /** True when the values are magnitudes, which are plotted with brighter at the top. */
    isMagnitude: boolean;
}

/**
 * Shortens a star's name so the part that tells stars apart stays visible.
 *
 * A long catalog number keeps its start and its last digits
 * ("Gaia DR3 1327617929278818176" becomes "Gaia DR3 …818176"), and a
 * position-only id becomes its sky position ("10.00 +39.58"; the subtitle
 * says it has no catalog match).
 * Any other name is returned unchanged.
 *
 * @param starName The star's name or id.
 * @returns The short label to show in a list row.
 */
export function formatStarListLabel(starName: string): string {
    const positionMatch = POSITION_ONLY_STAR_ID_PATTERN.exec(starName);
    if (positionMatch) {
        const rightAscensionDegrees = Number(positionMatch[1]).toFixed(2);
        const declinationDegrees = Number(positionMatch[2]);
        const declinationText = `${declinationDegrees >= 0 ? '+' : '-'}${Math.abs(declinationDegrees).toFixed(2)}`;
        return `${rightAscensionDegrees} ${declinationText}`;
    }

    const longNumberMatch = /^(.*\D)(\d+)$/.exec(starName);
    if (longNumberMatch && longNumberMatch[2].length > LONGEST_WHOLE_NUMBER_LENGTH) {
        const trailingDigits = longNumberMatch[2].slice(-VISIBLE_TRAILING_DIGITS);
        return `${longNumberMatch[1]}…${trailingDigits}`;
    }
    return starName;
}

/**
 * Formats a magnitude if it is a real catalog magnitude.
 *
 * @param magnitude The star's raw magnitude field.
 * @returns The magnitude to two decimals, or null when it is missing or is
 *     an instrument reading.
 */
export function formatCatalogMagnitude(magnitude: unknown): string | null {
    if (magnitude === null || magnitude === undefined || magnitude === '') return null;
    const magnitudeNumber = Number(magnitude);
    if (!Number.isFinite(magnitudeNumber) || magnitudeNumber < BRIGHTEST_CATALOG_MAGNITUDE) return null;
    return magnitudeNumber.toFixed(2);
}

/**
 * Builds the second line of a list row: brightness and spectral type.
 *
 * @param summary A star summary from the backend list.
 * @returns For example "mag 7.48 · M2V" or "no catalog match", or an empty string when nothing is known.
 */
export function buildStarListSubtitle(summary: {
    id?: unknown;
    magnitude?: unknown;
    spectralType?: unknown;
}): string {
    const parts: string[] = [];
    if (POSITION_ONLY_STAR_ID_PATTERN.test(String(summary.id ?? ''))) parts.push('no catalog match');
    const magnitudeText = formatCatalogMagnitude(summary.magnitude);
    if (magnitudeText !== null) parts.push(`mag ${magnitudeText}`);
    const spectralType = typeof summary.spectralType === 'string' ? summary.spectralType.trim() : '';
    if (spectralType !== '' && spectralType !== 'Unknown') parts.push(spectralType);
    return parts.join(' · ');
}

/**
 * Formats a coordinate in degrees with a fixed number of decimals.
 *
 * @param value The raw coordinate, a number or a numeric string.
 * @param fractionDigits Decimal places to keep. Five is about 0.04 arcseconds.
 * @returns The formatted text, or an empty string when the value is not a number.
 */
export function formatCoordinateDegrees(value: unknown, fractionDigits: number = 5): string {
    if (value === null || value === undefined || value === '') return '';
    const coordinate = Number(value);
    return Number.isFinite(coordinate) ? coordinate.toFixed(fractionDigits) : '';
}

/**
 * Describes the time between two timestamps in the largest sensible unit.
 *
 * @param firstTimestamp The earliest timestamp.
 * @param lastTimestamp The latest timestamp.
 * @returns For example "18 min", "2.5 h" or "3.2 d", or an empty string if either is invalid.
 */
export function formatTimeSpan(firstTimestamp: string, lastTimestamp: string): string {
    const spanMilliseconds = new Date(lastTimestamp).getTime() - new Date(firstTimestamp).getTime();
    if (!Number.isFinite(spanMilliseconds) || spanMilliseconds < 0) return '';
    const spanMinutes = spanMilliseconds / 60000;
    if (spanMinutes < 90) return `${Math.round(spanMinutes)} min`;
    const spanHours = spanMinutes / 60;
    if (spanHours < 48) return `${spanHours.toFixed(1)} h`;
    return `${(spanHours / 24).toFixed(1)} d`;
}

/**
 * Picks which of a star's light curve series to plot.
 *
 * Prefers magnitudes, then detrended flux, then normalized flux, then raw
 * flux. A series is skipped when its length does not match the number of
 * timestamps, since its points could not be paired with times.
 * Normalized and detrended values are the star's flux divided by a
 * per-frame reference flux, so they are ratios without units; raw flux is
 * in ADU (the camera's counting unit).
 *
 * @param photometry The star's photometry, if any.
 * @returns The chosen values and axis title; empty values when nothing can be plotted.
 */
export function selectLightCurveSeries(photometry: PhotometryResult | null | undefined): LightCurveSeries {
    const timestampCount = photometry?.timestamps?.length ?? 0;
    const candidates: LightCurveSeries[] = [
        { values: photometry?.magnitudes ?? [], axisTitle: 'Magnitude', isMagnitude: true },
        {
            values: photometry?.fluxesDetrended ?? [],
            axisTitle: 'Relative flux (detrended)',
            isMagnitude: false,
        },
        {
            values: photometry?.fluxesNormalized ?? [],
            axisTitle: 'Relative flux',
            isMagnitude: false,
        },
        { values: photometry?.fluxes ?? [], axisTitle: 'Flux (ADU)', isMagnitude: false },
    ];
    const usableSeries = candidates.find(
        (candidate) => candidate.values.length > 0 && candidate.values.length === timestampCount
    );
    return usableSeries ?? { values: [], axisTitle: 'Flux (ADU)', isMagnitude: false };
}


/** Spectral letters in order from hottest to coolest. */
const SPECTRAL_LETTER_ORDER = 'OBAFGKM';

/** A match differs from the catalog type when their ladder positions are more than this many subtypes apart. */
const CATALOG_DISAGREEMENT_SUBTYPES = 8;

/** How a spectrum's template match should be shown. */
export interface TemplateMatchDescription {
    /** Badge text, for example "Template match: A5V". */
    badgeText: string;
    /** Longer hover text. */
    hoverText: string;
    /** True when the match is poor (a large difference from every reference). */
    isPoor: boolean;
    /** True when the matched type is far from the catalog's type. */
    differsFromCatalog: boolean;
}

/**
 * Turns a spectral type such as "K0Va" or "A5V+M3" into a position on the O to M ladder.
 * @param spectralType A spectral type from a catalog or a template match.
 * @returns The position (each letter is 10 subtypes wide), or null when the text has no letter and number.
 */
export function spectralLadderPosition(spectralType: string | null | undefined): number | null {
    const match = /^\s*([OBAFGKM])\s*(\d(?:\.\d)?)/i.exec(spectralType ?? '');
    if (!match) return null;
    return SPECTRAL_LETTER_ORDER.indexOf(match[1].toUpperCase()) * 10 + Number(match[2]);
}

/**
 * Describes a spectrum's best-matching reference type and whether to trust it.
 *
 * The match is a comparison of the spectrum's shape with reference spectra,
 * not a probability. A poor match (large root-mean-square difference) or one
 * far from the catalog type is flagged so it is not read as a measurement.
 *
 * @param spectroscopy The star's spectroscopy result.
 * @param catalogSpectralType The star's catalog spectral type, if known.
 * @returns The description, or null when no type was determined.
 */
export function describeTemplateMatch(
    spectroscopy: SpectroscopyResult | null | undefined,
    catalogSpectralType: string | null | undefined
): TemplateMatchDescription | null {
    const matchedType = spectroscopy?.selfDeterminedSpectralType;
    if (!matchedType || matchedType === 'Unknown') return null;
    const rms = spectroscopy?.selfDeterminedSpectralTypeRms;
    const isPoor = typeof rms === 'number' && rms > 0.15;
    const matchedPosition = spectralLadderPosition(matchedType);
    const catalogPosition = spectralLadderPosition(catalogSpectralType);
    const differsFromCatalog =
        matchedPosition !== null &&
        catalogPosition !== null &&
        Math.abs(matchedPosition - catalogPosition) > CATALOG_DISAGREEMENT_SUBTYPES;
    const candidates = spectroscopy?.selfDeterminedSpectralTypeCandidates ?? [];
    const closest = candidates
        .slice(0, 3)
        .map((candidate) => `${candidate.spectral_type} (${(Number(candidate.rms) * 100).toFixed(1)}%)`)
        .join(', ');
    const parts = [
        'A comparison of this spectrum\'s shape with reference spectra, not a measurement of the star.',
        closest ? `Closest references, with their difference from the spectrum: ${closest}.` : '',
        isPoor ? `The best reference (${matchedType}) is still far from this spectrum, so no type is claimed.` : '',
        differsFromCatalog ? `It differs from the catalog type (${catalogSpectralType}).` : '',
    ].filter((part) => part !== '');
    return {
        // A poor match names no type at all: the best reference is still far
        // from the spectrum, so its label would read as a finding it is not.
        badgeText: isPoor
            ? 'No good template match'
            : `Template match: ${matchedType}${typeof rms === 'number' ? ` (${(rms * 100).toFixed(0)}% off)` : ''}`,
        hoverText: parts.join(' '),
        isPoor,
        differsFromCatalog,
    };
}

/**
 * Says whether a search verdict means something about the star.
 * @param verdict The verdict text from a period, dip or feature test.
 * @returns True for "detected" and "possible".
 */
export function isSignificantVerdict(verdict: string | null | undefined): boolean {
    return verdict === 'detected' || verdict === 'possible';
}

/**
 * Formats a chance of a false alarm for display.
 * @param probability A probability from 0 to 1.
 * @returns For example "0.5%", "12%", or "< 0.1%".
 */
export function formatFalseAlarmProbability(probability: number | null | undefined): string {
    if (probability === null || probability === undefined || !Number.isFinite(probability)) return 'n/a';
    if (probability < 0.001) return '< 0.1%';
    const percent = probability * 100;
    return percent < 10 ? `${percent.toFixed(1)}%` : `${Math.round(percent)}%`;
}

/**
 * Picks the features worth drawing on the spectrum: those detected or possible.
 * @param features Every tested feature.
 * @returns The features with a detected or possible verdict.
 */
export function significantFeatures(features: SpectralFeatureResult[] | null | undefined): SpectralFeatureResult[] {
    return (features ?? []).filter((feature) => isSignificantVerdict(feature.verdict));
}


/** Short names for the absorption features the backend tests, keyed by the backend's full names. */
const FEATURE_SHORT_NAMES: Record<string, string> = {
    'Hydrogen Balmer series (H-alpha)': 'H-alpha',
    'Hydrogen Balmer series (H-beta)': 'H-beta',
    'Hydrogen Balmer series (H-gamma)': 'H-gamma',
    'Hydrogen Balmer series (H-delta)': 'H-delta',
    'Calcium II H & K': 'Ca H & K',
    'Magnesium b triplet': 'Mg b',
    'Sodium D doublet': 'Na D',
    'Iron/titanium blend (G band)': 'G band',
};

/**
 * Shortens an absorption feature's name so it fits in a table cell or on a plot label.
 * @param featureName The backend's full feature name.
 * @returns The short name, or the text in trailing parentheses, or the full name when neither applies.
 */
export function shortFeatureName(featureName: string): string {
    const knownShortName = FEATURE_SHORT_NAMES[featureName];
    if (knownShortName) return knownShortName;
    const parenthesized = featureName.match(/\(([^)]+)\)\s*$/);
    return parenthesized ? parenthesized[1] : featureName;
}


/**
 * Formats a period in the unit that reads best: minutes, hours or days.
 * @param periodDays The period in days.
 * @returns For example "4.5 min", "6.0 h" or "2.35 d", or "n/a" when the period is not a positive number.
 */
export function formatPeriod(periodDays: number | null | undefined): string {
    if (periodDays === null || periodDays === undefined || !Number.isFinite(periodDays) || periodDays <= 0) return 'n/a';
    const periodMinutes = periodDays * 1440;
    if (periodMinutes < 90) return `${periodMinutes.toFixed(1)} min`;
    if (periodDays < 2) return `${(periodDays * 24).toFixed(1)} h`;
    return `${periodDays.toFixed(2)} d`;
}
