/**
 * @file announceFinishedStack.ts
 * @description Tells the rest of the app that a stacking job made a new stack. A restack writes the
 * same stack path as before, so a viewer that shows that stack must be told to read it again. This
 * file sends that news once per finished job, whichever part of the screen noticed the end first.
 */
import {
    fetchJob,
    fetchStackSummary,
    STACK_PICTURE_REMADE_EVENT,
} from '../../common/services/imaging/stackReviewService';
import type { StackKind } from '../../common/services/imaging/stackReviewService';
import type { ProcessingJob } from '../../common/types/backendTypes';
import { emit as emitEvent } from '../../common/utils/eventBus';
import { isJobEnded } from './jobStatus';

/** The stack kinds a stacking job can make. */
const STACK_KINDS: StackKind[] = ['imaging', 'spectral'];

/** The jobs already announced (or being announced), so a job is never announced twice. */
const announcedJobIds = new Set<string>();

/** Forgets every announced job. Used by tests. */
export function resetAnnouncedStackJobs(): void {
    announcedJobIds.clear();
}

/**
 * Reads the path of one kind of stack from the saved stack summary.
 *
 * @param targetId The target.
 * @param kind The imaging or spectral stack.
 * @return The stack's path, or `null` when the target has no such stack.
 */
async function readStackPath(targetId: string, kind: StackKind): Promise<string | null> {
    try {
        const summary = await fetchStackSummary(targetId, kind);
        return summary?.stack_path ?? null;
    } catch {
        return null;
    }
}

/**
 * Announces a finished stacking job once. A job that completed makes the library data refresh
 * (`targetsUpdated`) and tells each viewer showing one of its stacks to read it again. A job that
 * failed or was cancelled announces nothing. A job that is still running is left for a later call.
 *
 * @param targetId The target the job stacked.
 * @param jobId The job's id.
 * @param knownJob The job as already read, or leave out to read it now.
 */
export async function announceFinishedStack(
    targetId: string,
    jobId: string,
    knownJob?: ProcessingJob | null,
): Promise<void> {
    if (announcedJobIds.has(jobId)) return;
    announcedJobIds.add(jobId);
    let job = knownJob ?? null;
    try {
        if (!job) job = await fetchJob(jobId);
    } catch {
        job = null;
    }
    if (!job || !isJobEnded(job.status)) {
        // Not over yet (or not readable): the next look may announce it.
        announcedJobIds.delete(jobId);
        return;
    }
    if (job.status !== 'completed') return;

    const paths = await Promise.all(STACK_KINDS.map((kind) => readStackPath(targetId, kind)));
    emitEvent('targetsUpdated', { id: targetId });
    for (const stackPath of paths) {
        if (stackPath) emitEvent(STACK_PICTURE_REMADE_EVENT, { targetId, stackPath });
    }
}
