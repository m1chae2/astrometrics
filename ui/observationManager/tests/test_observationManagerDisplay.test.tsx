/**
 * @file test_observationManagerDisplay.test.tsx
 * @description Tests of the whole Observation Manager display with the backend mocked: the layout
 * (queue on top; no Target Status, Mosaic Planner or Calibration Library), the package library
 * reading, Visible Targets starting a package, and package edits saving with the revision.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderWithProviders } from '../../common/tests/testUtils';
import { ObservationManager } from '../ObservationManager';
import { FilterType, FrameType, ObservationPackage } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();

vi.mock('../../common/services/backendApi', async (importOriginal) => ({
    ...(await importOriginal<object>()),
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../hooks/useExecutionQueue', () => ({
    useExecutionQueue: () => ({
        queue: [], addToQueue: vi.fn(), removeFromQueue: vi.fn(), updateQueueItem: vi.fn(), reorderQueue: vi.fn(),
    }),
}));
vi.mock('../../common/queries/useExecutionQueueQuery', () => ({
    useExecutionQueueQuery: () => ({ isLoading: false, data: [] }),
}));
vi.mock('../hooks/useTargetVisibility', () => ({
    useTargetVisibility: () => ({
        visibleTargets: [{ id: 'M 31', altitude_deg: 50, azimuth_deg: 120, set_utc: null }],
        loading: false,
    }),
}));
vi.mock('../LastImageViewer', () => ({ LastImageViewer: () => <div>Last Image stub</div> }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({ items: [], filterText: '', setFilterText: vi.fn(), isLoading: false }),
}));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: vi.fn() }));

const plannedPackage: ObservationPackage = {
    id: 'p1', name: 'M 81', target_id: 'M 81', revision: 4,
    exposure_requests: [{ frameType: FrameType.LIGHT, filter: FilterType.L, count: 20, exposureSec: 60 }],
};

/** Answers the library reads and package writes of the mocked backend. */
function answerBackend(overrides: Record<string, (params: any) => unknown> = {}) {
    callBackendMock.mockImplementation(async (action: string, params: any) => {
        if (overrides[action]) return overrides[action](params);
        if (action === 'plan:list_packages') return params.status === 'PLANNED' ? [plannedPackage] : [];
        if (action === 'plan:create_package') return { id: 'p9', name: params.target, target_id: params.target, revision: 0, exposure_requests: [] };
        if (action === 'plan:edit_package') return { ...plannedPackage, revision: 5, exposure_requests: params.exposure_requests ?? [] };
        return null;
    });
}

/** Renders the display inside a fresh query client. */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return renderWithProviders(
        <QueryClientProvider client={client}><ObservationManager /></QueryClientProvider>
    );
}

describe('ObservationManager display', () => {
    beforeEach(() => { callBackendMock.mockReset(); localStorage.clear(); });

    it('puts the Execution Queue above the columns and drops the removed panels', async () => {
        answerBackend();
        const { container } = renderDisplay();
        const queue = container.querySelector('.observation-manager__queue') as HTMLElement;
        const body = container.querySelector('.observation-manager__body') as HTMLElement;
        expect(queue).toHaveTextContent('Execution Queue');
        expect(queue.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
        expect(screen.queryByText('Target Status')).not.toBeInTheDocument();
        expect(screen.queryByText('Mosaic Planner')).not.toBeInTheDocument();
        expect(screen.queryByText(/Calibration Library/)).not.toBeInTheDocument();
        expect(screen.getByText('Visible Targets')).toBeInTheDocument();
        expect(screen.getByText('Last Image stub')).toBeInTheDocument();
        await screen.findByText('L 20 x 60 s');
    });

    it('lists the library packages and opens one in the editor', async () => {
        answerBackend();
        renderDisplay();
        expect(screen.getAllByText('Choose a package or New package').length).toBeGreaterThan(0);
        await userEvent.click(await screen.findByRole('button', { name: 'Package for M 81' }));
        expect(screen.queryByText('Choose a package or New package')).not.toBeInTheDocument();
        expect(callBackendMock).toHaveBeenCalledWith('plan:list_packages', { status: 'PLANNED' }, expect.anything());
    });

    it('saves a row edit through plan:edit_package with the expected revision', async () => {
        answerBackend();
        renderDisplay();
        await userEvent.click(await screen.findByRole('button', { name: 'Package for M 81' }));
        await userEvent.click(screen.getByTitle('Remove Item'));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'plan:edit_package',
            { package_id: 'p1', exposure_requests: [], expected_revision: 4 },
            expect.anything()
        ));
    });

    it('shows a refused save as an alert', async () => {
        answerBackend({ 'plan:edit_package': () => { throw new Error('revision conflict'); } });
        renderDisplay();
        await userEvent.click(await screen.findByRole('button', { name: 'Package for M 81' }));
        await userEvent.click(screen.getByTitle('Remove Item'));
        expect(await screen.findByRole('alert')).toHaveTextContent('revision conflict');
    });

    it('starts a new package when a Visible Targets row is clicked', async () => {
        answerBackend();
        renderDisplay();
        await userEvent.click(await screen.findByText('M 31'));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'plan:create_package', { target: 'M 31' }, expect.anything()
        ));
    });

    it('shows an unknown target refusal as an alert', async () => {
        answerBackend({ 'plan:create_package': () => { throw new Error('unknown target'); } });
        renderDisplay();
        await userEvent.click(await screen.findByText('M 31'));
        expect(await screen.findByRole('alert')).toHaveTextContent('unknown target');
    });
});
