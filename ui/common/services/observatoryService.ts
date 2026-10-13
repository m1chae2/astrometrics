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
