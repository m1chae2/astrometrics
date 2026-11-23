/**
 * @fileoverview Tests for useJobLog, the one mechanism that follows a job's log by its id: a
 * running job of any kind, including a kind that does not exist yet, streams live, and a job that
 * is not running shows its whole stored log.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useJobLog } from '../hooks/useJobLog';

type LineHandler = (line: string) => void;
const streams: { jobId: string; push: LineHandler; cancel: ReturnType<typeof vi.fn> }[] = [];
const storedLogs: Record<string, string[]> = {};

vi.mock('../../common/services/imaging/processingService', () => ({
    fetchJobLogTail: vi.fn(async (jobId: string) => storedLogs[jobId] ?? []),
    streamJobLog: vi.fn(async (jobId: string, onChunk: LineHandler) => {
        const cancel = vi.fn();
        streams.push({ jobId, push: onChunk, cancel });
        return cancel;
    }),
}));

describe('useJobLog', () => {
    beforeEach(() => {
        streams.length = 0;
        for (const key of Object.keys(storedLogs)) delete storedLogs[key];
    });

    /** A running job of a kind the app has never heard of streams its lines as they come. */
    it('streams a running job of an unknown kind live', async () => {
        const { result } = renderHook(() => useJobLog('new-kind-job', true));
        await waitFor(() => expect(streams).toHaveLength(1));
        act(() => streams[0].push('GraXpert finished'));
        act(() => streams[0].push('Masked stretch done'));
        act(() => streams[0].push('Cosmic Clarity done'));
        expect(result.current.lines).toEqual(['GraXpert finished', 'Masked stretch done', 'Cosmic Clarity done']);
    });

    /** When the job ends, the whole stored log replaces what the stream had delivered. */
    it('shows the full stored log once the job is no longer running', async () => {
        storedLogs['new-kind-job'] = ['one', 'two', 'three', 'four'];
        const { result, rerender } = renderHook(
            ({ running }) => useJobLog('new-kind-job', running),
            { initialProps: { running: true } },
        );
        await waitFor(() => expect(streams).toHaveLength(1));
        act(() => streams[0].push('one'));
        rerender({ running: false });
        await waitFor(() => expect(result.current.lines).toEqual(['one', 'two', 'three', 'four']));
        expect(streams[0].cancel).toHaveBeenCalled();
    });

    /** Picking a finished job shows its whole log, and a late stream line of the old job is dropped. */
    it('shows a finished job whole and starts clean when the job changes', async () => {
        storedLogs.finished = ['a', 'b'];
        const { result, rerender } = renderHook(({ id }) => useJobLog(id, id === 'running'), {
            initialProps: { id: 'running' },
        });
        await waitFor(() => expect(streams).toHaveLength(1));
        act(() => streams[0].push('live line'));
        rerender({ id: 'finished' });
        await waitFor(() => expect(result.current.lines).toEqual(['a', 'b']));
        act(() => streams[0].push('late line'));
        expect(result.current.lines).toEqual(['a', 'b']);
    });
});
