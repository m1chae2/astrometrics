/**
 * @fileoverview Tests that the job list, the one-job read, the log viewer's read and the reindex
 * progress all read the job history (`jobs:query`) and turn its answer into the app's records.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../common/services/backendApi', () => ({ callBackend: vi.fn() }));

import { callBackend } from '../common/services/backendApi';
import { BackendError } from '../common/services/backendError';
import { fetchJobLogTail, fetchJobs } from '../common/services/imaging/processingService';
import { fetchJob } from '../common/services/imaging/stackReviewService';
import { getIngestionJobStatus } from '../common/services/ingestionService';

/** One job as `jobs:query` sends it with `detail: 'full'`. */
const fullJob = {
    id: 'job-1', job_type: 'stacking', target_id: 'M 31', status: 'completed', progress_done: 7,
    progress_total: 7, message: 'Done', created_at: '2026-11-05T21:00:00', log_file_path: '/logs/job-1.log',
    input_metrics: { steps: ['stack'] }, output_metrics: { result: { stars: 42 } },
};

describe('job history reads', () => {
    beforeEach(() => {
        vi.mocked(callBackend).mockReset();
    });

    it('lists whole job records with paging before a time', async () => {
        vi.mocked(callBackend).mockResolvedValue({ detail: 'full', jobs: [fullJob] } as never);

        const jobs = await fetchJobs('M 31', undefined, { before: '2026-11-06T00:00:00', limit: 30 });

        expect(callBackend).toHaveBeenCalledWith('jobs:query', {
            target_id: 'M 31', job_type: undefined, detail: 'full', limit: 30, before: '2026-11-06T00:00:00',
        });
        expect(jobs[0]).toMatchObject({
            id: 'job-1', targetId: 'M 31', progressCurrent: 7, progressTotal: 7,
            inputMetrics: { steps: ['stack'] }, outputMetrics: { result: { stars: 42 } }, logFilePath: '/logs/job-1.log',
        });
    });

    it('asks for 50 jobs when no limit is given', async () => {
        vi.mocked(callBackend).mockResolvedValue({ detail: 'full', jobs: [] } as never);
        await fetchJobs('M 57');
        expect(callBackend).toHaveBeenCalledWith('jobs:query', expect.objectContaining({ limit: 50, detail: 'full' }));
    });

    it('reads one job with its stored result as an object, and null for an unknown job', async () => {
        vi.mocked(callBackend).mockResolvedValueOnce({ detail: 'full', job: fullJob } as never);
        expect((await fetchJob('job-1'))?.outputMetrics?.result).toEqual({ stars: 42 });

        vi.mocked(callBackend).mockRejectedValueOnce(new BackendError({ code: 'not_found', message: 'No job' } as never));
        expect(await fetchJob('missing')).toBeNull();
    });

    it('reads every log line whole and formats it for the log viewer', async () => {
        vi.mocked(callBackend).mockResolvedValue({
            detail: 'log_tail', log_lines: [{ time: '10:00', level: 'INFO', text: 'Stage 1 of 7' }],
        } as never);

        const lines = await fetchJobLogTail('job-1', 5000);

        expect(callBackend).toHaveBeenCalledWith(
            'jobs:query', { job_id: 'job-1', detail: 'log_tail', lines: 5000, max_line_chars: null }, { silent: true });
        expect(lines).toEqual(['10:00 - INFO - Stage 1 of 7']);
    });

    it('gives the reindex progress as numbers and text', async () => {
        vi.mocked(callBackend).mockResolvedValue({
            detail: 'full', job: { ...fullJob, job_type: 'reindex', status: 'running', progress_done: 3, progress_total: 9 },
        } as never);

        const status = await getIngestionJobStatus('job-1');

        expect(status).toMatchObject({ status: 'running', progress: '3/9', progressCurrent: 3, type: 'reindex' });
    });
});
