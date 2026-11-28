/**
 * @file test_stackFinishedAnnounce.test.tsx
 * @description A finished stacking job tells the viewers once, with the stack path; a failed job
 * tells nothing; a viewer showing the stack reads it again.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useStackingJob } from '../hooks/useStackingJob';
import { useViewerState } from '../hooks/useViewerState';
import { resetAnnouncedStackJobs } from '../utils/announceFinishedStack';
import { on } from '../../common/utils/eventBus';
import { createQueryClientTestWrapper } from '../../tests/queryClientTestWrapper';

const fetchJobMock = vi.fn();
const callBackendMock = vi.fn();
const fetchImageByPathMock = vi.fn();

vi.mock('../../common/services/imaging/stackReviewService', () => ({
    STACK_PICTURE_REMADE_EVENT: 'stackPictureRemade',
    fetchJob: (...args: unknown[]) => fetchJobMock(...args),
    // The summary reader goes through the backend mock, so each test sets its answer there.
    fetchStackSummary: (target: string, kind: string) =>
        callBackendMock('processing:stack_summary', { target, kind }, { silent: true }),
}));
vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/services/imaging/imageService', () => ({
    fetchLightFrame: vi.fn(),
    fetchImageByPath: (...args: unknown[]) => fetchImageByPathMock(...args),
}));
vi.mock('../../common/services/imaging/processingService', () => ({
    processTarget: vi.fn(async () => ({ jobId: 'job-1' })),
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
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ activeJobs: [] }),
}));

const jobWith = (status: string) => ({ id: 'job-1', targetId: 'M 81', jobType: 'stacking', status });

describe('a stacking job that ends', () => {
    let remade: unknown[];
    let updated: unknown[];

    beforeEach(() => {
        resetAnnouncedStackJobs();
        remade = [];
        updated = [];
        on('stackPictureRemade', (payload) => remade.push(payload));
        on('targetsUpdated', (payload) => updated.push(payload));
        callBackendMock.mockReset();
        callBackendMock.mockImplementation(async (_method: string, params: { kind: string }) =>
            params.kind === 'imaging' ? { stack_path: '/s/M_81_Stacked.fits' } : null);
    });

    /** A completed job announces its stack once. */
    it('announces the stack path once when the job completed', async () => {
        fetchJobMock.mockResolvedValue(jobWith('completed'));
        const { result } = renderHook(() => useStackingJob('M 81', true, false), { wrapper: createQueryClientTestWrapper().wrapper });
        await act(async () => { await result.current.startProcessing(); });
        await waitFor(() => expect(remade).toHaveLength(1));
        expect(remade[0]).toEqual({ targetId: 'M 81', stackPath: '/s/M_81_Stacked.fits' });
        expect(updated).toHaveLength(1);
    });

    /** A failed job announces nothing. */
    it('announces nothing when the job failed', async () => {
        fetchJobMock.mockResolvedValue(jobWith('failed'));
        const { result } = renderHook(() => useStackingJob('M 81', true, false), { wrapper: createQueryClientTestWrapper().wrapper });
        await act(async () => { await result.current.startProcessing(); });
        await waitFor(() => expect(result.current.isProcessing).toBe(false));
        expect(remade).toHaveLength(0);
        expect(updated).toHaveLength(0);
    });
});

describe('a viewer showing the restacked file', () => {
    /** The announcement makes the viewer fetch the same path again. */
    it('reads the stack again when the job is announced', async () => {
        resetAnnouncedStackJobs();
        callBackendMock.mockResolvedValue({ stack_path: '/s/M_81_Stacked.fits' });
        fetchJobMock.mockResolvedValue(jobWith('completed'));
        fetchImageByPathMock.mockResolvedValue({ imageData: 'data:image/jpeg;base64,AAAA', min: 0, max: 1 });
        const { result } = renderHook(() => useViewerState('M 81', [], '/s/M_81_Stacked.fits'));
        await waitFor(() => expect(result.current.imageUrl).toBe('data:image/jpeg;base64,AAAA'));
        fetchImageByPathMock.mockResolvedValue({ imageData: 'data:image/jpeg;base64,BBBB', min: 0, max: 1 });
        const { announceFinishedStack } = await import('../utils/announceFinishedStack');
        await act(async () => { await announceFinishedStack('M 81', 'job-1'); });
        await waitFor(() => expect(result.current.imageUrl).toBe('data:image/jpeg;base64,BBBB'));
    });
});
