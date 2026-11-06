/**
 * @fileoverview Service for the observatory-wide monitoring/controller mode
 * toggle -- the real backing for the retired "Safe Mode" config checkbox.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend, BulkDelegationOutcome } from './backendApi';

/**
 * Moves every hardware-facing capability toward DELEGATED ("monitoring
 * mode"): watches and computes against every capability, issuing nothing.
 * Always fully succeeds.
 * @param evidenceNote Optional human-readable note recorded on the transition.
 * @return Which capabilities reached DELEGATED (always all of them).
 */
export async function enterMonitoringMode(evidenceNote = ''): Promise<BulkDelegationOutcome> {
    return callBackend('observatory:enter_monitoring_mode', { evidence_note: evidenceNote });
}

/**
 * Moves every hardware-facing capability toward AUTHORITATIVE ("controller
 * mode"). A capability not yet eligible (a correction capability not
 * already shadowed, or capture before its dependencies are authoritative)
 * is reported in the result's `rejected` map rather than silently skipped
 * or forced -- callers must surface that to the user, not swallow it.
 * @param evidenceNote Optional human-readable note recorded on each
 *     successful transition.
 * @return Which capabilities reached AUTHORITATIVE, and why any that
 *     didn't were rejected.
 */
export async function enterControllerMode(evidenceNote = ''): Promise<BulkDelegationOutcome> {
    return callBackend('observatory:enter_controller_mode', { evidence_note: evidenceNote });
}

/** Where the app gets its observatory from. */
/** `offline` is a state, not a choice: the chosen simulator failed to start and the app has no telescope. */
export type ObservatorySource = 'real' | 'simulated' | 'offline';

/** What the backend reports about the observatory source. */
export interface ObservatorySourceState {
    /** The source this backend is running with. */
    source: ObservatorySource;
    /** The saved choice. It differs from `source` while a restart is pending. */
    chosen: ObservatorySource;
    /** The status chip text ("SIMULATOR", "SIMULATOR FAILED"), or an empty text for the real telescope. */
    label: string;
    /** True when the saved choice is not yet in force. */
    restart_pending: boolean;
    /** True while the simulator's stand-ins are starting. */
    starting: boolean;
    /** The current start-up step ("Starting telescope", "Seeding targets", "Ready", "Failed"), or an empty text. */
    step?: string;
    /** True when the app can be used (the simulated telescope is up, or it failed, or the source is real). */
    ready?: boolean;
    /** True when the simulator's stand-ins are running. */
    running: boolean;
    /** Why the simulator could not start, or null. */
    error: string | null;
    /**
     * True when the saved source is a simulator that failed to start, so the app is running on the
     * offline fallback. Older backends leave it out.
     */
    failed?: boolean;
    /** A short reason for `failed`. Older backends leave it out. */
    reason?: string;
    /** Programs the simulator needs that are not installed. */
    missing_programs: string[];
}

/**
 * Asks which observatory source the backend is using.
 * @return The source state.
 */
export async function getObservatorySource(): Promise<ObservatorySourceState> {
    return callBackend('observatory:get_source', {});
}

/**
 * Saves a new observatory source. The backend starts again to use it, so the caller must wait for it to come back.
 * @param source The new source.
 * @return Whether the backend is restarting, and the source it saved.
 */
export async function setObservatorySource(source: ObservatorySource): Promise<{ restarting: boolean; source: string }> {
    return callBackend('observatory:set_source', { source });
}

/** Seconds between looks at the backend while it restarts. */
const RESTART_POLL_MS = 1000;
/** Longest wait for the backend to come back, in milliseconds. */
const RESTART_TIMEOUT_MS = 180000;

/**
 * Waits until the backend has restarted and answers again.
 *
 * It first waits for the old backend to go away (a call fails), then for the new one to answer. If the old one
 * never goes away within the first seconds (nothing was restarting), it returns as soon as a call succeeds.
 * @param isRestarting True when the backend was told to restart.
 * @return True when the backend answers again, false if it did not within the time limit.
 */
export async function waitForBackendRestart(isRestarting: boolean): Promise<boolean> {
    const started = Date.now();
    let sawDown = !isRestarting;
    while (Date.now() - started < RESTART_TIMEOUT_MS) {
        await new Promise((resolve) => setTimeout(resolve, RESTART_POLL_MS));
        try {
            const state = await callBackend('observatory:get_source', {}, { silent: true, maxRetries: 0, timeoutMs: 3000 });
            if ((sawDown || Date.now() - started > 8000) && state.ready !== false) {
                // Back, and the simulated telescope (if any) is up or has failed.
                return Boolean(state);
            }
        } catch {
            sawDown = true;
        }
    }
    return false;
}
