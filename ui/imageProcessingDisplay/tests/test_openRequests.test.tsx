/**
 * @file test_openRequests.test.tsx
 * @description Counts the backend calls Image Processing makes when it opens on a target. The job
 * history and its log, and the astrometry stars, are only on screen after the person opens the
 * Analysis tab or turns the Astrometry switch on, so they must not be read before that. The
 * grouped frame summary is asked for by two panels and must go out once.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@testing-library/react';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ connected: true, activeJobs: [] }),
}));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({
        selectedTarget: 'M 81', pendingTarget: 'M 81', setPendingTarget: () => undefined,
        setSelectedTarget: () => undefined, reloadKey: 0, framesReloadKey: 0, forceReload: () => undefined,
        invalidate: () => undefined, raShared: '1', decShared: '2', commonName: '', catalogId: '',
    }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: new Set() }) }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({
        items: [{ id: 'M 81', value: 'M 81', label: 'M 81' }],
        filterPanel: {}, filterText: '', setFilterText: () => undefined, isLoading: false, isLocalTarget: true,
    }),
}));
vi.mock('../../common/radioList/TargetListFilterPanel', () => ({ TargetListFilterPanel: () => null }));
vi.mock('../../common/fitsViewer/FitsViewerManager', () => ({ FitsViewerManager: () => <div /> }));
vi.mock('../fileBrowser/FileBrowser', () => ({ FileBrowser: () => <div />, FileBrowserToolbar: () => <div /> }));
vi.mock('../../common/components/SwitchDisplayButton', () => ({ SwitchDisplayButton: () => <span /> }));
vi.mock('../../common/components/AddTargetModal', () => ({ AddTargetModal: () => null }));
vi.mock('../../common/components/FitsHeaderModal', () => ({ FitsHeaderModal: () => null }));
vi.mock('../components/StackedImagePanel', () => ({ StackedImagePanel: () => <div /> }));
vi.mock('../imageDetailsManager/ImageDetailsManager', () => ({ ImageDetailsManager: () => <div /> }));

vi.mock('../../common/radioList/RadioListManager', () => ({ RadioListManager: () => <div /> }));

import { ImageProcessingDisplay } from '../ImageProcessingDisplay';

/** Answers each backend method with an empty but valid reply. */
function answer(method: string): unknown {
    if (method === 'target:get_frames_grouped') return [];
    if (method === 'processing:list_jobs') return [];
    if (method === 'astronomy:get_overlay_stars') return { stars: [], reason: 'No sky solution' };
    if (method === 'calibration:query') return { darks: [], biases: [], flats: [] };
    return null;
}

/** Counts the calls made so far, by method. */
function countsByMethod(): Record<string, number> {
    const counts: Record<string, number> = {};
    for (const [method] of callBackendMock.mock.calls) counts[method as string] = (counts[method as string] ?? 0) + 1;
    return counts;
}

/** Renders the display with a fresh query cache. */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <ImageProcessingDisplay />
        </QueryClientProvider>
    );
}

describe('Image Processing backend calls on open', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        callBackendMock.mockImplementation(async (method: string) => answer(method));
        localStorage.clear();
    });

    it('does not read the job history or the astrometry stars before they are shown', async () => {
        renderDisplay();
        await waitFor(() => expect(callBackendMock).toHaveBeenCalled());
        await new Promise((resolve) => setTimeout(resolve, 100));
        const counts = countsByMethod();
        console.info('Image Processing calls on open:', JSON.stringify(counts));
        expect(counts['processing:list_jobs']).toBeUndefined();
        expect(counts['jobs:query']).toBeUndefined();
        expect(counts['astronomy:get_overlay_stars']).toBeUndefined();
        expect(counts['target:get_frames_grouped']).toBe(1);
    });

    it('reads the job history when the Analysis tab is opened', async () => {
        renderDisplay();
        await new Promise((resolve) => setTimeout(resolve, 50));
        await userEvent.click(screen.getByRole('tab', { name: 'Analysis' }));
        await waitFor(() => expect(countsByMethod()['processing:list_jobs']).toBeGreaterThan(0));
    });
});
