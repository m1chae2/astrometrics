/**
 * @file useCitedFrames.ts
 * @description Hooks for the Image Viewer's cited-frames list: one follows the
 * latest `showFrames` request, the other reads the cited rows from the
 * backend with `quality:query` (`row_ids`) and the unit of each metric with
 * `quality:metrics`.
 */

import { useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import type { MetricSummary, QualityMeasurement } from '../../common/types/backendTypes';
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

/** Rows returned for the cited ids, plus the ids the library no longer has. */
export interface CitedFramesData {
  /** The rows the library returned, in the order returned. */
  rows: QualityMeasurement[];
  /** Unit of each metric name, as the metric list declares it. */
  unitByMetric: Record<string, string>;
  /** Cited row ids that the library did not return. */
  missingRowIds: number[];
}

/**
 * Reads the cited quality rows.
 *
 * @param rowIds The cited row ids; nothing is fetched when empty.
 * @returns The React Query result with the rows and the missing ids.
 */
export function useCitedFramesQuery(rowIds: number[]) {
  return useQuery<CitedFramesData>({
    queryKey: ['citedFrames', rowIds],
    enabled: rowIds.length > 0,
    queryFn: async () => {
      const [rows, metrics] = await Promise.all([
        callBackend('quality:query', { row_ids: rowIds }),
        callBackend('quality:metrics', { detail: 'summary' }),
      ]);
      const unitByMetric: Record<string, string> = {};
      for (const metric of (metrics ?? []) as MetricSummary[]) {
        unitByMetric[metric.name] = metric.unit;
      }
      const returnedIds = new Set(rows.map((row) => row.rowId));
      return {
        rows,
        unitByMetric,
        missingRowIds: rowIds.filter((rowId) => !returnedIds.has(rowId)),
      };
    },
  });
}
