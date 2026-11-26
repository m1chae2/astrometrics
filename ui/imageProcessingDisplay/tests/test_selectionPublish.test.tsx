/**
 * @file test_selectionPublish.test.tsx
 * @description Checks that Image Processing tells the backend what is picked (target, file, star) for the
 * Command Console's `selection` object, and that it shows a file the console asks for.
 */
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
const navigateMock = vi.fn(async (..._args: unknown[]) => ({ handledRemotely: true }));
vi.mock('../../common/utils/displayCoordinator', () => ({ navigateToElement: (...args: unknown[]) => navigateMock(...args) }));
vi.mock('../../common/radioList/RadioListManager', () => ({ RadioListManager: () => <div /> }));

import { ImageProcessingDisplay } from '../ImageProcessingDisplay';
import { saveImageView } from '../../common/utils/imageViewStore';
import type { SavedImageView } from '../../common/utils/imageViewStore';
import { postHandoff } from '../../common/utils/displayHandoffStore';
import type { AstrometryOverlayStar } from '../../common/services/astronomyService';

/** Two raw frames; the second has the longer exposure. */
const RAW_FILES = [
    { path: '/raw/a.fits', name: 'a.fits', camera: 'cam', iso: '', exposure: '10', filter: 'L', date: '' },
    { path: '/raw/b.fits', name: 'b.fits', camera: 'cam', iso: '', exposure: '60', filter: 'L', date: '' },
];

/** One named star of the stack, with photometry under the library id "HD 1". */
const STACK_STAR = { id: 'det-1', name: 'HD 1', libraryId: 'HD 1', x: 250, y: 100, raDeg: 148.9, decDeg: 69.1, referenceWidth: 1000, referenceHeight: 500 };

/** Sets what the backend returns: the target's files, the stack's star names and the stars with photometry. */
function answerBackend() {
    callBackendMock.mockImplementation(async (method: string) => {
        if (method === 'target:get_files') {
            return { files: RAW_FILES, stackedImage: '/stack/M81.fits', stackedSpectralTarget: null, totalExposure: 70 };
        }
        if (method === 'analysis:get_stack_identification') {
            return {
                running: false, jobId: null, progress: null, message: null, error: null,
                identification: { stars: [STACK_STAR], matchedCount: 1, unmatchedCount: 0, catalogOnlyCount: 0, solutionReused: true, notes: [] },
            };
        }
        if (method === 'astronomy:list') return [{ id: 'HD 1' }];
        if (method === 'target:get_frames_grouped') return [];
        // The picture never arrives, so the screen does not fall back from the stack to a raw frame.
        if (method === 'images:convert_fits' || method === 'images:get_fits_file') return new Promise(() => undefined);
        if (method === 'calibration:query') return { darks: [], biases: [], flats: [] };
        return null;
    });
}

/** The payloads sent with `ui:set_selection`, in order. */
function sentSelections(): unknown[] {
    return callBackendMock.mock.calls.filter(([method]) => method === 'ui:set_selection').map(([, params]) => params);
}

/** The last payload sent with `ui:set_selection`, or undefined. */
function lastSentSelection(): unknown {
    const sent = sentSelections();
    return sent[sent.length - 1];
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

describe('Image Processing selection for the Command Console', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        navigateMock.mockClear();
        localStorage.clear();
        viewerProps = {};
        answerBackend();
    });

    it('sends the shown stack, then the clicked star, then the picked raw frame with no star', async () => {
        postHandoff('Image Processing', 'targetSelected', { targetId: 'M 81', enableAstrometry: true });
        renderDisplay();
        await waitFor(() => expect(sentSelections()).toContainEqual(
            { target: 'M 81', path: '/stack/M81.fits', kind: 'stack', star: null }
        ));
        // The star is drawn once its photometry is known; clicking it selects it.
        await waitFor(() => expect((viewerProps.overlayStars as AstrometryOverlayStar[] | undefined)?.length).toBe(1));
        const drawn = (viewerProps.overlayStars as AstrometryOverlayStar[])[0];
        act(() => (viewerProps.onStarClick as (star: AstrometryOverlayStar) => void)(drawn));
        await waitFor(() => expect(lastSentSelection()).toEqual({
            target: 'M 81', path: '/stack/M81.fits', kind: 'stack',
            star: { id: 'det-1', name: 'HD 1', library_id: 'HD 1', ra: 148.9, dec: 69.1, x: 250, y: 100 },
        }));
        // Another file: the star is not on it, so no star is sent.
        await userEvent.click(screen.getByText('pick-raw'));
        await waitFor(() => expect(lastSentSelection()).toEqual(
            { target: 'M 81', path: '/raw/a.fits', kind: 'frame', star: null }
        ));
    });

    it('sends the restored view when the screen first opens with one', async () => {
        const saved: SavedImageView = {
            target: 'M 81', file: '/raw/a.fits', displayMode: 'linear', astrometry: false, detections: false,
            view: null, returnPending: true, arrivedFromStar: null,
        };
        saveImageView(saved);
        renderDisplay();
        await waitFor(() => expect(lastSentSelection()).toEqual(
            { target: 'M 81', path: '/raw/a.fits', kind: 'frame', star: null }
        ));
        // The default stack is never sent in between, because the pick rests on the restored file.
        expect(sentSelections()).not.toContainEqual(expect.objectContaining({ path: '/stack/M81.fits' }));
    });

    it('shows the file the Command Console asks for with show(target=..., path=...)', async () => {
        postHandoff('Image Processing', 'targetSelected', { targetId: 'M 81', filePath: '/raw/a.fits' });
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('/raw/a.fits'));
        // It stays on that file: the default stack does not replace it.
        await new Promise((resolve) => setTimeout(resolve, 50));
        expect(screen.getByTestId('selected').textContent).toBe('/raw/a.fits');
    });
});
