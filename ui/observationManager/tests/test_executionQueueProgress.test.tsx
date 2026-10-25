/**
 * @file test_executionQueueProgress.test.tsx
 * @description Tests of the Execution Queue strip's progress for tonight's session: the frame
 * progress bar, the "Imaging now" chip and `aria-current="step"`, the run-state words, the "as of"
 * time, the progress loading and error states, and the Checklist dialog (ticks, empty state, focus
 * returning to the button).
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ExecutionQueue } from '../ExecutionQueue';
import { beginImagingSession } from '../../common/services/sequencerService';
import { SequenceObject } from '../hooks/useExecutionQueue';
import { ObservationSessionWithProgress, RunState } from '../../common/types/backendTypes';

vi.mock('../../common/services/sequencerService', () => ({ beginImagingSession: vi.fn() }));

/** What the mocked policy answers; tests change it. */
const availability = vi.hoisted(() => ({ canCommand: true, reason: 'Checking whether Ekos is in control…' }));
vi.mock('../../common/hooks/useCommandAvailability', () => ({
    useCommandAvailability: () => ({ ...availability, delegations: [], isChecking: false }),
}));

const queue: SequenceObject[] = [
    { id: 'e1', target_name: 'M 81', items: [{ count: 20, exposure: 60, filter: 'L' }], total_duration: 1200, status: 'pending' },
    { id: 'e2', target_name: 'M 13', items: [{ count: 10, exposure: 60, filter: 'L' }], total_duration: 600, status: 'pending' },
    { id: 'e3', target_name: 'M 57', items: [{ count: 5, exposure: 60, filter: 'L' }], total_duration: 300, status: 'pending' },
    { id: 'e4', target_name: 'M 51', items: [{ count: 5, exposure: 60, filter: 'L' }], total_duration: 300, status: 'pending' },
];

const entry = (entryId: string, targetId: string, requested: number, captured: number, state: RunState, imagingNow = false) => (
    { entryId, targetId, requested, captured, flagged: 0, notJudged: 0, state, imagingNow }
);

const session: ObservationSessionWithProgress = {
    id: 's1', nightDate: '2026-10-25', siteProfileId: 'site', telescopeId: 't', cameraId: 'c', revision: 3,
    checklist: [
        { itemId: 'c1', rule: 'flat_residual', label: 'Take new flats', createdAt: '2026-10-25T01:00:00Z' },
        { itemId: 'c2', rule: 'bias_old', label: 'Retake the old biases', createdAt: '2026-10-25T01:00:00Z', tickedAt: '2026-10-25T02:00:00Z' },
    ],
    progress: {
        sessionId: 's1',
        asOf: '2026-10-25T22:41:00Z',
        runState: RunState.IMAGING,
        imagingNowEntryId: 'e1',
        entries: [
            entry('e1', 'M 81', 20, 12, RunState.IMAGING, true),
            entry('e2', 'M 13', 10, 0, RunState.UPCOMING),
            entry('e3', 'M 57', 5, 5, RunState.COMPLETE),
            entry('e4', 'M 51', 5, 0, RunState.NOT_RUN),
        ],
    },
};

/** Renders the strip with defaults that tests override. */
function renderQueue(overrides: Partial<React.ComponentProps<typeof ExecutionQueue>> = {}) {
    const props: React.ComponentProps<typeof ExecutionQueue> = {
        queue, removeFromQueue: vi.fn(), onEdit: vi.fn(), reorderQueue: vi.fn(), session, onTickChecklistItem: vi.fn(),
        ...overrides,
    };
    render(<ExecutionQueue {...props} />);
    return props;
}

describe('ExecutionQueue progress', () => {
    it('shows "12 of 20 frames" and marks the entry imaging now', () => {
        renderQueue();
        const bar = screen.getByRole('progressbar', { name: 'Frames captured for M 81' });
        expect(bar).toHaveAttribute('aria-valuetext', '12 of 20 frames');
        expect(screen.getByText('12 of 20 frames')).toBeInTheDocument();
        const imagingCard = screen.getByText('M 81').closest('.imaging-card') as HTMLElement;
        expect(imagingCard).toHaveAttribute('aria-current', 'step');
        expect(within(imagingCard).getByText('Imaging now')).toBeInTheDocument();
        expect(screen.getAllByText('Imaging now')).toHaveLength(1);
        const otherCard = screen.getByText('M 13').closest('.imaging-card') as HTMLElement;
        expect(otherCard).not.toHaveAttribute('aria-current');
    });

    it('shows a run-state word on every other card', () => {
        renderQueue();
        expect(within(screen.getByText('M 13').closest('.imaging-card') as HTMLElement).getByText('Not imaging')).toBeInTheDocument();
        expect(within(screen.getByText('M 57').closest('.imaging-card') as HTMLElement).getByText('Done')).toBeInTheDocument();
        expect(within(screen.getByText('M 51').closest('.imaging-card') as HTMLElement).getByText('Not run')).toBeInTheDocument();
    });

    it('puts no live region on the cards', () => {
        renderQueue();
        expect(document.querySelector('.imaging-card [aria-live]')).toBeNull();
    });

    it('shows the "as of" time from the reply', () => {
        renderQueue();
        expect(screen.getByText(/^as of \d{1,2}:\d{2}/)).toBeInTheDocument();
    });

    it('shows no progress for an entry the library did not count', () => {
        renderQueue({ queue: [{ ...queue[0], id: 'unknown' }] });
        expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    });

    it('shows the loading and error states of the progress read', async () => {
        const onRetrySession = vi.fn();
        const { unmount } = render(
            <ExecutionQueue queue={queue} removeFromQueue={vi.fn()} onEdit={vi.fn()} reorderQueue={vi.fn()} isSessionLoading />
        );
        expect(screen.getByText('Loading progress…')).toHaveAttribute('aria-busy', 'true');
        unmount();
        renderQueue({ session: null, sessionError: 'boom', onRetrySession });
        expect(screen.getByRole('alert')).toHaveTextContent('Could not load progress.');
        await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(onRetrySession).toHaveBeenCalled();
    });
});

describe('ExecutionQueue checklist', () => {
    it('opens the checklist, ticks an item and returns focus to the button on close', async () => {
        const props = renderQueue();
        const opener = screen.getByRole('button', { name: 'Checklist (2)' });
        await userEvent.click(opener);
        const dialog = screen.getByRole('dialog', { name: 'Checklist for tonight' });
        const open = within(dialog).getByRole('checkbox', { name: /Take new flats/ });
        expect(open).not.toBeChecked();
        expect(within(dialog).getByRole('checkbox', { name: /Retake the old biases/ })).toBeChecked();
        await userEvent.click(open);
        expect(props.onTickChecklistItem).toHaveBeenCalledWith('c1');
        await userEvent.keyboard('{Escape}');
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        await waitFor(() => expect(opener).toHaveFocus());
    });

    it('closes with the Close button and returns focus', async () => {
        renderQueue();
        const opener = screen.getByRole('button', { name: /Checklist/ });
        await userEvent.click(opener);
        await userEvent.click(screen.getByRole('button', { name: 'Close' }));
        await waitFor(() => expect(opener).toHaveFocus());
    });

    it('shows the empty state when the checklist has no items', async () => {
        renderQueue({ session: { ...session, checklist: [] } });
        await userEvent.click(screen.getByRole('button', { name: 'Checklist (0)' }));
        expect(screen.getByText(/Nothing on the checklist/)).toBeInTheDocument();
    });

    it('explains that there is no session yet', async () => {
        renderQueue({ session: null });
        await userEvent.click(screen.getByRole('button', { name: 'Checklist (0)' }));
        expect(screen.getByText(/No session yet/)).toBeInTheDocument();
    });

    it('shows a refused tick as an alert', async () => {
        renderQueue({ tickError: 'Your tick was not saved: conflict' });
        await userEvent.click(screen.getByRole('button', { name: /Checklist/ }));
        expect(screen.getByRole('alert')).toHaveTextContent('Your tick was not saved');
    });
});

describe('ExecutionQueue monitor mode and keyboard', () => {
    it('starts the session when the app may command the equipment', async () => {
        availability.canCommand = true;
        vi.mocked(beginImagingSession).mockResolvedValue(undefined as never);
        renderQueue();
        await userEvent.click(screen.getByRole('button', { name: 'Start Imaging Session' }));
        expect(beginImagingSession).toHaveBeenCalled();
    });

    it('greys Start while Ekos controls the equipment and gives a Why unavailable button', async () => {
        availability.canCommand = false;
        availability.reason = 'Equipment commands are off. Ekos controls the equipment.';
        vi.mocked(beginImagingSession).mockClear();
        renderQueue();
        const start = screen.getByRole('button', { name: 'Start Imaging Session' });
        expect(start).toBeDisabled();
        expect(start).toHaveAttribute('aria-disabled', 'true');
        await userEvent.click(start);
        expect(beginImagingSession).not.toHaveBeenCalled();
        await userEvent.click(screen.getByRole('button', { name: 'Why unavailable?' }));
        expect(screen.getByRole('note')).toHaveTextContent('Ekos controls the equipment');
        availability.canCommand = true;
    });

    it('shows a refused start as visible alert text', async () => {
        availability.canCommand = true;
        vi.mocked(beginImagingSession).mockRejectedValue(new Error('Ekos is busy'));
        renderQueue();
        await userEvent.click(screen.getByRole('button', { name: 'Start Imaging Session' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not start the imaging session: Ekos is busy');
    });

    it('moves a package later and earlier with buttons', async () => {
        const props = renderQueue();
        await userEvent.click(screen.getByRole('button', { name: 'Move M 81 later' }));
        expect(props.reorderQueue).toHaveBeenLastCalledWith([queue[1], queue[0], queue[2], queue[3]]);
        expect(screen.queryByRole('button', { name: 'Move M 51 later' })).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole('button', { name: 'Move M 81 earlier' }));
        expect(props.reorderQueue).toHaveBeenLastCalledWith(queue);
    });

    it('opens a card from the keyboard and names the edit and remove buttons', async () => {
        renderQueue();
        const toggle = screen.getByRole('button', { name: 'M 81, show details' });
        toggle.focus();
        await userEvent.keyboard('{Enter}');
        expect(screen.getByRole('button', { name: 'M 81, hide details' })).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByRole('button', { name: 'Edit M 81' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Remove M 81' })).toBeInTheDocument();
    });

    it('keeps Tab inside the checklist dialog', async () => {
        renderQueue();
        await userEvent.click(screen.getByRole('button', { name: /Checklist/ }));
        const dialog = screen.getByRole('dialog');
        const close = within(dialog).getByRole('button', { name: 'Close' });
        close.focus();
        await userEvent.tab();
        expect(dialog.contains(document.activeElement)).toBe(true);
        await userEvent.tab({ shift: true });
        await userEvent.tab({ shift: true });
        expect(dialog.contains(document.activeElement)).toBe(true);
    });
});
