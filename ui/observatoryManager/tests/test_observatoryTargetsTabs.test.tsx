/**
 * @fileoverview Tests of the Targets panel tabs in the Observatory Manager: the List tab is
 * shown first and holds the search box and the list, the Filter tab holds the filter choices,
 * a filter choice changes the list, and a short line names the filter in use.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '@testing-library/react';

vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (method: string) => {
        if (method === 'history:nights') return [];
        if (method === 'calibration:query') return { detail: 'counts', darks: [], biases: [], flats: [], camera_totals: [] };
        if (method === 'observatory:get_active_telescope') return { id: 't1', name: 'Scope', focal_length_mm: 500 };
        if (method === 'observatory:safety_status') return { delegation_policy: { id: 'p', capability_delegations: [] } };
        throw new Error(`unexpected ${method}`);
    },
}));
vi.mock('../../common/components/IngestFramesModal', () => ({ IngestFramesModal: () => null }));
vi.mock('../../common/hooks/useIngestionManager', () => ({ useIngestionManager: () => ({ isActive: false }) }));
vi.mock('../IndiStatusPanel', () => ({ IndiStatusPanel: () => <div>Devices</div> }));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
    useTelescopeStatus: () => ({ trackingStatus: 'Idle', telemetry: { alignmentAttempts: [], guidingHistory: [] } }),
}));
vi.mock('../../common/context/AstrometricsContext', () => ({ useAstrometrics: () => ({ connected: true, activeJobs: [] }) }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
    useTargetContext: () => ({
        selectedTarget: null, pendingTarget: null, setPendingTarget: () => undefined,
        reloadKey: 0, forceReload: () => undefined,
    }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: [] }) }));
vi.mock('../../common/hooks/useTargetListLogic', async () => {
    const react = await import('react');
    return {
        /** A stand-in for the real hook: the catalog choice decides which targets are listed. */
        useTargetListLogic: () => {
            const [catalog, setCatalog] = react.useState('All Targets');
            const [sort, setSort] = react.useState('A–Z');
            const items = catalog === 'All Targets'
                ? [{ id: 'M_13', value: 'M_13', label: 'M 13' }, { id: 'NGC_7331', value: 'NGC_7331', label: 'NGC 7331' }]
                : [{ id: 'M_13', value: 'M_13', label: 'M 13' }];
            return {
                items,
                filterPanel: {
                    selectedCatalog: catalog,
                    onCatalogChange: setCatalog,
                    selectedCamera: 'All Cameras',
                    sort,
                    onSortChange: setSort,
                },
                filterText: '',
                setFilterText: () => undefined,
                isLoading: false,
            };
        },
    };
});
vi.mock('../../common/radioList/TargetListFilterPanel', () => ({
    TargetListFilterPanel: (props: { onCatalogChange: (catalog: string) => void; onSortChange: (sort: string) => void }) => (
        <div>
            <button type="button" onClick={() => props.onCatalogChange('Messier Catalog')}>Pick Messier</button>
            <button type="button" onClick={() => props.onSortChange('Newest')}>Pick Newest</button>
        </div>
    ),
}));
vi.mock('../../common/components/AddTargetModal', () => ({ AddTargetModal: () => null }));
vi.mock('../panels/GuidingStatusPanel', () => ({
    GuidingTrendsPlot: () => <div>trends</div>,
    GuidingTargetPlot: () => <div>target</div>,
}));
vi.mock('../../common/hooks/useEquipmentConfiguration', () => ({
    useEquipmentConfiguration: () => ({
        configuration: null, availableCameras: [], loading: false, error: null, reload: () => undefined, setActiveCamera: () => undefined,
    }),
}));

import { ObservatoryManager } from '../ObservatoryManager';

/**
 * Renders the Observatory Manager inside a fresh query client.
 * @return The render result.
 */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}><ObservatoryManager /></QueryClientProvider>);
}

/**
 * Finds the tab strip of the Targets panel.
 * @return The queries scoped to that strip.
 */
const targetTabs = () => within(screen.getByRole('tablist', { name: 'Targets views' }));

describe('Observatory Manager Targets tabs', () => {
    /** The List tab is selected at the start and shows the search box and the list, not the filter. */
    it('starts on the List tab with the search box and the list', () => {
        renderDisplay();
        expect(targetTabs().getByRole('tab', { name: 'List' }).getAttribute('aria-selected')).toBe('true');
        expect(screen.getByPlaceholderText('Enter name or ID...')).toBeTruthy();
        expect(screen.getByText('M 13')).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Pick Messier' })).toBeNull();
    });

    /** The Filter tab holds the filter choices and hides the list. */
    it('shows the filter choices on the Filter tab', async () => {
        const user = userEvent.setup();
        renderDisplay();
        await user.click(targetTabs().getByRole('tab', { name: 'Filter' }));
        expect(screen.getByRole('button', { name: 'Pick Messier' })).toBeTruthy();
        expect(screen.queryByPlaceholderText('Enter name or ID...')).toBeNull();
        expect(screen.queryByText('M 13')).toBeNull();
    });

    /** A filter choice is kept and the List tab then shows the filtered result. */
    it('keeps a filter choice and shows the filtered list when the user goes back to List', async () => {
        const user = userEvent.setup();
        renderDisplay();
        expect(screen.getByText('NGC 7331')).toBeTruthy();
        await user.click(targetTabs().getByRole('tab', { name: 'Filter' }));
        await user.click(screen.getByRole('button', { name: 'Pick Messier' }));
        await user.click(targetTabs().getByRole('tab', { name: 'List' }));
        expect(screen.getByText('M 13')).toBeTruthy();
        expect(screen.queryByText('NGC 7331')).toBeNull();
    });

    /** The List tab names the filter in use in a small line. */
    it('shows the active filter on the List tab', async () => {
        const user = userEvent.setup();
        renderDisplay();
        expect(screen.getByTestId('active-filter').textContent).toBe('All Targets · A–Z');
        await user.click(targetTabs().getByRole('tab', { name: 'Filter' }));
        await user.click(screen.getByRole('button', { name: 'Pick Messier' }));
        await user.click(screen.getByRole('button', { name: 'Pick Newest' }));
        await user.click(targetTabs().getByRole('tab', { name: 'List' }));
        expect(screen.getByTestId('active-filter').textContent).toBe('Messier Catalog · Newest');
    });
});
