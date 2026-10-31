/**
 * @fileoverview Tests for the set-aside frames list: a plain sentence for each stored kind, the
 * measurement as detail, the file and exposure, "no record of why" where the reason is missing,
 * the restore note for a frame that cannot be put back, and that nothing internal is shown.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
    NO_RECORD_OF_WHY,
    SetAsideFramesList,
    describeSetAsideKind,
    hasNoRecordedReason,
    restoreNoteOf,
} from '../components/SetAsideFramesList';
import { StackSummaryPanel } from '../components/StackSummaryPanel';

const callBackendMock = vi.fn();
// A plain function throws the failure, because vitest 5 fails a test whose mock returns a rejected promise.
const failure = { error: null as Error | null };
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => {
        if (failure.error) throw failure.error;
        return callBackendMock(...args);
    },
}));

const frame = (overrides: Record<string, unknown> = {}) => ({
    file: 'M81_L_0001.fits', path: '/lib/_excluded/M81_L_0001.fits', subjectId: 'M 81/abc',
    originalPath: '/lib/M81_L_0001.fits', kind: 'trailed', reason: 'star roundness 0.62, below 0.70',
    reasonSource: 'stored_row', actionTaken: 'frame moved to _excluded/: trailed (secret internal text)',
    exposureSeconds: 30, captureSettingId: 4, movedAt: '2026-10-30T01:00:00+00:00',
    restorable: true, restoreNote: '', ...overrides,
});

/** Renders the list in a fresh query client. */
function renderList() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <SetAsideFramesList targetId="M 81" count={3} />
        </QueryClientProvider>
    );
}

describe('helpers', () => {
    it('words the two stored kinds without changing them', () => {
        expect(describeSetAsideKind('clouded')).toMatch(/Clouds/);
        expect(describeSetAsideKind('trailed')).toBe('Stars smeared, or far fewer stars than usual.');
    });

    it('says there is no record when the kind is missing', () => {
        expect(describeSetAsideKind(null)).toBe(NO_RECORD_OF_WHY);
        expect(hasNoRecordedReason(frame({ kind: null, reason: '' }) as never)).toBe(true);
        expect(hasNoRecordedReason(frame() as never)).toBe(false);
    });

    it('gives a note only for a frame that cannot be restored', () => {
        expect(restoreNoteOf(frame() as never)).toBeNull();
        expect(restoreNoteOf(frame({ restorable: false, restoreNote: 'No manifest entry.' }) as never))
            .toBe('No manifest entry.');
        expect(restoreNoteOf(frame({ restorable: false, restoreNote: '' }) as never)).toMatch(/cannot be put back/);
    });
});

describe('SetAsideFramesList', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        failure.error = null;
    });

    it('shows the sentence, the measurement, the file and the exposure, and hides internals', async () => {
        callBackendMock.mockResolvedValue([frame()]);
        const { container } = renderList();
        expect(await screen.findByText('Stars smeared, or far fewer stars than usual.')).toBeTruthy();
        expect(screen.getByText('star roundness 0.62, below 0.70')).toBeTruthy();
        expect(screen.getByText(/M81_L_0001\.fits, 30 s exposure/)).toBeTruthy();
        expect(container.textContent).not.toMatch(/secret internal text|M 81\/abc|frame moved to/);
        expect(screen.queryByRole('button')).toBeNull();
        expect(callBackendMock).toHaveBeenCalledWith('quality:set_aside_frames', { target: 'M 81' }, { silent: true });
    });

    it('says no record of why where the reason is missing', async () => {
        callBackendMock.mockResolvedValue([
            frame({ kind: null, reason: '', reasonSource: 'folder_only', exposureSeconds: null, restorable: false,
                    restoreNote: 'The folder does not say where it came from.' }),
        ]);
        renderList();
        expect(await screen.findByText(NO_RECORD_OF_WHY)).toBeTruthy();
        expect(screen.getByText('The folder does not say where it came from.')).toBeTruthy();
        expect(screen.queryByText(/exposure/)).toBeNull();
    });

    it('shows an empty note, an error and a loading state', async () => {
        callBackendMock.mockResolvedValue([]);
        const first = renderList();
        expect(screen.getByText(/Reading the set-aside frames/)).toBeTruthy();
        expect(await screen.findByText(/No set-aside frames were found/)).toBeTruthy();
        first.unmount();

        failure.error = new Error('down');
        renderList();
        expect(await screen.findByRole('alert')).toBeTruthy();
    });
});

describe('the count in the Stack Summary block', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        failure.error = null;
    });

    it('reads the list only when the disclosure is opened', async () => {
        callBackendMock.mockImplementation(async (action: string) => {
            if (action === 'processing:stack_summary') {
                return {
                    target_id: 'M 81', kind: 'imaging', stack_path: '/s.fits', made_at: '2026-10-30T04:00:00+00:00',
                    frames_submitted: 10, frames_stacked: 9, frames_skipped: 1, skipped_reasons: [],
                    frames_set_aside_before_stacking: 3, exposure_groups: [], flagged: false, flag_reasons: [],
                    star_width_px: {}, calibration_mismatches: 0,
                };
            }
            return [frame()];
        });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        render(
            <QueryClientProvider client={client}>
                <StackSummaryPanel targetId="M 81" kind="imaging" />
            </QueryClientProvider>
        );
        const summary = await screen.findByText('Why they were set aside');
        expect(callBackendMock).not.toHaveBeenCalledWith('quality:set_aside_frames', expect.anything(), expect.anything());
        const details = summary.closest('details') as HTMLDetailsElement;
        details.open = true;
        fireEvent(details, new Event('toggle'));
        expect(await screen.findByText('Stars smeared, or far fewer stars than usual.')).toBeTruthy();
    });
});
