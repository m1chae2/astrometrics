/**
 * @file useReviewJob.ts
 * @description Runs one slow stack check (a preview remake or a stack comparison) and follows
 * it to the end. The backend answers a start with the finished result, or with the job to
 * follow. This hook polls that job until it completes or fails, and hands the result back.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useOnValueChange } from './useOnValueChange';
import { fetchJob } from '../../common/services/imaging/stackReviewService';
import { fetchJobs } from '../../common/services/imaging/processingService';
import type { ProcessingJob, StackReviewStart } from '../../common/types/backendTypes';

/** How long to wait between two looks at a running job, in milliseconds. */
export const JOB_POLL_INTERVAL_MS = 2000;

/** Where a check stands. */
export type ReviewJobPhase = 'idle' | 'starting' | 'running' | 'done' | 'failed';

/** What the hook reports about one check. */
export interface ReviewJobState<T> {
    phase: ReviewJobPhase;
    /** The result, once the phase is `done`. */
    result: T | null;
    /** Why the check failed, once the phase is `failed`. */
    message: string | null;
}

/** What the hook needs to know about the kind of job it follows. */
export interface ReviewJobOptions<T> {
    /** The target the job works on. */
    targetId: string;
    /** The job type the backend records, used to find a job that has no id yet. */
    jobType: string;
    /** Reads the result out of a finished job. */
    readResult: (job: ProcessingJob) => T | null;
}

const IDLE = { phase: 'idle', result: null, message: null } as const;

/**
 * Follows one slow stack check.
 * @param options The target, the job type and how to read the job's result.
 * @return The state, a function that starts a check, and a function that clears the state.
 */
export function useReviewJob<T>(options: ReviewJobOptions<T>) {
    const [state, setState] = useState<ReviewJobState<T>>(IDLE);
    const [watch, setWatch] = useState<{ jobId: string | null; startedAt: number } | null>(null);
    const optionsRef = useRef(options);
    useEffect(() => {
        optionsRef.current = options;
    });

    // Forget the last check when another target is selected.
    useOnValueChange(options.targetId, () => {
        setState(IDLE);
        setWatch(null);
    });

    // Poll a running job until it ends.
    useEffect(() => {
        if (!watch) return undefined;
        let stopped = false;
        let timer: ReturnType<typeof setTimeout> | undefined;

        const look = async () => {
            let job: ProcessingJob | null | undefined;
            try {
                if (watch.jobId) {
                    job = await fetchJob(watch.jobId);
                } else {
                    // The job has no id yet (it may be waiting for another job).
                    // The newest job of this type that began after the click is ours.
                    const jobs = await fetchJobs(optionsRef.current.targetId, optionsRef.current.jobType);
                    job = jobs.find((candidate) => {
                        const created = candidate.createdAt ? Date.parse(candidate.createdAt) : NaN;
                        return !Number.isNaN(created) && created >= watch.startedAt - JOB_POLL_INTERVAL_MS;
                    });
                }
            } catch {
                job = undefined;
            }
            if (stopped) return;
            if (job && job.status === 'completed') {
                setState({ phase: 'done', result: optionsRef.current.readResult(job), message: null });
                setWatch(null);
                return;
            }
            if (job && job.status === 'failed') {
                setState({ phase: 'failed', result: null, message: job.message ?? 'The job failed.' });
                setWatch(null);
                return;
            }
            timer = setTimeout(look, JOB_POLL_INTERVAL_MS);
        };
        timer = setTimeout(look, JOB_POLL_INTERVAL_MS);
        return () => {
            stopped = true;
            if (timer) clearTimeout(timer);
        };
    }, [watch]);

    const run = useCallback(async (starter: () => Promise<StackReviewStart>) => {
        setState({ phase: 'starting', result: null, message: null });
        setWatch(null);
        const startedAt = Date.now();
        try {
            const reply = await starter();
            if (reply.status === 'completed') {
                setState({ phase: 'done', result: (reply.result ?? null) as T | null, message: null });
            } else {
                setState({ phase: 'running', result: null, message: null });
                setWatch({ jobId: reply.job_id ?? null, startedAt });
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            setState({ phase: 'failed', result: null, message });
        }
    }, []);

    const reset = useCallback(() => {
        setState(IDLE);
        setWatch(null);
    }, []);

    return { state, run, reset };
}
