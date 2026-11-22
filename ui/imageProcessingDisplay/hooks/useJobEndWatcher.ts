/**
 * @file useJobEndWatcher.ts
 * @description Tells a screen when one job it started has ended, even if the job failed before the
 * app-wide list of running jobs ever showed it. That list only changes when a job is running at
 * the moment it is read, so a job that fails at once never appears there and the screen would wait
 * forever. This hook looks at the job itself, once right away and then at a steady pace.
 */
import { useEffect, useRef } from 'react';
import { fetchJob } from '../../common/services/imaging/stackReviewService';
import type { ProcessingJob } from '../../common/types/backendTypes';

/** How long to wait between two looks at a running job, in milliseconds. */
export const JOB_END_POLL_INTERVAL_MS = 2000;

/** The job states that mean the job is over. */
const ENDED_STATUSES = new Set(['completed', 'failed', 'cancelled', 'canceled']);

/**
 * Calls `onEnded` once with the job when the job with the given id has completed, failed or
 * been cancelled.
 *
 * @param jobId The job to watch, or `null` to watch nothing.
 * @param onEnded Called with the ended job. The latest function is always used.
 */
export function useJobEndWatcher(
    jobId: string | null,
    onEnded: (job: ProcessingJob) => void,
): void {
    const onEndedRef = useRef(onEnded);
    useEffect(() => {
        onEndedRef.current = onEnded;
    });

    useEffect(() => {
        if (!jobId) return undefined;
        let stopped = false;
        let timer: ReturnType<typeof setTimeout> | undefined;

        const look = async () => {
            let job: ProcessingJob | null;
            try {
                job = await fetchJob(jobId);
            } catch {
                job = null;
            }
            if (stopped) return;
            if (job && ENDED_STATUSES.has(job.status)) {
                onEndedRef.current(job);
                return;
            }
            timer = setTimeout(look, JOB_END_POLL_INTERVAL_MS);
        };
        void look();
        return () => {
            stopped = true;
            if (timer) clearTimeout(timer);
        };
    }, [jobId]);
}
