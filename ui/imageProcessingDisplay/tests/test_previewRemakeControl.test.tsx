/**
 * @fileoverview Tests for the "Make the picture again" control: the plain-language account of
 * what made a picture, the choices sent to the backend, a job that is followed to the end, the
 * message when a picture you attached is on display, and the viewer reload event.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
    PreviewRemakeControl,
    choiceToFlag,
    describePreviewSettings,
    parseStrength,
} from '../components/PreviewRemakeControl';
import { on as onEvent } from '../../common/utils/eventBus';

const callBackendMock = vi.fn();
const failure = { error: null as Error | null };
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => {
        if (failure.error) throw failure.error;
        return callBackendMock(...args);
    },
}));
vi.mock('../../common/services/imaging/processingService', () => ({ fetchJobs: vi.fn().mockResolvedValue([]) }));

const record = {
    made_at: '2026-10-31T01:02:03+00:00',
    settings_used: { denoise: true, denoise_strength: 0.5, star_toning: false, starnet: false, bright_object: false },
    steps_run: ['GraXpert done', 'Star toning off'],
    custom: true,
};

const result = (overrides: Record<string, unknown> = {}) => ({
    target_id: 'M 81', stack_path: '/s/M_81_Stacked.fits', preview_path: '/s/M_81_Stacked_preview.jpg',
    steps_run: [], shown_in_viewer: true, previous_pictures: ['/s/_previous_preview/x.jpg'],
    stack_file_unchanged: true, preview_settings: record, ...overrides,
});

/** Opens the disclosure and presses the button. */
function pressMake() {
    fireEvent.click(screen.getByText('Make the picture again'));
    fireEvent.click(screen.getByRole('button', { name: 'Make picture again' }));
}

describe('helpers', () => {
    it('turns a choice into a flag, with the app setting as undefined', () => {
        expect(choiceToFlag('app')).toBeUndefined();
        expect(choiceToFlag('on')).toBe(true);
        expect(choiceToFlag('off')).toBe(false);
    });

    it('accepts empty text and numbers from 0 to 1, and refuses the rest', () => {
        expect(parseStrength('')).toBeUndefined();
        expect(parseStrength('0.4')).toBe(0.4);
        expect(parseStrength('1.5')).toBeNull();
        expect(parseStrength('abc')).toBeNull();
    });

    it('describes the settings that were used', () => {
        expect(describePreviewSettings(record as never)).toEqual([
            'Noise removal (Cosmic Clarity) was on, at strength 0.5.',
            'Star toning was off.',
            'These choices were for that run only.',
        ]);
    });

    it('says a bright object skipped the cleanup', () => {
        const bright = { ...record, custom: false, settings_used: { ...record.settings_used, bright_object: true } };
        const lines = describePreviewSettings(bright as never);
        expect(lines[0]).toMatch(/bright object/);
        expect(lines[1]).toBe('These were the app settings.');
    });

    it('says unknown when there is no record, and shows no default', () => {
        expect(describePreviewSettings(null)).toEqual(['What made this picture is unknown. No record was saved with it.']);
    });
});

describe('PreviewRemakeControl', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        failure.error = null;
    });
    afterEach(() => vi.useRealTimers());

    it('sends only the chosen settings and shows what made the picture', async () => {
        callBackendMock.mockResolvedValue({ status: 'completed', job_id: 'j1', result: result() });
        const remade = vi.fn();
        const detach = onEvent('stackPictureRemade', remade);
        const onShowPicture = vi.fn();
        render(<PreviewRemakeControl targetId="M 81" kind="imaging" onShowPicture={onShowPicture} />);
        fireEvent.click(screen.getByText('Make the picture again'));
        fireEvent.click(screen.getByRole('button', { name: 'Noise removal: Use the app setting' }));
        fireEvent.click(screen.getByRole('button', { name: 'On' }));
        fireEvent.change(screen.getByLabelText('Noise removal strength'), { target: { value: '0.5' } });
        fireEvent.click(screen.getByRole('button', { name: 'Make picture again' }));

        expect(await screen.findByText('Noise removal (Cosmic Clarity) was on, at strength 0.5.')).toBeTruthy();
        expect(callBackendMock).toHaveBeenCalledWith(
            'processing:remake_preview',
            { target_id: 'M 81', kind: 'imaging', denoise: true, denoise_strength: 0.5, star_toning: undefined },
            { silent: true });
        await waitFor(() => expect(onShowPicture).toHaveBeenCalledWith('/s/M_81_Stacked.fits'));
        expect(remade).toHaveBeenCalled();
        detach();
    });

    it('never shows the folder of previous pictures', async () => {
        callBackendMock.mockResolvedValue({ status: 'completed', job_id: 'j1', result: result() });
        const { container } = render(<PreviewRemakeControl targetId="M 81" kind="imaging" />);
        pressMake();
        await screen.findByText(/New picture made/);
        expect(container.textContent).not.toMatch(/previous/i);
    });

    it('says the picture was made but an attached picture is on display', async () => {
        callBackendMock.mockResolvedValue({
            status: 'completed', job_id: 'j1', result: result({ shown_in_viewer: false }),
        });
        const onShowPicture = vi.fn();
        render(<PreviewRemakeControl targetId="M 81" kind="imaging" onShowPicture={onShowPicture} />);
        pressMake();
        expect(await screen.findByText(/a picture you attached is on display/)).toBeTruthy();
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(onShowPicture).not.toHaveBeenCalled();
    });

    it('follows a running job until it completes', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        callBackendMock.mockImplementation(async (action: string) => {
            if (action === 'processing:remake_preview') return { status: 'running', job_id: 'j9', result: null };
            return { id: 'j9', status: 'completed', outputMetrics: { result: result() } };
        });
        render(<PreviewRemakeControl targetId="M 81" kind="imaging" />);
        pressMake();
        expect(await screen.findByText(/Making the picture/)).toBeTruthy();
        await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
        await waitFor(() => expect(screen.getByText(/New picture made/)).toBeTruthy());
        expect(callBackendMock).toHaveBeenCalledWith('processing:get_job', { job_id: 'j9' }, { silent: true });
    });

    it('shows the message of a failed job', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        callBackendMock.mockImplementation(async (action: string) => {
            if (action === 'processing:remake_preview') return { status: 'running', job_id: 'j9', result: null };
            return { id: 'j9', status: 'failed', message: 'No preview was made.' };
        });
        render(<PreviewRemakeControl targetId="M 81" kind="imaging" />);
        pressMake();
        expect(await screen.findByText(/Making the picture/)).toBeTruthy();
        await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
        expect((await screen.findByRole('alert')).textContent).toMatch(/No preview was made/);
    });

    it('shows an error that comes back at once', async () => {
        failure.error = new Error('The stack file is missing.');
        render(<PreviewRemakeControl targetId="M 81" kind="imaging" />);
        pressMake();
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toMatch(/The stack file is missing/);
    });

    it('blocks a strength out of range', () => {
        render(<PreviewRemakeControl targetId="M 81" kind="imaging" />);
        fireEvent.click(screen.getByText('Make the picture again'));
        fireEvent.change(screen.getByLabelText('Noise removal strength'), { target: { value: '3' } });
        expect((screen.getByRole('button', { name: 'Make picture again' }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText('Enter a number from 0 to 1.')).toBeTruthy();
        expect(callBackendMock).not.toHaveBeenCalled();
    });
});
