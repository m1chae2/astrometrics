/**
 * @fileoverview Tests for the stacking job log: starting a new job must not replay the
 * previous job's log, a late line from a stopped stream is ignored, and picking a job from
 * the history still shows that job's log.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useStackingJob } from '../hooks/useStackingJob';

type LineHandler = (line: string) => void;
const streams: { jobId: string; push: LineHandler; cancel: ReturnType<typeof vi.fn> }[] = [];
let resolveStart: (value: { jobId: string }) => void = () => undefined;

const oldJob = { id: 'old', jobType: 'stacking', status: 'completed' };
const olderJob = { id: 'older', jobType: 'stacking', status: 'completed' };

vi.mock('../../common/services/imaging/processingService', () => ({
    processTarget: vi.fn(() => new Promise((resolve) => { resolveStart = resolve; })),
    cancelProcessing: vi.fn(),
    fetchAllProcesses: vi.fn(),
    fetchJobs: vi.fn(async () => [oldJob, olderJob]),
    fetchJobLogTail: vi.fn(async (jobId: string) => [`${jobId} line`]),
    streamJobLog: vi.fn(async (jobId: string, onChunk: LineHandler) => {
        // Like the real service, the old job replays its stored lines at once.
        onChunk(`${jobId} replay`);
        const cancel = vi.fn();
        streams.push({ jobId, push: onChunk, cancel });
        return cancel;
    }),
    dismissJob: vi.fn(),
    openSiril: vi.fn(),
}));
vi.mock('../../common/services/targetService', () => ({ fetchFrameStatsGrouped: vi.fn(async () => []) }));
vi.mock('../../common/utils/reportError', () => ({ reportError: vi.fn() }));
// One shared function, as in the app: a new one per render would rerun the hook's effects.
const invalidateStable = vi.fn();
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({ framesReloadKey: 0, invalidate: invalidateStable }),
}));
const activeJobsRef = { current: [] as unknown[] };
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ activeJobs: activeJobsRef.current }),
}));

describe('useStackingJob log', () => {
    beforeEach(() => {
        streams.length = 0;
        activeJobsRef.current = [];
    });

    /** A new job starts with an empty log; the old job's lines never appear. */
    it('does not replay the previous job when a new job starts', async () => {
        const { result } = renderHook(() => useStackingJob('M 81'));
        await waitFor(() => expect(result.current.activeJobId).toBe('old'));
        await waitFor(() => expect(result.current.logLines).toEqual(['old line']));

        const seen: string[][] = [];
        let startPromise: Promise<void> = Promise.resolve();
        act(() => { startPromise = result.current.startProcessing(); });
        seen.push(result.current.logLines);
        expect(result.current.activeJobId).toBeNull();
        expect(streams).toHaveLength(0);

        await act(async () => { resolveStart({ jobId: 'new' }); await startPromise; });
        await waitFor(() => expect(result.current.activeJobId).toBe('new'));
        await waitFor(() => expect(streams.map((s) => s.jobId)).toEqual(['new']));
        seen.push(result.current.logLines);
        for (const lines of seen) expect(lines.join(' ')).not.toContain('old');
        expect(result.current.logLines).toEqual(['new replay']);
    });

    /** A line arriving from a stream that was already stopped is dropped. */
    it('ignores a late line from a cancelled stream', async () => {
        activeJobsRef.current = [{ id: 'old', targetId: 'M 81', jobType: 'stacking' }];
        const { result, rerender } = renderHook(() => useStackingJob('M 81'));
        await waitFor(() => expect(streams.length).toBeGreaterThan(0));
        const first = streams[0];
        activeJobsRef.current = [{ id: 'new', targetId: 'M 81', jobType: 'stacking' }];
        rerender();
        await waitFor(() => expect(result.current.activeJobId).toBe('new'));
        await waitFor(() => expect(streams.some((s) => s.jobId === 'new')).toBe(true));
        expect(first.cancel).toHaveBeenCalled();
        act(() => first.push('old late line'));
        expect(result.current.logLines).not.toContain('old late line');
    });

    /** Choosing a job from the history shows that job's stored log. */
    it('shows the log of a job picked from the history', async () => {
        const { result } = renderHook(() => useStackingJob('M 81'));
        await waitFor(() => expect(result.current.activeJobId).toBe('old'));
        act(() => result.current.onSelectJob(olderJob as never));
        await waitFor(() => expect(result.current.logLines).toEqual(['older line']));
        expect(result.current.activeJobId).toBe('older');
    });
});
