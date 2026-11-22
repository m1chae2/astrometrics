/**
 * @file test_imageDetailsManagerAnalyze.test.tsx
 * @description Tests the Analyze Target split button in ImageDetailsManager: the main click, the stage
 * menu, the stage it reports, the reasons stages are off, and Cancel taking its place while a job runs.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';
import { ImageDetailsManager } from '../imageDetailsManager/ImageDetailsManager';
import type { AnalysisSelectionInput } from '../utils/analysisSelection';

const bothStacks: AnalysisSelectionInput = {
    filterType: 'L',
    stackedImage: '/t/L_stack.fits',
    stackedSpectralTarget: '/t/SPEC_stack.fits',
    checkedFiles: new Set<string>(),
    selectedFile: null,
    filteredFilePaths: [],
};

/**
 * Draws the manager with spy callbacks.
 * @param extra Props to override.
 * @return The spies.
 */
function setup(extra: Partial<React.ComponentProps<typeof ImageDetailsManager>> = {}) {
    const onAnalyze = vi.fn();
    const onAnalyzeStage = vi.fn();
    render(
        <ImageDetailsManager
            selectedTarget="M 57"
            onAnalyze={onAnalyze}
            onAnalyzeStage={onAnalyzeStage}
            analysisSelectionInput={bothStacks}
            {...extra}
        />,
    );
    return { onAnalyze, onAnalyzeStage };
}

describe('ImageDetailsManager Analyze Target split button', () => {
    /** The main part keeps today's action. */
    it('runs the main action on a click', async () => {
        const user = userEvent.setup();
        const { onAnalyze, onAnalyzeStage } = setup();
        await user.click(screen.getByRole('button', { name: 'Analyze Target' }));
        expect(onAnalyze).toHaveBeenCalledTimes(1);
        expect(onAnalyzeStage).not.toHaveBeenCalled();
    });

    /** The menu lists the four stages and reports the picked one. */
    it('reports the picked stage', async () => {
        const user = userEvent.setup();
        const { onAnalyze, onAnalyzeStage } = setup();
        await user.click(screen.getByRole('button', { name: 'Choose an analysis stage' }));
        expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
            expect.stringContaining('Astrometry'),
            expect.stringContaining('Photometry'),
            expect.stringContaining('Spectroscopy'),
            expect.stringContaining('Spectral Variation'),
        ]);
        for (const item of screen.getAllByRole('menuitem')) {
            expect(item.querySelector('.split-button__item-detail')).toBeNull();
        }
        await user.click(screen.getByRole('menuitem', { name: /Photometry/ }));
        expect(onAnalyzeStage).toHaveBeenCalledWith('photometry');
        expect(onAnalyze).not.toHaveBeenCalled();
    });

    /** With no imaging stack and no selected frame, imaging stages are off and say why. */
    it('turns off astrometry and photometry without an imaging source', async () => {
        const user = userEvent.setup();
        const { onAnalyzeStage } = setup({ analysisSelectionInput: { ...bothStacks, stackedImage: null } });
        await user.click(screen.getByRole('button', { name: 'Choose an analysis stage' }));
        const astrometry = screen.getByRole('menuitem', { name: /Astrometry/ });
        expect(astrometry.getAttribute('aria-disabled')).toBe('true');
        expect(astrometry.getAttribute('title')).toBe('Stack the luminance frames or select a frame first');
        expect(screen.getByRole('menuitem', { name: /Spectroscopy/ }).getAttribute('aria-disabled')).toBe('true');
        await user.click(astrometry);
        expect(onAnalyzeStage).not.toHaveBeenCalled();
    });

    /** Spectroscopy is off with no spectral source, while the imaging stages stay on. */
    it('turns off spectroscopy without a spectral source', async () => {
        const user = userEvent.setup();
        setup({ analysisSelectionInput: { ...bothStacks, stackedSpectralTarget: null } });
        await user.click(screen.getByRole('button', { name: 'Choose an analysis stage' }));
        const spectroscopy = screen.getByRole('menuitem', { name: /Spectroscopy/ });
        expect(spectroscopy.getAttribute('aria-disabled')).toBe('true');
        expect(spectroscopy.getAttribute('title')).toContain('spectral');
        expect(screen.getByRole('menuitem', { name: /Photometry/ }).getAttribute('aria-disabled')).toBeNull();
    });

    /** Spectral Variation is on when raw spectral frames are in view, off with a reason otherwise. */
    it('turns Spectral Variation on only with raw spectral frames in view', async () => {
        const user = userEvent.setup();
        const spectralView = { ...bothStacks, filterType: 'SPEC', filteredFilePaths: ['/raw_001.fits', '/raw_002.fits'] };
        const { onAnalyzeStage } = setup({ analysisSelectionInput: spectralView });
        await user.click(screen.getByRole('button', { name: 'Choose an analysis stage' }));
        await user.click(screen.getByRole('menuitem', { name: /Spectral Variation/ }));
        expect(onAnalyzeStage).toHaveBeenCalledWith('spectral_variation');
    });

    /** Without raw spectral frames in view, Spectral Variation is off and says why. */
    it('turns Spectral Variation off without raw spectral frames', async () => {
        const user = userEvent.setup();
        setup();
        await user.click(screen.getByRole('button', { name: 'Choose an analysis stage' }));
        const variation = screen.getByRole('menuitem', { name: /Spectral Variation/ });
        expect(variation.getAttribute('aria-disabled')).toBe('true');
        expect(variation.getAttribute('title')).toContain('raw spectral frames');
    });

    /** A reason from the panel turns the main click off and becomes its tooltip. */
    it('turns the main click off with a reason', () => {
        setup({ analyzeDisabledReason: 'Select a file first' });
        const main = screen.getByRole('button', { name: 'Analyze Target' }) as HTMLButtonElement;
        expect(main.disabled).toBe(true);
        expect(main.getAttribute('title')).toBe('Select a file first');
    });

    /** While a job runs, Cancel replaces the split button in its place; when the job ends the split button returns. */
    it('swaps the split button for Cancel while analyzing', () => {
        const onCancel = vi.fn();
        const { rerender } = render(
            <ImageDetailsManager selectedTarget="M 57" onAnalyze={vi.fn()} onAnalyzeStage={vi.fn()}
                analysisSelectionInput={bothStacks} isAnalyzing onCancel={onCancel} />,
        );
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Analyze Target/ })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Choose an analysis stage' })).toBeNull();
        rerender(
            <ImageDetailsManager selectedTarget="M 57" onAnalyze={vi.fn()} onAnalyzeStage={vi.fn()}
                analysisSelectionInput={bothStacks} isAnalyzing={false} onCancel={onCancel} />,
        );
        expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
        expect(screen.getByRole('button', { name: 'Analyze Target' })).toBeTruthy();
    });

    /** The split button spans the panel like its siblings. */
    it('fills the panel width', () => {
        setup();
        expect(screen.getByRole('button', { name: 'Analyze Target' }).closest('.split-button')?.classList.contains('split-button--fill')).toBe(true);
    });
});
