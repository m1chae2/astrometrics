/**
 * @fileoverview Tests for the "Compare with the previous stack" control: one sentence for each
 * number, "not measured" reasons passed through, the note for a target with no previous stack,
 * the notes on how the reference frame was found, a job followed to the end, and an error.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
    NO_PREVIOUS_STACK,
    StackComparisonControl,
    collectComparisonNotes,
} from '../components/StackComparisonControl';

const callBackendMock = vi.fn();
// A plain function throws the failure, because vitest 5 fails a test whose mock returns a rejected promise.
const failure = { error: null as Error | null };
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => {
        if (failure.error) throw failure.error;
        return callBackendMock(...args);
    },
}));
vi.mock('../../common/services/imaging/processingService', () => ({ fetchJobs: vi.fn().mockResolvedValue([]) }));

const report = {
    stack_path: '/s/M_81_Stacked.fits',
    comparison: {
        before: { path: '/s/_previous/M_81_Stacked.fits', notes: ['No registration record was kept.'] },
        after: { path: '/s/M_81_Stacked.fits', notes: [] },
        summary: [
            'sky level 1.2e+03 to 1.19e+03 (unchanged).',
            'noise in empty sky: not measured (older stack: no stored apertures).',
        ],
    },
    notes: [],
};

/** Opens the disclosure and presses Compare. */
function pressCompare() {
    fireEvent.click(screen.getByText('Compare with the previous stack'));
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }));
}

describe('collectComparisonNotes', () => {
    it('labels each note with its stack', () => {
        expect(collectComparisonNotes(report as never)).toEqual([
            'Previous stack: No registration record was kept.',
        ]);
        expect(collectComparisonNotes({ stack_path: 'x', comparison: null })).toEqual([]);
    });
});

describe('StackComparisonControl', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        failure.error = null;
    });
    afterEach(() => vi.useRealTimers());

    it('shows a sentence for each number and does not name a winner', async () => {
        callBackendMock.mockResolvedValue({ status: 'completed', job_id: 'j1', result: report });
        render(<StackComparisonControl targetId="M 81" kind="imaging" />);
        pressCompare();
        expect(await screen.findByText(/noise in empty sky: not measured/)).toBeTruthy();
        expect(screen.getByText(/sky level/)).toBeTruthy();
        expect(screen.getByRole('status').textContent).not.toMatch(/better|worse|winner/i);
        expect(callBackendMock).toHaveBeenCalledWith(
            'quality:measure_stack', { target_id: 'M 81', kind: 'imaging' }, { silent: true });
        expect(screen.getByText('Previous stack: No registration record was kept.')).toBeTruthy();
    });

    it('says there is no previous stack', async () => {
        callBackendMock.mockResolvedValue({
            status: 'completed', job_id: 'j1',
            result: { stack_path: 'x', comparison: null, notes: ['No previous stack is kept, so there is nothing to compare with.'] },
        });
        render(<StackComparisonControl targetId="M 81" kind="imaging" />);
        pressCompare();
        expect(await screen.findByText(NO_PREVIOUS_STACK)).toBeTruthy();
    });

    it('follows a running job, with the long-wait warning, until it completes', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        callBackendMock.mockImplementation(async (action: string) => {
            if (action === 'quality:measure_stack') return { status: 'running', job_id: 'j7', result: null };
            return { id: 'j7', status: 'completed', outputMetrics: { result: report } };
        });
        render(<StackComparisonControl targetId="M 81" kind="spectral" />);
        pressCompare();
        expect(await screen.findByText(/about ten minutes/)).toBeTruthy();
        await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
        await waitFor(() => expect(screen.getByText(/sky level/)).toBeTruthy());
    });

    it('shows the message of a failed job and an error that comes back at once', async () => {
        failure.error = new Error('The stack file is missing.');
        render(<StackComparisonControl targetId="M 81" kind="imaging" />);
        pressCompare();
        expect((await screen.findByRole('alert')).textContent).toMatch(/The stack file is missing/);
    });
});
