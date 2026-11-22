/**
 * @file test_cancelJobs.test.tsx
 * @description The red Cancel button stops the job that is running: an analysis job through
 * `analysis:cancel`. A refusal shows the backend's reason. The button wiring is in test_cancelButton.
 */
import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAnalysisJob } from '../hooks/useAnalysisJob';
import { cancelAnalysis } from '../../common/services/imaging/processingService';
import { emitToast } from '../../common/utils/emitToast';
import { reportError } from '../../common/utils/reportError';
import { callBackend } from '../../common/services/backendApi';

vi.mock('../../common/services/backendApi', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../common/services/backendApi')>()),
    callBackend: vi.fn(),
}));
vi.mock('../../common/services/imaging/stackReviewService', () => ({ fetchJob: vi.fn(async () => null) }));
vi.mock('../../common/utils/emitToast', () => ({ emitToast: vi.fn() }));
vi.mock('../../common/utils/reportError', () => ({ reportError: vi.fn() }));
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ activeJobs: [] }),
}));

describe('the service', () => {
    beforeEach(() => vi.mocked(callBackend).mockReset());

    /** The analysis cancel uses its own RPC, not the stacking one. */
    it('cancelAnalysis calls analysis:cancel', async () => {
        vi.mocked(callBackend).mockResolvedValueOnce({ cancelled: true, reason: null });
        await cancelAnalysis('M 52');
        expect(callBackend).toHaveBeenCalledWith('analysis:cancel', { target_id: 'M 52' });
    });
});

describe('Cancel on the analysis hook', () => {
    beforeEach(() => {
        vi.mocked(callBackend).mockReset();
        vi.mocked(emitToast).mockReset();
        vi.mocked(reportError).mockReset();
    });

    /** A cancel the backend accepts shows nothing: the busy state ends with the job. */
    it('asks the backend and stays quiet on success', async () => {
        vi.mocked(callBackend).mockImplementation(async (method: string) =>
            method === 'analysis:cancel' ? { cancelled: true, reason: null } : null);
        const { result } = renderHook(() => useAnalysisJob('M 52', true));
        await act(async () => { await result.current.cancelAnalysisJob(); });
        expect(callBackend).toHaveBeenCalledWith('analysis:cancel', { target_id: 'M 52' });
        expect(callBackend).not.toHaveBeenCalledWith('processing:cancel', expect.anything());
        expect(emitToast).not.toHaveBeenCalled();
    });

    /** A cancel the backend refuses shows its reason. */
    it('shows the reason when the job cannot be cancelled', async () => {
        const reason = 'This job runs in another process (process 42). Stop it there.';
        vi.mocked(callBackend).mockImplementation(async (method: string) =>
            method === 'analysis:cancel' ? { cancelled: false, reason } : null);
        const { result } = renderHook(() => useAnalysisJob('M 52', true));
        await act(async () => { await result.current.cancelAnalysisJob(); });
        expect(emitToast).toHaveBeenCalledWith(reason, 'warning', 'cancelAnalysisJob');
    });

    /** A request that fails is an error the user sees. */
    it('reports an error when the request fails', async () => {
        vi.mocked(callBackend).mockImplementation(async (method: string) => {
            if (method === 'analysis:cancel') throw new Error('down');
            return null;
        });
        const { result } = renderHook(() => useAnalysisJob('M 52', true));
        await act(async () => { await result.current.cancelAnalysisJob(); });
        expect(reportError).toHaveBeenCalled();
    });
});
