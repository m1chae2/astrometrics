/**
 * @file test_targetReadInvalidation.test.tsx
 * @description Tests that a change to one target reads again only that target's kept reads (star
 * shapes, frame statuses and grouped light frames). Two targets, M 13 and M 31, are on screen at once
 * inside the real target context. An update of M 13 must read M 13 again and leave M 31 alone. An
 * update that names no target, or a calibration folder sync, must read both again. A finished
 * restack of M 31 must read M 31 again. At start-up a hidden Image Processing display must make no
 * star-shape or frame-status call, and showing it must make them.
 */
import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TargetProvider, DeferWhileHidden } from '../../common/context/TargetContext';
import { emit as emitEvent } from '../../common/utils/eventBus';
import { socketClient } from '../../common/utils/socketClient';
import { UI_EVENTS } from '../../common/types/backendTypes';
import { useStarShapeReview } from '../hooks/useStarShapeReview';
import { useFrameStatuses } from '../hooks/useFrameStatuses';
import { useGroupedFrameStats } from '../hooks/useGroupedFrameStats';
import { announceFinishedStack, resetAnnouncedStackJobs } from '../utils/announceFinishedStack';
import type { ProcessingJob } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/utils/reportError', () => ({ reportError: vi.fn() }));

/** The three kept reads, as the backend methods that make them. */
const TARGET_READ_METHODS = ['advisory:star_shapes', 'target:get_frame_statuses', 'target:get_frames_grouped'];

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
 * Counts the calls of each of the three kept reads for one target.
 * @param targetId The target.
 * @return The count for each read, in the order of `TARGET_READ_METHODS`.
 */
function countReads(targetId: string): number[] {
    return TARGET_READ_METHODS.map((method) => countCalls(method, targetId));
}

/**
 * Sends a backend event to every listener the socket client holds.
 * @param action The event name.
 * @param payload The event payload.
 */
function sendAction(action: string, payload: unknown): void {
    (socketClient as unknown as { emit: (name: string, ...args: unknown[]) => void }).emit('action', action, payload);
}

/**
 * Mounts the three kept reads of one target, as the Frame Analysis panel does.
 * @param props.targetId The target to read.
 * @return Nothing on screen.
 */
const TargetReads: React.FC<{ targetId: string }> = ({ targetId }) => {
    useStarShapeReview(targetId);
    useFrameStatuses(targetId);
    useGroupedFrameStats(targetId);
    return null;
};

/**
 * Mounts M 13's and M 31's reads inside the target context and waits until each was read once.
 * @return Nothing; the reads are counted with `countReads`.
 */
async function renderBothTargets(): Promise<void> {
    const queryClient = new QueryClient();
    render(
        <QueryClientProvider client={queryClient}>
            <TargetProvider>
                <TargetReads targetId="M 13" />
                <TargetReads targetId="M 31" />
            </TargetProvider>
        </QueryClientProvider>,
    );
    await waitFor(() => {
        expect(countReads('M 13')).toEqual([1, 1, 1]);
        expect(countReads('M 31')).toEqual([1, 1, 1]);
    });
}

/**
 * Waits a little so a read that should not happen has the time to show up.
 * @return A promise that settles after the wait.
 */
async function settle(): Promise<void> {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
}

beforeEach(() => {
    callBackendMock.mockReset();
    resetAnnouncedStackJobs();
    callBackendMock.mockImplementation(async (method: string, params: { target?: string; target_id?: string }) => {
        const targetId = params?.target ?? params?.target_id;
        if (method === 'advisory:star_shapes') return { target: targetId, frames: [] };
        if (method === 'target:get_frame_statuses') return { stackedAt: 1, frames: [] };
        if (method === 'target:get_frames_grouped') return [{ gain: 100, exposureS: 60, count: 3 }];
        return null;
    });
});

describe('a change to one target reads only that target again', () => {
    /** An update of M 13 (an edit, a finished download) reads M 13 again and leaves M 31 alone. */
    it('reads M 13 again, not M 31, when M 13 is updated', async () => {
        await renderBothTargets();
        act(() => emitEvent('targetsUpdated', { id: 'M 13' }));
        await waitFor(() => expect(countReads('M 13')).toEqual([2, 2, 2]));
        await settle();
        expect(countReads('M 31')).toEqual([1, 1, 1]);
    });

    /** New frames of M 13 from a download read M 13 again and leave M 31 alone. */
    it('reads M 13 again, not M 31, when frames of M 13 are added', async () => {
        await renderBothTargets();
        act(() => sendAction(UI_EVENTS['library:frames_updated'], { targetId: 'M 13', framesAdded: 1, isCalibration: false }));
        await waitFor(() => expect(countReads('M 13')).toEqual([2, 2, 2]));
        await settle();
        expect(countReads('M 31')).toEqual([1, 1, 1]);
    });

    /** An update that names no target reads every target again. */
    it('reads every target again when the update names no target', async () => {
        await renderBothTargets();
        act(() => emitEvent('targetsUpdated'));
        await waitFor(() => {
            expect(countReads('M 13')).toEqual([2, 2, 2]);
            expect(countReads('M 31')).toEqual([2, 2, 2]);
        });
    });

    /** A calibration folder sync can change any target's calibration match, so every target reads again. */
    it('reads every target again after a calibration folder sync', async () => {
        await renderBothTargets();
        act(() => sendAction(UI_EVENTS['library:frames_updated'], { targetId: 'Dark', framesAdded: 4, isCalibration: true }));
        await waitFor(() => {
            expect(countReads('M 13')).toEqual([2, 2, 2]);
            expect(countReads('M 31')).toEqual([2, 2, 2]);
        });
    });

    /** A finished restack of M 31 reads M 31 again and leaves M 13 alone. */
    it('reads M 31 again, not M 13, when a restack of M 31 finishes', async () => {
        await renderBothTargets();
        const finishedJob = { id: 'job-31', status: 'completed' } as unknown as ProcessingJob;
        await act(async () => { await announceFinishedStack('M 31', 'job-31', finishedJob); });
        await waitFor(() => expect(countReads('M 31')).toEqual([2, 2, 2]));
        await settle();
        expect(countReads('M 13')).toEqual([1, 1, 1]);
    });
});

describe('a hidden Image Processing display at start-up', () => {
    /**
     * The display is mounted hidden, as App.tsx does at start-up (`DeferWhileHidden` with
     * `active` false). It must not ask for star shapes or frame statuses until it is shown.
     */
    it('makes no star-shape or frame-status call until it is shown', async () => {
        const queryClient = new QueryClient();
        /**
         * The display slot as App.tsx builds it, shown or hidden.
         * @param props.isActive Whether Image Processing is the shown display.
         * @return M 13's reads inside the slot.
         */
        const Slot: React.FC<{ isActive: boolean }> = ({ isActive }) => (
            <QueryClientProvider client={queryClient}>
                <TargetProvider>
                    <DeferWhileHidden active={isActive}>
                        <TargetReads targetId="M 13" />
                    </DeferWhileHidden>
                </TargetProvider>
            </QueryClientProvider>
        );
        const { rerender } = render(<Slot isActive={false} />);
        await settle();
        expect(countCalls('advisory:star_shapes', 'M 13')).toBe(0);
        expect(countCalls('target:get_frame_statuses', 'M 13')).toBe(0);

        rerender(<Slot isActive />);
        await waitFor(() => {
            expect(countCalls('advisory:star_shapes', 'M 13')).toBe(1);
            expect(countCalls('target:get_frame_statuses', 'M 13')).toBe(1);
        });
    });
});
