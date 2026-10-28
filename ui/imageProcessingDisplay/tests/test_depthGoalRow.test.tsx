/**
 * @fileoverview Tests for the Depth goal row: levels, message, changing the
 * level, the no-goal state, and the loading and error states.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DepthGoalRow, describeDepthDetail } from '../components/DepthGoalRow';

const callBackendMock = vi.fn();
const backend = { callBackend: callBackendMock };
vi.mock('../../common/services/backendApi', () => ({ callBackend: (...args: unknown[]) => callBackendMock(...args) }));

const options = [
    { level: 'quick_look', label: 'Quick look', goalDepth: 22 },
    { level: 'good', label: 'Good', goalDepth: 24.7 },
    { level: 'deep', label: 'Deep', goalDepth: 26 },
];

const baseStatus = {
    targetId: 'M 31', level: 'good', levelLabel: 'Good', options,
    state: 'below_goal', depthKind: 'surface_brightness', depthUnit: 'mag/arcsec2',
    currentDepth: 24.0, goalDepth: 24.7,
    message: 'About 1 h 20 min more integration needed.', reason: '',
};

/** Renders the row inside a fresh query client. */
function renderRow() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <DepthGoalRow targetId="M 31" />
        </QueryClientProvider>
    );
}

describe('DepthGoalRow', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
    });

    it('shows the levels and the message', async () => {
        backend.callBackend.mockResolvedValue(baseStatus);
        renderRow();
        expect(await screen.findByText(baseStatus.message)).toBeTruthy();
        expect(screen.getByRole('option', { name: 'Quick look' })).toBeTruthy();
        expect(screen.getByRole('option', { name: 'Deep' })).toBeTruthy();
        expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('good');
        expect(backend.callBackend).toHaveBeenCalledWith('advisory:depth_goal', { target: 'M 31' });
    });

    it('sets the level on change and shows the returned message', async () => {
        backend.callBackend.mockResolvedValueOnce(baseStatus);
        renderRow();
        await screen.findByText(baseStatus.message);
        backend.callBackend.mockResolvedValueOnce({ ...baseStatus, level: 'deep', message: 'Integration goal reached.' });
        fireEvent.change(screen.getByRole('combobox'), { target: { value: 'deep' } });
        expect(await screen.findByText('Integration goal reached.')).toBeTruthy();
        expect(backend.callBackend).toHaveBeenCalledWith('advisory:set_depth_goal', { target: 'M 31', level: 'deep' });
    });

    it('shows a muted note and no dropdown when there is no depth goal', async () => {
        backend.callBackend.mockResolvedValue({ ...baseStatus, state: 'no_depth_goal', options: [], reason: 'Planets move.' });
        renderRow();
        const note = await screen.findByText('No depth goal for this kind of object.');
        expect(note.getAttribute('title')).toBe('Planets move.');
        expect(screen.queryByRole('combobox')).toBeNull();
    });

    it('shows loading, then an error', async () => {
        callBackendMock.mockRejectedValue(new Error('down'));
        renderRow();
        expect(screen.getByText(/Loading depth goal/)).toBeTruthy();
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toContain('Could not load');
    });

    it('describes the goal with units', () => {
        expect(describeDepthDetail(baseStatus as never)).toBe(
            'Goal: 24.7 mag/arcsec² (surface brightness, 3σ over 10″). Now: 24.0.');
        expect(describeDepthDetail({ ...baseStatus, depthKind: 'point_source', goalDepth: 19.3, currentDepth: 18.2 } as never)).toBe(
            'Goal: 19.3 mag (faintest star detected at 5σ). Now: 18.2.');
    });
});
