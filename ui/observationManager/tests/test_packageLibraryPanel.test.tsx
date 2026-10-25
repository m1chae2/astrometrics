/**
 * @file test_packageLibraryPanel.test.tsx
 * @description Tests of the package library panel: tabs with counts, cards, the loading, empty and
 * error states, and the New package search.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PackageLibraryPanel, PackageLibraryPanelProps } from '../PackageLibraryPanel';
import { ExposureRequest, FilterType, FrameType, ObservationPackage } from '../../common/types/backendTypes';

vi.mock('../../common/hooks/useTargetListLogic', () => ({
    useTargetListLogic: () => ({
        items: [{ id: 'M_81', value: 'M_81', label: 'M 81' }, { id: 'M_82', value: 'M_82', label: 'M 82' }],
        filterText: '',
        setFilterText: vi.fn(),
        isLoading: false,
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
