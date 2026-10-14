/**
 * @fileoverview Tests for choosing which files an Analyze click sends.
 */

import { describe, expect, it } from 'vitest';
import {
    AnalysisSelectionInput,
    chooseAnalysisSelection,
    chooseTemporalVariationSelection,
} from '../utils/analysisSelection';

const baseInput: AnalysisSelectionInput = {
    filterType: 'SPEC',
    stackedImage: null,
    stackedSpectralTarget: '/lights/Vega/Vega_SPEC_Stacked.fits',
    checkedFiles: new Set<string>(),
    selectedFile: null,
    filteredFilePaths: ['/lights/Vega/raw_001.fits', '/lights/Vega/raw_002.fits'],
};

describe('chooseAnalysisSelection', () => {
    it('analyzes the master spectral stack first, even when raw frames are ticked', () => {
        const selection = chooseAnalysisSelection({
            ...baseInput,
            checkedFiles: new Set(['/lights/Vega/raw_001.fits']),
        });
        expect(selection).toEqual({ paths: ['/lights/Vega/Vega_SPEC_Stacked.fits'], filterType: 'SPEC' });
    });

    it('uses ticked files when there is no master spectral stack', () => {
        const selection = chooseAnalysisSelection({
            ...baseInput,
            stackedSpectralTarget: null,
            checkedFiles: new Set(['/lights/Vega/raw_001.fits']),
        });
        expect(selection?.paths).toEqual(['/lights/Vega/raw_001.fits']);
    });

    it('does not prefer the spectral stack for a non-spectral filter', () => {
        const selection = chooseAnalysisSelection({
            ...baseInput,
            filterType: 'L',
            stackedImage: '/lights/Vega/Vega_L_Stacked.fits',
        });
        expect(selection?.paths).toEqual(['/lights/Vega/Vega_L_Stacked.fits']);
    });

    it('falls back to every file in view, and to nothing when the view is empty', () => {
        expect(chooseAnalysisSelection({ ...baseInput, stackedSpectralTarget: null })?.paths).toHaveLength(2);
        expect(
            chooseAnalysisSelection({ ...baseInput, stackedSpectralTarget: null, filteredFilePaths: [] })
        ).toBeNull();
    });
});

describe('chooseTemporalVariationSelection', () => {
    it('offers the raw frames once a master spectral stack exists', () => {
        expect(chooseTemporalVariationSelection(baseInput)).toEqual({
            paths: ['/lights/Vega/raw_001.fits', '/lights/Vega/raw_002.fits'],
            filterType: 'SPEC',
        });
    });

    it('prefers the ticked frames', () => {
        const selection = chooseTemporalVariationSelection({
            ...baseInput,
            checkedFiles: new Set(['/lights/Vega/raw_002.fits']),
        });
        expect(selection?.paths).toEqual(['/lights/Vega/raw_002.fits']);
    });

    it('is not offered without a master stack or for a non-spectral filter', () => {
        expect(chooseTemporalVariationSelection({ ...baseInput, stackedSpectralTarget: null })).toBeNull();
        expect(chooseTemporalVariationSelection({ ...baseInput, filterType: 'L' })).toBeNull();
    });
});
