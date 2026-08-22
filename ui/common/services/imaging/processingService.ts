/**
 * Service for managing image processing, log tailing, and variability analysis actions via the JSON-RPC backend api.
 */

import { callBackend } from '../backendApi';
import { ProcessStatus, AnalysisResult, ProcessingJob } from '../../types/backendTypes';

/**
 * Initiates image processing (stacking) for a target.
 */
export async function processTarget(
    objectId: string,
    imageFiles?: string[],
    logFile?: string
): Promise<ProcessStatus | null> {
    try {
        const result = await callBackend('processing:stack', {
            target_id: objectId,
            image_files: imageFiles,
            log_file: logFile
        });
        return result as ProcessStatus;
    } catch {
        return null;
    }
}

/**
 * Fetches all active processing jobs.
 */
export async function fetchAllProcesses(): Promise<ProcessStatus[]> {
    try {
        const result = await callBackend('processing:active_jobs', {});
        return (result || []) as ProcessStatus[];
    } catch {
        return [];
    }
}

/**
 * Checks if a processing job is active for a target.
 */
export async function isProcessing(objectId: string): Promise<boolean> {
    try {
        const result = await callBackend('processing:status', { target_id: objectId });
        return result?.processing ?? false;
    } catch {
        return false;
    }
}

/**
 * Requests cancellation of a processing job.
 */
export async function cancelProcessing(
    objectId: string
): Promise<ProcessStatus | null> {
    try {
        const result = await callBackend('processing:cancel', { target_id: objectId });
        return result as ProcessStatus;
    } catch {
        return null;
    }
}

/**
 * Initiates scientific image analysis (astrometry, photometry, or spectroscopy) for a target.
 */
export async function analyzeImage(
    objectId: string,
    imageFiles?: string[],
    filterType?: string,
    type: string = 'photometry'
): Promise<AnalysisResult | null> {
    try {
        const result = await callBackend('analysis:analyze_image', {
            target_id: objectId,
            image_files: imageFiles,
            filter_type: filterType,
            type: type
        });
        return result || null;
    } catch {
        return null;
    }
}

export const analyzeTarget = (
    objectId: string,
    imageFiles?: string[],
    filterType?: string
) => analyzeImage(objectId, imageFiles, filterType, 'photometry');


/**
 * Fetches the results of variability analysis.
 */
export async function fetchAnalysisResults(
    objectId: string
): Promise<AnalysisResult | null> {
    try {
        const result = await callBackend('analysis:get_results', { target_id: objectId });
        return result as AnalysisResult;
    } catch {
        return null;
    }
}


// useStackingJob fires two independent effects that both call fetchJobs() for
// the same target as soon as it's selected (job-history load + process-status
// check); coalesce same-target/jobType calls into one shared in-flight request.
const inFlightJobsRequests = new Map<string, Promise<ProcessingJob[]>>();

/**
 * Fetches jobs from the registry, optionally filtered by target and job type.
 *
 * Concurrent calls with the same targetId/jobType are coalesced into a single
 * shared in-flight request rather than issuing duplicate backend calls — e.g.
 * useStackingJob fires two independent effects that both call fetchJobs() for
 * the same target as soon as it's selected.
 *
 * @param {string} [targetId] - Restricts results to jobs for this target, if provided.
 * @param {string} [jobType] - Restricts results to jobs of this type, if provided.
 * @returns {Promise<ProcessingJob[]>} The matching processing jobs.
 */
export function fetchJobs(targetId?: string, jobType?: string): Promise<ProcessingJob[]> {
    const key = `${targetId ?? ''}::${jobType ?? ''}`;
    const inFlight = inFlightJobsRequests.get(key);
    if (inFlight) return inFlight;

    const request = (async () => {
        try {
            const result = await callBackend('processing:list_jobs', { target_id: targetId, job_type: jobType });
            return (result || []) as ProcessingJob[];
        } catch {
            return [];
        }
    })().finally(() => {
        inFlightJobsRequests.delete(key);
    });

    inFlightJobsRequests.set(key, request);
    return request;
}

/**
 * Dismisses (deletes) a job record from history.
 */
export async function dismissJob(jobId: string): Promise<boolean> {
    try {
        const result = await callBackend('processing:delete_job', { job_id: jobId });
        return !!result;
    } catch {
        return false;
    }
}

/**
 * Fetches the tail of logs for a specific job ID.
 */
export async function fetchJobLogTail(jobId: string, lines: number = 100): Promise<string[]> {
    try {
        const result = await callBackend('processing:job_log_tail', { job_id: jobId, lines });
        return (result || []) as string[];
    } catch {
        return [];
    }
}

/**
 * Streams the tail of logs for a specific job ID by polling the DB-backed
 * job_log_tail endpoint, which is scoped strictly to this job_id and cannot
 * be cross-contaminated by unrelated logging elsewhere in the process.
 */
export async function streamJobLog(
    jobId: string,
    onChunk: (line: string) => void
): Promise<() => void> {
    let cancelled = false;
    let lastSeenLine = '';

    const poll = async () => {
        if (cancelled) return;
        try {
            const lines = await fetchJobLogTail(jobId, 2000);
            if (!cancelled && lines.length > 0) {
                if (!lastSeenLine) {
                    for (const line of lines) {
                        onChunk(line);
                    }
                    lastSeenLine = lines[lines.length - 1];
                } else {
                    const idx = lines.lastIndexOf(lastSeenLine);
                    const newLines = idx !== -1 ? lines.slice(idx + 1) : lines;
                    for (const line of newLines) {
                        onChunk(line);
                    }
                    if (lines.length > 0) {
                        lastSeenLine = lines[lines.length - 1];
                    }
                }
            }
        } catch {
            // Silently ignore log fetch failure during transient state
        }
        if (!cancelled) {
            setTimeout(poll, 1500);
        }
    };

    poll();

    return (): void => {
        cancelled = true;
    };
}

/**
 * Requests the backend to launch the Siril GUI for a specific target.
 * @param targetId The unique identifier of the target.
 * @return True if the request was successfully sent.
 */
export async function openSiril(targetId: string): Promise<boolean> {
    try {
        const result = await callBackend('processing:siril_open', { target_id: targetId });
        return !!result;
    } catch {
        return false;
    }
}
