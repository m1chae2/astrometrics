/**
 * @file test_targetReadPrefetch.test.tsx
 * @description Tests the read-ahead of the selected target's star shapes and frame statuses
 * (`useTargetReadPrefetch`). Nothing may be asked during start-up. Once the app is ready and the
 * browser is idle, each read is asked once for the selected target only. Opening Image Processing
 * afterwards must show the answers at once with no new call. A new selection is read ahead once,
 * after it settles, and a change marked for the selected target while the display is hidden is read
 * ahead again at the next idle moment. The timers are fake, so each test moves time on by hand.
 */
import React from 'react';
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TargetReadPrefetcher, SELECTION_SETTLE_MS } from '../hooks/useTargetReadPrefetch';
import { useStarShapeReview } from '../hooks/useStarShapeReview';
import { useFrameStatuses } from '../hooks/useFrameStatuses';
import { invalidateTargetReads } from '../../common/queries/targetReadQueryKeys';
import { TARGET_LIST_QUERY_KEY } from '../../common/queries/useTargetListQuery';
import { DisplayActiveContext } from '../../common/context/DisplayActiveContext';

/** The app-ready flag and the selected target the tests control, with the watchers of each. */
const controls = vi.hoisted(() => ({
    isAppReady: false,
    selectedTarget: 'M 81',
    listeners: new Set<() => void>(),
}));

/**
 * Changes what the mocked app state reports and tells its watchers, inside `act` so React draws the change.
 * @param change The new values.
 */
function setControls(change: Partial<Pick<typeof controls, 'isAppReady' | 'selectedTarget'>>): void {
    act(() => {
        Object.assign(controls, change);
        controls.listeners.forEach((listener) => listener());
    });
}

vi.mock('../../common/utils/appBootReadiness', async () => {
    const { useSyncExternalStore } = await import('react');
    /**
     * Watches the test's controls.
     * @param listener Called when they change.
     * @return A function that stops watching.
     */
    const subscribe = (listener: () => void) => {
        controls.listeners.add(listener);
        return () => { controls.listeners.delete(listener); };
    };
    return { useIsAppReady: () => useSyncExternalStore(subscribe, () => controls.isAppReady) };
});

vi.mock('../../common/context/TargetContext', async () => {
    const { useSyncExternalStore } = await import('react');
    /**
     * Watches the test's controls.
     * @param listener Called when they change.
     * @return A function that stops watching.
     */
    const subscribe = (listener: () => void) => {
        controls.listeners.add(listener);
        return () => { controls.listeners.delete(listener); };
    };
    return {
        useTargetContext: () => ({
            selectedTarget: useSyncExternalStore(subscribe, () => controls.selectedTarget),
        }),
    };
});

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

/**
 * Counts every star-shape and frame-status call, for any target.
 * @return How many were made.
 */
function countAllReads(): number {
    return callBackendMock.mock.calls.filter(([calledMethod]) =>
        calledMethod === 'advisory:star_shapes' || calledMethod === 'target:get_frame_statuses').length;
}

/**
 * Moves the fake clock on and lets the waiting promises and renders finish.
 * @param milliseconds How far to move it.
 */
async function passTime(milliseconds: number): Promise<void> {
    await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}

/**
 * Makes a cache that already holds the library's target list, as it does once start-up is over.
 * @return The cache.
 */
function makeQueryClient(): QueryClient {
    const queryClient = new QueryClient();
    queryClient.setQueryData(TARGET_LIST_QUERY_KEY, [{ id: 'M 81' }, { id: 'M 57' }, { id: 'M 27' }]);
    return queryClient;
}

/**
 * Mounts the read-ahead with the test's cache.
 * @param queryClient The cache to use.
 * @return The rendered tree.
 */
function mountPrefetcher(queryClient: QueryClient) {
    return render(<QueryClientProvider client={queryClient}><TargetReadPrefetcher /></QueryClientProvider>);
}

beforeEach(() => {
    vi.useFakeTimers();
    controls.isAppReady = false;
    controls.selectedTarget = 'M 81';
    callBackendMock.mockReset();
    callBackendMock.mockImplementation(async (method: string, params: { target?: string; target_id?: string }) => {
        const targetId = params.target ?? params.target_id;
        if (method === 'advisory:star_shapes') return { target: targetId, frames: [] };
        if (method === 'target:get_frame_statuses') return { stackedAt: 1, frames: [{ path: `/lib/${targetId}.fits`, name: 'a.fits', state: 'included' }] };
        return null;
    });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('useTargetReadPrefetch', () => {
    /** Nothing is asked while the app is still starting. */
    it('asks nothing before the app is ready', async () => {
        mountPrefetcher(makeQueryClient());
        await passTime(10_000);
        expect(countAllReads()).toBe(0);
    });

    /** Once ready and idle, each read is asked once, for the selected target only. */
    it('reads each answer once for the selected target after the app is ready', async () => {
        mountPrefetcher(makeQueryClient());
        setControls({ isAppReady: true });
        await passTime(500);
        expect(countCalls('advisory:star_shapes', 'M 81')).toBe(1);
        expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(1);
        expect(countAllReads()).toBe(2);
        // Nothing more is asked while the answers are fresh.
        await passTime(10_000);
        expect(countAllReads()).toBe(2);
    });

    /** Opening Image Processing afterwards shows the answers at once, with no new call. */
    it('lets the display show the read-ahead answers with no new call', async () => {
        const queryClient = makeQueryClient();
        mountPrefetcher(queryClient);
        setControls({ isAppReady: true });
        await passTime(500);
        expect(countAllReads()).toBe(2);

        const latest: { review: unknown; statusCount: number } = { review: null, statusCount: -1 };
        /**
         * Reads both answers the way the display does, and keeps the newest render's values.
         * @return Nothing on screen.
         */
        const DisplayProbe: React.FC = () => {
            latest.review = useStarShapeReview('M 81');
            latest.statusCount = useFrameStatuses('M 81').statuses.size;
            return null;
        };
        render(
            <QueryClientProvider client={queryClient}>
                <DisplayActiveContext.Provider value={true}><DisplayProbe /></DisplayActiveContext.Provider>
            </QueryClientProvider>,
        );
        // The first render already has the answers.
        expect(latest.review).toMatchObject({ target: 'M 81' });
        expect(latest.statusCount).toBe(1);
        await passTime(1_000);
        expect(countAllReads()).toBe(2);
    });

    /** A target without frames on this computer is not read ahead. */
    it('asks nothing for a target that is not in the library', async () => {
        setControls({ selectedTarget: 'NGC 9999' });
        mountPrefetcher(makeQueryClient());
        setControls({ isAppReady: true });
        await passTime(5_000);
        expect(countAllReads()).toBe(0);
    });

    /** Clicking through targets asks nothing; the one the selection settles on is read once. */
    it('reads a new selection once, after it settles', async () => {
        mountPrefetcher(makeQueryClient());
        setControls({ isAppReady: true });
        await passTime(500);
        expect(countAllReads()).toBe(2);

        setControls({ selectedTarget: 'M 57' });
        await passTime(SELECTION_SETTLE_MS / 2);
        setControls({ selectedTarget: 'M 27' });
        await passTime(SELECTION_SETTLE_MS / 2);
        // Neither click has settled yet.
        expect(countAllReads()).toBe(2);
        await passTime(SELECTION_SETTLE_MS);
        expect(countCalls('advisory:star_shapes', 'M 57')).toBe(0);
        expect(countCalls('advisory:star_shapes', 'M 27')).toBe(1);
        expect(countCalls('target:get_frame_statuses', 'M 27')).toBe(1);
        expect(countAllReads()).toBe(4);
        await passTime(10_000);
        expect(countAllReads()).toBe(4);
    });

    /** A change marked for the selected target while the display is hidden is read ahead again. */
    it('reads again at idle after the selected target is marked changed', async () => {
        const queryClient = makeQueryClient();
        mountPrefetcher(queryClient);
        setControls({ isAppReady: true });
        await passTime(500);
        expect(countAllReads()).toBe(2);

        await act(async () => { await invalidateTargetReads(queryClient, ['M 81']); });
        // React Query does not read a hidden query again by itself.
        expect(countAllReads()).toBe(2);
        await passTime(SELECTION_SETTLE_MS + 500);
        expect(countCalls('advisory:star_shapes', 'M 81')).toBe(2);
        expect(countCalls('target:get_frame_statuses', 'M 81')).toBe(2);
        await passTime(10_000);
        expect(countAllReads()).toBe(4);
    });

    /** A change to another target does not read ahead anything. */
    it('ignores a change to a target that is not selected', async () => {
        const queryClient = makeQueryClient();
        mountPrefetcher(queryClient);
        setControls({ isAppReady: true });
        await passTime(500);
        await act(async () => { await invalidateTargetReads(queryClient, ['M 57']); });
        await passTime(SELECTION_SETTLE_MS + 500);
        expect(countAllReads()).toBe(2);
    });
});
