/**
 * @file test_stackRefused.test.tsx
 * @description A Stack Frames click that the backend refuses (for example frames that mix imaging
 * and spectral frames) starts no job: the busy state ends. `callBackend` shows the message as a
 * warning, and the shared `reportError` logs a refusal with `console.warn`, never `console.error`.
 * Other failures keep their `console.error`.
 */
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { reportError } from '../../common/utils/reportError';
import { useStackingJob } from '../hooks/useStackingJob';

import { BackendError } from '../../common/services/backendError';

const refusal = new BackendError({
    code: 'invalid_argument',
    message: 'These frames mix imaging and spectral frames (3 imaging, 1 spectral). Select only one kind and stack again.',
});

vi.mock('../../common/services/imaging/stackReviewService', () => ({ fetchJob: vi.fn() }));
vi.mock('../../common/services/imaging/processingService', () => ({
    processTarget: vi.fn(async () => { throw refusal; }),
    cancelProcessing: vi.fn(),
    fetchAllProcesses: vi.fn(),
    fetchJobs: vi.fn(async () => []),
    fetchJobLogTail: vi.fn(async () => []),
    streamJobLog: vi.fn(async () => vi.fn()),
    openSiril: vi.fn(),
}));
vi.mock('../../common/services/targetService', () => ({ fetchFrameStatsGrouped: vi.fn(async () => []) }));
vi.mock('../../common/utils/emitToast', () => ({ emitToast: vi.fn() }));
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({ framesReloadKey: 0, invalidate: vi.fn() }),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ activeJobs: [] }),
}));

describe('a refused Stack Frames click', () => {
    /** The refusal is logged as a warning, never console.error, and no job is watched. */
    it('shows the refusal and starts no job', async () => {
        const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
        const warnLog = vi.spyOn(console, 'warn').mockImplementation(() => {});
        const { result } = renderHook(() => useStackingJob('M 57', true, false));
        await act(async () => { await result.current.startProcessing(['/a.fits', '/b.fits']); });
        expect(errorLog).not.toHaveBeenCalled();
        expect(warnLog).toHaveBeenCalled();
        errorLog.mockRestore();
        warnLog.mockRestore();
        expect(result.current.isProcessing).toBe(false);
        expect(result.current.activeJobId).toBeNull();
    });

    /** Any other failure still logs console.error and shows its toast. */
    it('keeps console.error for other failures', () => {
        const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
        reportError(new Error('disk full'), 'test');
        expect(errorLog).toHaveBeenCalled();
        errorLog.mockRestore();
    });
});
