/**
 * @file test_defaultFileSelection.test.tsx
 * @description Checks which file Image Processing opens a target with: the stack when the target
 * has one, otherwise the raw frame with the highest exposure. A file the person picks by hand is
 * never replaced, even if the stack arrives later.
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
vi.mock('../fileBrowser/FileBrowser', () => ({
    FileBrowser: (props: { selectedFile: string | null; onSelectFile: (path: string | null) => void }) => (
        <div>
            <span data-testid="selected">{props.selectedFile ?? 'none'}</span>
            <button onClick={() => props.onSelectFile('/raw/a.fits')}>pick-raw</button>
        </div>
    ),
    FileBrowserToolbar: () => <div />,
}));
vi.mock('../../common/components/SwitchDisplayButton', () => ({ SwitchDisplayButton: () => <span /> }));
vi.mock('../../common/components/AddTargetModal', () => ({ AddTargetModal: () => null }));
vi.mock('../../common/components/FitsHeaderModal', () => ({ FitsHeaderModal: () => null }));
vi.mock('../components/StackedImagePanel', () => ({ StackedImagePanel: () => <div /> }));
vi.mock('../imageDetailsManager/ImageDetailsManager', () => ({ ImageDetailsManager: () => <div /> }));
vi.mock('../../common/radioList/RadioListManager', () => ({ RadioListManager: () => <div /> }));

import { ImageProcessingDisplay } from '../ImageProcessingDisplay';

/** Two raw frames; the second has the longer exposure. */
const RAW_FILES = [
    { path: '/raw/a.fits', name: 'a.fits', camera: 'cam', iso: '', exposure: '10', filter: 'L', date: '' },
    { path: '/raw/b.fits', name: 'b.fits', camera: 'cam', iso: '', exposure: '60', filter: 'L', date: '' },
];

/** Sets what the backend returns for the target's files; a promise delays the reply. */
function answerFiles(stackedImage: string | null, gate?: Promise<void>) {
    callBackendMock.mockImplementation(async (method: string) => {
        if (method === 'target:get_files') {
            if (gate) await gate;
            return { files: RAW_FILES, stackedImage, stackedSpectralTarget: null, totalExposure: 70 };
        }
        if (method === 'target:get_frames_grouped') return [];
        if (method === 'images:convert_fits' || method === 'images:get_fits_file') {
            throw new Error('Image not found');
        }
        if (method === 'calibration:query') return { darks: [], biases: [], flats: [] };
        return null;
    });
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

describe('Image Processing default file', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        localStorage.clear();
    });

    it('opens the stack when the target has one', async () => {
        answerFiles('/stack/M81.fits');
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/stack/M81.fits'));
    });

    it('opens the highest-exposure frame when there is no stack', async () => {
        answerFiles(null);
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/raw/b.fits'));
    });

    it('keeps a frame the person picked', async () => {
        answerFiles(null);
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/raw/b.fits'));
        await userEvent.click(screen.getByText('pick-raw'));
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/raw/a.fits'));
        await new Promise((resolve) => setTimeout(resolve, 50));
        expect(screen.getByTestId('selected').textContent).toBe('/raw/a.fits');
    });

    it('falls back to the highest-exposure frame once when the stack cannot be opened', async () => {
        answerFiles('/stack/missing.fits');
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/raw/b.fits'));
        await new Promise((resolve) => setTimeout(resolve, 50));
        expect(screen.getByTestId('selected').textContent).toBe('/raw/b.fits');
    });
});
