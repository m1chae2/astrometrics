/**
 * @fileoverview Service for wayfindinglib's Observation Execution facade.
 * Aligns with the Google TypeScript Style Guide.
 *
 * Covers the operations whose inputs are plain data. Session advancement,
 * meridian flips, and fault recovery take bundles of hardware-driving
 * callables and therefore run in-process on the backend rather than being
 * callable from here.
 */

import { callBackend, ObservationSessionSummary } from '../backendApi';
import { reportError } from '../../utils/reportError';

/**
 * Lists every persisted observation session, newest night first.
 * @return Session summaries, or an empty array on failure.
 */
export async function fetchObservationSessions(): Promise<ObservationSessionSummary[]> {
    try {
        return await callBackend('execution:list_sessions', {});
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to list observation sessions: ${msg}`), 'backend');
        return [];
    }
}

/**
 * Fetches one observation session in full.
 * @param sessionId Identifier of the session.
 * @return The session document, or null on failure.
 */
export async function fetchObservationSession(
    sessionId: string
): Promise<Record<string, any> | null> {
    try {
        return await callBackend('execution:get_session', { session_id: sessionId });
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to load session ${sessionId}: ${msg}`), 'backend');
        return null;
    }
}

/**
 * Aborts a session, skipping its remaining pending entries.
 * @param sessionId Identifier of the session to abort.
 * @param reason Operator-supplied reason, recorded on the session.
 * @return The aborted session, or null on failure.
 */
export async function abortObservationSession(
    sessionId: string,
    reason: string
): Promise<Record<string, any> | null> {
    try {
        return await callBackend('execution:abort_session', { session_id: sessionId, reason });
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to abort session ${sessionId}: ${msg}`), 'backend');
        return null;
    }
}

/**
 * Runs post-session reconciliation and persists the results.
 * @param sessionId Identifier of the session to reconcile.
 * @return The reconciled session, or null on failure.
 */
export async function reconcileObservationSession(
    sessionId: string
): Promise<Record<string, any> | null> {
    try {
        return await callBackend('execution:reconcile_session', { session_id: sessionId });
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to reconcile session ${sessionId}: ${msg}`), 'backend');
        return null;
    }
}
