/**
 * @file test_foldStatePersists.test.tsx
 * @description Checks that the folded panels on the Image Processing screen stay folded after
 * the window refreshes, and that storage which throws leaves every panel open.
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
        invalidate: () => undefined, commonName: '', catalogId: '',
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


/** Renders the screen with a fresh query cache, like a window that just loaded. */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <ImageProcessingDisplay />
        </QueryClientProvider>
    );
}

describe('Image Processing fold state', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        callBackendMock.mockImplementation(async (method: string) => {
            if (method === 'target:get_frames_grouped') return [];
            if (method === 'calibration:query') return { darks: [], biases: [], flats: [] };
            return null;
        });
        localStorage.clear();
        vi.restoreAllMocks();
    });

    it('keeps folded panels folded after a refresh', async () => {
        const first = renderDisplay();
        await userEvent.click(await screen.findByTitle('Collapse down'));
        await userEvent.click(screen.getByTitle('Collapse to the right'));
        await userEvent.click(screen.getByTitle('Collapse the File Browser to a tab bar'));
        first.unmount();

        renderDisplay();
        expect(await screen.findByTitle('Expand Frame Analysis')).toBeTruthy();
        expect(screen.getByTitle('Expand the File Browser')).toBeTruthy();
        // Image Controls was folded too; it stays folded once the column opens again.
        await userEvent.click(screen.getByTitle('Expand Frame Analysis'));
        expect(screen.getByTitle('Expand Image Controls')).toBeTruthy();
    });

    it('opens every panel when storage throws', async () => {
        vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
        vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
        renderDisplay();
        expect(await screen.findByTitle('Collapse to the right')).toBeTruthy();
        expect(screen.getByTitle('Collapse down')).toBeTruthy();
        expect(screen.getByTitle('Collapse the File Browser to a tab bar')).toBeTruthy();
        // Folding still works for this session even though nothing can be saved.
        await userEvent.click(screen.getByTitle('Collapse down'));
        expect(screen.getByTitle('Expand Image Controls')).toBeTruthy();
    });

    it('folds Frame Analysis and Image Controls together and opens both again', async () => {
        renderDisplay();
        const hide = await screen.findByTitle('Collapse to the right');
        expect(hide.getAttribute('aria-expanded')).toBe('true');
        expect(screen.getByTitle('Collapse down')).toBeTruthy();
        await userEvent.click(hide);
        const show = screen.getByTitle('Expand Frame Analysis');
        expect(show.getAttribute('aria-expanded')).toBe('false');
        // Image Controls goes with the column: neither its panel nor a tab for it is left.
        expect(screen.queryByTitle('Collapse down')).toBeNull();
        expect(screen.queryByTitle('Expand Image Controls')).toBeNull();
        await userEvent.click(show);
        expect(screen.getByTitle('Collapse to the right')).toBeTruthy();
        expect(screen.getByTitle('Collapse down')).toBeTruthy();
    });
});
