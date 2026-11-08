/**
 * @file test_jobHistoryList.test.tsx
 * @description Tests for the Recent Jobs list: tonight's jobs plus pinned running jobs by default,
 * earlier nights loaded on demand with a `before` cutoff, headings per night, and no delete button.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { JobHistoryList } from '../components/JobHistoryList';

const fetchJobsMock = vi.fn();
vi.mock('../../common/services/imaging/processingService', () => ({
    fetchJobs: (...args: unknown[]) => fetchJobsMock(...args),
}));

/** Builds a minimal job. */
const job = (id: string, createdAt: string, status = 'completed') => ({
    id, targetId: 'M 31', jobType: 'stacking', status, createdAt,
});

// "Now" is Thursday Nov 5, 2026 at 22:00, so tonight began at noon on Nov 5.
const NOW = new Date(2026, 10, 5, 22, 0);

describe('JobHistoryList', () => {
    beforeEach(() => {
        fetchJobsMock.mockReset();
        fetchJobsMock.mockResolvedValue([]);
    });

    /** Only tonight's jobs show, and nothing older is fetched until asked. */
    it('shows only tonight by default and fetches nothing', () => {
        render(<JobHistoryList targetId="M 31" now={NOW} jobs={[
            job('tonight', '2026-11-05T20:00:00'),
            job('last-night', '2026-11-04T21:00:00'),
        ] as never} />);
        expect(screen.getAllByRole('listitem')).toHaveLength(1);
        expect(fetchJobsMock).not.toHaveBeenCalled();
    });

    /** A running job from an older night is pinned at the top and not shown twice. */
    it('pins running jobs on top whatever their age', () => {
        const running = job('running', '2026-11-01T21:00:00', 'running');
        render(<JobHistoryList targetId="M 31" now={NOW}
            pinnedJobs={[running] as never}
            jobs={[job('tonight', '2026-11-05T20:00:00'), running] as never} />);
        const rows = screen.getAllByRole('listitem');
        expect(rows).toHaveLength(2);
        expect(rows[0].textContent).toContain('running');
    });

    /** The delete button is gone and the Console button stays. */
    it('has no delete button but keeps Console', () => {
        render(<JobHistoryList targetId="M 31" now={NOW} jobs={[job('a', '2026-11-05T20:00:00')] as never} />);
        expect(screen.queryByTitle('Dismiss Job')).toBeNull();
        expect(screen.getByRole('button', { name: 'Console' })).toBeTruthy();
    });

    /** Clicking a row selects it. */
    it('selects a job when its row is clicked', () => {
        const onSelectJob = vi.fn();
        render(<JobHistoryList targetId="M 31" now={NOW} onSelectJob={onSelectJob}
            jobs={[job('a', '2026-11-05T20:00:00')] as never} />);
        fireEvent.click(screen.getByRole('listitem'));
        expect(onSelectJob).toHaveBeenCalledTimes(1);
    });

    /** Expanding loads jobs before tonight's start and groups them under night headings. */
    it('loads earlier nights with a before cutoff and shows a heading per night', async () => {
        fetchJobsMock.mockResolvedValueOnce([
            job('wed', '2026-11-04T21:00:00'),
            job('tue', '2026-11-03T21:00:00'),
        ]);
        render(<JobHistoryList targetId="M 31" now={NOW} jobs={[] as never} />);
        fireEvent.click(screen.getByRole('button', { name: /Earlier nights/ }));
        await waitFor(() => expect(screen.getByRole('heading', { name: 'Wed Nov 4' })).toBeTruthy());
        expect(screen.getByRole('heading', { name: 'Tue Nov 3' })).toBeTruthy();
        expect(fetchJobsMock).toHaveBeenCalledWith('M 31', undefined, { before: '2026-11-05T12:00:00', limit: 30 });
    });

    /** "Show more" continues from the oldest job already loaded. */
    it('pages further back from the oldest loaded job', async () => {
        const fullPage = Array.from({ length: 30 }, (_, index) =>
            job(`j${index}`, `2026-11-04T${String(20 - Math.floor(index / 2)).padStart(2, '0')}:${index % 2 ? '30' : '00'}:00`));
        fetchJobsMock.mockResolvedValueOnce(fullPage).mockResolvedValueOnce([job('old', '2026-10-30T21:00:00')]);
        render(<JobHistoryList targetId="M 31" now={NOW} jobs={[] as never} />);
        fireEvent.click(screen.getByRole('button', { name: /Earlier nights/ }));
        const more = await screen.findByRole('button', { name: /Show more/ });
        const oldest = fullPage.map((entry) => entry.createdAt).sort()[0];
        fireEvent.click(more);
        await waitFor(() => expect(fetchJobsMock).toHaveBeenCalledTimes(2));
        expect(fetchJobsMock).toHaveBeenLastCalledWith('M 31', undefined, { before: oldest, limit: 30 });
        await waitFor(() => expect(screen.getByRole('heading', { name: 'Fri Oct 30' })).toBeTruthy());
    });

    /** "Hide earlier nights" folds the list back to tonight; opening it again fetches fresh. */
    it('hides the earlier nights again', async () => {
        fetchJobsMock.mockResolvedValue([job('wed', '2026-11-04T21:00:00')]);
        render(<JobHistoryList targetId="M 31" now={NOW} jobs={[] as never} />);
        fireEvent.click(screen.getByRole('button', { name: /Earlier nights/ }));
        await screen.findByRole('heading', { name: 'Wed Nov 4' });

        fireEvent.click(screen.getByRole('button', { name: /Hide earlier nights/ }));

        expect(screen.queryByRole('heading', { name: 'Wed Nov 4' })).toBeNull();
        expect(screen.queryByRole('button', { name: /Hide earlier nights/ })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: /Earlier nights/ }));
        await screen.findByRole('heading', { name: 'Wed Nov 4' });
        expect(fetchJobsMock).toHaveBeenCalledTimes(2);
    });
});
