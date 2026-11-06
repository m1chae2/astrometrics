/**
 * @fileoverview Tests for the Stack Summary block: the numbers with units, the warnings of
 * the stacking job (and the stored checks for a stack with no job id), the exposure groups (hidden for one),
 * the hidden zero-count rows, the empty state for a target with no stack, and the loading and error states.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
    StackSummaryPanel,
    describeGroupNotes,
    formatExposureSeconds,
} from '../components/StackSummaryPanel';
import { describeStackWarningTitle, describeWarningNumbers } from '../utils/stackWarnings';
import { BackendError } from '../../common/services/backendError';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({ callBackend: (...args: unknown[]) => callBackendMock(...args) }));

const summary = {
    target_id: 'M 81', kind: 'imaging', stack_path: '/stacks/M81.fits',
    made_at: '2026-10-30T04:00:00+00:00',
    frames_submitted: 40, frames_stacked: 36, frames_skipped: 4,
    skipped_reasons: [{ file: 'a.fits', reason: 'trailed stars' }],
    frames_set_aside_before_stacking: 3,
    sessions: [], rejected_pixel_fraction: 0.0123,
    star_width_px: { stack: 2.31, expected_from_inputs: 2.2, median_of_inputs: 2.4 },
    exposure_groups: [
        { exposure_seconds: 30, frames_submitted: 30, frames_stacked: 30, saturated: true, clipped_at_zero: false,
          alignment_shift_pixels: null, left_out_reason: null },
        { exposure_seconds: 5, frames_submitted: 10, frames_stacked: 6, saturated: false, clipped_at_zero: true,
          alignment_shift_pixels: [3, 4], left_out_reason: 'gain disagrees' },
    ],
    calibration_mismatches: 0,
    job_id: 'job-stack-1',
};

/** A warning of the stacking job, as `jobs:query` sends it. */
const jobWarning = {
    pipeline: 'stacking', metric: 'stacking.gate_rejected_pixel_fraction', subject: 'M 81/M81.fits',
    subject_level: 'stack', target_id: 'M 81', reason: 'rejected pixel fraction 16.1% at or above threshold',
    outcome: 'failed', value: 0.161, limit: 0.15, unit: 'fraction', register_entry: 'STK-24',
    action_taken: null, measured_at: '2026-10-30T04:00:00+00:00',
};

/** A stored failed check, as `quality:query` sends it. */
const storedRow = (metric: string, subjectId: string, outcome: string, value: number, limit: number) => ({
    metric, definitionVersion: 1, status: 'measured', value, reason: '', checkOutcome: outcome,
    checkLimit: { value: limit, unit: 'fraction', tier: 'design_estimate', derivation: 'x', registerEntry: 'STK-26' },
    subject: { level: 'stack', subjectId, targetId: 'M 81' }, origin: 'measured', runId: 'r', measuredAt: 'x',
});

/**
 * Answers each backend call by its method name.
 * @param answers The answer for each method; a method left out answers with an empty list.
 */
function answerWith(answers: Record<string, unknown>) {
    callBackendMock.mockImplementation(async (method: string) => {
        if (method in answers) {
            const answer = answers[method];
            if (answer instanceof Error) throw answer;
            return answer;
        }
        return method === 'jobs:query' ? { detail: 'warnings', warnings: [] } : [];
    });
}

/** Renders the panel inside a fresh query client. */
function renderPanel(kind: 'imaging' | 'spectral' = 'imaging') {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <StackSummaryPanel targetId="M 81" kind={kind} />
        </QueryClientProvider>
    );
}

describe('StackSummaryPanel', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
    });

    it('shows the frame count and the set-aside row, and no pixels rejected or star width row', async () => {
        answerWith({ 'processing:stack_summary': summary });
        renderPanel();
        expect(await screen.findByText('Imaging stack summary: 36 of 40 frames')).toBeTruthy();
        expect(screen.getByText('3 frames')).toBeTruthy();
        expect(screen.queryByText(/Pixels rejected/)).toBeNull();
        expect(screen.queryByText(/% of pixels/)).toBeNull();
        expect(screen.queryByText(/Star width/)).toBeNull();
        expect(callBackendMock).toHaveBeenCalledWith(
            'processing:stack_summary', { target: 'M 81', kind: 'imaging' }, { silent: true });
    });

    it('hides the set-aside and left-out rows when their counts are 0', async () => {
        answerWith({ 'processing:stack_summary': {
            ...summary, frames_stacked: 40, frames_skipped: 0, skipped_reasons: [],
            frames_set_aside_before_stacking: 0,
        } });
        renderPanel();
        await screen.findByText('Imaging stack summary: 40 of 40 frames');
        expect(screen.queryByText('Set aside before stacking')).toBeNull();
        expect(screen.queryByText('Left out while stacking')).toBeNull();
    });

    it('shows the set-aside and left-out rows when their counts are above 0', async () => {
        answerWith({ 'processing:stack_summary': summary });
        renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        expect(screen.getByText('Set aside before stacking')).toBeTruthy();
        expect(screen.getByText('Left out while stacking')).toBeTruthy();
        expect(screen.getByText('4 frames')).toBeTruthy();
    });

    it('is closed at first and shows one line with the frames only, and the date row inside', async () => {
        answerWith({ 'processing:stack_summary': summary, 'jobs:query': { detail: 'warnings', warnings: [jobWarning] } });
        const { container } = renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        await screen.findByTestId('stack-warning-count');
        const disclosure = container.querySelector('details.stack-summary') as HTMLDetailsElement;
        expect(disclosure.open).toBe(false);
        const line = disclosure.querySelector(':scope > summary') as HTMLElement;
        expect(line.textContent).toBe('Imaging stack summary: 36 of 40 frames · 1 warning');
        const firstLabel = disclosure.querySelector('dl > dt') as HTMLElement;
        expect(firstLabel.textContent).toBe('Stack date');
        expect((firstLabel.nextElementSibling as HTMLElement).textContent).not.toBe('');
    });

    it('adds the warning count to the closed line only when there are warnings', async () => {
        answerWith({ 'processing:stack_summary': summary, 'jobs:query': { detail: 'warnings', warnings: [jobWarning, jobWarning] } });
        const first = renderPanel();
        expect((await screen.findByTestId('stack-warning-count')).textContent).toBe(' · 2 warnings');
        first.unmount();
        answerWith({ 'processing:stack_summary': summary });
        renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        expect(screen.queryByTestId('stack-warning-count')).toBeNull();
    });

    it('keeps the summary content reachable by keyboard through the summary element', async () => {
        answerWith({ 'processing:stack_summary': summary });
        const { container } = renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        const summaries = container.querySelectorAll('summary');
        // A summary element is focusable and opens with Enter or Space in a browser.
        expect(summaries.length).toBeGreaterThan(1);
        expect(container.querySelector('details.stack-summary > summary')).toBeTruthy();
    });

    it('lists the warnings of the stacking job with title, number, limit and outcome', async () => {
        answerWith({
            'processing:stack_summary': summary,
            'jobs:query': { detail: 'warnings', warnings: [
                jobWarning,
                { ...jobWarning, metric: 'stacking.gate_raw_frame_clipping', value: 0.3, limit: 0.2 },
                { ...jobWarning, metric: 'stacking.something_new', value: null, limit: null, unit: '',
                  reason: 'a reason with no number' },
            ] },
        });
        const { container } = renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        expect(callBackendMock).toHaveBeenCalledWith(
            'jobs:query', { job_id: 'job-stack-1', detail: 'warnings' }, { silent: true });
        const items = await screen.findAllByRole('listitem', {}).then(
            () => Array.from(container.querySelectorAll('.stack-summary__flag-list > li')));
        expect(items).toHaveLength(3);
        expect(items[0].textContent).toContain('Many rejected pixels.');
        expect(items[0].textContent).toContain('Measured 16.1%; limit 15%.');
        expect(items[0].textContent).toContain('Outcome: failed.');
        expect(items[0].textContent).toContain('STK-24');
        // The clipping warning is named for what it is, not as a group left out.
        expect(items[1].textContent).toContain('Raw frames cut off at zero.');
        expect(items[1].textContent).not.toContain('left out');
        // A check with no number shows its sentence; a metric with no title gets one made from its name.
        expect(items[2].textContent).toContain('Something new.');
        expect(items[2].textContent).toContain('a reason with no number');
        expect(container.querySelectorAll('.stack-summary__flag-list details')).toHaveLength(0);
    });

    it('reads the stored failed checks of the stack when it has no job id', async () => {
        const { job_id: _omitted, ...withoutJob } = summary;
        answerWith({
            'processing:stack_summary': withoutJob,
            'quality:query': [
                storedRow('stacking.gate_zero_pixel_fraction', 'M 81/M81.fits', 'failed', 0.6, 0.5),
                storedRow('stacking.gate_negative_pixels', 'M 81/M81.fits', 'passed', 1, 50),
                storedRow('stacking.gate_zero_pixel_fraction', 'M 81/other.fits', 'failed', 0.9, 0.5),
            ],
        });
        const { container } = renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        const items = await screen.findAllByText(/Many zero pixels in the stack/);
        expect(items).toHaveLength(1);
        expect(container.querySelectorAll('.stack-summary__flag-list > li')).toHaveLength(1);
        expect(container.textContent).toContain('Measured 60%; limit 50%.');
        expect(callBackendMock).toHaveBeenCalledWith(
            'quality:query',
            { target_id: 'M 81', level: 'stack', library_only: true, latest_only: true },
            { silent: true });
        expect(callBackendMock).not.toHaveBeenCalledWith('jobs:query', expect.anything(), expect.anything());
    });

    it('falls back to the stored checks when the job has no warnings on record', async () => {
        answerWith({
            'processing:stack_summary': summary,
            'quality:query': [storedRow('stacking.gate_stack_sharpness', 'M 81/M81.fits', 'failed', 1.26, 1.2)],
        });
        renderPanel();
        expect(await screen.findByText(/Stars wider than expected/)).toBeTruthy();
    });

    it('says when the warnings could not be read', async () => {
        answerWith({ 'processing:stack_summary': summary, 'jobs:query': new Error('down') });
        renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        expect(await screen.findByText('Could not read the warnings.')).toBeTruthy();
    });

    it('lists exposure groups in a closed disclosure with Frames used and Notes', async () => {
        answerWith({ 'processing:stack_summary': summary });
        const { container } = renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        const disclosure = screen.getByText('Exposure groups (2)').closest('details') as HTMLDetailsElement;
        expect(disclosure.open).toBe(false);
        expect(container.querySelector('caption')).toBeNull();
        const headers = Array.from(container.querySelectorAll('thead th')).map((cell) => cell.textContent);
        expect(headers).toEqual(['Exposure', 'Frames used', 'Notes']);
        const rows = container.querySelectorAll('tbody tr');
        expect(rows).toHaveLength(2);
        expect(within(rows[0] as HTMLElement).getByText('bright stars clipped')).toBeTruthy();
        expect(within(rows[1] as HTMLElement).getByText('gain disagrees; sky cut off at zero')).toBeTruthy();
        expect(within(rows[1] as HTMLElement).getByText('6 of 10')).toBeTruthy();
    });

    it('hides the exposure groups when there is only one', async () => {
        answerWith({ 'processing:stack_summary': { ...summary, exposure_groups: [summary.exposure_groups[0]] } });
        renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        expect(screen.queryByText(/Exposure groups/)).toBeNull();
        expect(screen.queryByRole('table')).toBeNull();
    });

    it('says "Warnings: none" without a preamble when no warnings were raised', async () => {
        answerWith({ 'processing:stack_summary': summary });
        const { container } = renderPanel();
        await screen.findByText('Imaging stack summary: 36 of 40 frames');
        await screen.findByText('none');
        expect((container.querySelector('.stack-summary__flags') as HTMLElement).textContent)
            .toBe('Warnings: none');
    });

    it('shows no preamble sentence above a warning list', async () => {
        answerWith({ 'processing:stack_summary': summary, 'jobs:query': { detail: 'warnings', warnings: [jobWarning] } });
        renderPanel();
        await screen.findByText(/Many rejected pixels/);
        expect(screen.queryByText(/Each is a check/)).toBeNull();
    });

    it('shows a muted note, not an error, when the target has no stack', async () => {
        answerWith({ 'processing:stack_summary': new BackendError({ code: 'not_found', message: 'no summary' }) });
        renderPanel('spectral');
        expect(await screen.findByText(/Spectral stack: no saved summary yet/)).toBeTruthy();
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('shows loading, then an error for other failures', async () => {
        answerWith({ 'processing:stack_summary': new Error('down') });
        renderPanel();
        expect(screen.getByText(/Loading imaging stack summary/)).toBeTruthy();
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toContain('Could not load');
    });

    it('formats numbers with units', () => {
        expect(formatExposureSeconds(0.5)).toBe('0.5 s');
        expect(formatExposureSeconds(30)).toBe('30 s');
        expect(describeGroupNotes({ exposure_seconds: 5, frames_submitted: 1, frames_stacked: 0, left_out_reason: 'x' })).toBe('x');
        expect(describeGroupNotes({ exposure_seconds: 5, frames_submitted: 2, frames_stacked: 1 })).toBe('some frames left out');
        expect(describeGroupNotes({ exposure_seconds: 5, frames_submitted: 1, frames_stacked: 1 })).toBe('—');
    });
});


describe('stack warning wording', () => {
    it.each([
        ['stacking.gate_raw_frame_clipping', 'Raw frames cut off at zero'],
        ['stacking.gate_stack_sharpness', 'Stars wider than expected'],
        ['stacking.gate_rejected_pixel_fraction', 'Many rejected pixels'],
        ['stacking.abandoned_after_s', 'Stacking ran out of time'],
        ['stacking.gate_dark_missing', 'Dark frames missing'],
    ])('titles %s', (metric, title) => {
        expect(describeStackWarningTitle(metric)).toBe(title);
    });

    it('makes a title from the name of a metric it does not know', () => {
        expect(describeStackWarningTitle('stacking.gate_new_check')).toBe('New check');
    });

    it('writes the number and the limit, a fraction as a percent', () => {
        const base = { metric: 'm', reason: '', actionTaken: null, registerEntry: null };
        expect(describeWarningNumbers({ ...base, value: 0.161, limit: 0.15, unit: 'fraction' }))
            .toBe('Measured 16.1%; limit 15%.');
        expect(describeWarningNumbers({ ...base, value: 3.456, limit: 1.2, unit: 'px' }))
            .toBe('Measured 3.46 px; limit 1.2 px.');
        expect(describeWarningNumbers({ ...base, value: 62, limit: null, unit: '' })).toBe('Measured 62.');
        expect(describeWarningNumbers({ ...base, value: null, limit: null, unit: '' })).toBe('');
    });
});
