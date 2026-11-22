/**
 * @file test_imageDetailsManagerStack.test.tsx
 * @description Tests the Stack Frames split button in ImageDetailsManager: the main click, the Post
 * Process Target entry (one picture remake for each stack that exists, in order), the reason it is
 * off, the busy button while it runs, and the events that make the Image Viewer read the picture again.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@testing-library/react';
import { ImageDetailsManager } from '../imageDetailsManager/ImageDetailsManager';
import type { AnalysisSelectionInput } from '../utils/analysisSelection';
import { on as onEvent } from '../../common/utils/eventBus';
import { STACK_PICTURE_REMADE_EVENT } from '../../common/services/imaging/stackReviewService';

const callBackendMock = vi.fn();
const failure = { error: null as Error | null };
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => {
        if (failure.error) throw failure.error;
        return callBackendMock(...args);
    },
}));
vi.mock('../../common/services/imaging/processingService', () => ({ fetchJobs: vi.fn().mockResolvedValue([]) }));

const bothStacks: AnalysisSelectionInput = {
    filterType: 'L',
    stackedImage: '/t/L_stack.fits',
    stackedSpectralTarget: '/t/SPEC_stack.fits',
    checkedFiles: new Set<string>(),
    selectedFile: null,
    filteredFilePaths: [],
};

/** A finished remake reply for the stack at the given path. */
const finished = (stackPath: string) => ({
    status: 'completed',
    job_id: 'j',
    result: { target_id: 'M 57', stack_path: stackPath, shown_in_viewer: true },
});

/** What the backend says stacking would do: two frames set aside and one group with its calibration. */
const prediction = {
    target_id: 'M 57',
    kind: 'imaging',
    camera: 'ZWO ASI 533MM Pro',
    frames_selected: 14,
    frames_kept: 12,
    set_aside: [
        { path: '/l/cloud.fits', file: 'cloud.fits', kind: 'clouded', reason: 'few stars under bright sky' },
        { path: '/l/trail.fits', file: 'trail.fits', kind: 'trailed', reason: 'stars trailed' },
    ],
    flagged: [],
    groups: [{
        exposure_seconds: 30, frames_kept: 12, first_file: 'a', last_file: 'b', filter_name: 'L',
        gain: '100', offset: '0', binning: '1x1',
        dark: { kind: 'dark', frame_count: 30, folders: ['/lib/darks/ZWO ASI 533MM Pro/0.0/30.0'], flags: [] },
        bias: { kind: 'bias', frame_count: 20, folders: ['/lib/biases/ZWO ASI 533MM Pro/0.0'], flags: [] },
        flat: { kind: 'flat', frame_count: 30, folders: ['/lib/flats/T/ZWO ASI 533MM Pro/L/0.0'], flags: [] },
        warnings: [],
    }],
    warnings: [],
    note: 'Nothing was stacked, moved, stored or saved.',
};

/** Draws the manager with spy callbacks. */
function setup(extra: Partial<React.ComponentProps<typeof ImageDetailsManager>> = {}) {
    const onProcess = vi.fn();
    const onShowPicture = vi.fn();
    render(
        <ImageDetailsManager
            selectedTarget="M 57"
            onProcess={onProcess}
            onShowPicture={onShowPicture}
            analysisSelectionInput={bothStacks}
            {...extra}
        />,
    );
    return { onProcess, onShowPicture };
}

/** Opens the Stack Frames menu. */
const openMenu = async (user: ReturnType<typeof userEvent.setup>) =>
    user.click(screen.getByRole('button', { name: 'Choose a stacking action' }));

describe('ImageDetailsManager Stack Frames split button', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
        failure.error = null;
    });

    /** The main part stacks, as before. */
    it('stacks on the main click', async () => {
        const user = userEvent.setup();
        const { onProcess } = setup();
        await user.click(screen.getByRole('button', { name: 'Stack Frames' }));
        expect(onProcess).toHaveBeenCalledTimes(1);
        expect(callBackendMock).not.toHaveBeenCalled();
    });

    /** The label and the main click keep today's rules for mixed optics. */
    it('keeps the optic warning label and turns the main click off', () => {
        setup({ hasMultipleOptics: true, selectedOptics: [200, 530] });
        const main = screen.getByRole('button', { name: /Pick one optic to stack/ }) as HTMLButtonElement;
        expect(main.disabled).toBe(true);
        expect(main.getAttribute('title')).toContain('200, 530 mm');
    });

    /** The menu has one entry, which remakes the imaging picture and then the spectral one. */
    it('remakes the picture of each stack in order and tells the viewer', async () => {
        const user = userEvent.setup();
        callBackendMock.mockImplementation(async (_action: string, params?: { kind: string }) =>
            finished(params?.kind === 'imaging' ? '/t/L_stack.fits' : '/t/SPEC_stack.fits'));
        const remade = vi.fn();
        const detach = onEvent(STACK_PICTURE_REMADE_EVENT, remade);
        const updated = vi.fn();
        const detachUpdated = onEvent('targetsUpdated', updated);
        const { onShowPicture, onProcess } = setup();
        await openMenu(user);
        expect(screen.getAllByRole('menuitem')).toHaveLength(2);
        await user.click(screen.getByRole('menuitem', { name: /Post Process Target/ }));
        await waitFor(() => expect(onShowPicture).toHaveBeenCalledTimes(2));
        const calls = callBackendMock.mock.calls.filter(([action]) => action === 'processing:remake_preview');
        expect(calls.map(([, params]) => params.kind)).toEqual(['imaging', 'spectral']);
        expect(calls[0][1]).toEqual({
            target_id: 'M 57', kind: 'imaging', denoise: undefined, denoise_strength: undefined, star_toning: undefined,
        });
        expect(onShowPicture.mock.calls.map(([path]) => path)).toEqual(['/t/L_stack.fits', '/t/SPEC_stack.fits']);
        expect(remade).toHaveBeenCalledTimes(2);
        expect(updated).toHaveBeenCalledTimes(2);
        expect(onProcess).not.toHaveBeenCalled();
        detach();
        detachUpdated();
    });

    /** With only a spectral stack, only that one is remade. */
    it('remakes only the stack that exists', async () => {
        const user = userEvent.setup();
        callBackendMock.mockResolvedValue(finished('/t/SPEC_stack.fits'));
        const { onShowPicture } = setup({ analysisSelectionInput: { ...bothStacks, stackedImage: null } });
        await openMenu(user);
        await user.click(screen.getByRole('menuitem', { name: /Post Process Target/ }));
        await waitFor(() => expect(onShowPicture).toHaveBeenCalledTimes(1));
        expect(callBackendMock).toHaveBeenCalledTimes(1);
        expect(callBackendMock.mock.calls[0][1].kind).toBe('spectral');
    });

    /** With no stack, the entry is off and says why. */
    it('turns the entry off with a reason when there is no stack', async () => {
        const user = userEvent.setup();
        setup({ analysisSelectionInput: { ...bothStacks, stackedImage: null, stackedSpectralTarget: null } });
        await openMenu(user);
        const entry = screen.getByRole('menuitem', { name: /Post Process Target/ });
        expect(entry.getAttribute('aria-disabled')).toBe('true');
        expect(entry.getAttribute('title')).toMatch(/no stack/);
        await user.click(entry);
        expect(callBackendMock).not.toHaveBeenCalled();
    });

    /** While a remake runs, the button shows busy and cannot be used; it returns when the job ends. */
    it('shows a busy button without Cancel while the picture is remade', async () => {
        const user = userEvent.setup();
        let finish: (value: unknown) => void = () => undefined;
        callBackendMock.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
        setup({ analysisSelectionInput: { ...bothStacks, stackedSpectralTarget: null }, onCancel: vi.fn() });
        await openMenu(user);
        await user.click(screen.getByRole('menuitem', { name: /Post Process Target/ }));
        const busy = await screen.findByRole('button', { name: /Post Processing/ });
        expect((busy as HTMLButtonElement).disabled).toBe(true);
        expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
        finish(finished('/t/L_stack.fits'));
        expect(await screen.findByText('Stack Frames')).toBeTruthy();
    });

    /** A failed remake shows its message and stops the queue. */
    it('shows the error of a failed remake and goes back to Stack Frames', async () => {
        const user = userEvent.setup();
        failure.error = new Error('The stack file is missing.');
        setup();
        await openMenu(user);
        await user.click(screen.getByRole('menuitem', { name: /Post Process Target/ }));
        expect((await screen.findByRole('alert')).textContent).toMatch(/The stack file is missing/);
        expect(await screen.findByText('Stack Frames')).toBeTruthy();
    });

    /** While stacking, Cancel replaces the split button. */
    it('shows Cancel while stacking', () => {
        setup({ isProcessing: true, onCancel: vi.fn() });
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Choose a stacking action' })).toBeNull();
    });

    /** Check Frames sends the same frames as the main click and shows what stacking would do. */
    it('checks the frames the Stack Frames click would send and shows the report', async () => {
        const user = userEvent.setup();
        callBackendMock.mockResolvedValue({ status: 'completed', job_id: 'j', result: prediction });
        const framesToStack = vi.fn(() => ['/lights/a_001.fits', '/lights/a_002.fits']);
        const { onProcess } = setup({ framesToStack });
        await openMenu(user);
        await user.click(screen.getByRole('menuitem', { name: /Check Frames/ }));
        const report = await screen.findByRole('region', { name: 'Frame check result' });
        expect(report.textContent).toMatch(/12 of 14 imaging frames would be stacked/);
        expect(report.textContent).toMatch(/2 set aside/);
        expect(report.textContent).toMatch(/darks\/ZWO ASI 533MM Pro\/0\.0\/30\.0/);
        expect(callBackendMock).toHaveBeenCalledWith('processing:check_stack', {
            target_id: 'M 57', image_files: ['/lights/a_001.fits', '/lights/a_002.fits'],
        }, { silent: true });
        expect(onProcess).not.toHaveBeenCalled();
    });

    /** With mixed cameras the whole split button is off, so Check Frames cannot start; the label says why. */
    it('cannot start Check Frames with mixed cameras', async () => {
        setup({ hasMultipleCameras: true, selectedCameras: ['A', 'B'] });
        const menuButton = screen.getByRole('button', { name: 'Choose a stacking action' }) as HTMLButtonElement;
        expect(menuButton.disabled).toBe(true);
        expect(screen.getByRole('button', { name: /requires matching cameras/ })).toBeTruthy();
        expect(callBackendMock).not.toHaveBeenCalled();
    });

    /** A failed check shows its message. */
    it('shows the error of a failed check', async () => {
        const user = userEvent.setup();
        failure.error = new Error('No imaging light frames match that selection.');
        setup({ framesToStack: () => ['/lights/a_001.fits'] });
        await openMenu(user);
        await user.click(screen.getByRole('menuitem', { name: /Check Frames/ }));
        expect((await screen.findByRole('alert')).textContent).toMatch(/No imaging light frames match/);
    });
});
