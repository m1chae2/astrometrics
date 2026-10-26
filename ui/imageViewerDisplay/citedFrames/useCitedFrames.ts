/**
 * @file useCitedFrames.ts
 * @description Hooks for the Image Viewer's cited-frames list: one follows the
 * latest `showFrames` request, the other reads the cited rows from the
 * backend: the measurements with `quality:query` (`row_ids`, each row carries
 * its `unit`) and the picture file of each row, the reason a row has none and
 * the ids the library no longer has with `quality:resolve_rows`.
 */

import { useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import type { QualityMeasurement, QualityRowFile } from '../../common/types/backendTypes';
import {
  CitedFramesRequest,
  clearCitedFramesRequest,
  getCitedFramesRequest,
  subscribeToCitedFrames,
} from './citedFramesStore';

/**
 * Follows the latest `showFrames` request.
 *
 * @returns The request (or null) and a function that clears it.
 */
export function useCitedFramesRequest(): {
  request: CitedFramesRequest | null;
  clear: () => void;
} {
  const [request, setRequest] = useState<CitedFramesRequest | null>(getCitedFramesRequest());
  useEffect(() => {
    setRequest(getCitedFramesRequest());
    return subscribeToCitedFrames(setRequest);
  }, []);
  const clear = useCallback(() => clearCitedFramesRequest(), []);
  return { request, clear };
}

/** Rows returned for the cited ids, with the picture file of each and the ids the library no longer has. */
export interface CitedFramesData {
  /** The measurement rows the library returned, in the order returned. */
  rows: QualityMeasurement[];
  /** The picture file (or the reason there is none) for each row the library found, by row id. */
  filesByRowId: Record<number, QualityRowFile>;
  /** Cited row ids that the library reports as not found. */
  missingRowIds: number[];
}

/**
 * Reads the cited quality rows.
 *
 * @param rowIds The cited row ids; nothing is fetched when empty.
 * @returns The React Query result with the rows, their files and the missing ids.
 */
export function useCitedFramesQuery(rowIds: number[]) {
  return useQuery<CitedFramesData>({
    queryKey: ['citedFrames', rowIds],
    enabled: rowIds.length > 0,
    queryFn: async () => {
      const [rows, resolution] = await Promise.all([
        callBackend('quality:query', { row_ids: rowIds }),
        callBackend('quality:resolve_rows', { row_ids: rowIds }),
      ]);
      const filesByRowId: Record<number, QualityRowFile> = {};
      for (const file of resolution?.rows ?? []) {
        filesByRowId[file.rowId] = file;
      }
      return {
        rows: rows ?? [],
        filesByRowId,
        missingRowIds: resolution?.notFoundRowIds ?? [],
      };
    },
  });
}
