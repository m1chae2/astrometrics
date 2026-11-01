/**
 * @fileoverview Tests for the Stack Summary block: the numbers with units, the flags,
 * the exposure groups, the empty state for a target with no stack, and the loading
 * and error states.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
    StackSummaryPanel,
    describeGroupClipping,
    describeGroupShift,
    formatExposureSeconds,
    formatPixelShare,
} from '../components/StackSummaryPanel';
import { describeFlagCount, describeStackFlag } from '../utils/stackFlagMeaning';
import { BackendError } from '../../common/services/backendError';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({ callBackend: (...args: unknown[]) => callBackendMock(...args) }));

const summary = {
    target_id: 'M 81', kind: 'imaging', stack_path: '/stacks/M81.fits',
    made_at: '2026-10-30T04:00:00+00:00',
    frames_submitted: 40, frames_stacked: 36, frames_skipped: 4,
    skipped_reasons: [{ file: 'a.fits', reason: 'trailed stars' }],
    frames_set_aside_before_stacking: 3,
    sessions: [], rejected_pixel_fraction: 0.0123, rejected_fraction_flagged: false,
    star_width_px: { stack: 2.31, expected_from_inputs: 2.2, median_of_inputs: 2.4, degraded: true },
    exposure_groups: [
        { exposure_seconds: 30, frames_submitted: 30, frames_stacked: 30, saturated: true, clipped_at_zero: false,
          alignment_shift_pixels: null, left_out_reason: null },
        { exposure_seconds: 5, frames_submitted: 10, frames_stacked: 6, saturated: false, clipped_at_zero: true,
          alignment_shift_pixels: [3, 4], left_out_reason: 'gain disagrees' },
    ],
    flagged: true, flag_reasons: ['Star width is wider than expected'], calibration_mismatches: 0,
};

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

    it('shows frames, rejected share, star width and flags with units', async () => {
        callBackendMock.mockResolvedValue(summary);
        renderPanel();
        expect(await screen.findByText('36 of 40 frames')).toBeTruthy();
        expect(screen.getByText('3 frames')).toBeTruthy();
        expect(screen.getByText(/1\.2% of pixels/)).toBeTruthy();
        expect(screen.getByText(/2\.31 px in the stack/)).toBeTruthy();
        expect(screen.getByText(/wider than expected\)/)).toBeTruthy();
        expect(screen.getByText('Star width is wider than expected')).toBeTruthy();
        expect(callBackendMock).toHaveBeenCalledWith(
            'processing:stack_summary', { target: 'M 81', kind: 'imaging' }, { silent: true });
    });

    it('is closed at first and shows one line with frames, flags and date', async () => {
        callBackendMock.mockResolvedValue(summary);
        const { container } = renderPanel();
        await screen.findByText('36 of 40 frames');
        const disclosure = container.querySelector('details.stack-summary') as HTMLDetailsElement;
        expect(disclosure.open).toBe(false);
        const line = disclosure.querySelector(':scope > summary') as HTMLElement;
        expect(line.textContent).toMatch(/^Imaging stack summary: 36 of 40 frames, 1 flag, made /);
    });

    it('keeps the summary content reachable by keyboard through the summary element', async () => {
        callBackendMock.mockResolvedValue(summary);
        const { container } = renderPanel();
        await screen.findByText('36 of 40 frames');
        const summaries = container.querySelectorAll('summary');
        // A summary element is focusable and opens with Enter or Space in a browser.
        expect(summaries.length).toBeGreaterThan(1);
        expect(container.querySelector('details.stack-summary > summary')).toBeTruthy();
    });

    it('explains a known flag in plain words with the raw text behind a disclosure', async () => {
        callBackendMock.mockResolvedValue({
            ...summary,
            flag_reasons: ['background split: two groups of frames', 'a flag nobody has seen before'],
        });
        const { container } = renderPanel();
        await screen.findByText('36 of 40 frames');
        expect(screen.getByText('Background split.')).toBeTruthy();
        const rawDisclosure = container.querySelector('.stack-summary__flag-list details') as HTMLElement;
        expect(within(rawDisclosure).getByText('background split: two groups of frames')).toBeTruthy();
        // An unknown flag shows as it is, with no disclosure of its own.
        expect(screen.getByText('a flag nobody has seen before')).toBeTruthy();
        expect(container.querySelectorAll('.stack-summary__flag-list details')).toHaveLength(1);
    });

    it('lists each exposure group with clipping, shift and why it was left out', async () => {
        callBackendMock.mockResolvedValue(summary);
        renderPanel();
        const rows = await screen.findAllByRole('row');
        expect(rows).toHaveLength(3);
        expect(screen.getByRole('table')).toBeTruthy();
        expect(screen.getByText('Bright stars at the camera limit')).toBeTruthy();
        expect(screen.getByText('Sky pixels cut off at zero')).toBeTruthy();
        expect(screen.getByText('Not moved (reference)')).toBeTruthy();
        expect(screen.getByText('5.0 px')).toBeTruthy();
        expect(screen.getByText('gain disagrees')).toBeTruthy();
    });

    it('says no flags were raised without judging the stack', async () => {
        callBackendMock.mockResolvedValue({ ...summary, flagged: false, flag_reasons: [], exposure_groups: [] });
        renderPanel();
        expect(await screen.findByText('None raised.')).toBeTruthy();
        expect(screen.queryByRole('table')).toBeNull();
    });

    it('shows a muted note, not an error, when the target has no stack', async () => {
        callBackendMock.mockRejectedValue(new BackendError({ code: 'not_found', message: 'no summary' }));
        renderPanel('spectral');
        expect(await screen.findByText(/Spectral stack: no saved summary yet/)).toBeTruthy();
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('shows loading, then an error for other failures', async () => {
        callBackendMock.mockRejectedValue(new Error('down'));
        renderPanel();
        expect(screen.getByText(/Loading imaging stack summary/)).toBeTruthy();
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toContain('Could not load');
    });

    it('formats numbers with units', () => {
        expect(formatPixelShare(0.5)).toBe('50.0% of pixels');
        expect(formatPixelShare(null)).toBe('not recorded');
        expect(formatExposureSeconds(0.5)).toBe('0.5 s');
        expect(formatExposureSeconds(30)).toBe('30 s');
        expect(describeGroupShift({ exposure_seconds: 5, frames_submitted: 1, frames_stacked: 0, left_out_reason: 'x' })).toBe('—');
        expect(describeGroupClipping({ exposure_seconds: 5, frames_submitted: 1, frames_stacked: 1 })).toBe('No');
    });
});


describe('describeStackFlag', () => {
    it.each([
        ['background split: x', 'Background split'],
        ['2 calibration metadata mismatch(es): gain', 'Calibration mismatch'],
        ['stacked FWHM 3.00px degraded vs the 2.00px its input frames predict', 'Wider stars'],
        ['rejected pixel fraction 12.0% at or above threshold', 'Many rejected pixels'],
        ['calibration frame count too low: dark', 'Too few calibration frames'],
        ['3 of 10 light frame(s) had no dark at their exposure: a', 'Dark frames missing'],
        ['4% of the stack\'s pixels are exactly zero: cause', 'Pixels at exactly zero'],
    ])('recognizes %s', (raw, title) => {
        expect(describeStackFlag(raw)).toMatchObject({ known: true, title });
    });

    it('returns an unknown flag unchanged', () => {
        expect(describeStackFlag('something new')).toEqual({ title: null, explanation: 'something new', known: false });
    });

    it('counts flags for the one-line summary', () => {
        expect(describeFlagCount(false, [])).toBe('no flags');
        expect(describeFlagCount(true, [])).toBe('1 flag');
        expect(describeFlagCount(true, ['a', 'b'])).toBe('2 flags');
    });
});
