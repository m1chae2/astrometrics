/**
 * @file test_targetListStatus.test.tsx
 * @description Tests of the Visible Targets panel states: loading (aria-busy), empty, error with
 * Try again, and a row click that starts a package.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TargetListStatus } from '../TargetListStatus';

/** What the mocked visibility hook answers; tests change it. */
const visibility = vi.hoisted(() => ({
    visibleTargets: [] as unknown[],
    loading: false,
    error: null as string | null,
    retry: vi.fn(),
}));
vi.mock('../hooks/useTargetVisibility', () => ({ useTargetVisibility: () => visibility }));

describe('TargetListStatus', () => {
    it('is busy while it calculates', () => {
        visibility.loading = true;
        visibility.visibleTargets = [];
        const { container } = render(<TargetListStatus />);
        expect(screen.getByText('Calculating visibility...')).toBeInTheDocument();
        expect(container.querySelector('.visible-targets-content')).toHaveAttribute('aria-busy', 'true');
    });

    it('says when nothing is visible', () => {
        visibility.loading = false;
        render(<TargetListStatus />);
        expect(screen.getByText('No targets currently visible')).toBeInTheDocument();
    });

    it('shows an error with Try again', async () => {
        visibility.loading = false;
        visibility.error = 'boom';
        render(<TargetListStatus />);
        expect(screen.getByRole('alert')).toHaveTextContent('Could not load the visible targets.');
        expect(screen.queryByText('No targets currently visible')).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(visibility.retry).toHaveBeenCalled();
        visibility.error = null;
    });

    it('starts a package from a row', async () => {
        visibility.visibleTargets = [{ id: 'M 81', altitude_deg: 40, azimuth_deg: 120, set_utc: '04:00' }];
        const onNewPackage = vi.fn();
        render(<TargetListStatus onNewPackage={onNewPackage} />);
        await userEvent.click(screen.getByText('M 81'));
        expect(onNewPackage).toHaveBeenCalledWith('M 81');
    });
});
