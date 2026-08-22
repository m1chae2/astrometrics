/**
 * @fileoverview Service module for managing astronomical stellar objects and catalog data.
 * Calls backend JSON-RPC methods and integrates with frontend spectrum types.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';
import { reportError } from '../utils/reportError';
import { Spectrum } from '../types/backendTypes';

async function getAstronomyList(targetId?: string): Promise<Spectrum[]> {
    try {
        const params: Record<string, unknown> = {};
        if (targetId && targetId.trim() !== '') {
            params.target_id = targetId.trim();
        }
        const data = await callBackend("astronomy:list", params);
        return Array.isArray(data) ? (data as Spectrum[]) : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return [];
    }
}

/**
 * Fetches the list of available astronomy objects, optionally filtered by target ID.
 * @param targetId Optional target identifier to restrict stellar objects.
 * @return List of Spectrum objects or strings.
 */
export const fetchAstronomyList = (targetId?: string): Promise<Spectrum[]> => {
    return getAstronomyList(targetId);
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
