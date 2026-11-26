/**
 * @fileoverview Service for managing library ingestion and re-indexing background tasks.
 * Calls backend JSON-RPC methods and maps jobs.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';

export interface IngestionJobStatus {
    status: 'running' | 'completed' | 'failed' | 'unknown' | 'started';
    progress: string;
    progressCurrent?: number;
    progressTotal?: number;
    message?: string;
    logs: string[];
    type: 'ingestion' | 'reindex';
}

/**
 * Starts a full library re-indexing background job.
 * @return The job ID.
 */
export async function startReindex(): Promise<string> {
    const data = await callBackend("ingestion:reindex", {});
    if (data) {
        return data;
    }
    throw new Error('Failed to start re-indexing');
}

/**
 * Fetches the current status of an ingestion or re-index job.
 * @param jobId The ID of the job to check.
 * @return The IngestionJobStatus object.
 */
export async function getIngestionJobStatus(jobId: string): Promise<IngestionJobStatus> {
    const answer = await callBackend("jobs:query", { job_id: jobId, detail: 'full' });
    const job = answer?.job;
    if (!job) {
        throw new Error('Failed to get job status');
    }
    return {
        status: job.status as IngestionJobStatus['status'],
        progress: `${job.progress_done}/${job.progress_total}`,
        progressCurrent: job.progress_done,
        progressTotal: job.progress_total,
        message: job.message || undefined,
        logs: [],
        type: job.job_type === 'reindex' ? 'reindex' : 'ingestion',
    };
}
