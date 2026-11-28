/**
 * @file test_targetReadCache.test.tsx
 * @description Tests that the Image Processing display keeps its slow target reads (star shapes, frame
 * statuses and the grouped light frames) for each target. Hiding and showing the display unmounts and
 * mounts its hooks, so each test mounts a hook, unmounts it and mounts it again with the same cache. The
 * same target must not ask the backend again, marking that target's reads old must, and going back to a
 * target already read must show its kept answer at once. Marking one target's reads old must leave the
 * other targets' reads alone. The star-shape and frame-status reads wait while the display is hidden,
 * show a kept answer while hidden, and read once the display is shown.
 */
import React from 'react';
import { act, render, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createQueryClientTestWrapper } from '../../tests/queryClientTestWrapper';
import { useStarShapeReview } from '../hooks/useStarShapeReview';
import { useFrameStatuses } from '../hooks/useFrameStatuses';
import { useGroupedFrameStats } from '../hooks/useGroupedFrameStats';
import { invalidateTargetReads } from '../../common/queries/targetReadQueryKeys';
import { DisplayActiveContext } from '../../common/context/DisplayActiveContext';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/utils/reportError', () => ({ reportError: vi.fn() }));

/**
 * Counts the backend calls of one method for one target.
 * @param method The RPC method.
 * @param targetId The target, read from `target` or `target_id`.
 * @return How many times it was called.
 */
function countCalls(method: string, targetId: string): number {
    return callBackendMock.mock.calls.filter(([calledMethod, params]) =>
        calledMethod === method && (params?.target ?? params?.target_id) === targetId).length;
}

beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockImplementation(async (method: string, params: { target?: string; target_id?: string }) => {
        const targetId = params.target ?? params.target_id;
        if (method === 'advisory:star_shapes') return { target: targetId, frames: [] };
        if (method === 'target:get_frame_statuses') return { stackedAt: 1, frames: [{ path: `/lib/${targetId}.fits`, name: 'a.fits', state: 'included' }] };
        if (method === 'target:get_frames_grouped') return [{ gain: 100, exposureS: 60, count: targetId === 'M 81' ? 12 : 3 }];
        return null;
    });
});

describe('useStarShapeReview keeps its answer', () => {
    /** Showing the display again with the same target makes no second call. */
    it('does not ask again when the hook mounts again with the same target', async () => {
        const { wrapper } = createQueryClientTestWrapper();
        const first = renderHook(() => useStarShapeReview('M 81'), { wrapper });
        await waitFor(() => expect(first.result.current).not.toBeNull());
        first.unmount();

        const second = renderHook(() => useStarShapeReview('M 81'), { wrapper });
        // The kept answer shows on the first render, with nothing to wait for.
        expect(second.result.current).toMatchObject({ target: 'M 81' });
        expect(countCalls('advisory:star_shapes', 'M 81')).toBe(1);
    });

    /** Marking the target's reads old (a restack, new frames) reads again. */
    it('asks again when the target changes', async () => {
        const queryClient = new QueryClient();
        const { wrapper } = createQueryClientTestWrapper(queryClient);
        const { result } = renderHook(() => useStarShapeReview('M 81'), { wrapper });
        await waitFor(() => expect(result.current).not.toBeNull());
        await act(async () => { await invalidateTargetReads(queryClient, ['M 81']); });
        await waitFor(() => expect(countCalls('advisory:star_shapes', 'M 81')).toBe(2));
    });

    /** Going to another target and back shows the first target's kept answer with no new call. */
    it('uses the kept answer when going back to a target already read', async () => {
        const { wrapper } = createQueryClientTestWrapper();
        const { result, rerender } = renderHook(({ targetId }) => useStarShapeReview(targetId), {
            wrapper, initialProps: { targetId: 'M 81' },
        });
        await waitFor(() => expect(result.current).toMatchObject({ target: 'M 81' }));
        rerender({ targetId: 'M 57' });
        // Another target's numbers are never shown while this target's are read.
        expect(result.current).toBeNull();
        await waitFor(() => expect(result.current).toMatchObject({ target: 'M 57' }));
        rerender({ targetId: 'M 81' });
        expect(result.current).toMatchObject({ target: 'M 81' });
        expect(countCalls('advisory:star_shapes', 'M 81')).toBe(1);
        expect(countCalls('advisory:star_shapes', 'M 57')).toBe(1);
    });

    /** A failed call is not kept, so the next visit asks again, and it shows as "not measured yet". */
    it('asks again on the next visit after a failed call', async () => {
        callBackendMock.mockImplementationOnce(async () => { throw new Error('backend not reachable'); });
        const { wrapper } = createQueryClientTestWrapper();
        const first = renderHook(() => useStarShapeReview('M 81'), { wrapper });
        await waitFor(() => expect(countCalls('advisory:star_shapes', 'M 81')).toBe(1));
        expect(first.result.current).toBeNull();
        first.unmount();

        const second = renderHook(() => useStarShapeReview('M 81'), { wrapper });
        await waitFor(() => expect(second.result.current).toMatchObject({ target: 'M 81' }));
        expect(countCalls('advisory:star_shapes', 'M 81')).toBe(2);
    });

    /** A target that is not local asks nothing. */
    it('asks nothing while the target is not local', () => {
        const { wrapper } = createQueryClientTestWrapper();
        const { result } = renderHook(() => useStarShapeReview('M 81', false), { wrapper });
        expect(result.current).toBeNull();
        expect(callBackendMock).not.toHaveBeenCalled();
    });
});

describe('useFrameStatuses keeps its answer', () => {
    /** Showing the display again with the same target makes no second call. */
    it('does not ask again when the hook mounts again with the same target', async () => {
        const { wrapper } = createQueryClientTestWrapper();
        const first = renderHook(() => useFrameStatuses('M 81'), { wrapper });
        await waitFor(() => expect(first.result.current.statuses.size).toBe(1));
        first.unmount();

        const second = renderHook(() => useFrameStatuses('M 81'), { wrapper });
        expect(second.result.current.statuses.size).toBe(1);
        expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(1);
    });

    /** Marking the target's reads old (a restack, new frames) reads again. */
    it('asks again when the target changes', async () => {
        const queryClient = new QueryClient();
        const { wrapper } = createQueryClientTestWrapper(queryClient);
        const { result } = renderHook(() => useFrameStatuses('M 81'), { wrapper });
        await waitFor(() => expect(result.current.report).not.toBeNull());
        await act(async () => { await invalidateTargetReads(queryClient, ['M 81']); });
        await waitFor(() => expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(2));
    });

    /** Going to another target and back shows the first target's kept answer with no new call. */
    it('uses the kept answer when going back to a target already read', async () => {
        const { wrapper } = createQueryClientTestWrapper();
        const { result, rerender } = renderHook(({ targetId }) => useFrameStatuses(targetId), {
            wrapper, initialProps: { targetId: 'M 81' },
        });
        await waitFor(() => expect(result.current.statuses.has('/lib/M 81.fits')).toBe(true));
        rerender({ targetId: 'M 57' });
        expect(result.current.report).toBeNull();
        await waitFor(() => expect(result.current.statuses.has('/lib/M 57.fits')).toBe(true));
        rerender({ targetId: 'M 81' });
        expect(result.current.statuses.has('/lib/M 81.fits')).toBe(true);
        expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(1);
    });

    /** `reload` (used after a frame is put back) reads again even when nothing else changed. */
    it('reads again when reload is called', async () => {
        const { wrapper } = createQueryClientTestWrapper();
        const { result } = renderHook(() => useFrameStatuses('M 81'), { wrapper });
        await waitFor(() => expect(result.current.report).not.toBeNull());
        act(() => { result.current.reload(); });
        await waitFor(() => expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(2));
        // The kept statuses stay on screen while the new read runs.
        expect(result.current.report).not.toBeNull();
    });
});

describe('useGroupedFrameStats keeps its answer', () => {
    /** Showing the display again makes no second call, and the kept answer shows with no loading state. */
    it('does not ask again when the hook mounts again with the same targets', async () => {
        const { wrapper } = createQueryClientTestWrapper();
        const first = renderHook(() => useGroupedFrameStats('M 81'), { wrapper });
        expect(first.result.current.isLoading).toBe(true);
        await waitFor(() => expect(first.result.current.groups).toHaveLength(1));
        expect(first.result.current.isLoading).toBe(false);
        first.unmount();

        const second = renderHook(() => useGroupedFrameStats('M 81'), { wrapper });
        expect(second.result.current.isLoading).toBe(false);
        expect(second.result.current.groups?.[0].count).toBe(12);
        expect(countCalls('target:get_frames_grouped', 'M 81')).toBe(1);
    });

    /** New frames or a finished stack of the target read again, and the kept groups stay on screen meanwhile. */
    it('asks again when the target changes', async () => {
        const queryClient = new QueryClient();
        const { wrapper } = createQueryClientTestWrapper(queryClient);
        const { result } = renderHook(() => useGroupedFrameStats('M 81'), { wrapper });
        await waitFor(() => expect(result.current.groups).toHaveLength(1));
        await act(async () => { await invalidateTargetReads(queryClient, ['M 81']); });
        await waitFor(() => expect(countCalls('target:get_frames_grouped', 'M 81')).toBe(2));
        expect(result.current.isLoading).toBe(false);
        expect(result.current.groups).toHaveLength(1);
    });

    /** Going to another target and back shows the first target's kept answer with no new call. */
    it('uses the kept answer when going back to a target already read', async () => {
        const { wrapper } = createQueryClientTestWrapper();
        const { result, rerender } = renderHook(({ targetId }) => useGroupedFrameStats(targetId), {
            wrapper, initialProps: { targetId: 'M 81' },
        });
        await waitFor(() => expect(result.current.groups?.[0].count).toBe(12));
        rerender({ targetId: 'M 57' });
        expect(result.current.groups).toBeUndefined();
        await waitFor(() => expect(result.current.groups?.[0].count).toBe(3));
        rerender({ targetId: 'M 81' });
        expect(result.current.isLoading).toBe(false);
        expect(result.current.groups?.[0].count).toBe(12);
        expect(countCalls('target:get_frames_grouped', 'M 81')).toBe(1);
    });

    /** The stacking panel and the calibration line mount together and share one call. */
    it('makes one call for two users of the same target at once', async () => {
        const queryClient = new QueryClient();
        const { wrapper } = createQueryClientTestWrapper(queryClient);
        const stackingPanel = renderHook(() => useGroupedFrameStats('M 81'), { wrapper });
        const calibrationLine = renderHook(() => useGroupedFrameStats('M 81'), { wrapper });
        await waitFor(() => expect(stackingPanel.result.current.groups).toHaveLength(1));
        await waitFor(() => expect(calibrationLine.result.current.groups).toHaveLength(1));
        expect(countCalls('target:get_frames_grouped', 'M 81')).toBe(1);
    });
});

/** What the hidden-display tests read from their probe on each render. */
interface ProbeReads {
    review: ReturnType<typeof useStarShapeReview>;
    statuses: ReturnType<typeof useFrameStatuses>;
}

/**
 * Mounts both slow reads of M 81 with the test's cache, inside a display that is shown or hidden.
 * @param queryClient The cache to use.
 * @param latest Filled with the reads of the newest render.
 * @return A function that draws the probe with the display shown or hidden; call it again to change it.
 */
function mountProbe(queryClient: QueryClient, latest: { current: ProbeReads | null }) {
    /**
     * Reads star shapes and frame statuses, and keeps the newest answers in `latest`.
     * @return Nothing on screen.
     */
    const Probe: React.FC = () => {
        latest.current = { review: useStarShapeReview('M 81'), statuses: useFrameStatuses('M 81') };
        return null;
    };
    /**
     * The probe inside the cache and the "display is on screen" flag.
     * @param props.isActive Whether the display is shown.
     * @return The probe inside both providers.
     */
    const Slot: React.FC<{ isActive: boolean }> = ({ isActive }) => (
        <QueryClientProvider client={queryClient}>
            <DisplayActiveContext.Provider value={isActive}><Probe /></DisplayActiveContext.Provider>
        </QueryClientProvider>
    );
    let rendered: ReturnType<typeof render> | null = null;
    return (isActive: boolean) => {
        if (rendered) rendered.rerender(<Slot isActive={isActive} />);
        else rendered = render(<Slot isActive={isActive} />);
        return rendered;
    };
}

describe('the slow reads wait while the display is hidden', () => {
    /** A hidden display asks nothing, and showing it reads both. */
    it('asks nothing while hidden and reads once shown', async () => {
        const latest: { current: ProbeReads | null } = { current: null };
        const show = mountProbe(new QueryClient(), latest);
        show(false);
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
        expect(countCalls('advisory:star_shapes', 'M 81')).toBe(0);
        expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(0);
        expect(latest.current?.review).toBeNull();

        show(true);
        await waitFor(() => expect(latest.current?.review).toMatchObject({ target: 'M 81' }));
        await waitFor(() => expect(latest.current?.statuses.statuses.size).toBe(1));
        expect(countCalls('advisory:star_shapes', 'M 81')).toBe(1);
        expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(1);
    });

    /** A kept answer shows at once while the display is hidden, with no new call. */
    it('shows the kept answer while hidden', async () => {
        const queryClient = new QueryClient();
        const firstReads: { current: ProbeReads | null } = { current: null };
        const first = mountProbe(queryClient, firstReads)(true);
        await waitFor(() => expect(firstReads.current?.review).not.toBeNull());
        first.unmount();

        const secondReads: { current: ProbeReads | null } = { current: null };
        mountProbe(queryClient, secondReads)(false);
        expect(secondReads.current?.review).toMatchObject({ target: 'M 81' });
        expect(countCalls('advisory:star_shapes', 'M 81')).toBe(1);
    });

    /** A change marked while hidden is read once the display is shown, not before. */
    it('reads a change marked while hidden once shown', async () => {
        const queryClient = new QueryClient();
        const latest: { current: ProbeReads | null } = { current: null };
        const show = mountProbe(queryClient, latest);
        show(true);
        await waitFor(() => expect(latest.current?.statuses.report).not.toBeNull());
        show(false);
        await act(async () => { await invalidateTargetReads(queryClient, ['M 81']); });
        expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(1);
        // The kept statuses stay while hidden.
        expect(latest.current?.statuses.report).not.toBeNull();

        show(true);
        await waitFor(() => expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(2));
    });
});
