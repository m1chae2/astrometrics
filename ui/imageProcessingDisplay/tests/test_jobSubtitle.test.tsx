/**
 * @file test_jobSubtitle.test.tsx
 * @description Tests for the Recent Jobs subtitle helper and for the card that shows it.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { JobHistoryList } from '../components/JobHistoryList';
import { jobStepsSubtitle } from '../utils/jobSubtitle';

vi.mock('../../common/services/imaging/processingService', () => ({ fetchJobs: vi.fn() }));

/** Builds an analysis job with the given recorded steps. */
const analysis = (steps: object, status = 'completed') => ({
    jobType: 'analysis', status, inputMetrics: { steps },
});

describe('jobStepsSubtitle', () => {
    /** A prerequisite shows before the stage that needed it. */
    it('puts an added prerequisite before the chosen stage', () => {
        expect(jobStepsSubtitle(analysis({
            requested: ['photometry'], prerequisites: ['astrometry'],
            run: ['astrometry', 'photometry'], finished: true,
        }))).toBe('Astrometry → Photometry');
    });

    /** Both stacks clicked at once join with a plus sign, after the prerequisite. */
    it('joins stages chosen together, with astrometry first', () => {
        expect(jobStepsSubtitle(analysis({
            requested: ['photometry', 'spectroscopy'], prerequisites: ['astrometry'],
            run: ['astrometry', 'photometry', 'spectroscopy'], finished: true,
        }))).toBe('Astrometry → Photometry + Spectroscopy');
        expect(jobStepsSubtitle(analysis({
            requested: ['photometry', 'spectroscopy'], run: ['photometry', 'spectroscopy'], finished: true,
        }))).toBe('Photometry + Spectroscopy');
    });

    /** Single stages and the spectral variation chain. */
    it('names single stages and spectroscopy before spectral variation', () => {
        expect(jobStepsSubtitle(analysis({ requested: ['spectroscopy'], run: ['spectroscopy'], finished: true })))
            .toBe('Spectroscopy');
        expect(jobStepsSubtitle(analysis({ requested: ['spectral_variation'], run: ['spectral_variation'], finished: true })))
            .toBe('Spectral Variation');
        expect(jobStepsSubtitle(analysis({
            requested: ['spectral_variation'], prerequisites: ['spectroscopy'],
            run: ['spectroscopy', 'spectral_variation'], finished: true,
        }))).toBe('Spectroscopy → Spectral Variation');
    });

    /** While the job runs, the requested steps and any added prerequisite show. */
    it('shows the requested steps and added prerequisites while running', () => {
        expect(jobStepsSubtitle(analysis({ requested: ['photometry'] }, 'running'))).toBe('Photometry');
        expect(jobStepsSubtitle(analysis({ requested: ['photometry'], prerequisites: ['astrometry'] }, 'running')))
            .toBe('Astrometry → Photometry');
    });

    /** A skipped stage gets a short note only when the reason is short. */
    it('shows a short skip reason and drops a long one', () => {
        expect(jobStepsSubtitle(analysis({
            requested: ['photometry'], run: [], skipped: { photometry: 'needs astrometry: no stack' }, finished: true,
        }))).toBe('Photometry (skipped: needs astrometry)');
        expect(jobStepsSubtitle(analysis({
            requested: ['spectroscopy'], run: [], finished: true,
            skipped: {
                spectroscopy: 'needs astrometry: its result is missing; run processing.astrometry.run(target) first',
            },
        }))).toBe('Spectroscopy (skipped: needs astrometry)');
        expect(jobStepsSubtitle(analysis({
            requested: ['photometry'], run: [],
            skipped: { photometry: 'the target has no imaging stack to solve at all' }, finished: true,
        }))).toBe('Photometry (skipped)');
    });

    /** Stacking jobs name the stack kind. */
    it('names the stack kind', () => {
        expect(jobStepsSubtitle({ jobType: 'stacking', status: 'completed', inputMetrics: { stackKinds: ['imaging'] } }))
            .toBe('Luminance');
        expect(jobStepsSubtitle({ jobType: 'stacking', status: 'running', inputMetrics: { stackKinds: ['imaging', 'spectral'] } }))
            .toBe('Luminance + Spectral');
    });

    /** Old jobs and failed jobs keep their message. */
    it('returns null for old jobs and failed jobs', () => {
        expect(jobStepsSubtitle({ jobType: 'analysis', status: 'completed', inputMetrics: {} })).toBeNull();
        expect(jobStepsSubtitle({ jobType: 'analysis', status: 'completed', inputMetrics: null })).toBeNull();
        expect(jobStepsSubtitle(analysis({ requested: ['photometry'] }, 'failed'))).toBeNull();
    });
});

describe('JobHistoryList subtitle', () => {
    const now = new Date(2026, 10, 5, 22, 0);
    const base = { targetId: 'M 31', createdAt: '2026-11-05T20:00:00' };

    /** A running job shows its requested steps; a finished one shows the steps run. */
    it('draws the steps while running and after, and the message for old jobs', () => {
        const running = { ...base, id: 'r', jobType: 'analysis', status: 'running',
            message: 'Working', inputMetrics: { steps: { requested: ['photometry'], prerequisites: ['astrometry'] } } };
        const done = { ...base, id: 'd', jobType: 'analysis', status: 'completed', message: 'Ran spectroscopy.',
            inputMetrics: { steps: { requested: ['spectroscopy'], run: ['spectroscopy'], finished: true } } };
        const old = { ...base, id: 'o', jobType: 'analysis', status: 'completed', message: 'Ran photometry.' };
        const failed = { ...base, id: 'f', jobType: 'analysis', status: 'failed', message: 'Boom',
            inputMetrics: { steps: { requested: ['photometry'] } } };
        render(<JobHistoryList targetId="M 31" now={now} jobs={[running, done, old, failed] as never} />);
        expect(screen.getByText('Astrometry → Photometry')).toBeTruthy();
        expect(screen.getByText('Spectroscopy')).toBeTruthy();
        expect(screen.getByText('Ran photometry')).toBeTruthy();
        expect(screen.getByText('Boom')).toBeTruthy();
        expect(screen.queryByText('Working')).toBeNull();
    });
});
