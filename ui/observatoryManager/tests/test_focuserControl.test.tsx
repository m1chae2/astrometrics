/**
 * @fileoverview Tests of the Focuser section of the Manual tab: it shows the position, moves in and out by
 * the chosen step with the right sign, is off behind the command gate or with no focuser, and toasts only a failure.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const callBackendMock = vi.fn();
const emitToastMock = vi.fn();

vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/utils/emitToast', () => ({
    emitToast: (...args: unknown[]) => emitToastMock(...args),
}));

import { FocuserControl } from '../panels/FocuserControl';
import { TelescopeControlPanel } from '../panels/TelescopeControlPanel';

/** Makes the backend answer the position read and the move. */
function answerBackend(move: () => Promise<boolean> = async () => true, position: () => Promise<number> = async () => 1234) {
    callBackendMock.mockImplementation((method: string) => (
        method === 'telescope:get_focuser_position' ? position() : move()
    ));
}

describe('FocuserControl', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        emitToastMock.mockReset();
    });

    /** The position read from the backend is shown. */
    it('shows the focuser position', async () => {
        answerBackend();
        render(<FocuserControl active />);
        expect(await screen.findByText('1234')).toBeTruthy();
    });

    /** With no focuser (the read fails) a dash shows and the buttons are off. */
    it('shows a dash and disables the buttons with no focuser', async () => {
        answerBackend(async () => true, async () => { throw new Error('no focuser'); });
        render(<FocuserControl active />);
        await waitFor(() => expect(callBackendMock).toHaveBeenCalled());
        expect(screen.getByText('—')).toBeTruthy();
        expect((screen.getByRole('button', { name: 'Focus in' }) as HTMLButtonElement).disabled).toBe(true);
        expect((screen.getByRole('button', { name: 'Focus out' }) as HTMLButtonElement).disabled).toBe(true);
    });

    /** Out sends a positive count, In a negative one, both with the default step. */
    it('moves out with positive steps and in with negative steps', async () => {
        answerBackend();
        render(<FocuserControl active />);
        await screen.findByText('1234');
        fireEvent.click(screen.getByRole('button', { name: 'Focus out' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('telescope:focus_move', { steps: 100 }));
        await waitFor(() => expect((screen.getByRole('button', { name: 'Focus in' }) as HTMLButtonElement).disabled).toBe(false));
        fireEvent.click(screen.getByRole('button', { name: 'Focus in' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('telescope:focus_move', { steps: -100 }));
        expect(emitToastMock).not.toHaveBeenCalled();
    });

    /** The picked step size is the one sent. */
    it('uses the chosen step size', async () => {
        answerBackend();
        render(<FocuserControl active />);
        await screen.findByText('1234');
        fireEvent.click(screen.getByRole('button', { name: /Step size/ }));
        fireEvent.click(await screen.findByText('500 steps'));
        fireEvent.click(screen.getByRole('button', { name: 'Focus in' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('telescope:focus_move', { steps: -500 }));
    });

    /** A failed move shows one short error toast. */
    it('toasts a failed move', async () => {
        answerBackend(async () => { throw new Error('boom'); });
        render(<FocuserControl active />);
        await screen.findByText('1234');
        fireEvent.click(screen.getByRole('button', { name: 'Focus out' }));
        await waitFor(() => expect(emitToastMock).toHaveBeenCalledWith('Focuser move failed', 'error'));
    });

    /** Behind the command gate nothing is read and nothing can move. */
    it('is off and silent when commands are disabled', () => {
        answerBackend();
        render(<FocuserControl active disabled />);
        expect((screen.getByRole('button', { name: 'Focus in' }) as HTMLButtonElement).disabled).toBe(true);
        expect((screen.getByRole('button', { name: 'Focus out' }) as HTMLButtonElement).disabled).toBe(true);
        expect(callBackendMock).not.toHaveBeenCalled();
    });

    /** Nothing is read while the Manual tab is hidden. */
    it('does not read while the tab is hidden', () => {
        answerBackend();
        render(<FocuserControl active={false} />);
        expect(callBackendMock).not.toHaveBeenCalled();
    });
});

describe('Manual tab', () => {
    /** The tab has Telescope and Focuser sections and keeps the pad. */
    it('shows the Telescope and Focuser sections', async () => {
        callBackendMock.mockReset();
        answerBackend();
        render(
            <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
            <TelescopeControlPanel
                isTracking={false} isParked={false} onSetTracking={vi.fn()} onPark={vi.fn()} onUnpark={vi.fn()}
                onStartMove={vi.fn()} onStopMove={vi.fn()} onSetSlewRate={vi.fn()} onStop={vi.fn()}
            />
            </QueryClientProvider>,
        );
        fireEvent.click(screen.getByRole('tab', { name: 'Manual' }));
        expect(screen.getByRole('heading', { name: 'Telescope' })).toBeTruthy();
        expect(screen.getByRole('heading', { name: 'Focuser' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Move north' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'STOP' })).toBeTruthy();
        expect(await screen.findByText('1234')).toBeTruthy();
    });
});
