/**
 * @file test_observatoryNightMode.test.tsx
 * @description Tests of the Observatory Manager's night mode. The shared night picker at the top
 * starts on Live (controls enabled, live plot). Choosing a past night shows a status banner,
 * greys the live-only controls with the reason, shows that night's guiding summary, lists only
 * that night's alignment attempts (or says there were none), and filters and highlights the
 * Target List. Choosing Live brings everything back. New guiding data refreshes the night list.
 */

import React from 'react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
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
vi.mock('../../common/hooks/useCommandAvailability', () => ({
    useCommandAvailability: () => ({ canCommand: true, reason: '' }),
}));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
    useTelescopeStatus: () => ({
        trackingStatus: 'Idle',
        telemetry: {
            alignmentAttempts: [{ status: 'aligned', targetName: 'Live Target', timestamp: 1_762_300_000, deltaRaArcsec: 1, deltaDecArcsec: 2 }],
            guidingHistory: [{ time: 1, dra: 0.1, ddec: 0.2, snr: 30, rmsRa: 0.3, rmsDec: 0.3, pulseRa: 0, pulseDec: 0 }],
        },
    }),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({ useAstrometrics: () => ({ connected: true }) }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({
        selectedTarget: 'M_13', pendingTarget: 'M_13', setPendingTarget: () => undefined, setSelectedTarget: () => undefined,
        reloadKey: 0, forceReload: () => undefined, raShared: '1', decShared: '2',
    }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: new Set() }) }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({
        items: [
            { id: 'M_13', value: 'M_13', label: 'M 13' },
            { id: 'M_57', value: 'M_57', label: 'M 57' },
            { id: 'NGC_7331', value: 'NGC_7331', label: 'NGC 7331' },
        ],
        filterPanel: {}, filterText: '', setFilterText: () => undefined, isLoading: false,
    }),
}));
vi.mock('../../common/radioList/TargetListFilterPanel', () => ({ TargetListFilterPanel: () => null }));
vi.mock('../../common/components/AddTargetModal', () => ({ AddTargetModal: () => null }));
vi.mock('../../common/components/plotting/TimeSeriesPlot', () => ({ TimeSeriesPlot: () => <div>live plot</div> }));
vi.mock('../../common/components/plotting/ScatterPlot', () => ({ ScatterPlot: () => <div>scatter</div> }));
vi.mock('../../common/hooks/useEquipmentConfiguration', () => ({
    useEquipmentConfiguration: () => ({
        configuration: null,
        availableCameras: [{ name: 'ZWO ASI533MM Pro', pixelSizeUm: 3.76, sensorWidthPx: 3008, sensorHeightPx: 3008 }],
        loading: false, error: null, reload: () => undefined, setActiveCamera: () => undefined,
    }),
}));

import { ObservatoryManager } from '../ObservatoryManager';
import { socketClient } from '../../common/utils/socketClient';

const NIGHT_REASON = 'Viewing the night of 2026-11-04. Controls are live-only; pick Live to use them.';

const nights = [
    { nightId: '2026-11-04', nightDate: '2026-11-04', targetsImaged: ['M_13', 'M_57'], hasGuidingData: true },
    { nightId: '2026-11-03', nightDate: '2026-11-03', targetsImaged: [], hasGuidingData: false },
];

const libraryReply = {
    detail: 'counts',
    darks: [{ row_key: 'dark-a', camera: 'ZWO ASI533MM Pro', iso: '100', offset: 10, binning: '1x1', exposure: 60, count: 20 }],
    biases: [],
    flats: [],
    camera_totals: [{ camera: 'ZWO ASI533MM Pro', dark_count: 20, bias_count: 0, flat_count: 0 }],
};

/**
 * Renders the Observatory Manager inside a fresh query client.
 * @return The render result.
 */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}><ObservatoryManager /></QueryClientProvider>);
}

/**
 * Counts the calls the mock backend has seen for one method.
 * @param method The method name.
 * @return The count.
 */
const countCalls = (method: string): number => callBackendMock.mock.calls.filter((call) => call[0] === method).length;

/**
 * Opens the night picker and chooses an entry.
 * @param user The user-event instance.
 * @param entryText The text of the entry, such as "Live" or the night's date.
 */
async function chooseNight(user: ReturnType<typeof userEvent.setup>, entryText: string) {
    await user.click(screen.getByRole('button', { name: /^Night:/ }));
    const menu = screen.getByRole('group', { name: 'Night' });
    await user.click(within(menu).getByText(entryText).closest('button') as HTMLButtonElement);
}

beforeAll(() => {
    // jsdom has no ResizeObserver, which the Target List uses to measure its height.
    vi.stubGlobal('ResizeObserver', class {
        observe(): void {}
        disconnect(): void {}
    });
});

beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockImplementation(async (method: string, params?: { detail?: string; session_id?: string }) => {
        if (method === 'history:nights') return nights;
        if (method === 'history:night_quality') {
            return {
                nightId: params?.session_id,
                needsAttention: { warningCount: 1 },
                targets: [
                    { targetId: 'M_13', framesTaken: 12, needsAttention: { warningCount: 2 } },
                    { targetId: 'M_57', framesTaken: 1, needsAttention: { warningCount: 0 } },
                ],
                guiding: { available: true, rmsPerAxisArcsec: 0.85, statements: [], runs: [] },
            };
        }
        if (method === 'telescope:list_alignment_sessions') return [{ sessionId: '2026-11-04', sessionDate: '2026-11-04' }];
        if (method === 'telescope:get_session_alignment') {
            return {
                alignmentAttempts: [{ status: 'aligned', targetName: 'Night Target', timestamp: 1_762_200_000, deltaRaArcsec: 3, deltaDecArcsec: 4 }],
                alignmentTargets: [], polarAlignment: null,
            };
        }
        if (method === 'calibration:query') return libraryReply;
        if (method === 'observatory:get_active_telescope') return { id: 't1', name: 'Scope', focal_length_mm: 500 };
        if (method === 'observatory:safety_status') return { delegation_policy: { id: 'p', capability_delegations: [] } };
        throw new Error(`unexpected ${method}`);
    });
});

describe('Observatory Manager night mode', () => {
    /** The view starts on Live: nothing is greyed for night reasons, the live plot and live alignment show. */
    it('starts on Live with controls enabled and the live views', async () => {
        renderDisplay();
        expect(screen.getByRole('button', { name: 'Night: Live' })).toBeTruthy();
        expect(screen.getByTestId('night-banner').textContent).toBe('');
        expect(screen.getByTestId('night-banner').getAttribute('role')).toBe('status');
        expect((screen.getByRole('button', { name: 'PARK' }) as HTMLButtonElement).disabled).toBe(false);
        expect((screen.getByRole('button', { name: 'Add Target' }) as HTMLButtonElement).disabled).toBe(false);
        expect((screen.getByRole('button', { name: 'Slew to Target' }) as HTMLButtonElement).disabled).toBe(false);
        expect((await screen.findByRole('button', { name: 'Ingest frames' }) as HTMLButtonElement).disabled).toBe(false);
        expect((screen.getByRole('button', { name: 'Measure noise' }) as HTMLButtonElement).disabled).toBe(false);
        expect(screen.getByText('live plot')).toBeTruthy();
        expect(screen.getByText('Live Target')).toBeTruthy();
        expect(screen.getByText('M 57')).toBeTruthy();
    });

    /** The picker lists every night, with the targets imaged as the detail. */
    it('offers each night with its targets', async () => {
        const user = userEvent.setup();
        renderDisplay();
        await waitFor(() => expect(countCalls('history:nights')).toBeGreaterThan(0));
        await user.click(screen.getByRole('button', { name: /^Night:/ }));
        const menu = screen.getByRole('group', { name: 'Night' });
        await waitFor(() => expect(within(menu).getByText('2026-11-04')).toBeTruthy());
        expect(within(menu).getByText('M 13, M 57')).toBeTruthy();
        expect(within(menu).getByText('2026-11-03')).toBeTruthy();
    });

    /** A past night: banner, greyed controls with the reason, guiding summary, that night's alignment, filtered targets. */
    it('shows the whole view for the chosen night and returns to Live', async () => {
        const user = userEvent.setup();
        renderDisplay();
        await waitFor(() => expect(countCalls('history:nights')).toBeGreaterThan(0));
        await user.click(screen.getByRole('button', { name: /^Night:/ }));
        await screen.findByText('2026-11-04');
        await user.click(screen.getByText('2026-11-04').closest('button') as HTMLButtonElement);

        expect(screen.getByRole('button', { name: 'Night: 2026-11-04' })).toBeTruthy();
        expect(screen.getByTestId('night-banner').textContent).toBe(NIGHT_REASON);
        expect(screen.getByTestId('night-banner').getAttribute('role')).toBe('status');

        // Live-only controls are greyed with the reason.
        const park = screen.getByRole('button', { name: 'PARK' }) as HTMLButtonElement;
        expect(park.disabled).toBe(true);
        expect(park.closest('[data-testid="command-gate"]')?.getAttribute('title')).toBe(NIGHT_REASON);
        for (const name of ['Add Target', 'Download Frames', 'Slew to Target', 'Ingest frames', 'Measure noise']) {
            const button = await screen.findByRole('button', { name }) as HTMLButtonElement;
            expect(button.disabled, name).toBe(true);
        }
        // The other Telescope Control tabs are greyed with the same reason.
        const telescopeTabs = within(screen.getByRole('tablist', { name: 'Telescope control groups' }));
        await user.click(telescopeTabs.getByRole('tab', { name: 'Alignment' }));
        expect((screen.getByLabelText('Alignment Accuracy') as HTMLInputElement).disabled).toBe(true);
        await user.click(telescopeTabs.getByRole('tab', { name: 'Guiding' }));
        expect((screen.getByRole('button', { name: 'Start Guiding' }) as HTMLButtonElement).disabled).toBe(true);
        await user.click(telescopeTabs.getByRole('tab', { name: 'Altitude' }));
        expect(((await screen.findByRole('button', { name: 'Apply safety limits' })) as HTMLButtonElement).disabled).toBe(true);
        await user.click(telescopeTabs.getByRole('tab', { name: 'Controls' }));
        expect(screen.getByRole('button', { name: 'Add Target' }).getAttribute('title')).toBe(NIGHT_REASON);
        expect(screen.getByRole('button', { name: 'Ingest frames' }).getAttribute('title')).toBe(NIGHT_REASON);
        expect(screen.getByRole('button', { name: 'Measure noise' }).getAttribute('title')).toBe(NIGHT_REASON);

        // Guiding Trends shows the night's summary.
        expect(await screen.findByText('0.85 arcsec')).toBeTruthy();
        expect(screen.queryByText('live plot')).toBeNull();
        expect(callBackendMock).toHaveBeenCalledWith('history:night_quality', { session_id: '2026-11-04' });

        // Alignment shows only that night's attempts.
        expect(await screen.findByText('Night Target')).toBeTruthy();
        expect(screen.queryByText('Live Target')).toBeNull();
        expect(callBackendMock).toHaveBeenCalledWith('telescope:get_session_alignment', { session_id: '2026-11-04' });

        // The Target List shows that night's targets, with the review's counts.
        expect(screen.getByText('M 13')).toBeTruthy();
        expect(screen.getByText('M 57')).toBeTruthy();
        expect(screen.queryByText('NGC 7331')).toBeNull();
        expect(screen.queryByRole('checkbox', { name: /Show all targets/ })).toBeNull();
        expect(await screen.findByText('12 frames, 2 warnings')).toBeTruthy();
        expect(screen.getByText('1 frame')).toBeTruthy();

        // Back to Live restores everything.
        await chooseNight(user, 'Live');
        expect(screen.getByRole('button', { name: 'Night: Live' })).toBeTruthy();
        expect(screen.getByTestId('night-banner').textContent).toBe('');
        expect((screen.getByRole('button', { name: 'PARK' }) as HTMLButtonElement).disabled).toBe(false);
        expect((screen.getByRole('button', { name: 'Add Target' }) as HTMLButtonElement).disabled).toBe(false);
        expect((screen.getByRole('button', { name: 'Ingest frames' }) as HTMLButtonElement).disabled).toBe(false);
        expect((screen.getByRole('button', { name: 'Measure noise' }) as HTMLButtonElement).disabled).toBe(false);
        expect(screen.getByText('live plot')).toBeTruthy();
        expect(screen.getByText('Live Target')).toBeTruthy();
        expect(screen.getByText('NGC 7331')).toBeTruthy();
        expect(screen.queryByText('Night Target')).toBeNull();
    });

    /** A night with no alignment session and no targets says so instead of showing rows. */
    it('shows empty states for a night with no alignment and no targets', async () => {
        const user = userEvent.setup();
        renderDisplay();
        await waitFor(() => expect(countCalls('history:nights')).toBeGreaterThan(0));
        await user.click(screen.getByRole('button', { name: /^Night:/ }));
        await screen.findByText('2026-11-03');
        await user.click(screen.getByText('2026-11-03').closest('button') as HTMLButtonElement);
        expect(await screen.findByText('No alignment was recorded on the night of 2026-11-03.')).toBeTruthy();
        expect(callBackendMock).not.toHaveBeenCalledWith('telescope:get_session_alignment', expect.anything());
        expect(screen.queryByText('M 13')).toBeNull();
    });

    /** The Guiding target tab says it plots live guiding while a past night is shown. */
    it('explains the Guiding target tab for a past night', async () => {
        const user = userEvent.setup();
        renderDisplay();
        await waitFor(() => expect(countCalls('history:nights')).toBeGreaterThan(0));
        await user.click(screen.getByRole('button', { name: /^Night:/ }));
        await screen.findByText('2026-11-04');
        await user.click(screen.getByText('2026-11-04').closest('button') as HTMLButtonElement);
        await user.click(screen.getByRole('tab', { name: 'Guiding target' }));
        expect(screen.getByText(/shows live guiding only/)).toBeTruthy();
    });

    /** New guiding data makes the night list and the shown night read again; other events do not. */
    it('reads the night list and the chosen night again on library:guiding_updated', async () => {
        const user = userEvent.setup();
        renderDisplay();
        await waitFor(() => expect(countCalls('history:nights')).toBeGreaterThan(0));
        await user.click(screen.getByRole('button', { name: /^Night:/ }));
        await screen.findByText('2026-11-04');
        await user.click(screen.getByText('2026-11-04').closest('button') as HTMLButtonElement);
        await screen.findByText('0.85 arcsec');
        const listCalls = countCalls('history:nights');
        const nightCalls = countCalls('history:night_quality');
        const send = (action: string): void =>
            (socketClient as unknown as { emit: (name: string, ...args: unknown[]) => void }).emit('action', action, {});

        act(() => send('quality:measured'));
        expect(countCalls('history:nights')).toBe(listCalls);

        act(() => send('library:guiding_updated'));
        await waitFor(() => expect(countCalls('history:nights')).toBeGreaterThan(listCalls));
        await waitFor(() => expect(countCalls('history:night_quality')).toBeGreaterThan(nightCalls));
    });
});
