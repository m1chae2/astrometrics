/**
 * @fileoverview Service module for managing astronomical stellar objects and catalog data.
 * Calls backend JSON-RPC methods and integrates with frontend spectrum types.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';
import { reportError } from '../utils/reportError';
import { Spectrum } from '../types/backendTypes';

export interface AstronomyListOptions {
    targetId?: string;
    search?: string;
    filterType?: string;
    limit?: number;
    offset?: number;
}

async function getAstronomyList(optionsOrTargetId?: string | AstronomyListOptions): Promise<Spectrum[]> {
    try {
        const params: Record<string, unknown> = {};
        if (typeof optionsOrTargetId === 'string') {
            if (optionsOrTargetId.trim() !== '') {
                params.target_id = optionsOrTargetId.trim();
            }
            params.limit = 100;
            params.offset = 0;
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
            params.limit = optionsOrTargetId.limit !== undefined ? optionsOrTargetId.limit : 100;
            if (optionsOrTargetId.offset !== undefined) {
                params.offset = optionsOrTargetId.offset;
            }
        } else {
            params.limit = 100;
            params.offset = 0;
        }
        const data = await callBackend("astronomy:list", params);
        return Array.isArray(data) ? (data as Spectrum[]) : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return [];
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
        const data = await callBackend("astronomy:get", { object_id: name.trim() });
        return data || null;
    } catch (err: unknown) {
        const errorMessage = err instanceof Error ? err.message : String(err);
        reportError(err instanceof Error ? err : new Error(errorMessage), 'backend');
        throw err;
    }
}
