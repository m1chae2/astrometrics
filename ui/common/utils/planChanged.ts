/**
 * @fileoverview What the app does when the backend sends `plan:changed`.
 *
 * It marks every cached plan read stale, so open plan screens read the plan
 * again, and tells the caller whether the change came from another window.
 * The event carries no sender, so a change counts as "from another window"
 * when the plan this window has cached is older than the announced revision.
 * A window that wrote the plan itself has already stored the newer plan.
 */

import type { QueryClient } from '@tanstack/react-query';
import { PLAN_QUERY_KEY_PREFIX } from '../queries/qualityQueryKeys';

/** The payload of the `plan:changed` event. */
export interface PlanChangedPayload {
    sessionId?: string;
    revision?: number;
    changed?: string[];
}

/** The text announced (politely) when another window changed the plan. */
export const PLAN_UPDATED_FROM_ANOTHER_WINDOW = 'Plan updated from another window';

/**
 * Refreshes the plan reads after a `plan:changed` event.
 * @param queryClient The app's query client.
 * @param payload The event payload.
 * @return `true` when the change looks like it came from another window.
 */
export function handlePlanChanged(queryClient: QueryClient, payload: PlanChangedPayload): boolean {
    const cachedRevision = queryClient
        .getQueryCache()
        .findAll({ queryKey: PLAN_QUERY_KEY_PREFIX })
        .map((query) => query.state.data as { id?: string; revision?: number } | undefined)
        .filter((plan) => plan && !Array.isArray(plan) && plan.id === payload.sessionId)
        .map((plan) => plan?.revision ?? 0)
        .reduce((newest, revision) => Math.max(newest, revision), -1);
    const fromAnotherWindow =
        typeof payload.revision === 'number' && cachedRevision >= 0 && cachedRevision < payload.revision;
    void queryClient.invalidateQueries({ queryKey: PLAN_QUERY_KEY_PREFIX });
    return fromAnotherWindow;
}
