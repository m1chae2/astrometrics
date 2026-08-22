/**
 * @fileoverview Service for mount movement control, tracking, and target data synchronization.
 * Communicates via JSON-RPC.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from '../backendApi';
import { reportError } from '../../utils/reportError';

/**
 * Initiates a light frame synchronization task from a remote observatory source.
 * @param objectId The identifier of the astronomical target to sync.
 * @return True if sync was successfully initiated.
 */
export async function syncLightFrames(objectId: string): Promise<boolean> {
    try {
        return await callBackend("telescope:sync", { object_id: objectId });
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to start sync for ${objectId}: ${msg}`), 'backend');
        throw new Error(`Failed to start sync for ${objectId}: ${msg}`);
    }
}


/**
 * Checks if a synchronization job is actively running for a target.
 * @param objectId The identifier of the target.
 * @return True if sync is active, false otherwise.
 */
export async function isSyncing(objectId: string): Promise<boolean> {
    try {
        return await callBackend("telescope:is_syncing", { object_id: objectId });
    } catch {
        return false;
    }
}

/**
 * Commands the telescope mount to slew to the specified celestial coordinates.
 * @param ra Right Ascension coordinate in decimal hours.
 * @param dec Declination coordinate in decimal degrees.
 * @return Success indicator.
 */
export async function slewTelescope(ra: number, dec: number): Promise<boolean> {
    try {
        return await callBackend("telescope:slew_coordinates", { ra, dec });
    } catch {
        return false;
    }
}

/**
 * Commands the telescope mount to slew to its parked position.
 * @return Success indicator.
 */
export async function parkTelescope(): Promise<boolean> {
    try {
        return await callBackend("telescope:park", {});
    } catch {
        return false;
    }
}

/**
 * Unparks/releases the telescope mount, making it ready to accept slew commands.
 * @return Success indicator.
 */
export async function unparkTelescope(): Promise<boolean> {
    try {
        return await callBackend("telescope:unpark", {});
    } catch {
        return false;
    }
}

/**
 * Enables or disables sidereal tracking on the telescope mount.
 * @param enabled True to track, false to stop.
 * @return Success indicator.
 */
export async function setTelescopeTracking(enabled: boolean): Promise<boolean> {
    try {
        return await callBackend("telescope:set_tracking", { enabled });
    } catch {
        return false;
    }
}

/**
 * Sends a manual move/jog nudge command to the telescope mount.
 * @param direction Direction to jog ('N', 'S', 'E', 'W' or 'STOP').
 * @param start True to initiate motion, false to stop.
 * @return Success indicator.
 */
export async function moveTelescope(direction: string, start: boolean = true): Promise<boolean> {
    try {
        return await callBackend("telescope:manual_move", { direction, start });
    } catch {
        return false;
    }
}

/**
 * Sets the speed/rate for subsequent manual jog motions.
 * @param rateIndex 0=Guide, 1=Centering, 2=Find, 3=Max.
 * @return Success indicator.
 */
export async function setTelescopeSlewRate(rateIndex: number): Promise<boolean> {
    try {
        return await callBackend("telescope:set_slew_rate", { rate_index: rateIndex });
    } catch {
        return false;
    }
}

/**
 * Aborts all telescope mount motion immediately.
 * @return Success indicator.
 */
export async function abortTelescopeMotion(): Promise<boolean> {
    try {
        return await callBackend("telescope:abort_motion", {});
    } catch {
        return false;
    }
}
