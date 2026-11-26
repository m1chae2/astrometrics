/**
 * @file test_jobEndWatcher.test.ts
 * @description Tests the one way Image Processing follows a job to its end: `isJobEnded` names the
 * final states the backend writes, and `useJobEndWatcher` follows a job by its id or by a finder
 * (for a job that has no id yet) and calls back once when it ends.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { isJobEnded } from '../utils/jobStatus';
import { useJobEndWatcher } from '../hooks/useJobEndWatcher';

const fetchJobMock = vi.fn();
vi.mock('../../common/services/imaging/stackReviewService', () => ({
    fetchJob: (...args: unknown[]) => fetchJobMock(...args),
}));

describe('isJobEnded', () => {
    /** The three final states the backend's job store writes end a job; a running one does not. */
    it('treats completed, failed and cancelled as ended', () => {
        expect(['completed', 'failed', 'cancelled'].map(isJobEnded)).toEqual([true, true, true]);
        expect(isJobEnded('started')).toBe(false);
        expect(isJobEnded(null)).toBe(false);
        expect(isJobEnded(undefined)).toBe(false);
    });

    /** The backend spells it "cancelled"; the one-l spelling is not one of its states. */
    it('does not accept the one-l spelling', () => {
        expect(isJobEnded('canceled')).toBe(false);
    });
});

describe('useJobEndWatcher', () => {
    beforeEach(() => {
        fetchJobMock.mockReset();
    });

    /** A job id is read at once, and the callback gets the ended job. */
    it('follows a job by its id', async () => {
        fetchJobMock.mockResolvedValue({ id: 'j1', status: 'failed', message: 'no frames' });
        const onEnded = vi.fn();
        renderHook(() => useJobEndWatcher('j1', onEnded));
        await waitFor(() => expect(onEnded).toHaveBeenCalledTimes(1));
        expect(fetchJobMock).toHaveBeenCalledWith('j1');
        expect(onEnded.mock.calls[0][0].status).toBe('failed');
    });

    /** A finder is used for a job with no id yet; nothing found yet means look again later. */
    it('follows a job found by a finder', async () => {
        const finder = vi.fn()
            .mockResolvedValueOnce(undefined)
            .mockResolvedValue({ id: 'j2', status: 'completed' });
        const onEnded = vi.fn();
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            renderHook(() => useJobEndWatcher(finder, onEnded));
            await vi.advanceTimersByTimeAsync(2100);
            await waitFor(() => expect(onEnded).toHaveBeenCalledTimes(1));
        } finally {
            vi.useRealTimers();
        }
        expect(fetchJobMock).not.toHaveBeenCalled();
        expect(finder).toHaveBeenCalledTimes(2);
    });

    /** Nothing to watch reads nothing. */
    it('reads nothing when there is no job', () => {
        renderHook(() => useJobEndWatcher(null, vi.fn()));
        expect(fetchJobMock).not.toHaveBeenCalled();
    });
});
