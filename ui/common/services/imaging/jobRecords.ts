/**
 * @file jobRecords.ts
 * @description Turns the answers of the job history read (`jobs:query`) into the forms the app
 * shows: a job record with its progress as two numbers, and a log line as one line of text.
 */

import type { JobLogLine, JobSummary, ProcessingJob } from '../../types/backendTypes';

/**
 * Turns one job from the job history read into the app's job record.
 * @param job A job as `jobs:query` lists it. With `detail: 'full'` it also carries the stored
 *   input and output numbers and the log file.
 * @return The same job in the app's record form.
 */
export function toProcessingJob(job: JobSummary): ProcessingJob {
    return {
        id: job.id,
        targetId: job.target_id ?? '',
        jobType: job.job_type,
        status: job.status,
        progressCurrent: job.progress_done,
        progressTotal: job.progress_total,
        message: job.message ?? null,
        logFilePath: job.log_file_path ?? null,
        createdAt: job.created_at ?? null,
        updatedAt: job.updated_at ?? null,
        completedAt: job.completed_at ?? null,
        inputMetrics: job.input_metrics ?? null,
        outputMetrics: job.output_metrics ?? null,
    };
}

/**
 * Turns one line of a job log into the text the log viewer shows.
 * @param line A log line as `jobs:query` sends it.
 * @return The line as `<time> - <level> - <message>`.
 */
export function formatJobLogLine(line: JobLogLine): string {
    return `${line.time} - ${line.level} - ${line.text}`;
}
