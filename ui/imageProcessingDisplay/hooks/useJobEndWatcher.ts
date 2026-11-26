/**
 * @file useJobEndWatcher.ts
 * @description Tells a screen when one job it started has ended, even if the job failed before the
 * app-wide list of running jobs ever showed it. That list only changes when a job is running at
 * the moment it is read, so a job that fails at once never appears there and the screen would wait
 * forever. This hook looks at the job itself, once right away and then at a steady pace. It is the
 * one place in Image Processing that follows a job to its end.
 */
import { useEffect, useRef } from 'react';
import { fetchJob } from '../../common/services/imaging/stackReviewService';
import type { ProcessingJob } from '../../common/types/backendTypes';
import { isJobEnded } from '../utils/jobStatus';

/** How long to wait between two looks at a running job, in milliseconds. */
export const JOB_END_POLL_INTERVAL_MS = 2000;

/**
 * Which job to watch: its id, or a function that finds the job (for a job that has no id yet).
 * A finder may return `null` or `undefined` while the job cannot be found; the hook looks again.
 * Keep a finder stable (for example with `useMemo`): a new function starts a new watch.
 */
export type JobLookup = string | (() => Promise<ProcessingJob | null | undefined>);

/**
 * Calls `onEnded` once with the job when the watched job has completed, failed or been
 * cancelled.
 *
 * @param lookup The job's id or a finder, or `null` to watch nothing.
 * @param onEnded Called with the ended job. The latest function is always used.
 */
export function useJobEndWatcher(
    lookup: JobLookup | null,
    onEnded: (job: ProcessingJob) => void,
): void {
    const onEndedRef = useRef(onEnded);
    useEffect(() => {
        onEndedRef.current = onEnded;
    });

    useEffect(() => {
        if (!lookup) return undefined;
        let stopped = false;
        let timer: ReturnType<typeof setTimeout> | undefined;

        const look = async () => {
            let job: ProcessingJob | null | undefined;
            try {
                job = typeof lookup === 'string' ? await fetchJob(lookup) : await lookup();
            } catch {
                job = null;
            }
            if (stopped) return;
            if (job && isJobEnded(job.status)) {
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
    }, [lookup]);
}
