/**
 * @file test_stackChoice.test.tsx
 * @description Checks the stack picker in Image Processing. A target with stacks from more than one
 * camera or lens gets a "Camera and lens" picker in the Data tab; a target with one stack does not.
 * Picking a stack shows that stack's file in the viewer, while the main stack stays the default and
 * stays the one the analysis uses.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '@testing-library/react';
import type { StackChoice } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/services/targetService', async (importOriginal) => ({
    ...(await importOriginal<Record<string, unknown>>()),
    fetchTargetFrameHeader: vi.fn(async () => null),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ connected: true, activeJobs: [] }),
}));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({
        selectedTarget: 'M 27', pendingTarget: 'M 27', setPendingTarget: () => undefined,
        setSelectedTarget: () => undefined, reloadKey: 0, framesReloadKey: 0, forceReload: () => undefined,
        invalidate: () => undefined, commonName: '', catalogId: '',
    }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: new Set() }) }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({
        items: [{ id: 'M 27', value: 'M 27', label: 'M 27' }],
        filterPanel: {}, filterText: '', setFilterText: () => undefined, isLoading: false, isLocalTarget: true,
    }),
}));
vi.mock('../../common/radioList/TargetListFilterPanel', () => ({ TargetListFilterPanel: () => null }));
vi.mock('../../common/fitsViewer/FitsViewerManager', () => ({ FitsViewerManager: () => <div /> }));
vi.mock('../fileBrowser/FileBrowser', () => ({
    FileBrowser: (props: { selectedFile: string | null }) => (
        <span data-testid="selected">{props.selectedFile ?? 'none'}</span>
    ),
    FileBrowserToolbar: () => <div />,
}));
vi.mock('../../common/components/SwitchDisplayButton', () => ({ SwitchDisplayButton: () => <span /> }));
vi.mock('../../common/components/AddTargetModal', () => ({ AddTargetModal: () => null }));
vi.mock('../../common/components/FitsHeaderModal', () => ({ FitsHeaderModal: () => null }));
vi.mock('../components/BrightObjectRow', () => ({ BrightObjectRow: () => <div data-testid="bright-object" /> }));
vi.mock('../components/DepthGoalRow', () => ({ DepthGoalRow: () => <div /> }));
vi.mock('../components/StackSummaryPanel', () => ({
    StackSummaryPanel: () => <div data-testid="stack-summary" />,
}));
vi.mock('../imageDetailsManager/ImageDetailsManager', () => ({
    ImageDetailsManager: (props: { analysisSelectionInput?: { stackedImage: string | null } }) => (
        <span data-testid="analysis-stack">{props.analysisSelectionInput?.stackedImage ?? 'none'}</span>
    ),
}));
vi.mock('../../common/radioList/RadioListManager', () => ({ RadioListManager: () => <div /> }));

import { ImageProcessingDisplay } from '../ImageProcessingDisplay';
import { StackedImagePanel } from '../components/StackedImagePanel';
import { stackChoiceLabel } from '../utils/stackChoices';

const MAIN = '/stacks/M 27/M_27_L_Stacked.fits';
const NIKON_300 = '/stacks/M 27/nikon_300/M_27_RGB_Stacked.fits';

/** The stacks of M 27: the ZWO main stack and one Nikon stack. */
const TWO_STACKS: StackChoice[] = [
    {
        configurationKey: 'ZWO ASI 533MM Pro@405mm', camera: 'ZWO ASI 533MM Pro', focalLengthMm: 405,
        framesStacked: 90, stackedImage: MAIN, isMain: true,
    },
    {
        configurationKey: 'Nikon DSLR DSC D5300@300mm', camera: 'Nikon DSLR DSC D5300', focalLengthMm: 300,
        framesStacked: 40, stackedImage: NIKON_300, isMain: false,
    },
];

/** Sets what the backend returns for the target's files. */
function answerFiles(stacks: StackChoice[]) {
    callBackendMock.mockImplementation(async (method: string) => {
        if (method === 'target:get_files') {
            return { files: [], stackedImage: MAIN, stackedSpectralTarget: null, totalExposure: 70, stacks };
        }
        if (method === 'calibration:query') return { darks: [], biases: [], flats: [] };
        if (method === 'target:get_frames_grouped') return [];
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

describe('stack picker names', () => {
    it('names a stack by camera and focal length, and marks the main one', () => {
        expect(stackChoiceLabel(TWO_STACKS[0])).toBe('ZWO ASI 533MM Pro · 405 mm (main)');
        expect(stackChoiceLabel(TWO_STACKS[1])).toBe('Nikon DSLR DSC D5300 · 300 mm');
    });
});

describe('StackedImagePanel stack picker', () => {
    it('is hidden when the target has one stack', () => {
        render(<StackedImagePanel path={MAIN} targetId="M 27" onView={() => {}}
            stackChoices={[TWO_STACKS[0]]} viewedStackKey={TWO_STACKS[0].configurationKey} onChooseStack={() => {}} />);
        expect(screen.queryByTestId('stack-choice-row')).toBeNull();
    });

    it('lists every stack and reports the one picked', async () => {
        const onChoose = vi.fn();
        render(<StackedImagePanel path={MAIN} targetId="M 27" onView={() => {}}
            stackChoices={TWO_STACKS} viewedStackKey={TWO_STACKS[0].configurationKey} onChooseStack={onChoose} />);
        await userEvent.click(screen.getByRole('button', { name: /Stack to show/ }));
        const menu = within(screen.getByRole('group', { name: 'Stack to show' }));
        expect(menu.getAllByRole('button').map((option) => option.textContent)).toEqual([
            'ZWO ASI 533MM Pro · 405 mm (main)90 frames',
            'Nikon DSLR DSC D5300 · 300 mm40 frames',
        ]);
        await userEvent.click(menu.getByRole('button', { name: /Nikon DSLR DSC D5300 · 300 mm/ }));
        expect(onChoose).toHaveBeenCalledWith('Nikon DSLR DSC D5300@300mm');
    });
});

describe('Image Processing stack choice', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        localStorage.clear();
    });

    it('opens the main stack and shows no picker for a target with one stack', async () => {
        answerFiles([TWO_STACKS[0]]);
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe(MAIN));
        expect(screen.queryByTestId('stack-choice-row')).toBeNull();
    });

    it('opens the picked stack without changing the main stack', async () => {
        answerFiles(TWO_STACKS);
        renderDisplay();
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe(MAIN));
        expect(screen.getByTestId('stack-summary')).toBeTruthy();

        await userEvent.click(screen.getByRole('button', { name: /Stack to show/ }));
        await userEvent.click(within(screen.getByRole('group', { name: 'Stack to show' }))
            .getByRole('button', { name: /Nikon DSLR DSC D5300 · 300 mm/ }));

        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe(NIKON_300));
        expect(screen.getByRole('button', { name: 'Stack to show: Nikon DSLR DSC D5300 · 300 mm' })).toBeTruthy();
        // The analysis and the stack summary stay with the main stack, and say so.
        expect(screen.getByTestId('analysis-stack').textContent).toBe(MAIN);
        expect(screen.queryByTestId('stack-summary')).toBeNull();
        expect(screen.getByTestId('other-stack-note')).toBeTruthy();
        // The file list was read once; picking a stack asks the backend for nothing new.
        expect(callBackendMock.mock.calls.filter(([method]) => method === 'target:get_files')).toHaveLength(1);

        await userEvent.click(screen.getByRole('button', { name: /Stack to show/ }));
        await userEvent.click(within(screen.getByRole('group', { name: 'Stack to show' }))
            .getByRole('button', { name: /\(main\)/ }));
        await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe(MAIN));
        expect(screen.getByTestId('stack-summary')).toBeTruthy();
    });
});
