/**
 * @module useMosaicPreview
 * @fileoverview Reads the mosaic panel centers and rotation from the library
 * (`mosaic:preview`) for the framing panel. The UI does no framing math; it
 * shows what the library returns.
 */

import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import type { MosaicPanel } from '../../common/types/backendTypes';

/** The grid the user asks the library to lay out. */
export interface MosaicGridRequest {
  /** Number of panel rows. */
  rows: number;
  /** Number of panel columns. */
  cols: number;
  /** Overlap between neighboring panels, in percent. */
  overlap: number;
}

/**
 * Fetches the mosaic panels for a center and a grid.
 *
 * @param center The grid center in degrees, or null when no target is chosen (nothing is fetched).
 * @param grid The grid size and overlap.
 * @param rotationDeg The camera rotation in degrees east of north.
 * @param enabled Whether to fetch at all (false while the framing panel is closed).
 * @returns The React Query result; `data` is the panel list in the order returned.
 */
export function useMosaicPreview(
  center: { ra: number; dec: number } | null,
  grid: MosaicGridRequest,
  rotationDeg: number,
  enabled: boolean,
) {
  return useQuery<MosaicPanel[]>({
    queryKey: ['mosaicPreview', center, grid, rotationDeg],
    queryFn: () =>
      callBackend('mosaic:preview', {
        center_ra: center!.ra,
        center_dec: center!.dec,
        grid,
        rotation_deg: rotationDeg,
      }),
    enabled: enabled && center !== null,
  });
}
