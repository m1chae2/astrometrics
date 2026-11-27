/**
 * @file test_putBackFrame.test.ts
 * @description Tests of how the target files hook lists set-aside frames and puts one back: the set-aside
 * frames come from the status report and stay out of the target's own files, Put back calls the backend
 * with the target and the frame, and a status filter keeps the frames that match.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useTargetFilesLogic } from '../hooks/useTargetFilesLogic';
import { createQueryClientTestWrapper } from '../../tests/queryClientTestWrapper';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/services/targetService', () => ({
    fetchTargetFiles: async () => ({
        files: [
            { path: '/lib/a.fits', name: 'a.fits', camera: 'ZWO', gain: null, exposureS: 60, filter: 'L', date: '2026-11-01 21:00:00' },
            { path: '/lib/b.fits', name: 'b.fits', camera: 'ZWO', gain: null, exposureS: 60, filter: 'L', date: '2026-11-01 21:05:00' },
        ],
        stackedImage: null, stackedSpectralTarget: null, totalExposure: 120,
    }),
}));
vi.mock('../../common/utils/emitToast', () => ({ emitToast: vi.fn() }));

const report = {
    stackedAt: 100,
    frames: [
        { path: '/lib/a.fits', name: 'a.fits', state: 'included' },
        { path: '/lib/b.fits', name: 'b.fits', state: 'not_stacked' },
        { path: '/lib/_excluded/c.fits', name: 'c.fits', state: 'set_aside', kind: 'trailed', reason: 'star roundness 0.62', date: '2026-11-01 21:10:00', exposureS: 60, restorable: true },
    ],
};

beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockImplementation(async (method: string) => {
        if (method === 'target:get_frame_statuses') return report;
        if (method === 'target:put_back_frames') return { restored: ['/lib/c.fits'] };
        return null;
    });
});

describe('useTargetFilesLogic set-aside frames', () => {
    it('lists set-aside frames apart from the target files', async () => {
        const { result } = renderHook(() => useTargetFilesLogic('NGC 7331', 0), { wrapper: createQueryClientTestWrapper().wrapper });
        await waitFor(() => expect(result.current.setAsideFiles).toHaveLength(1));
        expect(result.current.setAsideFiles[0]).toMatchObject({ path: '/lib/_excluded/c.fits', name: 'c.fits', date: '2026-11-01 21:10:00' });
        expect(result.current.allFiles.map((file) => file.path)).toEqual(['/lib/a.fits', '/lib/b.fits']);
        expect(result.current.frameStatuses.get('/lib/_excluded/c.fits')?.label).toBe('Set aside: trailed');
    });

    it('keeps only the frames that match a status filter', async () => {
        const { result } = renderHook(() => useTargetFilesLogic('NGC 7331', 0), { wrapper: createQueryClientTestWrapper().wrapper });
        await waitFor(() => expect(result.current.frameStatuses.size).toBe(3));
        await waitFor(() => expect(result.current.allFiles).toHaveLength(2));
        act(() => result.current.setFileFilterText('status:not-stacked'));
        expect(result.current.filteredFiles.map((file) => file.path)).toEqual(['/lib/b.fits']);
    });

    it('puts a frame back through the backend and reads the statuses again', async () => {
        const { result } = renderHook(() => useTargetFilesLogic('NGC 7331', 0), { wrapper: createQueryClientTestWrapper().wrapper });
        await waitFor(() => expect(result.current.setAsideFiles).toHaveLength(1));
        const readsBefore = callBackendMock.mock.calls.filter(([method]) => method === 'target:get_frame_statuses').length;
        await act(async () => { await result.current.putBackFrame('/lib/_excluded/c.fits'); });
        expect(callBackendMock).toHaveBeenCalledWith('target:put_back_frames', { target_id: 'NGC 7331', paths: ['/lib/_excluded/c.fits'] });
        await waitFor(() => {
            const readsAfter = callBackendMock.mock.calls.filter(([method]) => method === 'target:get_frame_statuses').length;
            expect(readsAfter).toBeGreaterThan(readsBefore);
        });
    });
});
