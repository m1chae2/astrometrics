/**
 * @file test_observationManagerDisplay.test.tsx
 * @description Tests of the whole Observation Manager display with the backend mocked: the layout
 * (queue on top; no Target Status, Mosaic Planner or Calibration Library), the package library
 * reading, Visible Targets starting a package, and package edits saving with the revision.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
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
const queueState = vi.hoisted(() => ({ queue: [] as unknown[], removeFromQueue: vi.fn() }));
vi.mock('../hooks/useExecutionQueue', () => ({
    useExecutionQueue: () => ({
        queue: queueState.queue, removeFromQueue: queueState.removeFromQueue, addToQueue: vi.fn(), updateQueueItem: vi.fn(), reorderQueue: vi.fn(),
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
    beforeEach(() => { callBackendMock.mockReset(); localStorage.clear(); queueState.queue = []; });

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
    });

    it('lists the library packages and opens one in the editor', async () => {
        answerBackend();
        renderDisplay();
        expect(screen.getAllByText('Choose a package or New package').length).toBeGreaterThan(0);
        await userEvent.click(await screen.findByRole('button', { name: 'M 81, planned package' }));
        expect(screen.queryByText('Choose a package or New package')).not.toBeInTheDocument();
        expect(callBackendMock).toHaveBeenCalledWith('plan:list_packages', { status: 'PLANNED' }, expect.anything());
    });

    it('saves a row edit through plan:edit_package with the expected revision', async () => {
        answerBackend();
        renderDisplay();
        await userEvent.click(await screen.findByRole('button', { name: 'M 81, planned package' }));
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
        await userEvent.click(await screen.findByRole('button', { name: 'M 81, planned package' }));
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

    it('opens the planned package of a clicked target and makes no create call', async () => {
        const planned = { ...plannedPackage, id: 'pp', target_id: 'M_31', name: 'M 31 planned' };
        const suggested = { ...plannedPackage, id: 'ps', target_id: 'M 31', name: 'M 31 suggested' };
        answerBackend({
            'plan:list_packages': (params) =>
                params.status === 'PLANNED' ? [planned] : params.status === 'SUGGESTED' ? [suggested] : [],
        });
        renderDisplay();
        await screen.findByRole('button', { name: 'M_31, planned package' });
        await userEvent.click(screen.getByText('M 31', { selector: 'td' }));
        await waitFor(() => expect(screen.queryByText('Choose a package or New package')).not.toBeInTheDocument());
        expect(callBackendMock.mock.calls.some(([action]) => action === 'plan:create_package')).toBe(false);
        await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
        await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'plan:edit_package', expect.objectContaining({ package_id: 'pp' }), expect.anything()
        ));
    });

    it('opens the suggested package when the target has no planned one', async () => {
        const suggested = { ...plannedPackage, id: 'ps', target_id: 'm 31', name: 'M 31 suggested' };
        answerBackend({
            'plan:list_packages': (params) => (params.status === 'SUGGESTED' ? [suggested] : []),
        });
        renderDisplay();
        await userEvent.click(await screen.findByRole('tab', { name: /Suggested/ }));
        await screen.findByRole('button', { name: 'm 31, suggested package' });
        await userEvent.click(screen.getByText('M 31', { selector: 'td' }));
        await waitFor(() => expect(screen.queryByText('Choose a package or New package')).not.toBeInTheDocument());
        expect(callBackendMock.mock.calls.some(([action]) => action === 'plan:create_package')).toBe(false);
    });

    it('creates a package for a target that has none', async () => {
        answerBackend();
        renderDisplay();
        await screen.findByRole('button', { name: 'M 81, planned package' });
        await userEvent.click(screen.getByText('M 31', { selector: 'td' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'plan:create_package', { target: 'M 31' }, expect.anything()
        ));
    });

    describe('single selection', () => {
        const queuedSequence = {
            id: 's1', target_name: 'M 51', total_duration: 600, status: 'pending',
            items: [{ count: 5, exposure: 30, filter: 'L', delay: 0 }],
        };

        /** Elements that currently show the selected state. */
        const selectedElements = (container: HTMLElement) => container.querySelectorAll('[data-selected="true"]');

        it('selecting a queue card unselects the library card and shows it in the editor', async () => {
            queueState.queue = [queuedSequence];
            answerBackend();
            const { container } = renderDisplay();
            await userEvent.click(await screen.findByRole('button', { name: 'M 81, planned package' }));
            expect(selectedElements(container)).toHaveLength(1);
            await userEvent.click(screen.getByTitle('Edit Sequence'));
            const selected = selectedElements(container);
            expect(selected).toHaveLength(1);
            expect(selected[0]).toHaveClass('imaging-card');
            expect(screen.getByRole('button', { name: 'M 81, planned package' })).not.toHaveAttribute('data-selected');
            expect(screen.queryByText('Choose a package or New package')).not.toBeInTheDocument();
        });

        it('selecting a library package unselects the queue card', async () => {
            queueState.queue = [queuedSequence];
            answerBackend();
            const { container } = renderDisplay();
            await userEvent.click(screen.getByTitle('Edit Sequence'));
            expect(selectedElements(container)[0]).toHaveClass('imaging-card');
            await userEvent.click(await screen.findByRole('button', { name: 'M 81, planned package' }));
            const selected = selectedElements(container);
            expect(selected).toHaveLength(1);
            expect(selected[0]).toHaveAttribute('aria-label', 'M 81, planned package');
        });

        it('expanding a queue card does not mark it selected', async () => {
            queueState.queue = [queuedSequence];
            answerBackend();
            const { container } = renderDisplay();
            await userEvent.click(screen.getByText('M 51'));
            expect(selectedElements(container)).toHaveLength(0);
        });

        it('removing the selected queue card clears the selection', async () => {
            queueState.queue = [queuedSequence];
            answerBackend();
            const { container } = renderDisplay();
            await userEvent.click(screen.getByTitle('Edit Sequence'));
            await userEvent.click(screen.getByTitle('Remove Sequence'));
            await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
            expect(queueState.removeFromQueue).toHaveBeenCalledWith('s1');
            expect(selectedElements(container)).toHaveLength(0);
            expect(screen.getAllByText('Choose a package or New package').length).toBeGreaterThan(0);
        });
    });

    describe('Delete', () => {
        /** Opens the library package and clicks Delete in the editor. */
        async function openAndClickDelete() {
            await userEvent.click(await screen.findByRole('button', { name: 'M 81, planned package' }));
            await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
        }

        it('does nothing when the confirmation is cancelled and returns focus to Delete', async () => {
            answerBackend();
            renderDisplay();
            await openAndClickDelete();
            await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
            expect(callBackendMock.mock.calls.some(([, params]) => params?.delete)).toBe(false);
            await waitFor(() => expect(screen.getByRole('button', { name: 'Delete' })).toHaveFocus());
            expect(screen.queryByText('Choose a package or New package')).not.toBeInTheDocument();
        });

        it('deletes with the expected revision and clears the selection', async () => {
            answerBackend();
            renderDisplay();
            await openAndClickDelete();
            await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
            await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
                'plan:edit_package',
                { package_id: 'p1', delete: true, expected_revision: 4 },
                expect.anything()
            ));
            await waitFor(() => expect(screen.getAllByText('Choose a package or New package').length).toBeGreaterThan(0));
        });

        it('shows a refused delete as an alert and keeps the selection', async () => {
            answerBackend({ 'plan:edit_package': () => { throw new Error('revision conflict'); } });
            renderDisplay();
            await openAndClickDelete();
            await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
            expect(await screen.findByRole('alert')).toHaveTextContent('revision conflict');
            expect(screen.queryByText('Choose a package or New package')).not.toBeInTheDocument();
        });
    });
});
