/**
 * @fileoverview Service module for telescope mosaic planning and panel creation.
 * Interfaces with backend JSON-RPC methods to preview and save coordinates.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';

export interface GridConfig {
    rows: number;
    cols: number;
    overlap: number;
}

/**
 * Previews the calculated coordinate panels for a mosaic grid.
 * @param centerRa Center Right Ascension.
 * @param centerDec Center Declination.
 * @param grid The grid dimensions and overlap configurations.
 * @return Mapped panel coordinate list.
 */
export async function previewMosaic(
    centerRa: number,
    centerDec: number,
    grid: GridConfig
): Promise<any[]> {
    try {
        const data = await callBackend("mosaic:preview", { center_ra: centerRa, center_dec: centerDec, grid });
        return data || [];
    } catch (err: unknown) {
        console.error('Failed to preview mosaic', err);
        throw err;
    }
}

/**
 * Creates individual target objects on the backend for each mosaic panel.
 * @param parentTargetId The original reference target ID.
 * @param grid The planned grid configuration.
 * @param panels Array of panel coordinates.
 * @param imageConfig Camera and exposure properties.
 * @param ditherConfig Auto-guiding dither configuration.
 * @return Created sub-target definitions.
 */
export async function createMosaic(
    parentTargetId: string,
    grid: GridConfig,
    panels: any[],
    imageConfig: any,
    ditherConfig: any
): Promise<any[]> {
    try {
        const data = await callBackend("mosaic:create", {
            parent_target_id: parentTargetId,
            grid,
            panels,
            image_config: imageConfig,
            dither_config: ditherConfig
        });
        return data || [];
    } catch (err: unknown) {
        console.error('Failed to create mosaic panels', err);
        throw err;
    }
}
