/**
 * @fileoverview Unit tests for polling an ingestion job's status.
 *
 * The backend raises `not_found` for a job it does not know. The UI shows
 * that as a failed job so the polling loop stops, as it did when the
 * backend answered with a "failed" status.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BackendError } from '../common/services/backendError';

const callBackend = vi.fn();

vi.mock('../common/services/backendApi', () => ({ callBackend }));

describe('fetchIngestStatus', () => {
  beforeEach(() => {
    callBackend.mockReset();
  });

  /** A known job's status is passed through, and the poll is silent. */
  it('returns the status of a known job', async () => {
    callBackend.mockResolvedValue({ status: 'running', progress: '40%', logs: ['a'] });
    const { fetchIngestStatus } = await import('../common/services/imaging/ingestionService');

    await expect(fetchIngestStatus('job1')).resolves.toEqual({ status: 'running', progress: '40%', logs: ['a'] });
    expect(callBackend).toHaveBeenCalledWith('ingestion:status', { job_id: 'job1' }, { silent: true });
  });

  /** An unknown job reads as failed, which stops the polling loop. */
  it('treats a job the backend cannot find as failed', async () => {
    callBackend.mockRejectedValue(new BackendError({ code: 'not_found', message: 'There is no ingestion job job1.' }));
    const { fetchIngestStatus } = await import('../common/services/imaging/ingestionService');

    await expect(fetchIngestStatus('job1')).resolves.toEqual({ status: 'failed', progress: '0%', logs: [] });
  });

  /** Any other failure is still thrown, so the poller can log it. */
  it('throws for other failures', async () => {
    callBackend.mockRejectedValue(new Error('network down'));
    const { fetchIngestStatus } = await import('../common/services/imaging/ingestionService');

    await expect(fetchIngestStatus('job1')).rejects.toThrow('Failed to fetch status');
  });
});
