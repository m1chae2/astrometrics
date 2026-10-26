/**
 * @file test_packageLibraryPanel.test.tsx
 * @description Tests of the package library panel: tabs with counts, cards, the loading, empty and
 * error states, and the New package search.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PackageLibraryPanel, PackageLibraryPanelProps } from '../PackageLibraryPanel';
import { ExposureRequest, FilterType, FrameType, ObservationPackage } from '../../common/types/backendTypes';

/** What the mocked target list answers; tests change it. */
const targetList = vi.hoisted(() => ({ isError: false, refetchTargets: vi.fn() }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({
        items: [{ id: 'M_81', value: 'M_81', label: 'M 81' }, { id: 'M_82', value: 'M_82', label: 'M 82' }],
        filterText: '',
        setFilterText: vi.fn(),
        isLoading: false,
        ...targetList,
    }),
}));

const exposure = (filter: FilterType, count: number, exposureSec: number): ExposureRequest => (
    { frameType: FrameType.LIGHT, filter, count, exposureSec }
);

const plannedPackage: ObservationPackage = {
    id: 'p1', name: 'M 81', target_id: 'M 81',
    exposure_requests: [exposure(FilterType.L, 20, 60), exposure(FilterType.Ha, 10, 300)],
};
const suggestedPackage: ObservationPackage = {
    id: 'p2', name: 'M 13', target_id: 'M 13', exposure_requests: [],
    suggestion: { sourceKind: 'rule', reason: 'Needs more frames to reach the noise goal.', confidence: 'design_estimate' },
};

/** Renders the panel with defaults that tests override. */
function renderPanel(overrides: Partial<PackageLibraryPanelProps> = {}) {
    const props: PackageLibraryPanelProps = {
        lists: { suggested: [suggestedPackage], planned: [plannedPackage], complete: [] },
        isLoading: false, error: null, onRetry: vi.fn(), selectedPackageId: null,
        onSelectPackage: vi.fn(), onNewPackage: vi.fn(), onAddTarget: vi.fn(),
        ...overrides,
    };
    render(<PackageLibraryPanel {...props} />);
    return props;
}

describe('PackageLibraryPanel', () => {
    it('shows the three tabs with counts', () => {
        renderPanel();
        expect(screen.getByRole('tab', { name: /Suggested \(1\)/ })).toBeInTheDocument();
        expect(screen.getByRole('tab', { name: /Planned \(1\)/ })).toBeInTheDocument();
        expect(screen.getByRole('tab', { name: /Complete \(0\)/ })).toBeInTheDocument();
    });

    it('shows a planned card with only the target name and selects it on click', async () => {
        const props = renderPanel();
        expect(screen.queryByText(/60 s/)).not.toBeInTheDocument();
        expect(screen.queryByText('No exposures yet')).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole('button', { name: 'M 81, planned package' }));
        expect(props.onSelectPackage).toHaveBeenCalledWith(plannedPackage);
    });

    it('marks the selected card', () => {
        renderPanel({ selectedPackageId: 'p1' });
        const card = screen.getByRole('button', { name: 'M 81, planned package' });
        expect(card).toHaveAttribute('data-selected', 'true');
        expect(card).toHaveClass('card--selected');
    });

    it('shows only the confidence chip for a suggestion, not the reason', async () => {
        renderPanel();
        await userEvent.click(screen.getByRole('tab', { name: /Suggested/ }));
        expect(screen.queryByText('Needs more frames to reach the noise goal.')).not.toBeInTheDocument();
        expect(screen.getByText('Tentative')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'M 13, suggested package, Tentative confidence' })).toBeInTheDocument();
    });

    it('shows the empty state of a tab', async () => {
        renderPanel();
        await userEvent.click(screen.getByRole('tab', { name: /Complete/ }));
        expect(screen.getByText('No complete packages yet.')).toBeInTheDocument();
    });

    it('shows the loading state', () => {
        renderPanel({ isLoading: true });
        expect(screen.getByText('Loading…')).toBeInTheDocument();
    });

    it('shows the error state with Try again', async () => {
        const props = renderPanel({ error: 'boom' });
        expect(screen.getByRole('alert')).toHaveTextContent('Could not load the package library.');
        await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(props.onRetry).toHaveBeenCalled();
    });

    it('shows a refused create as an alert that can be dismissed', async () => {
        const onDismissActionError = vi.fn();
        renderPanel({ actionError: 'Could not create a package for X: unknown target', onDismissActionError });
        expect(screen.getByRole('alert')).toHaveTextContent('unknown target');
        await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
        expect(onDismissActionError).toHaveBeenCalled();
    });

    it('opens the target search from New package and starts a package for the pick', async () => {
        const props = renderPanel();
        await userEvent.click(screen.getByRole('button', { name: 'New package' }));
        expect(screen.getByRole('searchbox', { name: 'Search library targets' })).toBeInTheDocument();
        await userEvent.click(screen.getByRole('button', { name: 'M 82' }));
        expect(props.onNewPackage).toHaveBeenCalledWith('M_82');
        expect(screen.queryByRole('searchbox')).not.toBeInTheDocument();
    });

    it('calls onAddTarget from Add Target', async () => {
        const props = renderPanel();
        await userEvent.click(screen.getByRole('button', { name: 'Add Target' }));
        expect(props.onAddTarget).toHaveBeenCalled();
    });
});

describe('PackageLibraryPanel suggestions and refresh', () => {
    it('offers Dismiss on a suggestion card and not on a planned card', async () => {
        const onDismissPackage = vi.fn();
        renderPanel({ onDismissPackage });
        expect(screen.queryByRole('button', { name: /Dismiss/ })).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole('tab', { name: /Suggested/ }));
        await userEvent.click(screen.getByRole('button', { name: 'Dismiss M 13' }));
        expect(onDismissPackage).toHaveBeenCalledWith(suggestedPackage);
    });

    it('shows the Undo notice and calls Undo', async () => {
        const onUndo = vi.fn();
        renderPanel({ undoNotice: { text: 'Dismissed the suggestion for M 13.', onUndo, onClose: vi.fn() } });
        expect(screen.getByText('Dismissed the suggestion for M 13.')).toBeInTheDocument();
        await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
        expect(onUndo).toHaveBeenCalled();
    });

    it('offers Reopen on a complete card', async () => {
        const onReopenPackage = vi.fn();
        const completePackage = { ...plannedPackage, id: 'p3', target_id: 'M 51' };
        renderPanel({ lists: { suggested: [], planned: [], complete: [completePackage] }, onReopenPackage });
        await userEvent.click(screen.getByRole('tab', { name: /Complete/ }));
        await userEvent.click(screen.getByRole('button', { name: 'Reopen M 51' }));
        expect(onReopenPackage).toHaveBeenCalledWith(completePackage);
    });

    it('shows the refresh line in a polite live region', () => {
        renderPanel({ refreshNotice: 'Suggestions updated: 1 added, 2 removed' });
        const line = screen.getByText('Suggestions updated: 1 added, 2 removed');
        expect(line).toHaveAttribute('aria-live', 'polite');
    });

    it('calls onRefreshSuggestions from the Refresh suggestions button on the Suggested tab', async () => {
        const onRefreshSuggestions = vi.fn();
        renderPanel({ onRefreshSuggestions, refreshStartedText: 'Refresh started. Changes will appear here.' });
        // The button sits at the top of the Suggested tab, not in the panel header.
        expect(screen.queryByRole('button', { name: 'Refresh suggestions' })).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole('tab', { name: /Suggested/ }));
        await userEvent.click(screen.getByRole('button', { name: 'Refresh suggestions' }));
        expect(onRefreshSuggestions).toHaveBeenCalled();
        expect(screen.getByText('Refresh started. Changes will appear here.')).toBeInTheDocument();
    });

    it('moves focus to the next card when the focused card goes away', async () => {
        const second = { ...suggestedPackage, id: 'p4', target_id: 'M 92' };
        const lists = { suggested: [suggestedPackage, second], planned: [], complete: [] };
        const props: PackageLibraryPanelProps = {
            lists, isLoading: false, error: null, onRetry: vi.fn(), selectedPackageId: null,
            onSelectPackage: vi.fn(), onNewPackage: vi.fn(), onAddTarget: vi.fn(), onDismissPackage: vi.fn(),
        };
        const { rerender } = render(<PackageLibraryPanel {...props} />);
        await userEvent.click(screen.getByRole('tab', { name: /Suggested/ }));
        await userEvent.click(screen.getByRole('button', { name: 'Dismiss M 13' }));
        rerender(<PackageLibraryPanel {...props} lists={{ ...lists, suggested: [second] }} />);
        expect(screen.getByRole('button', { name: /^M 92, suggested package/ })).toHaveFocus();
    });
});

describe('PackageLibraryPanel search and notices', () => {
    it('returns focus to New package when the search closes', async () => {
        renderPanel();
        await userEvent.click(screen.getByRole('button', { name: 'New package' }));
        await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        await waitFor(() => expect(screen.getByRole('button', { name: 'New package' })).toHaveFocus());
    });

    it('shows an error with Try again when the target list cannot load', async () => {
        targetList.isError = true;
        renderPanel();
        await userEvent.click(screen.getByRole('button', { name: 'New package' }));
        expect(screen.getByRole('alert')).toHaveTextContent('Could not load the target list.');
        await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(targetList.refetchTargets).toHaveBeenCalled();
        targetList.isError = false;
    });

    it('does not announce the undo notice', () => {
        renderPanel({ undoNotice: { text: 'Dismissed the suggestion for M 13.', onUndo: vi.fn(), onClose: vi.fn() } });
        expect(screen.getByText('Dismissed the suggestion for M 13.').parentElement).not.toHaveAttribute('role');
    });
});
