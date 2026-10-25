/**
 * @file test_packageSuggestions.test.tsx
 * @description Tests of the Observation Manager's suggestion handling with the backend mocked:
 * Dismiss with Undo, Reopen, the Refresh suggestions button, the "Suggestions updated" line after a
 * refresh event, and the notice (with "Save as planned package") when the suggestion open in the
 * editor is removed by a refresh.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderWithProviders } from '../../common/tests/testUtils';
import { ObservationManager } from '../ObservationManager';
import { PACKAGE_LIBRARY_QUERY_KEY, PACKAGE_REFRESH_NOTICE_QUERY_KEY } from '../../common/queries/packageLibraryQueryKeys';
import { FilterType, FrameType, ObservationPackage, PackageStatus } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();

vi.mock('../../common/services/backendApi', async (importOriginal) => ({
    ...(await importOriginal<object>()),
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../hooks/useExecutionQueue', () => ({
    useExecutionQueue: () => ({
        queue: [], removeFromQueue: vi.fn(), addToQueue: vi.fn(), updateQueueItem: vi.fn(), reorderQueue: vi.fn(),
    }),
}));
vi.mock('../../common/queries/useExecutionQueueQuery', () => ({
    useExecutionQueueQuery: () => ({ isLoading: false, data: [] }),
}));
vi.mock('../hooks/useTargetVisibility', () => ({ useTargetVisibility: () => ({ visibleTargets: [], loading: false }) }));
vi.mock('../LastImageViewer', () => ({ LastImageViewer: () => <div>Last Image stub</div> }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({ items: [], filterText: '', setFilterText: vi.fn(), isLoading: false }),
}));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: vi.fn() }));

const suggestedPackage: ObservationPackage = {
    id: 's1', name: 'M 13', target_id: 'M 13', revision: 2, status: PackageStatus.SUGGESTED,
    exposure_requests: [{ frameType: FrameType.LIGHT, filter: FilterType.L, count: 10, exposureSec: 60 }],
    suggestion: { sourceKind: 'rule', reason: 'Needs more frames.', confidence: 'design_estimate' },
};
const completePackage: ObservationPackage = {
    id: 'c1', name: 'M 51', target_id: 'M 51', revision: 7, status: PackageStatus.COMPLETE, exposure_requests: [],
};

/** Packages the mocked library lists, per status; tests change these between reads. */
const library = { SUGGESTED: [suggestedPackage] as ObservationPackage[], PLANNED: [] as ObservationPackage[], COMPLETE: [completePackage] };

/** Answers the reads and writes of the mocked backend. */
function answerBackend(overrides: Record<string, (params: any) => unknown> = {}) {
    callBackendMock.mockImplementation(async (action: string, params: any) => {
        if (overrides[action]) return overrides[action](params);
        if (action === 'plan:list_packages') return library[params.status as keyof typeof library] ?? [];
        if (action === 'plan:edit_package') return { ...suggestedPackage, revision: 3 };
        if (action === 'plan:create_package') return { id: 'n1', name: params.target, target_id: params.target, revision: 0, status: PackageStatus.PLANNED, exposure_requests: params.exposure_requests ?? [] };
        if (action === 'plan:refresh_suggestions') return { started: true };
        return null;
    });
}

/** Renders the display in a client the test can reach. */
function renderDisplay() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithProviders(<QueryClientProvider client={client}><ObservationManager /></QueryClientProvider>);
    return client;
}

describe('Observation Manager suggestions', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        localStorage.clear();
        library.SUGGESTED = [suggestedPackage];
    });

    it('dismisses a suggestion and undoes it', async () => {
        answerBackend();
        renderDisplay();
        await userEvent.click(await screen.findByRole('tab', { name: /Suggested/ }));
        await userEvent.click(await screen.findByRole('button', { name: 'Dismiss M 13' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'plan:edit_package', { package_id: 's1', dismiss: true, expected_revision: 2 }, expect.anything()
        ));
        await userEvent.click(await screen.findByRole('button', { name: 'Undo' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'plan:edit_package', { package_id: 's1', undismiss: true, expected_revision: 3 }, expect.anything()
        ));
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument());
    });

    it('reopens a complete package', async () => {
        answerBackend();
        renderDisplay();
        await userEvent.click(await screen.findByRole('tab', { name: /Complete/ }));
        await userEvent.click(await screen.findByRole('button', { name: 'Reopen M 51' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'plan:edit_package', { package_id: 'c1', reopen: true, expected_revision: 7 }, expect.anything()
        ));
    });

    it('starts a refresh from the header button and says it started', async () => {
        answerBackend();
        renderDisplay();
        await userEvent.click(await screen.findByRole('button', { name: 'Refresh suggestions' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('plan:refresh_suggestions', {}, expect.anything()));
        expect(await screen.findByText(/Refresh started/)).toBeInTheDocument();
    });

    it('shows a refused refresh as an alert', async () => {
        answerBackend({ 'plan:refresh_suggestions': () => ({ started: false }) });
        renderDisplay();
        await userEvent.click(await screen.findByRole('button', { name: 'Refresh suggestions' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not start a refresh');
    });

    it('shows "Suggestions updated" after a refresh event', async () => {
        answerBackend();
        const client = renderDisplay();
        await screen.findByRole('button', { name: 'Refresh suggestions' });
        act(() => {
            client.setQueryData(PACKAGE_REFRESH_NOTICE_QUERY_KEY, { added: 1, deleted: 2, receivedAt: 1 });
        });
        expect(await screen.findByText('Suggestions updated: 1 added, 2 removed')).toBeInTheDocument();
    });

    it('keeps the form with an alert when the open suggestion is removed, and saves it as planned', async () => {
        answerBackend();
        const client = renderDisplay();
        await userEvent.click(await screen.findByRole('tab', { name: /Suggested/ }));
        await userEvent.click(await screen.findByRole('button', { name: /^M 13, suggested package/ }));
        expect(screen.queryByText(/removed this suggestion/)).not.toBeInTheDocument();

        library.SUGGESTED = [];
        await act(async () => { await client.invalidateQueries({ queryKey: PACKAGE_LIBRARY_QUERY_KEY }); });

        const alert = await screen.findByText(/removed this suggestion/);
        expect(alert.closest('[role="alert"]')).not.toBeNull();
        expect(screen.queryByText('Choose a package or New package')).not.toBeInTheDocument();

        await userEvent.click(screen.getByRole('button', { name: 'Save as planned package' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'plan:create_package',
            expect.objectContaining({ target: 'M 13', exposure_requests: suggestedPackage.exposure_requests }),
            expect.anything()
        ));
        await waitFor(() => expect(screen.queryByText(/removed this suggestion/)).not.toBeInTheDocument());
    });
});
