/**
 * @fileoverview The stored night plan for the Observation Planner: the list of
 * plans, the plan the user picked, and the writes (create, edit, tick).
 *
 * The plans live in the library. This hook reads them with `plan:get` and
 * writes with `plan:create`, `plan:edit` and `plan:tick_checklist_item`. Every
 * edit sends the revision it was based on. When another window changed the plan
 * first, the library answers with a `conflict` error; the hook then reads the
 * plan again and tells the user. It decides nothing about the plan itself.
 */

import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import type { ObservationSessionSummary } from '../../common/services/backendApi';
import { BackendError } from '../../common/services/backendError';
import { planQueryKeys } from '../../common/queries/qualityQueryKeys';
import type { ExposureRequest, ObservationSession, PlanChange } from '../../common/types/backendTypes';

/** The text shown when another window changed the plan first. */
export const PLAN_CONFLICT_TEXT = 'The plan changed in another window; reloaded. Make your change again.';

/** A message about the last plan write that stays until the user dismisses it. */
export interface PlanNotice {
    /** `conflict` when the revision was old; `rejected` when the library refused the write. */
    kind: 'conflict' | 'rejected';
    /** The words to show. For `rejected` these are the library's own reason. */
    text: string;
}

/** The parts of a plan edit the Planner makes. */
export interface PlanEdit {
    /** Every entry id left after `remove`, in the new order. */
    order?: string[];
    /** Entry ids to take out of the queue. */
    remove?: string[];
    /** New exposure requests, by entry id. */
    set_exposures?: Record<string, ExposureRequest[]>;
}

/** What `useNightPlan` returns. */
export interface NightPlanController {
    /** The plans the library listed, in the order returned. */
    plans: ObservationSessionSummary[];
    /** True while the list loads. */
    plansLoading: boolean;
    /** True when the list could not be read. */
    plansFailed: boolean;
    /** Reads the list again. */
    reloadPlans: () => void;
    /** The id of the plan shown, or `null` when there is none. */
    planId: string | null;
    /** Shows another plan. */
    pickPlan: (planId: string) => void;
    /** The shown plan, or `undefined` while it loads. */
    session: ObservationSession | undefined;
    /** True while the shown plan loads. */
    sessionLoading: boolean;
    /** True when the shown plan could not be read. */
    sessionFailed: boolean;
    /** Reads the shown plan again. */
    reloadSession: () => void;
    /** True while a write is running. */
    busy: boolean;
    /** The last write's refusal or conflict, until dismissed. */
    notice: PlanNotice | null;
    /** Dismisses the notice. */
    dismissNotice: () => void;
    /** Makes a plan. */
    createPlan: () => Promise<void>;
    /** Edits the queue. Returns `true` when the library accepted it. */
    editQueue: (edit: PlanEdit) => Promise<boolean>;
    /** Ticks a checklist item. Returns `true` when the library accepted it. */
    tickItem: (itemId: string) => Promise<boolean>;
}

/**
 * Reads and writes the stored night plan.
 * @return The plan, the list of plans and the write functions.
 */
export function useNightPlan(): NightPlanController {
    const queryClient = useQueryClient();
    const [pickedPlanId, setPickedPlanId] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState<PlanNotice | null>(null);

    const plansQuery = useQuery({
        queryKey: planQueryKeys.list(),
        queryFn: async (): Promise<ObservationSessionSummary[]> => {
            const reply = await callBackend('plan:get', {});
            return Array.isArray(reply) ? reply : [];
        },
    });
    const plans = plansQuery.data ?? [];
    // With nothing picked yet, the first plan in the order returned is shown.
    const planId = pickedPlanId ?? plans[0]?.id ?? null;

    const sessionQuery = useQuery({
        queryKey: planQueryKeys.session(planId ?? ''),
        enabled: planId !== null,
        queryFn: async (): Promise<ObservationSession> => {
            const reply = await callBackend('plan:get', { session_id: planId as string });
            if (Array.isArray(reply)) throw new Error('The library returned a list instead of one plan.');
            return reply;
        },
    });

    /** Reads the shown plan again after a conflict. */
    const reloadAfterConflict = useCallback(() => {
        void queryClient.invalidateQueries({ queryKey: planQueryKeys.list() });
        if (planId) void queryClient.invalidateQueries({ queryKey: planQueryKeys.session(planId) });
    }, [queryClient, planId]);

    /** Runs one write and turns a failure into the notice. */
    const runWrite = useCallback(
        async (write: () => Promise<PlanChange>): Promise<boolean> => {
            setBusy(true);
            try {
                const change = await write();
                if (planId) queryClient.setQueryData(planQueryKeys.session(planId), change.session);
                void queryClient.invalidateQueries({ queryKey: planQueryKeys.list() });
                return true;
            } catch (error) {
                if (error instanceof BackendError && error.code === 'conflict') {
                    setNotice({ kind: 'conflict', text: PLAN_CONFLICT_TEXT });
                    reloadAfterConflict();
                } else {
                    const reason = error instanceof Error ? error.message : 'The plan did not accept the change.';
                    setNotice({ kind: 'rejected', text: reason });
                }
                return false;
            } finally {
                setBusy(false);
            }
        },
        [planId, queryClient, reloadAfterConflict],
    );

    const editQueue = useCallback(
        (edit: PlanEdit) => {
            if (!planId) return Promise.resolve(false);
            setNotice(null);
            return runWrite(() =>
                callBackend('plan:edit', {
                    session_id: planId,
                    expected_revision: sessionQuery.data?.revision,
                    ...edit,
                }),
            );
        },
        [planId, runWrite, sessionQuery.data?.revision],
    );

    const tickItem = useCallback(
        (itemId: string) => {
            if (!planId) return Promise.resolve(false);
            setNotice(null);
            return runWrite(() =>
                callBackend('plan:tick_checklist_item', {
                    session_id: planId,
                    item_id: itemId,
                    expected_revision: sessionQuery.data?.revision,
                }),
            );
        },
        [planId, runWrite, sessionQuery.data?.revision],
    );

    const createPlan = useCallback(async () => {
        setBusy(true);
        setNotice(null);
        try {
            const created = await callBackend('plan:create', {});
            // `plan:create` sends no `plan:changed` event, so the lists are refreshed here.
            queryClient.setQueryData(planQueryKeys.session(created.id), created);
            await queryClient.invalidateQueries({ queryKey: planQueryKeys.list() });
            setPickedPlanId(created.id);
        } catch (error) {
            const reason = error instanceof Error ? error.message : 'The plan could not be created.';
            setNotice({ kind: 'rejected', text: reason });
        } finally {
            setBusy(false);
        }
    }, [queryClient]);

    return {
        plans,
        plansLoading: plansQuery.isLoading,
        plansFailed: plansQuery.isError,
        reloadPlans: () => void plansQuery.refetch(),
        planId,
        pickPlan: setPickedPlanId,
        session: sessionQuery.data,
        sessionLoading: planId !== null && sessionQuery.isLoading,
        sessionFailed: sessionQuery.isError,
        reloadSession: () => void sessionQuery.refetch(),
        busy,
        notice,
        dismissNotice: () => setNotice(null),
        createPlan,
        editQueue,
        tickItem,
    };
}
