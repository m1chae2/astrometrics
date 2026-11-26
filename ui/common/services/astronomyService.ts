/**
 * @fileoverview Service module for managing astronomical stellar objects and catalog data.
 * Calls backend JSON-RPC methods and integrates with frontend spectrum types.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';
import { reportError } from '../utils/reportError';
import { OverlayStar, Spectrum, TargetStarCount } from '../types/backendTypes';

export interface AstronomyListOptions {
    targetId?: string;
    search?: string;
    filterType?: string;
    limit?: number;
    offset?: number;
}

/** Builds the `target_id`/`search`/`filter_type` scoping params shared by the list and count RPCs. */
function buildAstronomyScopeParams(optionsOrTargetId?: string | AstronomyListOptions): Record<string, unknown> {
    const params: Record<string, unknown> = {};
    if (typeof optionsOrTargetId === 'string') {
        if (optionsOrTargetId.trim() !== '') {
            params.target_id = optionsOrTargetId.trim();
        }
    } else if (optionsOrTargetId && typeof optionsOrTargetId === 'object') {
        if (optionsOrTargetId.targetId && optionsOrTargetId.targetId.trim() !== '') {
            params.target_id = optionsOrTargetId.targetId.trim();
        }
        if (optionsOrTargetId.search && optionsOrTargetId.search.trim() !== '') {
            params.search = optionsOrTargetId.search.trim();
        }
        if (optionsOrTargetId.filterType && optionsOrTargetId.filterType.trim() !== '') {
            params.filter_type = optionsOrTargetId.filterType.trim();
        }
    }
    return params;
}

async function getAstronomyList(optionsOrTargetId?: string | AstronomyListOptions): Promise<Spectrum[]> {
    try {
        const params = buildAstronomyScopeParams(optionsOrTargetId);
        if (typeof optionsOrTargetId === 'string' || !optionsOrTargetId || typeof optionsOrTargetId !== 'object') {
            params.limit = 100;
            params.offset = 0;
        } else {
            params.limit = optionsOrTargetId.limit !== undefined ? optionsOrTargetId.limit : 100;
            if (optionsOrTargetId.offset !== undefined) {
                params.offset = optionsOrTargetId.offset;
            }
        }
        const data = await callBackend("astronomy:list", params);
        return Array.isArray(data) ? (data as Spectrum[]) : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/**
 * Fetches how many stars match a scope (target, search, and/or filter), unpaginated.
 * Used to compute how many pages a paginated star listing has.
 * @param optionsOrTargetId Optional target identifier or options object.
 * @return The total matching count. Throws on failure, so callers never cache a failure as 0.
 */
export async function fetchAstronomyCount(optionsOrTargetId?: string | AstronomyListOptions): Promise<number> {
    try {
        const params = buildAstronomyScopeParams(optionsOrTargetId);
        const data = await callBackend("astronomy:count", params);
        return typeof data === 'number' ? data : 0;
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/**
 * Fetches the list of available astronomy objects, optionally filtered by target ID, search, and category.
 * @param optionsOrTargetId Optional target identifier or options object.
 * @return List of Spectrum objects or strings. Throws on failure, so callers never cache a failure as an empty list.
 */
export const fetchAstronomyList = (optionsOrTargetId?: string | AstronomyListOptions): Promise<Spectrum[]> => {
    return getAstronomyList(optionsOrTargetId);
};

/**
 * Whether a target has any star with spectra and/or photometry data, and how many stars it has.
 * The library's `TargetStarCount` model, generated into `backendTypes.ts`.
 */
export type TargetDataAvailability = TargetStarCount;

/**
 * Fetches, per target, whether any of its stars have spectra or photometry data.
 * @return A map from target ID to its data availability. Throws on failure.
 */
export async function fetchTargetDataAvailability(): Promise<Record<string, TargetDataAvailability>> {
    try {
        const data = await callBackend('astronomy:target_data_availability', {});
        return (data as Record<string, TargetDataAvailability>) ?? {};
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/** A catalog spectral class present in the library, with how many stars have it. */
export interface SpectralClassSummary {
    spectralClass: string;
    label: string;
    count: number;
}

/**
 * Fetches the catalog spectral classes present in the library, with counts.
 * @return The spectral classes, sorted alphabetically. Throws on failure.
 */
export async function fetchSpectralClassSummary(): Promise<SpectralClassSummary[]> {
    try {
        const data = await callBackend('astronomy:spectral_class_summary', {});
        return Array.isArray(data) ? (data as SpectralClassSummary[]) : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/** One star in a catalog spectral class listing, ranked by self-determined match quality. */
export interface SpectralClassStar {
    id: string;
    name: string;
    ra: number | null;
    dec: number | null;
    magnitude: number | null;
    /** The library's verdict that `magnitude` is a real catalog magnitude. */
    hasCatalogMagnitude?: boolean;
    spectralType: string;
    hasSpectra: boolean;
    hasPhotometry: boolean;
    selfDeterminedSpectralTypeRms: number | null;
}

/**
 * Fetches the stars in one catalog spectral class, best self-determined match first.
 * @param spectralClass The spectral class letter (or a full catalog string such as "G2V").
 * @return The matching stars, best match first. Throws on failure.
 */
export async function fetchStarsBySpectralClass(spectralClass: string): Promise<SpectralClassStar[]> {
    try {
        const data = await callBackend('astronomy:stars_by_spectral_class', { spectral_class: spectralClass });
        return Array.isArray(data) ? (data as SpectralClassStar[]) : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/**
 * Fetches detailed astronomy data for a named object (with fuzzy matching).
 * @param name The name or ID of the object.
 * @param signal Optional AbortSignal (ignored in RPC mode).
 * @return The parsed astronomy data or null.
 */
export async function fetchAstronomyData(
    name: string,
    signal?: AbortSignal
): Promise<any> {
    try {
        const data = await callBackend("astronomy:get", { star_id: name.trim() }, { signal });
        return data || null;
    } catch (err: unknown) {
        if ((err as any)?.name === 'AbortError') {
            return null;
        }
        const errorMessage = err instanceof Error ? err.message : String(err);
        reportError(err instanceof Error ? err : new Error(errorMessage), 'backend');
        throw err;
    }
}

/**
 * Runs the period and transit search on a star's light curve and saves the result.
 * @param starId The id of the star to analyze.
 * @return The star with any new analysis attached, or null if it does not exist.
 */
export async function analyzeStarPeriodicity(starId: string): Promise<Spectrum | null> {
    try {
        const data = await callBackend("astronomy:analyze_periodicity", { star_id: starId.trim() });
        return (data as Spectrum | null) || null;
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/** How a star of a stack's own star names was found. */
export type StarMatchKind = 'matched' | 'unmatched' | 'catalog_only';

/**
 * One catalog star placed on a target's image, for the astrometry overlay.
 * The library's `OverlayStar` model, generated into `backendTypes.ts`. A star of a stack's own
 * star names (see {@link fetchStackIdentification}) also says how it was found and where it is on the sky.
 */
export type AstrometryOverlayStar = OverlayStar & {
    /** `matched`: a detection with a catalog star; `unmatched`: a detection with none; `catalog_only`: a missed catalog star. */
    matchKind?: StarMatchKind;
    /** Right ascension, in degrees. */
    raDeg?: number;
    /** Declination, in degrees. */
    decDeg?: number;
    /** Gaia G magnitude, when a Gaia star is matched. */
    gMag?: number | null;
    /** The Gaia DR3 source id (digits only), when a Gaia star is matched. */
    gaiaSourceId?: string | null;
    /** Where the name came from: `simbad`, `gaia` or `none`. */
    nameSource?: string;
    /** The id of the star's record in the library, set when the library has one (see `useLibraryStars`). */
    libraryId?: string | null;
};

/** The saved star names of a stack. */
export interface StackIdentificationResult {
    /** Every detection, then the catalog stars with no detection. */
    stars: AstrometryOverlayStar[];
    /** How many detections have a catalog name. */
    matchedCount: number;
    /** How many detections have no catalog star. */
    unmatchedCount: number;
    /** How many bright catalog stars have no detection. */
    catalogOnlyCount: number;
    /** True when the stack's saved plate solution was used. */
    solutionReused: boolean;
    /** Sentences for the person, such as why SIMBAD names are missing. */
    notes: string[];
}

/** The star names of a stack, and the job that makes them. */
export interface StackIdentificationState {
    /** True while a job is naming the stars. */
    running: boolean;
    /** The latest naming job, or null. */
    jobId: string | null;
    /** Share done, 0 to 1, while running. */
    progress: number | null;
    /** What the running job is doing. */
    message: string | null;
    /** Why the last job failed, or null. */
    error: string | null;
    /** The saved names, or null when there are none for the stack as it is. */
    identification: StackIdentificationResult | null;
}

/**
 * Starts naming the detected stars of a stack, as a job of the analysis type.
 * @param targetId The target.
 * @param stackPath The stack shown.
 * @returns The job id, and whether one was already running.
 */
export async function startStackIdentification(
    targetId: string,
    stackPath: string
): Promise<{ status: string; jobId: string }> {
    return callBackend("analysis:identify_stack", { target_id: targetId.trim(), stack_path: stackPath });
}

/**
 * Reads the saved star names of a stack and the state of the job that makes them.
 * @param targetId The target.
 * @param stackPath The stack shown.
 * @returns The state.
 */
export async function fetchStackIdentification(
    targetId: string,
    stackPath: string
): Promise<StackIdentificationState> {
    return callBackend("analysis:get_stack_identification", { target_id: targetId.trim(), stack_path: stackPath });
}

/**
 * The stars placed on an image, with the reason when there are none.
 */
export interface AstrometryOverlayResult {
    /** The stars placed on the image. */
    stars: AstrometryOverlayStar[];
    /** Why there are no stars (for example, no sky solution), or null. */
    reason: string | null;
}

/**
 * Fetches identified stars and their pixel coordinates for astrometry overlay.
 * @param targetId The target identifier to fetch stars for.
 * @param limit Maximum number of stars to return. Zero (the default) means
 *   every star the library places on the image.
 * @param imagePath The image being shown. The stars are placed on it. When
 *   omitted, the target's current stack is used.
 * @returns The star overlay items with pixel coordinates and labels, and the
 *   reason when there are none.
 */
export async function fetchAstrometryOverlayStars(
    targetId: string,
    limit = 0,
    imagePath?: string | null
): Promise<AstrometryOverlayResult> {
    if (!targetId || targetId.trim() === '') return { stars: [], reason: null };
    const data = await callBackend("astronomy:get_overlay_stars", {
        target_id: targetId.trim(),
        limit,
        ...(imagePath ? { image_path: imagePath } : {})
    });
    return {
        stars: Array.isArray(data?.stars) ? (data.stars as AstrometryOverlayStar[]) : [],
        reason: typeof data?.reason === 'string' ? data.reason : null
    };
}

/**
 * Asks, in one call, for the ids of a target's stars that have photometry (a light curve).
 * Single-frame detections are left out, as in the Astronomy Manager's list.
 *
 * @param targetId The target whose stars are wanted.
 * @returns The star ids with photometry. Empty when the target has none.
 */
export async function fetchPhotometryStarIds(targetId: string): Promise<string[]> {
    if (!targetId || targetId.trim() === '') return [];
    const rows = await callBackend("astronomy:list", { target_id: targetId.trim(), filter_type: 'photometry', limit: 0 });
    return Array.isArray(rows) ? rows.map((row) => row.id).filter((id): id is string => typeof id === 'string' && id !== '') : [];
}

/**
 * Asks which of some star ids have a record in the library, in one call.
 *
 * @param starIds The ids to look for.
 * @returns The ids that have a record.
 */
export async function fetchExistingStarIds(starIds: string[]): Promise<string[]> {
    if (starIds.length === 0) return [];
    const found = await callBackend("astronomy:existing_ids", { star_ids: starIds });
    return Array.isArray(found) ? found : [];
}
