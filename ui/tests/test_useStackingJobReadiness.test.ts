/**
 * @fileoverview Regression test for useStackingJob's `isLoadingFrames` flag.
 *
 * `shouldFetch` (passed in as whether the selected target is confirmed
 * local) often starts false and only flips true once a prerequisite --
 * the shared target list query -- resolves elsewhere in the app. The app
 * boot splash screen (see ui/common/utils/appBootReadiness.ts) gates on
 * `isLoadingFrames` to know when the Image Processing display has actually
 * settled. This test locks in that `isLoadingFrames` reads `true`
 * immediately on the same render where `shouldFetch` flips true, with no
 * one-render window where it reads stale/false before the fetch it
 * describes has had a chance to start.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useStackingJob } from '../imageProcessingDisplay/hooks/useStackingJob';

vi.mock('../common/context/TargetContext', () => ({
    useTargetContext: () => ({ framesReloadKey: 0, invalidate: vi.fn() }),
}));

vi.mock('../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ activeJobs: [] }),
}));

vi.mock('../common/services/imagingService', () => ({
    processTarget: vi.fn(),
    cancelProcessing: vi.fn(),
    fetchAllProcesses: vi.fn(),
    fetchJobs: vi.fn().mockResolvedValue([]),
    fetchJobLogTail: vi.fn().mockResolvedValue([]),
    streamJobLog: vi.fn(),
    dismissJob: vi.fn(),
    openSiril: vi.fn(),
}));

vi.mock('../common/services/targetService', () => ({
    fetchFrameStatsGrouped: vi.fn(() => new Promise(() => { /* never resolves in this test */ })),
}));

describe('useStackingJob isLoadingFrames', () => {
    it('reads true the instant shouldFetch flips true, with no stale-false render', async () => {
        const { result, rerender } = renderHook(
            ({ shouldFetch }) => useStackingJob('M 81', shouldFetch),
            { initialProps: { shouldFetch: false } }
        );

        // While shouldFetch is false (e.g. the shared target list hasn't yet
        // confirmed this target is local), nothing is pending.
        expect(result.current.isLoadingFrames).toBe(false);
        expect(result.current.lightFrames).toEqual([]);

        // shouldFetch flips true (e.g. the target list query resolved and
        // confirmed the target is local) -- the very next render must
        // already report loading, synchronously, before fetchFrameStatsGrouped
        // (mocked to hang forever above) has any chance to settle.
        rerender({ shouldFetch: true });
        expect(result.current.isLoadingFrames).toBe(true);

        // Stays true since the mocked fetch never resolves.
        await waitFor(() => expect(result.current.isLoadingFrames).toBe(true));
    });

    it('settles to false once the frame fetch resolves', async () => {
        const targetService = await import('../common/services/targetService');
        vi.mocked(targetService.fetchFrameStatsGrouped).mockResolvedValueOnce([
            { iso: '800', exposure: '30', count: 12 } as any,
        ]);

        const { result } = renderHook(() => useStackingJob('M 81', true));

        expect(result.current.isLoadingFrames).toBe(true);
        await waitFor(() => expect(result.current.isLoadingFrames).toBe(false));
        expect(result.current.lightFrames).toEqual([['800', '30', 12]]);
    });
});
