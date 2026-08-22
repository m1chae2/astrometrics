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
    const data = await callBackend("processing:get_job", { job_id: jobId });
    if (data) {
        return data as unknown as IngestionJobStatus;
    }
    throw new Error('Failed to get job status');
}
