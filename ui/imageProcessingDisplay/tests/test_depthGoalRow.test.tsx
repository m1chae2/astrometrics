/**
 * @fileoverview Tests for the Integration Goal row: levels, message, changing the
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
    { level: 'good', label: 'Standard', goalDepth: 24.7 },
    { level: 'deep', label: 'Deep', goalDepth: 26 },
];

const baseStatus = {
    targetId: 'M 31', level: 'good', levelLabel: 'Standard', options,
    state: 'below_goal', depthKind: 'surface_brightness', depthUnit: 'mag/arcsec2',
    currentDepth: 24.0, goalDepth: 24.7,
    message: 'About 1 h 20 min more integration needed.', reason: '',
};

/** The library's target list that the mocked backend returns. */
const libraryTargetList = [{ id: 'M 31', name: 'M 31' }];

/**
 * Answers the target list read with the library list and every other call with the given answer.
 * @param answer What the depth goal calls return (or a function that makes the answer).
 */
function answerBackend(answer: (action: string, params: unknown) => unknown) {
    callBackendMock.mockImplementation(async (action: string, params: unknown) => (
        action === 'target:get_targets' ? libraryTargetList : answer(action, params)
    ));
}

/** Renders the row inside a fresh query client. */
function renderRow(targetId: string = 'M 31') {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <DepthGoalRow targetId={targetId} />
        </QueryClientProvider>
    );
}

describe('DepthGoalRow', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
    });

    it('shows the levels and the message', async () => {
        answerBackend(() => baseStatus);
        renderRow();
        expect(await screen.findByText(baseStatus.message)).toBeTruthy();
        const picker = screen.getByRole('button', { name: 'Integration Goal: Standard' });
        fireEvent.click(picker);
        expect(screen.getByRole('button', { name: 'Quick look' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Deep' })).toBeTruthy();
        expect(backend.callBackend).toHaveBeenCalledWith('advisory:depth_goal', { target: 'M 31' });
    });

    it('sets the level on change and shows the returned message', async () => {
        answerBackend((action) => (
            action === 'advisory:set_depth_goal'
                ? { ...baseStatus, level: 'deep', message: 'Integration goal reached.' }
                : baseStatus
        ));
        renderRow();
        await screen.findByText(baseStatus.message);
        fireEvent.click(screen.getByRole('button', { name: /^Integration Goal:/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Deep' }));
        expect(await screen.findByText('Integration goal reached.')).toBeTruthy();
        expect(backend.callBackend).toHaveBeenCalledWith('advisory:set_depth_goal', { target: 'M 31', level: 'deep' });
    });

    it('shows a muted note and no dropdown when there is no depth goal', async () => {
        answerBackend(() => ({ ...baseStatus, state: 'no_depth_goal', options: [], reason: 'Planets move.' }));
        renderRow();
        const note = await screen.findByText('No depth goal for this kind of object.');
        expect(note.getAttribute('title')).toBe('Planets move.');
        expect(screen.queryByRole('button', { name: /^Integration Goal:/ })).toBeNull();
    });

    it('shows loading, then an error', async () => {
        answerBackend(() => new Promise((_, reject) => setTimeout(() => reject(new Error('down')), 30)));
        renderRow();
        expect(await screen.findByText('Loading the depth goal')).toBeTruthy();
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toContain('Could not load');
    });

    it('reads no depth goal and shows nothing for a star that is not in the library', async () => {
        answerBackend(() => baseStatus);
        const { container } = renderRow('HIP 48113');
        await vi.waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('target:get_targets', { detail: 'summary' }));
        // Give the row a chance to react to the loaded list before checking.
        await new Promise((resolve) => setTimeout(resolve, 50));
        expect(callBackendMock.mock.calls.some(([action]) => String(action).startsWith('advisory:'))).toBe(false);
        expect(container.textContent).toBe('');
    });

    it('describes the goal with units', () => {
        expect(describeDepthDetail(baseStatus as never)).toBe(
            'Goal: 24.7 mag/arcsec² (surface brightness, 3σ over 10″). Now: 24.0.');
        expect(describeDepthDetail({ ...baseStatus, depthKind: 'point_source', goalDepth: 19.3, currentDepth: 18.2 } as never)).toBe(
            'Goal: 19.3 mag (faintest star detected at 5σ). Now: 18.2.');
    });
});
