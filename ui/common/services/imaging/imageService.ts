import { callBackend, resolveImageSrc } from '../backendApi';
import { reportError } from '../../utils/reportError';

/**
 * @fileoverview Service for managing, retrieving, and converting FITS files and target images.
 * Aligns with the Google TypeScript Style Guide.
 */

/**
 * Fetches the processed image blob for a target object using its registered target details.
 * Retrieves target metadata through the JSON-RPC backend call, resolves the static path,
 * and fetches the image blob directly.
 */
export async function fetchProcessedImage(
    objectId: string,
    signal?: AbortSignal
): Promise<Blob | null> {
    try {
        const target = await callBackend("target:get", { target_id: objectId });
        if (!target) {
            return null;
        }

        let imagePath = target.processedImage || target.stackedImage || target.stackedSpectralTarget || target.processed_image || target.stacked_image;

        if (!imagePath && target.frames) {
            const stackedFrame = target.frames.find(f =>
                f.path.toLowerCase().includes('stacked') ||
                f.path.toLowerCase().includes('processed')
            );
            if (stackedFrame) {
                imagePath = stackedFrame.path;
            }
        }

        if (!imagePath) {
            return null;
        }

        const ext = imagePath.split('.').pop()?.toLowerCase();
        if (ext === 'fits' || ext === 'fit') {
            const result = await callBackend("images:convert_fits", { path: imagePath, stretch: true });
            if (result && result.imageData) {
                const res = await fetch(result.imageData, { signal });
                return res.blob();
            }
            return null;
        }

        const src = resolveImageSrc(imagePath);
        if (!src) {
            return null;
        }
        const response = await fetch(src, { method: 'GET', signal });
        if (response.status === 404 || response.status === 204) {
            return null;
        }
        if (!response.ok) {
            throw new Error(
                `Failed to fetch processed image from static path ${src}: ${response.status} ${response.statusText}`
            );
        }
        return response.blob();
    } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') throw err;
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        throw err;
    }
}

/**
 * Fetches a raw target FITS frame path via RPC and retrieves it as a binary blob.
 */
export async function fetchTargetFrame(
    objectId: string,
    iso: string,
    exposure: string,
    index = 0,
    signal?: AbortSignal
): Promise<Blob> {
    const path = await callBackend("images:get_target_frame", {
        target_id: objectId,
        iso,
        exposure,
        index
    });
    const src = resolveImageSrc(path);
    const response = await fetch(src, { method: 'GET', signal });
    if (!response.ok) {
        throw new Error(`Failed to fetch target frame blob from path ${src}: ${response.status} ${response.statusText}`);
    }
    return response.blob();
}

/**
 * Fetches proper visual representation and statistics for a specific light frame.
 */
export async function fetchLightFrame(
    objectId: string,
    iso: string,
    exposure: string,
    index = 0,
    maxdim = 2000,
    center?: number,
    width?: number,
    cmap = 'gray',
    stretch = true
): Promise<{ id: string; min: number; max: number; imageData: string } | null> {
    try {
        const result = await callBackend("images:get_light_frame_data", {
            target_id: objectId,
            iso,
            exposure,
            index,
            stretch
        });
        if (result) {
            return {
                id: result.id ?? '',
                min: result.min ?? 0,
                max: result.max ?? 0,
                imageData: result.imageData,
            };
        }
        return null;
    } catch (error) {
        reportError(error instanceof Error ? error : new Error(String(error)), 'backend');
        return null;
    }
}

/**
 * Fetches proper visual representation of an arbitrary FITS file by its system path.
 */
export async function fetchImageByPath(path: string, stretch: boolean = true): Promise<import('../../types/backendTypes').RenderedImage | null> {
    try {
        const result = await callBackend("images:convert_fits", { path, stretch });
        if (result) {
            return {
                id: result.id ?? '',
                min: result.min ?? 0,
                max: result.max ?? 0,
                imageData: result.imageData ?? (result as any).image_data,
            };
        }
        return null;
    } catch (err) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return null;
    }
}

/**
 * Deletes multiple files from disk and updates target association.
 */
export async function deleteFiles(paths: string[], targetId?: string): Promise<{ deleted: string[], failed: { path: string, reason: string }[] } | null> {
    try {
        const result = await callBackend("images:delete", { paths, target_id: targetId });
        return result;
    } catch (err) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return null;
    }
}

/**
 * Fetches the last captured image metadata and data.
 */
export async function fetchLastImage(stretch: boolean = true): Promise<{ id: string; min: number; max: number; image_data: string; path: string } | null> {
    try {
        const result = await callBackend("images:last", { stretch });
        return result;
    } catch (err) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return null;
    }
}
