/**
 * @file test_executionQueueStop.test.tsx
 * @description Tests of Pause Session and Stop Session in the Execution Queue strip: Pause calls
 * the pause service, Stop asks first and calls the stop service only after the user confirms,
 * keeping the session running when the user cancels, and a refusal is shown as an alert.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ExecutionQueue } from '../ExecutionQueue';
import { pauseImagingSession, stopImagingSession } from '../../common/services/sequencerService';

vi.mock('../../common/services/sequencerService', () => ({
    beginImagingSession: vi.fn(),
    pauseImagingSession: vi.fn(),
    stopImagingSession: vi.fn(),
}));
vi.mock('../../common/hooks/useCommandAvailability', () => ({
    useCommandAvailability: () => ({ canCommand: false, reason: 'Ekos controls the equipment.', delegations: [], isChecking: false, canCommandCapability: () => false, reasonForCapability: () => 'Ekos controls the equipment.' }),
}));

/** Renders an empty strip; Pause and Stop do not depend on the queue. */
function renderStrip() {
    render(<ExecutionQueue queue={[]} removeFromQueue={vi.fn()} onEdit={vi.fn()} reorderQueue={vi.fn()} />);
}

describe('ExecutionQueue pause and stop', () => {
    beforeEach(() => vi.clearAllMocks());

    it('pauses the session when Pause Session is clicked, even while Ekos controls the equipment', async () => {
        vi.mocked(pauseImagingSession).mockResolvedValue(undefined);
        renderStrip();
        await userEvent.click(screen.getByRole('button', { name: 'Pause Session' }));
        expect(pauseImagingSession).toHaveBeenCalledTimes(1);
    });

    it('asks before stopping and stops only after the user confirms', async () => {
        vi.mocked(stopImagingSession).mockResolvedValue(undefined);
        renderStrip();
        await userEvent.click(screen.getByRole('button', { name: 'Stop Session' }));
        expect(stopImagingSession).not.toHaveBeenCalled();
        const dialog = screen.getByRole('dialog', { name: 'Stop Session' });
        expect(dialog).toHaveTextContent('does not stop the mount or the guider');
        await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, Stop' }));
        expect(stopImagingSession).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('keeps the session when the user cancels the confirmation', async () => {
        renderStrip();
        await userEvent.click(screen.getByRole('button', { name: 'Stop Session' }));
        await userEvent.click(screen.getByRole('button', { name: 'Keep Imaging' }));
        expect(stopImagingSession).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('shows a refused stop or pause as an alert', async () => {
        vi.mocked(stopImagingSession).mockRejectedValue(new Error('backend is down'));
        vi.mocked(pauseImagingSession).mockRejectedValue(new Error('no session'));
        renderStrip();
        await userEvent.click(screen.getByRole('button', { name: 'Pause Session' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not pause the imaging session: no session');
        await userEvent.click(screen.getByRole('button', { name: 'Stop Session' }));
        await userEvent.click(screen.getByRole('button', { name: 'Yes, Stop' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not stop the imaging session: backend is down');
    });
});
