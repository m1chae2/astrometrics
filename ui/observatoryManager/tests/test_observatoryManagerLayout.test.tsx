/**
 * @fileoverview Tests of the Observatory Manager layout: the Focuser and
 * Filter Wheel panels are gone, the Calibration Library and Camera Profiles
 * panels are shown, command controls are greyed out with a hover reason that
 * screen readers also get, and a "Show in calibration library" request
 * highlights its row whether it arrives before or after the display opens.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { act, render, screen, waitFor, within } from '@testing-library/react';

const callBackendMock = vi.fn();

vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/components/IngestFramesModal', () => ({ IngestFramesModal: () => null }));
vi.mock('../../common/hooks/useIngestionManager', () => ({ useIngestionManager: () => ({ isActive: false }) }));
vi.mock('../IndiStatusPanel', () => ({ IndiStatusPanel: () => <div>Devices</div> }));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
    useTelescopeStatus: () => ({ trackingStatus: 'Idle', telemetry: { alignmentAttempts: [], guidingHistory: [] } }),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({ useAstrometrics: () => ({ connected: true }) }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({
        selectedTarget: null, pendingTarget: null, setPendingTarget: () => undefined,
        reloadKey: 0, forceReload: () => undefined, raShared: '1', decShared: '2',
    }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: [] }) }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({ items: [], filterPanel: {}, filterText: '', setFilterText: () => undefined, isLoading: false }),
}));
vi.mock('../../common/radioList/RadioListManager', () => ({
    RadioListManager: ({ actions }: { actions: React.ReactNode }) => <div>{actions}</div>,
}));
vi.mock('../../common/radioList/TargetListFilterPanel', () => ({ TargetListFilterPanel: () => null }));
vi.mock('../../common/components/AddTargetModal', () => ({ AddTargetModal: () => null }));
vi.mock('../panels/GuidingStatusPanel', () => ({
    GuidingTrendsPlot: () => <div>trends</div>,
    GuidingTargetPlot: () => <div>target</div>,
}));
vi.mock('../../common/hooks/useEquipmentConfiguration', () => ({
    useEquipmentConfiguration: () => ({
        configuration: null,
        availableCameras: [{ name: 'ZWO ASI533MM Pro', pixelSizeUm: 3.76, sensorWidthPx: 3008, sensorHeightPx: 3008 }],
        loading: false, error: null, reload: () => undefined, setActiveCamera: () => undefined,
    }),
}));

import { ObservatoryManager } from '../ObservatoryManager';

const libraryReply = {
    detail: 'counts',
    darks: [{ row_key: 'dark-a', camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '1x1', exposure: 60, count: 20 }],
    biases: [],
    flats: [],
    camera_totals: [{ camera: 'ZWO ASI533MM Pro', dark_count: 20, bias_count: 0, flat_count: 0 }],
};
const matchReply = { detail: 'match', matched_row_keys: ['dark-a'], camera_totals: [] };

/**
 * Renders the Observatory Manager inside a fresh query client.
 * @return The render result.
 */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}><ObservatoryManager /></QueryClientProvider>);
}

beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockImplementation(async (method: string, params?: { detail?: string }) => {
        if (method === 'calibration:query') return params?.detail === 'match' ? matchReply : libraryReply;
        if (method === 'observatory:get_active_telescope') return { id: 't1', name: 'Scope', focal_length_mm: 500 };
        if (method === 'observatory:safety_status') return { delegation_policy: { id: 'p', capability_delegations: [] } };
        throw new Error(`unexpected ${method}`);
    });
});

describe('ObservatoryManager layout', () => {
    /** The Calibration Library is in the top row beside Tracking Settings, with Ingest frames in its header. */
    it('puts the Calibration Library in the top row with Ingest frames in its header', async () => {
        renderDisplay();
        const library = document.getElementById('calibrationLibrary') as HTMLElement;
        expect(library.parentElement?.classList.contains('observatory-top-row')).toBe(true);
        expect(library.previousElementSibling?.textContent).toContain('Tracking Settings');
        expect(library.querySelector('.panel__header')?.textContent).toContain('Ingest frames');
        await screen.findByText('Darks:');
    });

    /** The Focuser and Filter Wheel panels are removed; the new panels are shown. */
    it('drops Focuser and Filter Wheel and shows the library and camera panels', async () => {
        renderDisplay();
        expect(screen.queryByText('Focuser')).toBeNull();
        expect(screen.queryByText('Filter Wheel')).toBeNull();
        expect(screen.getByText('Telescope Control')).toBeTruthy();
        expect(screen.getByText('Manual Guide')).toBeTruthy();
        expect(screen.getByText('Safety Limits')).toBeTruthy();
        expect(screen.getByText('Alignment and Guiding')).toBeTruthy();
        expect(screen.getByText('Camera Profiles')).toBeTruthy();
        expect(screen.getByText('Calibration Library')).toBeTruthy();
        expect(screen.queryByTestId('camera-noise-example')).toBeNull();
        expect(await screen.findByText('Darks:')).toBeTruthy();
    });

    /** Command controls are disabled; hover reason and aria-describedby carry the reason. */
    it('greys out command controls and gives the reason on hover and to screen readers', async () => {
        renderDisplay();
        await screen.findByLabelText('Minimum altitude');
        const gates = screen.getAllByTestId('command-gate');
        // Telescope Control, Tracking Settings, Manual Guide, Safety Limits, Slew to Target.
        expect(gates).toHaveLength(5);
        gates.forEach((gate) => {
            expect(gate.getAttribute('title')).toBeTruthy();
            const describedBy = gate.getAttribute('aria-describedby') as string;
            expect(document.getElementById(describedBy)?.textContent).toBe(gate.getAttribute('title'));
        });

        const parkButton = screen.getByRole('button', { name: 'PARK' }) as HTMLButtonElement;
        expect(parkButton.disabled).toBe(true);
        expect(parkButton.closest('[data-testid="command-gate"]')?.getAttribute('title')).toBeTruthy();
        expect((screen.getByRole('button', { name: 'STOP' }) as HTMLButtonElement).disabled).toBe(true);

        const accuracy = screen.getByLabelText('Alignment Accuracy') as HTMLInputElement;
        expect(accuracy.disabled).toBe(true);
        expect(document.getElementById(accuracy.getAttribute('aria-describedby') as string)?.textContent).toBeTruthy();

        const minimumAltitude = document.getElementById('input-limit-min-alt') as HTMLInputElement;
        expect(minimumAltitude.disabled).toBe(true);
        await userEvent.click(screen.getByRole('tab', { name: 'Hour angle' }));
        const flipInput = document.getElementById('input-meridian-flip-ha') as HTMLInputElement;
        expect(flipInput.disabled).toBe(true);
        expect(flipInput.getAttribute('aria-describedby')).toBeTruthy();

        const slew = screen.getByRole('button', { name: 'Slew to Target' }) as HTMLButtonElement;
        expect(slew.disabled).toBe(true);
        expect(slew.getAttribute('aria-disabled')).toBe('true');
        expect(within(slew.closest('[data-testid="command-gate"]') as HTMLElement).getByText(/Checking|Equipment commands are off/)).toBeTruthy();
    });

    /** Tracking Settings and the alignment panel use tabs that follow the WAI-ARIA tab pattern. */
    it('shows the tracking and alignment tabs with keyboard arrow selection', async () => {
        renderDisplay();
        const user = userEvent.setup();
        const trackingTabs = within(screen.getByRole('tablist', { name: 'Tracking setting groups' }));
        const alignmentTab = trackingTabs.getByRole('tab', { name: 'Alignment', selected: true });
        expect(screen.getByLabelText('Alignment Accuracy')).toBeTruthy();
        expect(screen.queryByLabelText('Guiding Gain')).toBeNull();
        alignmentTab.focus();
        await user.keyboard('{ArrowRight}');
        const guidingTab = trackingTabs.getByRole('tab', { name: 'Guiding', selected: true });
        expect(document.activeElement).toBe(guidingTab);
        expect(screen.getByLabelText('Guiding Gain')).toBeTruthy();
        expect(screen.queryByLabelText('Alignment Accuracy')).toBeNull();

        // The shared alignment panel: Alignment table first, Guiding target on the second tab.
        expect(screen.getByText('No alignment attempts')).toBeTruthy();
        const sharedTabs = within(screen.getByRole('tablist', { name: 'Alignment and guiding views' }));
        expect(sharedTabs.getByRole('tab', { name: 'Alignment' }).getAttribute('aria-selected')).toBe('true');
        await user.click(sharedTabs.getByRole('tab', { name: 'Guiding target' }));
        expect(screen.getByText('target')).toBeTruthy();
        expect(screen.queryByText('No alignment attempts')).toBeNull();
    });

    /** Adding a target is not an equipment command and stays enabled. */
    it('leaves Add Target enabled', () => {
        renderDisplay();
        expect((screen.getByRole('button', { name: 'Add Target' }) as HTMLButtonElement).disabled).toBe(false);
    });

    /** A request that arrives while the display is open highlights its row. */
    it('highlights the requested calibration row', async () => {
        renderDisplay();
        await screen.findByText('Darks:');
        act(() => {
            window.dispatchEvent(new CustomEvent('astrometrics:showCalibrationRow', {
                detail: { calibrationKey: 'dark', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 100, offset: 10, binning: 1 }, rule: 'r' },
            }));
        });
        await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]').length).toBe(1));
    });

    /** A request sent before the display opened is kept and shown once it opens. */
    it('shows a request that arrived before the display opened', async () => {
        window.dispatchEvent(new CustomEvent('astrometrics:showCalibrationRow', {
            detail: { calibrationKey: 'dark', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 100, offset: 10, binning: 1 }, rule: 'r' },
        }));
        renderDisplay();
        await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]').length).toBe(1));
    });
});
