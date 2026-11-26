/**
 * @file jobStatus.ts
 * @description Says whether a background job is over. The backend's job store writes one of four
 * states: `started`, `completed`, `failed` or `cancelled` (with two l's). The last three are final.
 */

/** The job states that mean the job is over. */
const ENDED_STATUSES: ReadonlySet<string> = new Set(['completed', 'failed', 'cancelled']);

/**
 * Tells whether a job state means the job is over.
 *
 * @param status The job's `status` field.
 * @return True for `completed`, `failed` and `cancelled`.
 */
export function isJobEnded(status: string | null | undefined): boolean {
    return status != null && ENDED_STATUSES.has(status);
}
