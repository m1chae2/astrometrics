import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { act, render, screen, waitFor } from '@testing-library/react';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ connected: true, activeJobs: [] }),
}));
const emitToastMock = vi.fn();
vi.mock('../../common/utils/emitToast', () => ({ emitToast: (...args: unknown[]) => emitToastMock(...args) }));
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
/** The props the viewer was last given, so the tests can read what the screen asked it to show. */
let viewerProps: Record<string, unknown> = {};
vi.mock('../../common/fitsViewer/FitsViewerManager', () => ({
    FitsViewerManager: (props: Record<string, unknown>) => { viewerProps = props; return <div />; },
}));
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
import { IMAGE_VIEW_STORAGE_KEY, loadImageView, saveImageView } from '../../common/utils/imageViewStore';
import type { SavedImageView } from '../../common/utils/imageViewStore';
import { postHandoff } from '../../common/utils/displayHandoffStore';
import { STAR_FOCUS_SCALE } from '../hooks/useStarFocusRequest';

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

/** Adds saved star names for the stack (and no photometry) to what the backend returns. */
function answerStackNames(stars: Record<string, unknown>[]) {
    const files = callBackendMock.getMockImplementation() as (method: string) => Promise<unknown>;
    callBackendMock.mockImplementation(async (method: string) => {
        if (method === 'analysis:get_stack_identification') {
            return {
                running: false, jobId: null, progress: null, message: null, error: null,
                identification: { stars, matchedCount: stars.length, unmatchedCount: 0, catalogOnlyCount: 0, solutionReused: true, notes: [] },
            };
        }
        if (method === 'astronomy:list') return [];
        return files(method);
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

/** A saved view of a raw frame (not the stack the screen would pick by default). */
const SAVED: SavedImageView = {
    target: 'M 81', file: '/raw/a.fits', displayMode: 'linear', astrometry: true, detections: true,
    view: { x: 0.3, y: 0.6, zoomOverFit: 5, turn: 0 }, returnPending: true, arrivedFromStar: 'Vega',
};

describe('Image Processing saved view', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        localStorage.clear();
        viewerProps = {};
    });

    it('puts the saved file, overlays and view back when the screen is shown again', async () => {
        answerFiles('/stack/M81.fits');
        saveImageView(SAVED);
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/raw/a.fits'));
        await waitFor(() => expect(viewerProps.restoreView).toMatchObject({ view: SAVED.view }));
        expect(viewerProps.showDetections).toBe(true);
        expect(viewerProps.showOverlay).toBe(true);
        // It is put back once: the record no longer asks for a return, and the button's flag is cleared.
        expect(loadImageView()).toMatchObject({ returnPending: false, arrivedFromStar: null, file: '/raw/a.fits' });
    });

    it('opens the default file when nothing was saved', async () => {
        answerFiles('/stack/M81.fits');
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/stack/M81.fits'));
        expect(viewerProps.restoreView ?? null).toBeNull();
    });

    it('saves the view once it rests, and marks it to be put back when the screen is left', async () => {
        answerFiles(null);
        const { unmount } = renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/raw/b.fits'));
        const view = { x: 0.2, y: 0.8, zoomOverFit: 3, turn: 0 };
        act(() => (viewerProps.onViewChange as (view: unknown) => void)(view));
        await waitFor(() => expect(loadImageView()?.view).toEqual(view), { timeout: 2000 });
        expect(loadImageView()).toMatchObject({ target: 'M 81', file: '/raw/b.fits', returnPending: false });
        unmount();
        expect(loadImageView()).toMatchObject({ file: '/raw/b.fits', view, returnPending: true });
    });

    it('lets a request to open a target win over the saved view', async () => {
        answerFiles('/stack/M81.fits');
        saveImageView(SAVED);
        postHandoff('Image Processing', 'targetSelected', { targetId: 'M 81' });
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/stack/M81.fits'));
        expect(viewerProps.restoreView ?? null).toBeNull();
        expect(loadImageView()?.returnPending).toBe(false);
    });

    it('shows a star asked for by "Back to image": main stack, star selected, centered and zoomed, over the saved view', async () => {
        answerFiles('/stack/M81.fits');
        answerStackNames([{ id: 'det-1', name: 'HD 1', gaiaSourceId: '42', x: 250, y: 100, referenceWidth: 1000, referenceHeight: 500 }]);
        saveImageView(SAVED);
        postHandoff('Image Processing', 'targetSelected', {
            targetId: 'M 81', enableAstrometry: true, focusLibraryStarId: 'HD 1', focusStarName: 'HD 1',
        });
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/stack/M81.fits'));
        await waitFor(() => expect(viewerProps.restoreView).toMatchObject({
            view: { x: 0.25, y: 0.2 }, scale: STAR_FOCUS_SCALE, pictureWidth: 1000, pickStar: true,
        }));
        expect(viewerProps.selectedStarId).toBe('det-1');
        expect(viewerProps.showOverlay).toBe(true);
        expect(loadImageView()?.returnPending).toBe(false);
    });

    it('still opens the stack, with a short note, when the stack does not name the star', async () => {
        answerFiles('/stack/M81.fits');
        answerStackNames([{ id: 'det-1', name: 'HD 1', x: 250, y: 100, referenceWidth: 1000, referenceHeight: 500 }]);
        postHandoff('Image Processing', 'targetSelected', {
            targetId: 'M 81', enableAstrometry: true, focusLibraryStarId: 'HD 999', focusStarName: 'HD 999',
        });
        renderDisplay();
        await waitFor(() => expect(emitToastMock).toHaveBeenCalledWith(
            'HD 999 is not among the named stars of this stack.', 'info', 'Astrometry'
        ));
        expect(screen.getByTestId('selected').textContent).toBe('/stack/M81.fits');
        expect(viewerProps.restoreView ?? null).toBeNull();
    });

    it('keeps working when storage holds something that is not a saved view', async () => {
        answerFiles(null);
        localStorage.setItem(IMAGE_VIEW_STORAGE_KEY, '{"nonsense": true}');
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/raw/b.fits'));
    });
});
