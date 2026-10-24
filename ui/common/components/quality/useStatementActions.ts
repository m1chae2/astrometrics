/**
 * @fileoverview The actions a quality statement can offer: Evidence, Send to
 * plan and Show in calibration library.
 *
 * "Evidence" and "Show in calibration library" ask another display to open
 * (through `navigateToElement`). "Send to plan" passes the statement to the
 * library, which decides what it becomes (a queue entry or a checklist item),
 * merges repeats and rejects what the camera cannot take. The UI does not
 * turn a statement into a plan entry itself.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { BackendError } from '../../services/backendError';
import { callBackend } from '../../services/backendApi';
import { planQueryKeys } from '../../queries/qualityQueryKeys';
import { navigateToElement } from '../../utils/displayCoordinator';
import type { PlanChange, TargetStatement } from '../../types/backendTypes';

/**
 * Whether the library can take a statement into a plan yet. Wave L2 adds the
 * parameter on `plan:edit` and sets this to true.
 */
export const SEND_TO_PLAN_AVAILABLE = false;

/** The visible reason shown while Send to plan is not available. */
export const SEND_TO_PLAN_UNAVAILABLE_TEXT = 'Send to plan arrives with the library plan update';

/** The mode name of the planner. Change to "Observation Planner" when it is renamed. */
export const OBSERVATION_PLANNER_MODE = 'Observation Manager';

/** The result of one Send to plan. */
export type SendToPlanOutcome =
    | { kind: 'sending' }
    | { kind: 'added'; detail: string }
    | { kind: 'merged'; detail: string }
    | { kind: 'rejected'; reason: string }
    | { kind: 'conflict' }
    | { kind: 'noPlan' };

/** The controller a statement uses to show and run its actions. */
export interface StatementActions {
    /** Whether Send to plan can run. */
    sendToPlanAvailable: boolean;
    /** The outcome of each statement's Send to plan, by statement key. */
    outcomes: Record<string, SendToPlanOutcome>;
    /** Opens the cited frames in the Image Viewer. */
    showFrames: (rowIds: number[], statementText: string) => void;
    /** Opens a calibration row in the Observatory Manager. */
    showCalibrationRow: (statement: TargetStatement) => void;
    /** Sends a statement to the plan. */
    sendToPlan: (statementKey: string, statement: TargetStatement) => Promise<void>;
    /** Makes a plan, then sends the statement. */
    createPlanAndSend: (statementKey: string, statement: TargetStatement) => Promise<void>;
    /** Clears an outcome (the user dismissed it). */
    dismissOutcome: (statementKey: string) => void;
}

/** Parameters of `plan:edit` once the library takes statements (wave L2). */
type PlanEditWithStatements = {
    session_id: string;
    add_statements: TargetStatement[];
    expected_revision?: number;
};

/**
 * Builds the actions for the statements of one target.
 * @param targetId The target the statements are about.
 * @param sendToPlanAvailable Whether the library takes statements. Defaults to the constant.
 * @return The action controller.
 */
export function useStatementActions(
    targetId: string,
    sendToPlanAvailable: boolean = SEND_TO_PLAN_AVAILABLE,
): StatementActions {
    const queryClient = useQueryClient();
    const [outcomes, setOutcomes] = useState<Record<string, SendToPlanOutcome>>({});

    const setOutcome = useCallback((key: string, outcome: SendToPlanOutcome | null) => {
        setOutcomes((previous) => {
            const next = { ...previous };
            if (outcome === null) delete next[key];
            else next[key] = outcome;
            return next;
        });
    }, []);

    const showFrames = useCallback(
        (rowIds: number[], statementText: string) => {
            void navigateToElement({
                targetDisplay: 'Image Viewer',
                targetElement: 'frameList',
                action: 'showFrames',
                payload: { rowIds, targetId, statementText },
            });
        },
        [targetId],
    );

    const showCalibrationRow = useCallback((statement: TargetStatement) => {
        void navigateToElement({
            targetDisplay: 'Observatory Manager',
            targetElement: 'calibrationLibrary',
            action: 'showCalibrationRow',
            payload: {
                calibrationKey: statement.proposedAction?.calibrationFrameType ?? null,
                captureSetting: statement.captureSetting ?? null,
                rule: statement.rule,
            },
            focus: { scrollToElement: true, flashHighlight: true },
        });
    }, []);

    const runSend = useCallback(
        async (key: string, statement: TargetStatement, planId: string | null) => {
            setOutcome(key, { kind: 'sending' });
            try {
                let sessionId = planId;
                if (!sessionId) {
                    const plans = await callBackend('plan:get', {});
                    const list = Array.isArray(plans) ? plans : [];
                    if (list.length === 0) {
                        setOutcome(key, { kind: 'noPlan' });
                        return;
                    }
                    sessionId = list[0].id;
                }
                const session = await callBackend('plan:get', { session_id: sessionId });
                const revision = Array.isArray(session) ? undefined : session.revision;
                const parameters: PlanEditWithStatements = {
                    session_id: sessionId,
                    add_statements: [statement],
                    expected_revision: revision,
                };
                const change: PlanChange = await callBackend(
                    'plan:edit',
                    parameters as unknown as Parameters<typeof callBackend<'plan:edit'>>[1],
                );
                const items = change.changes ?? [];
                const detail = items.map((item) => item.detail || item.kind).join('; ');
                const merged = items.some((item) => item.kind.includes('merge'));
                void queryClient.invalidateQueries({ queryKey: planQueryKeys.list() });
                void queryClient.invalidateQueries({ queryKey: planQueryKeys.session(sessionId) });
                setOutcome(key, merged ? { kind: 'merged', detail } : { kind: 'added', detail });
            } catch (error) {
                if (error instanceof BackendError && error.code === 'conflict') {
                    setOutcome(key, { kind: 'conflict' });
                } else {
                    const reason = error instanceof Error ? error.message : 'The plan did not accept the statement.';
                    setOutcome(key, { kind: 'rejected', reason });
                }
            }
        },
        [queryClient, setOutcome],
    );

    const sendToPlan = useCallback(
        async (key: string, statement: TargetStatement) => {
            if (!sendToPlanAvailable) return;
            await runSend(key, statement, null);
        },
        [runSend, sendToPlanAvailable],
    );

    const createPlanAndSend = useCallback(
        async (key: string, statement: TargetStatement) => {
            if (!sendToPlanAvailable) return;
            setOutcome(key, { kind: 'sending' });
            try {
                const created = await callBackend('plan:create', {});
                await runSend(key, statement, created.id);
            } catch (error) {
                const reason = error instanceof Error ? error.message : 'The plan could not be created.';
                setOutcome(key, { kind: 'rejected', reason });
            }
        },
        [runSend, sendToPlanAvailable, setOutcome],
    );

    const dismissOutcome = useCallback((key: string) => setOutcome(key, null), [setOutcome]);

    return {
        sendToPlanAvailable,
        outcomes,
        showFrames,
        showCalibrationRow,
        sendToPlan,
        createPlanAndSend,
        dismissOutcome,
    };
}
