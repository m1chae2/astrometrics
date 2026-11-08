/**
 * @fileoverview Tests of the Observatory Manager layout: the Focuser and
 * Filter Wheel panels are gone, the Calibration Library and Camera Profiles
 * panels are shown, command controls are greyed out with a hover reason that
 * screen readers also get, and a "Show in calibration library" request
 * highlights its row whether it arrives before or after the display opens.
 */

import { postHandoff } from '../../common/utils/displayHandoffStore';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';

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
vi.mock('../../common/components/SelectableList', () => ({ SelectableList: () => <div>target list</div> }));
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
    /** The top block holds Telescope Control, Camera Profiles and the tall Calibration Library, in that order. */
    it('puts the Calibration Library in the top block with Ingest frames in its header', async () => {
        renderDisplay();
        const library = document.getElementById('calibrationLibrary') as HTMLElement;
        const block = library.parentElement as HTMLElement;
        expect(block.classList.contains('observatory-top-block')).toBe(true);
        const titles = Array.from(block.children).map((child) => child.querySelector('.panel__header')?.textContent ?? '');
        expect(titles[0]).toContain('Telescope Control');
        expect(titles[1]).toContain('Camera Profiles');
        expect(titles[2]).toContain('Calibration Library');
        expect(titles).toHaveLength(3);
        expect(library.querySelector('.panel__header')?.textContent).toContain('Ingest frames');
        await screen.findByRole('tab', { name: /^Darks/ });
    });

    /** The left column holds Session, Targets and Controls, top to bottom, and no slim bar is left above the columns. */
    it('has Session, Targets and Controls panels in the left column and no night bar', () => {
        renderDisplay();
        const column = document.querySelector('.observatory-left-column') as HTMLElement;
        const titles = Array.from(column.querySelectorAll(':scope > .panel > .panel__header')).map((header) => header.querySelector(':scope > span')?.textContent);
        expect(titles).toEqual(['Session', 'Targets', 'Controls']);
        expect(document.querySelector('.observatory-night-bar')).toBeNull();
        expect(screen.queryByText('Filter Targets')).toBeNull();
        expect(screen.queryByText('Target List')).toBeNull();
        const session = column.querySelector(':scope > .panel') as HTMLElement;
        expect(within(session).getByTestId('night-banner').getAttribute('role')).toBe('status');
        expect(within(session).getByRole('button', { name: /Night/ })).toBeTruthy();
        const controlsPanel = column.querySelectorAll(':scope > .panel')[2] as HTMLElement;
        // The Why unavailable? button is in the panel header, like the other panels.
        expect(within(controlsPanel.querySelector('.panel__header') as HTMLElement).getByRole('button', { name: 'Why unavailable?' })).toBeTruthy();
        const controls = within(controlsPanel);
        expect(controls.getByRole('button', { name: 'Add Target' })).toBeTruthy();
        expect(controls.getByRole('button', { name: 'Slew to Target' })).toBeTruthy();
    });

    /** The Focuser and Filter Wheel panels are removed; the new panels are shown. */
    it('drops Focuser and Filter Wheel and shows the library and camera panels', async () => {
        renderDisplay();
        expect(screen.queryByText('Focuser')).toBeNull();
        expect(screen.queryByText('Filter Wheel')).toBeNull();
        expect(screen.getByText('Telescope Control')).toBeTruthy();
        expect(screen.queryByText('Manual Guide')).toBeNull();
        expect(screen.queryByText('Safety Limits')).toBeNull();
        expect(screen.queryByText('Tracking Settings')).toBeNull();
        expect(screen.getByText('Alignment and Guiding')).toBeTruthy();
        expect(screen.getByText('Camera Profiles')).toBeTruthy();
        expect(screen.getByText('Calibration Library')).toBeTruthy();
        expect(screen.queryByTestId('camera-noise-example')).toBeNull();
        expect(await screen.findByRole('tab', { name: /^Darks/ })).toBeTruthy();
    });

    /**
     * The Calibration Library table keeps room for its header and four full rows. jsdom has no layout,
     * so this reads the style rule: a 26px header and four 25px rows with a 2px border need 128px.
     */
    it('keeps room in the Calibration Library table for four full rows', () => {
        const css = readFileSync('ui/observatoryManager/observatoryManager.css', 'utf8');
        const minimumHeight = Number(/\.calibration-panel \.calibration-table-container \{[^}]*min-height: (\d+)px/.exec(css)?.[1]);
        expect(minimumHeight).toBeGreaterThanOrEqual(26 + 4 * 25 + 2);
    });

    /**
     * The top block is a grid: one Telescope Control spanning the first two columns on row 1, Camera Profiles under it, and the Calibration
     * Library spanning both rows. jsdom has no layout, so this reads the style rules.
     */
    it('lays the top block out as a grid with one wide Telescope Control and a tall Calibration Library', () => {
        const css = readFileSync('ui/observatoryManager/observatoryManager.css', 'utf8');
        expect(css).toContain('grid-template-areas: "telescope telescope library" "cameras cameras library";');
        // The first two columns are the old Telescope Control and Manual Guide widths, so the library keeps its width.
        expect(css).toContain('grid-template-columns: clamp(240px, 24%, 300px) clamp(190px, 24vh, 260px) minmax(240px, 1fr);');
        expect(css).not.toContain('observatory-manual-guide');
        expect(css).toMatch(/grid-template-rows: var\(--telescope-row-height\)/);
        expect(css).toMatch(/\.calibration-panel \{ grid-area: library; \}/);
    });

    /** The Mount tab holds only the action buttons; the Manual tab holds the direction pad and the slew-speed slider. */
    it('puts the action buttons on Mount and the direction pad on Manual', async () => {
        renderDisplay();
        const user = userEvent.setup();
        const tabs = within(screen.getByRole('tablist', { name: 'Telescope control groups' }));
        expect(tabs.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Mount', 'Manual', 'Limits']);
        ['Start Alignment', 'Stop Alignment', 'Tracking ON', 'Tracking OFF', 'PARK', 'UNPARK'].forEach((name) => {
            expect(screen.getByRole('button', { name })).toBeTruthy();
        });
        expect(document.getElementById('btn-move-n')).toBeNull();
        await user.click(tabs.getByRole('tab', { name: 'Manual' }));
        ['btn-move-nw', 'btn-move-n', 'btn-move-ne', 'btn-move-w', 'btn-manual-stop', 'btn-move-e', 'btn-move-sw', 'btn-move-s', 'btn-move-se', 'input-slew-rate-slider']
            .forEach((id) => expect(document.getElementById(id)).toBeTruthy());
        expect(screen.queryByRole('button', { name: 'PARK' })).toBeNull();
    });

    /** Command controls are disabled; hover reason and aria-describedby carry the reason. */
    it('greys out command controls and gives the reason on hover and to screen readers', async () => {
        renderDisplay();
        await screen.findByRole('tab', { name: /^Darks/ });
        const gates = screen.getAllByTestId('command-gate');
        // Telescope Control buttons (Mount tab), Slew to Target.
        expect(gates).toHaveLength(2);
        gates.forEach((gate) => {
            expect(gate.getAttribute('title')).toBeTruthy();
            const describedBy = gate.getAttribute('aria-describedby') as string;
            expect(document.getElementById(describedBy)?.textContent).toBe(gate.getAttribute('title'));
        });

        const parkButton = screen.getByRole('button', { name: 'PARK' }) as HTMLButtonElement;
        expect(parkButton.disabled).toBe(true);
        expect(parkButton.closest('[data-testid="command-gate"]')?.getAttribute('title')).toBeTruthy();
        await userEvent.click(within(screen.getByRole('tablist', { name: 'Telescope control groups' })).getByRole('tab', { name: 'Manual' }));
        expect((screen.getByRole('button', { name: 'STOP' }) as HTMLButtonElement).disabled).toBe(true);
        // The Telescope and Focuser sections each have their own gate, plus Slew to Target.
        expect(screen.getAllByTestId('command-gate')).toHaveLength(3);

        await userEvent.click(within(screen.getByRole('tablist', { name: 'Telescope control groups' })).getByRole('tab', { name: 'Limits' }));
        const accuracy = screen.getByLabelText('Alignment Accuracy') as HTMLInputElement;
        expect(accuracy.disabled).toBe(true);
        expect(document.getElementById(accuracy.getAttribute('aria-describedby') as string)?.textContent).toBeTruthy();
        await screen.findByLabelText('Minimum altitude');
        const minimumAltitude = document.getElementById('input-limit-min-alt') as HTMLInputElement;
        expect(minimumAltitude.disabled).toBe(true);
        const flipInput = document.getElementById('input-meridian-flip-ha') as HTMLInputElement;
        expect(flipInput.disabled).toBe(true);
        expect(flipInput.getAttribute('aria-describedby')).toBeTruthy();

        const slew = screen.getByRole('button', { name: 'Slew to Target' }) as HTMLButtonElement;
        expect(slew.disabled).toBe(true);
        expect(within(slew.closest('[data-testid="command-gate"]') as HTMLElement).getByText(/Checking|Equipment commands are off/)).toBeTruthy();
    });

    /** Telescope Control and the alignment panel use tabs that follow the WAI-ARIA tab pattern. */
    it('shows the tracking and alignment tabs with keyboard arrow selection', async () => {
        renderDisplay();
        const user = userEvent.setup();
        const trackingTabs = within(screen.getByRole('tablist', { name: 'Telescope control groups' }));
        const controlsTab = trackingTabs.getByRole('tab', { name: 'Mount', selected: true });
        expect(screen.queryByLabelText('Alignment Accuracy')).toBeNull();
        controlsTab.focus();
        await user.keyboard('{ArrowRight}');
        trackingTabs.getByRole('tab', { name: 'Manual', selected: true });
        await user.keyboard('{ArrowRight}');
        const limitsTab = trackingTabs.getByRole('tab', { name: 'Limits', selected: true });
        expect(document.activeElement).toBe(limitsTab);
        expect(screen.getByLabelText('Alignment Accuracy')).toBeTruthy();
        expect(screen.getByLabelText('Guiding Gain')).toBeTruthy();

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
        await screen.findByRole('tab', { name: /^Darks/ });
        act(() => {
            postHandoff('Observatory Manager', 'showCalibrationRow', { calibrationKey: 'dark', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 100, offset: 10, binning: 1 }, rule: 'r' });
        });
        await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]').length).toBe(1));
    });

    /** A request sent before the display opened is kept and shown once it opens. */
    it('shows a request that arrived before the display opened', async () => {
        postHandoff('Observatory Manager', 'showCalibrationRow', { calibrationKey: 'dark', captureSetting: { camera: 'ZWO ASI533MM Pro', gainSetting: 100, offset: 10, binning: 1 }, rule: 'r' });
        renderDisplay();
        await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]').length).toBe(1));
    });
});
