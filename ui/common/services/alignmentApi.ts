/**
 * @fileoverview Service module for managing telescope alignment operations via unified JSON-RPC.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';
import { reportError } from '../utils/reportError';

/**
 * Starts the telescope alignment process at the specified coordinates.
 * @param targetRightAscension The target Right Ascension coordinate, as a raw sexagesimal string (e.g. "12h 34m 56s"). Parsed to degrees server-side.
 * @param targetDeclination The target Declination coordinate, as a raw sexagesimal string (e.g. "+41d 16m 09s"). Parsed to degrees server-side.
 * @return A promise resolving to true if alignment initiated successfully, or false.
 */
export async function startAlignment(targetRightAscension: string, targetDeclination: string): Promise<boolean> {
    try {
        const result = await callBackend("telescope:alignment_start", {
            target_ra: targetRightAscension,
            target_dec: targetDeclination
        });
        return !!result;
    } catch (err) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'alignment-start');
        return false;
    }
}

/**
 * Stops/cancels the current telescope alignment process.
 * @return A promise resolving to true if alignment was cancelled successfully, or false.
 */
export async function stopAlignment(): Promise<boolean> {
    try {
        const result = await callBackend("telescope:alignment_stop", {});
        return !!result;
    } catch (err) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'alignment-stop');
        return false;
    }
}
