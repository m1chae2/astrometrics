/**
 * @fileoverview Tests for the Astronomy Manager's text-formatting helpers.
 */

import { describe, expect, it } from 'vitest';
import {
    buildStarListSubtitle,
    formatCatalogMagnitude,
    formatCoordinateDegrees,
    formatStarListLabel,
    formatTimeSpan,
    selectLightCurveSeries,
} from '../utils/starDisplayFormat';

describe('formatStarListLabel', () => {
    it('keeps the last digits of a long catalog number so stars stay distinguishable', () => {
        expect(formatStarListLabel('Gaia DR3 1327617929278818176')).toBe('Gaia DR3 …818176');
        expect(formatStarListLabel('Gaia DR3 1327617929278818177')).not.toBe(
            formatStarListLabel('Gaia DR3 1327617929278818176')
        );
    });

    it('turns a position-only id into its sky position', () => {
        expect(formatStarListLabel('FIELD_J249.8348+35.5319')).toBe('249.83 +35.53');
        expect(formatStarListLabel('FIELD_J10.0007-39.5771')).toBe('10.00 -39.58');
    });

    it('leaves short and named stars alone', () => {
        expect(formatStarListLabel('HD 150679')).toBe('HD 150679');
        expect(formatStarListLabel('Vega')).toBe('Vega');
        expect(formatStarListLabel('TYC 3105-899-1')).toBe('TYC 3105-899-1');
    });
});

describe('formatCatalogMagnitude', () => {
    it('formats a catalog magnitude to two decimals', () => {
        expect(formatCatalogMagnitude(7.479027271270752)).toBe('7.48');
    });

    it('rejects a missing or instrumental magnitude', () => {
        expect(formatCatalogMagnitude(null)).toBeNull();
        expect(formatCatalogMagnitude('')).toBeNull();
        expect(formatCatalogMagnitude(-16.38)).toBeNull();
    });
});

describe('buildStarListSubtitle', () => {
    it('joins the magnitude and spectral type', () => {
        expect(buildStarListSubtitle({ magnitude: 7.48, spectralType: 'M2V' })).toBe('mag 7.48 · M2V');
    });

    it('says when a star has no catalog match', () => {
        expect(buildStarListSubtitle({ id: 'FIELD_J10.0007+39.5771', magnitude: -16.4 })).toBe('no catalog match');
    });

    it('leaves out what is unknown', () => {
        expect(buildStarListSubtitle({ magnitude: -16.4, spectralType: 'Unknown' })).toBe('');
        expect(buildStarListSubtitle({ magnitude: 9, spectralType: '' })).toBe('mag 9.00');
    });
});

describe('formatCoordinateDegrees', () => {
    it('rounds to five decimals by default', () => {
        expect(formatCoordinateDegrees(249.83482477201062)).toBe('249.83482');
        expect(formatCoordinateDegrees('35.53198327407618')).toBe('35.53198');
    });

    it('returns an empty string for a value that is not a number', () => {
        expect(formatCoordinateDegrees(undefined)).toBe('');
        expect(formatCoordinateDegrees('abc')).toBe('');
    });
});

describe('formatTimeSpan', () => {
    it('picks the largest sensible unit', () => {
        expect(formatTimeSpan('2026-05-24T04:46:00Z', '2026-05-24T05:04:00Z')).toBe('18 min');
        expect(formatTimeSpan('2026-05-24T00:00:00Z', '2026-05-24T05:00:00Z')).toBe('5.0 h');
        expect(formatTimeSpan('2026-05-20T00:00:00Z', '2026-05-24T00:00:00Z')).toBe('4.0 d');
    });
});

describe('selectLightCurveSeries', () => {
    const timestamps = ['2026-05-24T04:46:00Z', '2026-05-24T04:47:00Z'];

    it('prefers detrended flux over raw flux and labels it as a ratio', () => {
        const series = selectLightCurveSeries({
            timestamps,
            fluxes: [28000, 29000],
            fluxesNormalized: [17, 18],
            fluxesDetrended: [17.5, 17.6],
        });
        expect(series.values).toEqual([17.5, 17.6]);
        expect(series.axisTitle).toContain('Relative flux');
    });

    it('falls back to raw flux in ADU when a series does not match the timestamps', () => {
        const series = selectLightCurveSeries({
            timestamps,
            fluxes: [28000, 29000],
            fluxesDetrended: [17.5],
        });
        expect(series.values).toEqual([28000, 29000]);
        expect(series.axisTitle).toBe('Flux (ADU)');
    });

    it('marks magnitudes so the plot can put brighter at the top', () => {
        const series = selectLightCurveSeries({ timestamps, magnitudes: [10.1, 10.2], fluxes: [1, 2] });
        expect(series.isMagnitude).toBe(true);
    });

    it('returns no values when there is no photometry', () => {
        expect(selectLightCurveSeries(null).values).toEqual([]);
    });
});

describe('spectral type and verdict helpers', () => {
    it('reads a spectral type as a position on the O to M ladder', async () => {
        const { spectralLadderPosition } = await import('../utils/starDisplayFormat');
        expect(spectralLadderPosition('A0Va')).toBe(20);
        expect(spectralLadderPosition('K2')).toBe(52);
        expect(spectralLadderPosition('A5V+M3-4V')).toBe(25);
        expect(spectralLadderPosition('Unknown')).toBeNull();
        expect(spectralLadderPosition(undefined)).toBeNull();
    });

    it('describes a good template match without cautions', async () => {
        const { describeTemplateMatch } = await import('../utils/starDisplayFormat');
        const description = describeTemplateMatch(
            { selfDeterminedSpectralType: 'A5V', selfDeterminedSpectralTypeRms: 0.07 },
            'A2'
        );
        expect(description?.badgeText).toBe('Template match: A5V (7% off)');
        expect(description?.isPoor).toBe(false);
        expect(description?.differsFromCatalog).toBe(false);
    });

    it('flags a poor match and a match far from the catalog type', async () => {
        const { describeTemplateMatch } = await import('../utils/starDisplayFormat');
        const poor = describeTemplateMatch(
            { selfDeterminedSpectralType: 'G0V', selfDeterminedSpectralTypeRms: 0.19 },
            'A2'
        );
        expect(poor?.isPoor).toBe(true);
        expect(poor?.badgeText).toBe('No good template match');
        expect(poor?.differsFromCatalog).toBe(true);
    });

    it('describes nothing when no type was determined', async () => {
        const { describeTemplateMatch } = await import('../utils/starDisplayFormat');
        expect(describeTemplateMatch({ selfDeterminedSpectralType: 'Unknown' }, 'A2')).toBeNull();
        expect(describeTemplateMatch(null, 'A2')).toBeNull();
    });

    it('treats only detected and possible verdicts as significant', async () => {
        const { isSignificantVerdict, significantFeatures } = await import('../utils/starDisplayFormat');
        expect(isSignificantVerdict('detected')).toBe(true);
        expect(isSignificantVerdict('possible')).toBe(true);
        expect(isSignificantVerdict('not_detected')).toBe(false);
        expect(isSignificantVerdict('insufficient_data')).toBe(false);
        expect(isSignificantVerdict('')).toBe(false);
        const features = [
            { feature: 'H-alpha', wavelength_angstrom: 6563, verdict: 'detected' as const },
            { feature: 'H-beta', wavelength_angstrom: 4861, verdict: 'not_detected' as const },
            { feature: 'Na D', wavelength_angstrom: 5893, verdict: 'not_covered' as const },
        ];
        expect(significantFeatures(features).map((feature) => feature.feature)).toEqual(['H-alpha']);
    });

    it('formats a false-alarm probability', async () => {
        const { formatFalseAlarmProbability } = await import('../utils/starDisplayFormat');
        expect(formatFalseAlarmProbability(0.0005)).toBe('< 0.1%');
        expect(formatFalseAlarmProbability(0.036)).toBe('3.6%');
        expect(formatFalseAlarmProbability(0.47)).toBe('47%');
        expect(formatFalseAlarmProbability(undefined)).toBe('n/a');
    });
});


describe('shortFeatureName', () => {
    it('shortens every feature the backend tests', async () => {
        const { shortFeatureName } = await import('../utils/starDisplayFormat');
        expect(shortFeatureName('Hydrogen Balmer series (H-alpha)')).toBe('H-alpha');
        expect(shortFeatureName('Iron/titanium blend (G band)')).toBe('G band');
        expect(shortFeatureName('Calcium II H & K')).toBe('Ca H & K');
        expect(shortFeatureName('Magnesium b triplet')).toBe('Mg b');
        expect(shortFeatureName('Sodium D doublet')).toBe('Na D');
    });

    it('falls back to the parenthesized text, then the full name', async () => {
        const { shortFeatureName } = await import('../utils/starDisplayFormat');
        expect(shortFeatureName('Some new line (Foo-1)')).toBe('Foo-1');
        expect(shortFeatureName('Plain name')).toBe('Plain name');
    });
});


describe('formatPeriod', () => {
    it('uses minutes, hours or days depending on length', async () => {
        const { formatPeriod } = await import('../utils/starDisplayFormat');
        expect(formatPeriod(0.0031)).toBe('4.5 min');
        expect(formatPeriod(0.25)).toBe('6.0 h');
        expect(formatPeriod(2.345)).toBe('2.35 d');
        expect(formatPeriod(0)).toBe('n/a');
        expect(formatPeriod(undefined)).toBe('n/a');
    });
});
