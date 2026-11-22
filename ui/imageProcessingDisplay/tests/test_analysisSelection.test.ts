/**
 * @fileoverview Tests for choosing which files an Analyze click sends.
 */

import { describe, expect, it } from 'vitest';
import {
    AnalysisSelectionInput,
    NO_ASTROMETRY_SOURCE_REASON,
    NO_IMAGING_SOURCE_REASON,
    NO_SPECTRAL_SOURCE_REASON,
    chooseAnalysisSelection,
    chooseStageSelection,
    describeStageAvailability,
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
    it('analyzes the master spectral stack, even when raw frames are ticked or previewed', () => {
        const selection = chooseAnalysisSelection({
            ...baseInput,
            checkedFiles: new Set(['/lights/Vega/raw_001.fits']),
            selectedFile: '/lights/Vega/raw_002.fits',
            selectedFileFilter: 'SPEC',
        });
        expect(selection).toEqual({ paths: ['/lights/Vega/Vega_SPEC_Stacked.fits'], filterType: 'SPEC' });
    });

    it('uses the imaging stack over ticked and previewed files', () => {
        const selection = chooseAnalysisSelection({
            ...baseInput,
            filterType: 'L',
            stackedImage: '/lights/Vega/Vega_L_Stacked.fits',
            checkedFiles: new Set(['/lights/Vega/raw_001.fits']),
            selectedFile: '/lights/Vega/raw_002.fits',
            selectedFileFilter: 'L',
        });
        expect(selection).toEqual({ paths: ['/lights/Vega/Vega_L_Stacked.fits'], filterType: 'L' });
    });

    it('uses the selected file, not the ticked files, when there is no stack', () => {
        const selection = chooseAnalysisSelection({
            ...baseInput,
            filterType: 'L',
            stackedSpectralTarget: null,
            checkedFiles: new Set(['/lights/Vega/raw_001.fits']),
            selectedFile: '/lights/Vega/raw_002.fits',
            selectedFileFilter: 'L',
        });
        expect(selection?.paths).toEqual(['/lights/Vega/raw_002.fits']);
    });

    it('is off with no stack and no selected file, even when files are in view', () => {
        expect(chooseAnalysisSelection({ ...baseInput, filterType: 'L', stackedSpectralTarget: null })).toBeNull();
    });

    it('does not use a selected frame of the wrong kind', () => {
        const selection = chooseAnalysisSelection({
            ...baseInput,
            stackedSpectralTarget: null,
            selectedFile: '/lights/Vega/raw_001.fits',
            selectedFileFilter: 'L',
        });
        expect(selection).toBeNull();
    });
});

describe('describeStageAvailability', () => {
    const imagingStack = '/lights/Vega/Vega_L_Stacked.fits';

    it('turns everything on when both stacks exist', () => {
        const availability = describeStageAvailability({ ...baseInput, stackedImage: imagingStack });
        expect(Object.values(availability).every((stage) => stage.enabled)).toBe(true);
    });

    it('turns astrometry and photometry off with no imaging stack and no selected imaging frame', () => {
        const availability = describeStageAvailability(baseInput);
        expect(availability.astrometry).toEqual({ enabled: false, reason: NO_IMAGING_SOURCE_REASON });
        expect(availability.photometry.enabled).toBe(false);
    });

    it('turns spectroscopy off when astrometry cannot run, with its own reason', () => {
        const availability = describeStageAvailability(baseInput);
        expect(availability.spectroscopy).toEqual({ enabled: false, reason: NO_ASTROMETRY_SOURCE_REASON });
    });

    it('turns spectroscopy off with no spectral stack and no selected spectral frame', () => {
        const availability = describeStageAvailability({
            ...baseInput,
            stackedImage: imagingStack,
            stackedSpectralTarget: null,
        });
        expect(availability.spectroscopy).toEqual({ enabled: false, reason: NO_SPECTRAL_SOURCE_REASON });
        expect(availability.photometry.enabled).toBe(true);
    });

    it('accepts a selected frame when there is no stack of that kind', () => {
        const availability = describeStageAvailability({
            ...baseInput,
            stackedSpectralTarget: null,
            selectedFile: '/lights/Vega/raw_001.fits',
            selectedFileFilter: 'L',
        });
        expect(availability.astrometry.enabled).toBe(true);
        expect(availability.spectroscopy.enabled).toBe(false);
    });
});

describe('chooseStageSelection', () => {
    it('sends the stack of the needed kind for each stage', () => {
        const input = { ...baseInput, stackedImage: '/lights/Vega/Vega_L_Stacked.fits', filterType: 'L' };
        expect(chooseStageSelection(input, 'astrometry')).toEqual({
            paths: ['/lights/Vega/Vega_L_Stacked.fits'],
            filterType: 'L',
        });
        expect(chooseStageSelection(input, 'spectroscopy')).toEqual({
            paths: ['/lights/Vega/Vega_SPEC_Stacked.fits'],
            filterType: 'SPEC',
        });
    });

    it('returns null for a stage that is off', () => {
        expect(chooseStageSelection(baseInput, 'photometry')).toBeNull();
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
