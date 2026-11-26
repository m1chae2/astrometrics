/**
 * @fileoverview Tests that the app-wide active-jobs feed reads the job history (`jobs:query`), leaves out
 * a job whose program stopped (the history marks it stale), and keeps the progress as two numbers.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../common/services/backendApi', () => ({ callBackend: vi.fn() }));

import { callBackend } from '../common/services/backendApi';
import { fetchAllProcesses } from '../common/services/imaging/processingService';

describe('fetchAllProcesses', () => {
  beforeEach(() => {
    vi.mocked(callBackend).mockReset();
  });

  it('asks the job history for the unfinished jobs and keeps the live ones', async () => {
    vi.mocked(callBackend).mockResolvedValue({
      detail: 'summary',
      jobs: [
        { id: 'a', job_type: 'stacking', target_id: 'M 31', status: 'running', progress_done: 3, progress_total: 10, is_active: true },
        { id: 'b', job_type: 'analysis', target_id: 'M 57', status: 'started', progress_done: 0, progress_total: 100, is_active: false, looks_stale: true },
      ],
    } as never);

    const jobs = await fetchAllProcesses();

    expect(callBackend).toHaveBeenCalledWith('jobs:query', { active_only: true, limit: 50 });
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({ id: 'a', jobType: 'stacking', targetId: 'M 31', progressCurrent: 3, progressTotal: 10 });
  });

  it('answers an empty list when the backend cannot be reached', async () => {
    vi.mocked(callBackend).mockRejectedValue(new Error('down'));
    expect(await fetchAllProcesses()).toEqual([]);
  });
});
