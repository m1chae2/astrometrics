/**
 * @file test_cancelButton.test.tsx
 * @description The red Cancel button calls the cancel of the job that runs: the analysis cancel while
 * analyzing, the stacking cancel while stacking.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ImageDetailsManager } from '../imageDetailsManager/ImageDetailsManager';

const stacks = {
    filterType: 'L',
    stackedImage: '/t/L_stack.fits',
    stackedSpectralTarget: null,
    checkedFiles: new Set<string>(),
    selectedFile: null,
    filteredFilePaths: [],
};

describe('the Cancel button', () => {
    /** While analyzing, Cancel stops the analysis, not the stack. */
    it('calls the analysis cancel while analyzing', async () => {
        const onCancel = vi.fn();
        const onCancelAnalysis = vi.fn();
        render(<ImageDetailsManager selectedTarget="M 52" analysisSelectionInput={stacks} isAnalyzing
            onCancel={onCancel} onCancelAnalysis={onCancelAnalysis} />);
        await userEvent.setup().click(screen.getByRole('button', { name: 'Cancel' }));
        expect(onCancelAnalysis).toHaveBeenCalledTimes(1);
        expect(onCancel).not.toHaveBeenCalled();
    });

    /** While stacking, Cancel stops the stack. */
    it('calls the stacking cancel while stacking', async () => {
        const onCancel = vi.fn();
        const onCancelAnalysis = vi.fn();
        render(<ImageDetailsManager selectedTarget="M 52" analysisSelectionInput={stacks} isProcessing
            onCancel={onCancel} onCancelAnalysis={onCancelAnalysis} />);
        await userEvent.setup().click(screen.getByRole('button', { name: 'Cancel' }));
        expect(onCancel).toHaveBeenCalledTimes(1);
        expect(onCancelAnalysis).not.toHaveBeenCalled();
    });
});
