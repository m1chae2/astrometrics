/**
 * @fileoverview The React Query keys of the quality RPCs.
 *
 * Every query that reads stored quality numbers (`quality:query`,
 * `quality:metrics`) builds its key here. All of them start with
 * `QUALITY_QUERY_KEY_PREFIX`, so one `invalidateQueries` call with that
 * prefix refreshes them all. `App.tsx` makes that call when the backend sends
 * the `quality:measured` event, which means a measurement job stored new rows.
 * `isAffectedByMeasurement` narrows that to the queries the new rows can change.
 */

/** The start of every quality query key. */
export const QUALITY_QUERY_KEY_PREFIX = ['quality'] as const;

/** The parameters of a `quality:query` call, as the screen passes them. */
export type QualityQueryParams = Record<string, unknown>;

/** The payload of the `quality:measured` event. */
export interface QualityMeasuredPayload {
    targetId?: string;
    framesMeasured?: number;
    rowsWritten?: number;
    observingNightIds?: string[];
}

/**
 * Tells whether new rows from a measurement job can change a quality query.
 * A query that names a target or an observing night is affected only when it
 * names the measured target or one of the measured nights. A query that
 * names neither reads everything, so it is always affected.
 * @param queryKey The React Query key of a cached query.
 * @param payload The `quality:measured` event payload.
 * @return `true` when the query should be read again.
 */
export function isAffectedByMeasurement(queryKey: readonly unknown[], payload: QualityMeasuredPayload): boolean {
    if (queryKey[0] !== QUALITY_QUERY_KEY_PREFIX[0]) return false;
    const params = (queryKey[2] ?? {}) as Record<string, unknown>;
    const targetId = params['target_id'];
    const nightId = params['observing_night_id'];
    if (typeof targetId === 'string' && targetId !== payload.targetId) return false;
    if (typeof nightId === 'string' && !(payload.observingNightIds ?? []).includes(nightId)) return false;
    return true;
}

/** Builders for the keys of each quality RPC. */
export const qualityQueryKeys = {
    /**
     * The key of a `quality:query` call.
     * @param params The call's parameters, such as the target id and metric.
     * @return The key, which starts with the quality prefix.
     */
    query: (params: QualityQueryParams = {}) => [...QUALITY_QUERY_KEY_PREFIX, 'query', params] as const,

    /**
     * The key of a `quality:metrics` call.
     * @param name One metric name, or nothing for all of them.
     * @return The key, which starts with the quality prefix.
     */
    metrics: (name?: string) => [...QUALITY_QUERY_KEY_PREFIX, 'metrics', name ?? null] as const,
};
