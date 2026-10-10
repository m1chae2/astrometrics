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
        return [];
    }
}

/**
 * Fetches how many stars match a scope (target, search, and/or filter), unpaginated.
 * Used to compute how many pages a paginated star listing has.
 * @param optionsOrTargetId Optional target identifier or options object.
 * @return The total matching count, or 0 on failure.
 */
export async function fetchAstronomyCount(optionsOrTargetId?: string | AstronomyListOptions): Promise<number> {
    try {
        const params = buildAstronomyScopeParams(optionsOrTargetId);
        const data = await callBackend("astronomy:count", params);
        return typeof data === 'number' ? data : 0;
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return 0;
    }
}

/**
 * Fetches the list of available astronomy objects, optionally filtered by target ID, search, and category.
 * @param optionsOrTargetId Optional target identifier or options object.
 * @return List of Spectrum objects or strings.
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
 * @return A map from target ID to its data availability, or an empty map on failure.
 */
export async function fetchTargetDataAvailability(): Promise<Record<string, TargetDataAvailability>> {
    try {
        const data = await callBackend('astronomy:target_data_availability', {});
        return (data as Record<string, TargetDataAvailability>) ?? {};
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return {};
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
 * @return The spectral classes, sorted alphabetically, or an empty list on failure.
 */
export async function fetchSpectralClassSummary(): Promise<SpectralClassSummary[]> {
    try {
        const data = await callBackend('astronomy:spectral_class_summary', {});
        return Array.isArray(data) ? (data as SpectralClassSummary[]) : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return [];
    }
}

/** One star in a catalog spectral class listing, ranked by self-determined match quality. */
export interface SpectralClassStar {
    id: string;
    name: string;
    ra: number | null;
    dec: number | null;
    magnitude: number | null;
    spectralType: string;
    hasSpectra: boolean;
    hasPhotometry: boolean;
    selfDeterminedSpectralTypeRms: number | null;
}

/**
 * Fetches the stars in one catalog spectral class, best self-determined match first.
 * @param spectralClass The spectral class letter (or a full catalog string such as "G2V").
 * @return The matching stars, best match first, or an empty list on failure.
 */
export async function fetchStarsBySpectralClass(spectralClass: string): Promise<SpectralClassStar[]> {
    try {
        const data = await callBackend('astronomy:stars_by_spectral_class', { spectral_class: spectralClass });
        return Array.isArray(data) ? (data as SpectralClassStar[]) : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return [];
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
        const data = await callBackend("astronomy:get", { object_id: name.trim() }, { signal });
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
 * @param objectId The id of the star to analyze.
 * @return The star with any new analysis attached, or null if it does not exist.
 */
export async function analyzeStarPeriodicity(objectId: string): Promise<Spectrum | null> {
    try {
        const data = await callBackend("astronomy:analyze_periodicity", { object_id: objectId.trim() });
        return (data as Spectrum | null) || null;
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/**
 * One catalog star placed on a target's image, for the astrometry overlay.
 * The library's `OverlayStar` model, generated into `backendTypes.ts`.
 */
export type AstrometryOverlayStar = OverlayStar;

/**
 * Fetches identified stars and their pixel coordinates for astrometry overlay.
 * @param targetId The target identifier to fetch stars for.
 * @param limit Maximum number of stars to return (defaults to 35).
 * @returns List of star overlay items with centroid coordinates and labels.
 */
export async function fetchAstrometryOverlayStars(
    targetId: string,
    limit = 35
): Promise<AstrometryOverlayStar[]> {
    if (!targetId || targetId.trim() === '') return [];
    const data = await callBackend("astronomy:get_overlay_stars", {
        target_id: targetId.trim(),
        limit
    });
    return Array.isArray(data) ? (data as AstrometryOverlayStar[]) : [];
}
