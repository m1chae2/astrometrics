/**
 * @fileoverview The React Query keys of the quality RPCs.
 *
 * Every query that reads stored quality numbers (`quality:query`,
 * `quality:metrics`) builds its key here. All of them start with
 * `QUALITY_QUERY_KEY_PREFIX`, so one `invalidateQueries` call with that
 * prefix refreshes them all. `App.tsx` makes that call when the backend sends
 * the `quality:measured` event, which means a measurement job stored new rows.
 * `isAffectedByMeasurement` narrows that to the queries the new rows can change.
 * Every key that depends on a target or a night carries an object with
 * `target_id` and/or `observing_night_id` as its third item. The tonight
 * ranking and the plan reads have their own prefixes.
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

/** The start of the tonight ranking's key. It is apart from the quality prefix because a measurement does not refresh it. */
const TONIGHT_RANKING_QUERY_KEY_PREFIX = ['qualityTonight'] as const;

/** The start of every night plan query key. `plan:changed` refreshes them all. */
export const PLAN_QUERY_KEY_PREFIX = ['plan'] as const;

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

    /**
     * The key of an `advisory:target_report` call.
     * @param targetId The target the report is about.
     * @return The key, with `target_id` so a measurement of that target refreshes it.
     */
    targetReport: (targetId: string) =>
        [...QUALITY_QUERY_KEY_PREFIX, 'advisory-target-report', { target_id: targetId }] as const,

    /**
     * The key of an `advisory:tonight` call.
     * @param planningTime The planning time (ISO 8601 text), or `null` for Now.
     * @return The key. It does not start with the quality prefix: the ranking is
     *     refetched when the planning time changes or on "Refresh", not by `quality:measured`.
     */
    tonightRanking: (planningTime: string | null) =>
        [...TONIGHT_RANKING_QUERY_KEY_PREFIX, { at: planningTime }] as const,

    /**
     * The key of an `advisory:result` call.
     * @param targetId The target the result belongs to.
     * @param objectId The star or object the result is about.
     * @return The key, with `target_id`.
     */
    resultStatements: (targetId: string, objectId: string) =>
        [...QUALITY_QUERY_KEY_PREFIX, 'advisory-result', { target_id: targetId, object_id: objectId }] as const,

    /**
     * The key of a `history:nights` call.
     * @param limit The most nights to list, or nothing for the default.
     * @return The key. It names no night, so every measurement refreshes it.
     */
    nights: (limit?: number) => [...QUALITY_QUERY_KEY_PREFIX, 'history-nights', { limit: limit ?? null }] as const,

    /**
     * The key of a `history:night_quality` call.
     * @param observingNightId The night, which is also the call's `session_id`.
     * @return The key, with `observing_night_id`.
     */
    nightQuality: (observingNightId: string) =>
        [...QUALITY_QUERY_KEY_PREFIX, 'history-night-quality', { observing_night_id: observingNightId }] as const,

    /**
     * The key of a `history:target_changes` call.
     * @param observingNightId The night to list, or nothing for the library's choice.
     * @return The key, with `observing_night_id` when a night is given.
     */
    targetChanges: (observingNightId?: string) =>
        [
            ...QUALITY_QUERY_KEY_PREFIX,
            'history-target-changes',
            observingNightId ? { observing_night_id: observingNightId } : {},
        ] as const,

    /**
     * The key of a `history:live_session_status` call.
     * @return The key. It names no target or night, so every measurement refreshes it.
     */
    liveSessionStatus: () => [...QUALITY_QUERY_KEY_PREFIX, 'history-live-status', {}] as const,
};
