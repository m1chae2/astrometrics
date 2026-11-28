/**
 * @file test_jobFailsAtOnce.test.tsx
 * @description A stacking or analysis job that fails before the running-jobs feed ever lists it
 * must end the busy state and show the job's own error message.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useStackingJob } from '../hooks/useStackingJob';
import { useAnalysisJob } from '../hooks/useAnalysisJob';
import { createQueryClientTestWrapper } from '../../tests/queryClientTestWrapper';

const failedJob = {
    id: 'job-1', targetId: 'M 52', jobType: 'stacking', status: 'failed',
    message: "cannot import name 'background_model_path_for'",
};
const fetchJobMock = vi.fn();

vi.mock('../../common/services/imaging/stackReviewService', () => ({
    fetchJob: (...args: unknown[]) => fetchJobMock(...args),
}));
vi.mock('../../common/services/imaging/processingService', () => ({
    processTarget: vi.fn(async () => ({ jobId: 'job-1' })),
    analyzeTarget: vi.fn(async () => ({ jobId: 'job-1' })),
    fetchAnalysisResults: vi.fn(async () => null),
    cancelProcessing: vi.fn(),
    fetchAllProcesses: vi.fn(),
    fetchJobs: vi.fn(async () => []),
    fetchJobLogTail: vi.fn(async () => []),
    streamJobLog: vi.fn(async () => vi.fn()),
    openSiril: vi.fn(),
}));
vi.mock('../../common/services/targetService', () => ({ requestFrameStatsGrouped: vi.fn(async () => []) }));
vi.mock('../../common/utils/emitToast', () => ({ emitToast: vi.fn() }));
vi.mock('../../common/utils/reportError', () => ({ reportError: vi.fn() }));
const invalidateStable = vi.fn();
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({ framesReloadKey: 0, invalidate: invalidateStable }),
}));
// The feed never lists the job: it failed before the feed was read.
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ activeJobs: [] }),
}));

describe('a job that fails at once', () => {
    beforeEach(() => {
        fetchJobMock.mockReset();
        fetchJobMock.mockResolvedValue(failedJob);
    });

    /** Stacking: busy ends and the message is kept. */
    it('ends the stacking busy state and reports the error', async () => {
        const { result } = renderHook(() => useStackingJob('M 52', true, false), { wrapper: createQueryClientTestWrapper().wrapper });
        await act(async () => { await result.current.startProcessing(); });
        await waitFor(() => expect(result.current.isProcessing).toBe(false));
        expect(result.current.processingError).toContain('background_model_path_for');
    });

    /** Analysis: busy ends and the message is kept. */
    it('ends the analysis busy state and reports the error', async () => {
        const onLog = vi.fn();
        const { result } = renderHook(() => useAnalysisJob('M 52', true, onLog));
        await act(async () => { await result.current.startAnalysis(); });
        await waitFor(() => expect(result.current.isAnalyzing).toBe(false));
        expect(result.current.analysisError).toContain('background_model_path_for');
    });
});
