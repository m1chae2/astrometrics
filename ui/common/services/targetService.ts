/**
 * @fileoverview Service module for managing targets and astronomical objects.
 * Wraps JSON-RPC backend calls into simple, frontend-usable asynchronous APIs.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';
import { reportError } from '../utils/reportError';
import { emitToast } from '../utils/emitToast';
import { TargetObject, TargetFilesResponse, GroupedFrameStat, FitsHeaderEntry } from '../types/backendTypes';

/**
 * Fetches the pre-shaped file list for a target.
 * @param targetId The identifier of the target.
 * @return The target files response.
 */
export async function fetchTargetFiles(targetId: string): Promise<TargetFilesResponse> {
    try {
        const data = await callBackend("target:get_files", { target_id: targetId });
        if (data) {
            return data as TargetFilesResponse;
        }
        return { files: [], stackedImage: null, stackedSpectralTarget: null, totalExposure: 0 };
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return { files: [], stackedImage: null, stackedSpectralTarget: null, totalExposure: 0 };
    }
}

/**
 * Fetches frame statistics grouped by filter and exposure.
 * @param targetId The identifier of the target.
 * @param camera Optional camera filter.
 * @return List of grouped frame statistics.
 */
export async function fetchFrameStatsGrouped(targetId: string, camera?: string): Promise<GroupedFrameStat[]> {
    try {
        const data = await callBackend("target:get_frames_grouped", { target_id: targetId, camera });
        if (data) {
            return data as GroupedFrameStat[];
        }
        return [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return [];
    }
}

/**
 * Fetches target summaries or target details by unique identifier.
 * @param targetId The target identifier name.
 * @return List of target summary objects, or specific TargetObject, or null.
 */
export async function getTargets(targetId?: string): Promise<any> {
    try {
        if (targetId) {
            const data = await callBackend("target:get_targets", { target_id: targetId });
            return data as TargetObject | null;
        } else {
            const data = await callBackend("target:get_targets", {});
            return Array.isArray(data) ? data : [];
        }
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return targetId ? null : [];
    }
}

// All six top-level mode displays mount at once at app boot and each calls
// fetchTargetList() independently via useTargetListLogic; without coalescing,
// that's N simultaneous, identical "target:get_targets" requests contending
// for the same backend round trip instead of one shared result.
let inFlightTargetListRequest: ReturnType<typeof getTargets> | null = null;

/**
 * Fetches the full list of target summary objects.
 *
 * All six top-level mode displays mount at once at app boot and each calls this
 * independently via useTargetListLogic; concurrent calls are coalesced into a
 * single shared in-flight request so N simultaneous callers issue only one
 * "target:get_targets" backend round trip instead of N identical ones.
 *
 * @return List of target summary objects.
 */
export const fetchTargetList = () => {
    if (!inFlightTargetListRequest) {
        inFlightTargetListRequest = getTargets().finally(() => {
            inFlightTargetListRequest = null;
        });
    }
    return inFlightTargetListRequest;
};

export const fetchTargetObject = (name: string) => getTargets(name);

/**
 * Updates TargetObject properties on the backend.
 * @param targetId The identifier of the target to update.
 * @param objectInfo The property updates to apply.
 * @return The result of the update operation.
 */
export async function updateTarget(
    targetId: string,
    objectInfo: Record<string, unknown>
): Promise<unknown | null> {
    try {
        const data = await callBackend("target:update", { target_id: targetId, updates: objectInfo });
        return data;
    } catch (err: unknown) {
        const message =
            err instanceof Error
                ? err.message
                : `Error updating target ${targetId}: ${String(err)}`;
        reportError(err instanceof Error ? err : new Error(message), 'backend');
        throw err;
    }
}

export const updateTargetById = updateTarget;

/**
 * Creates a new target on the backend.
 * @param targetId The identifier for the new target.
 * @param ra Optional Right Ascension.
 * @param dec Optional Declination.
 * @return The created TargetObject or null.
 */
export async function createTarget(
    targetId: string,
    ra?: string,
    dec?: string
): Promise<TargetObject | null> {
    try {
        const data = await callBackend("target:create", { target_id: targetId, ra, dec });
        return data as TargetObject | null;
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/**
 * Deletes a target or stellar object from the backend.
 * @param objectId The unique identifier of the target or stellar object.
 * @return The result of the deletion.
 */
export async function deleteTarget(objectId: string): Promise<unknown | null> {
    try {
        // Try Delete Target
        try {
            const deletedTarget = await callBackend("target:delete", { target_id: objectId });
            if (deletedTarget) {
                emitToast(`Deleted target ${objectId}`, 'success', 'backend');
                return { deleted: true };
            }
        } catch (err: any) {
            // If it's a method/not found error or 404 behavior, proceed to try stellar object
            if (err.message && !err.message.includes("not found")) {
                throw err;
            }
        }

        // Try Delete Stellar Object from Library
        const deletedStellar = await callBackend("astronomy:delete", { object_id: objectId });
        if (deletedStellar) {
            emitToast(`Deleted stellar object ${objectId}`, 'success', 'backend');
            return { deleted: true };
        } else {
            throw new Error(`Failed to delete ${objectId}: not found or execution failed.`);
        }
    } catch (err: unknown) {
        const message =
            err instanceof Error
                ? err.message
                : `Error deleting ${objectId}: ${String(err)}`;
        reportError(new Error(message), 'backend');
        throw new Error(message);
    }
}

export const deleteObject = deleteTarget;


/**
 * Links processed image files to a target object.
 * @param imageFiles Path or array of paths to image files.
 * @param targetId Unique identifier of the target.
 * @param camera Optional camera identifier.
 * @return The backend operation result.
 */
export async function addTargetData(
    imageFiles: string | string[],
    targetId: string,
    camera?: string
): Promise<Record<string, unknown>> {
    try {
        const filesString = Array.isArray(imageFiles) ? imageFiles[0] : imageFiles;
        const data = await callBackend("target:add_data", { target_id: targetId, image_file: filesString });
        emitToast(`Added processed image(s) to ${targetId}`, 'success', 'backend');
        return (data || {}) as Record<string, unknown>;
    } catch (error: unknown) {
        const message =
            error instanceof Error
                ? error.message
                : `Error adding images to ${targetId}: ${String(error)}`;
        reportError(
            error instanceof Error ? error : new Error(message),
            'backend'
        );
        throw error;
    }
}

export interface FrameStat {
    telescope: string;
    camera: string;
    iso: string;
    exposure: string;
    filter: string;
    count: number;
}

/**
 * Fetches aggregated frame statistics for a target.
 * @param targetId The identifier of the target.
 * @return List of frame statistics.
 */
export async function getFrameStats(targetId: string): Promise<FrameStat[]> {
    try {
        const data = await callBackend("target:get_frames", { target_id: targetId });
        return (data as unknown as FrameStat[]) || [];
    } catch (err) {
        console.error(`Failed to fetch frames for ${targetId}`, err);
        return [];
    }
}


export interface VisibleTarget {
    id: string;
    ra: string;
    dec: string;
    alt: string;
    az: string;
    visible: boolean;
    rise_time: string;
    set_time: string;
}

/**
 * Fetches the list of all currently visible targets.
 * @return List of visible targets.
 */
export async function fetchVisibleTargets(): Promise<VisibleTarget[]> {
    try {
        const data = await callBackend("astronomy:visible", {});
        return (data || []) as VisibleTarget[];
    } catch (err) {
        console.error('Failed to fetch visible targets', err);
        return [];
    }
}



/**
 * Fetches the FITS header info for a specific frame of a target.
 * @param targetId The identifier of the target.
 * @param framePath The system file path to the FITS frame.
 * @return List of FITS header entry objects.
 */
export async function fetchTargetFrameHeader(
    targetId: string,
    framePath: string
): Promise<FitsHeaderEntry[]> {
    try {
        const data = await callBackend("target:get_frame_header", { target_id: targetId, frame_path: framePath });
        return (data || []) as FitsHeaderEntry[];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/**
 * Refreshes/reindexes a specific target.
 * @param targetId The identifier of the target.
 * @param pruneMissing Whether to prune missing files.
 */
export async function refreshTarget(targetId: string, pruneMissing: boolean = false): Promise<void> {
    try {
        await callBackend("target:refresh", { target_id: targetId, prune_missing: pruneMissing });
        emitToast(`Reindexed target ${targetId}`, 'success', 'backend');
    } catch (err: unknown) {
        const txt = `Error reindexing target ${targetId}`;
        reportError(err instanceof Error ? err : new Error(txt), 'backend');
        throw err;
    }
}
