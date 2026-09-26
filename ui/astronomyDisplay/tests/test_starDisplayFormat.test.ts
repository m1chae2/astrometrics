/**
 * @fileoverview Tests for the Astronomy Manager's text-formatting helpers.
 */

import { describe, expect, it } from 'vitest';
import {
    buildStarListSubtitle,
    describeEmissionLineVerdict,
    emissionLineMarks,
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

    it('treats exactly zero as unknown, not as a very bright star', () => {
        expect(formatCatalogMagnitude(0)).toBeNull();
        expect(formatCatalogMagnitude('0')).toBeNull();
        expect(formatCatalogMagnitude(0.03)).toBe('0.03'); // Vega
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
        expect(description?.badgeText).toBe('Spectrum: A5V (7% off)');
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
        expect(poor?.badgeText).toBe('Spectrum: no good match');
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

    it('picks out inconclusive features, which are not counted as significant', async () => {
        const { inconclusiveFeatures, isSignificantVerdict } = await import('../utils/starDisplayFormat');
        expect(isSignificantVerdict('inconclusive')).toBe(false);
        const features = [
            { feature: 'H-alpha', wavelength_angstrom: 6563, verdict: 'inconclusive' as const },
            { feature: 'H-beta', wavelength_angstrom: 4861, verdict: 'detected' as const },
            { feature: 'Na D', wavelength_angstrom: 5893, verdict: 'not_detected' as const },
        ];
        expect(inconclusiveFeatures(features).map((feature) => feature.feature)).toEqual(['H-alpha']);
        expect(inconclusiveFeatures(null)).toEqual([]);
    });

    it('measures a span from the earliest and latest times, not the first and last entries', async () => {
        const { formatTimestampsSpan } = await import('../utils/starDisplayFormat');
        const inOrder = ['2026-05-24T05:50:00Z', '2026-05-24T06:00:00Z', '2026-05-24T06:12:00Z'];
        expect(formatTimestampsSpan(inOrder)).toBe('22 min');
        // A stored light curve can be out of order: here the last entry is earlier than the first.
        const outOfOrder = ['2026-05-24T06:12:00Z', '2026-05-24T05:50:00Z', '2026-05-24T06:10:00Z'];
        expect(formatTimestampsSpan(outOfOrder)).toBe('22 min');
        expect(formatTimestampsSpan(['2026-05-24T06:12:00Z'])).toBe('');
        expect(formatTimestampsSpan([])).toBe('');
        expect(formatTimestampsSpan(['not a date', '2026-05-24T06:12:00Z'])).toBe('');
        expect(formatTimestampsSpan(['2026-05-24T00:00:00Z', '2026-05-27T12:00:00Z'])).toBe('3.5 d');
    });

    it('describes each feature verdict in plain words', async () => {
        const { describeFeatureVerdict } = await import('../utils/starDisplayFormat');
        const verdicts = ['detected', 'possible', 'inconclusive', 'not_detected', 'not_covered'];
        const labels = verdicts.map((verdict) => describeFeatureVerdict(verdict).label);
        expect(labels).toEqual(['Detected', 'Possible', 'Unclear', 'No dip', 'No data']);
        expect(new Set(labels).size).toBe(verdicts.length);
        for (const verdict of verdicts) {
            expect(describeFeatureVerdict(verdict).explanation.length).toBeGreaterThan(20);
        }
        expect(describeFeatureVerdict('surprise')).toEqual({ label: 'surprise', explanation: '' });
        expect(describeFeatureVerdict(null)).toEqual({ label: '', explanation: '' });
    });

    it('describes a search that found no pattern, with the details for a tooltip', async () => {
        const { describeMissingPattern } = await import('../utils/starDisplayFormat');
        const found = describeMissingPattern(
            {
                verdict: 'not_detected',
                falseAlarmProbability: 0.056,
                searchedMinPeriodDays: 0.002,
                searchedMaxPeriodDays: 0.4,
            },
            'peak'
        );
        expect(found.value).toBe('None found');
        expect(found.explanation).toBe(
            'The strongest peak would appear by chance 5.6% of the time. Searched periods from 3 min to 576 min.'
        );

        const tooLittle = describeMissingPattern({ verdict: 'insufficient_data', note: 'Only 3 measurements.' }, 'peak');
        expect(tooLittle).toEqual({ value: 'Not enough data', explanation: 'Only 3 measurements.' });
        expect(describeMissingPattern({ verdict: 'insufficient_data' }, 'peak').explanation).toBe('');

        const withNote = describeMissingPattern(
            { verdict: 'not_detected', falseAlarmProbability: 0.5, note: 'Only one dip was seen.' },
            'repeating dip'
        );
        expect(withNote.explanation).toBe(
            'The strongest repeating dip would appear by chance 50% of the time. Only one dip was seen.'
        );
    });

    it('shows one chip when no type is known, and two only when each says something', async () => {
        const { describeStarTypeBadges } = await import('../utils/starDisplayFormat');
        const goodMatch = { selfDeterminedSpectralType: 'B1V', selfDeterminedSpectralTypeRms: 0.07 } as never;
        const poorMatch = { selfDeterminedSpectralType: 'B1V', selfDeterminedSpectralTypeRms: 0.23 } as never;

        // Neither the catalog nor the spectrum names a type: one grey chip.
        const unknown = describeStarTypeBadges('Unknown', poorMatch);
        expect(unknown.map((badge) => badge.text)).toEqual(['Type: unknown']);
        expect(unknown[0].tone).toBe('neutral');
        expect(describeStarTypeBadges('', null).map((badge) => badge.text)).toEqual(['Type: unknown']);

        // Only the spectrum names a type.
        expect(describeStarTypeBadges('Unknown', goodMatch).map((badge) => badge.text)).toEqual([
            'Spectrum: B1V (7% off)',
        ]);

        // Only the catalog names a type, and the spectrum could not confirm it: both, the second as a caution.
        const unconfirmed = describeStarTypeBadges('K2', poorMatch);
        expect(unconfirmed.map((badge) => badge.text)).toEqual(['Catalog: K2', 'Spectrum: no good match']);
        expect(unconfirmed[1].tone).toBe('caution');

        // Only the catalog has a type and there is no spectrum: one chip.
        expect(describeStarTypeBadges('K2', null).map((badge) => badge.text)).toEqual(['Catalog: K2']);

        // Both name a type, and they disagree: the measured one is a caution.
        const disagreeing = describeStarTypeBadges('K2', goodMatch);
        expect(disagreeing.map((badge) => badge.text)).toEqual([
            'Catalog: K2',
            'Spectrum: B1V (7% off) · differs from catalog',
        ]);
        expect(disagreeing[1].tone).toBe('caution');

        // Both agree.
        const agreeing = describeStarTypeBadges('B2', goodMatch);
        expect(agreeing[1].tone).toBe('measured');
    });

    it('shows a pattern chip only for a detected or possible pattern', async () => {
        const { describePatternBadges } = await import('../utils/starDisplayFormat');
        expect(describePatternBadges(null)).toEqual([]);
        expect(
            describePatternBadges({
                coefficientOfVariation: 0.19,
                periodogram: { verdict: 'not_detected', bestPeriodDays: 0.1, falseAlarmProbability: 0.5 },
                transitCandidate: { verdict: 'insufficient_data' },
            } as never)
        ).toEqual([]);

        const badges = describePatternBadges({
            coefficientOfVariation: 0.0189,
            periodogram: { verdict: 'detected', bestPeriodDays: 0.125, falseAlarmProbability: 0.002 },
            transitCandidate: { verdict: 'possible', periodDays: 2.5, falseAlarmProbability: 0.03 },
        } as never);
        expect(badges.map((badge) => badge.text)).toEqual(['Cycle: 3.0 h', 'Possible repeating dips: 2.50 d']);
        expect(badges[0].tone).toBe('pattern');
        expect(badges[0].title).toContain('0.2%');
        expect(badges[0].title).toContain('1.9%');
    });

    it('detects whether any feature was compared with a reference spectrum', async () => {
        const { hasReferenceExpectations } = await import('../utils/starDisplayFormat');
        const withoutReference = [
            { feature: 'H-beta', wavelength_angstrom: 4861, verdict: 'detected' as const },
            { feature: 'Mg b', wavelength_angstrom: 5175, verdict: 'inconclusive' as const, expected_depth: null },
        ];
        expect(hasReferenceExpectations(withoutReference)).toBe(false);
        expect(hasReferenceExpectations([])).toBe(false);
        expect(hasReferenceExpectations(null)).toBe(false);
        expect(
            hasReferenceExpectations([...withoutReference, { ...withoutReference[0], expected_depth: 0.12 }])
        ).toBe(true);
        expect(
            hasReferenceExpectations([{ ...withoutReference[0], probability_present: 0.7 }])
        ).toBe(true);
    });

    it('folds away features with nothing found, and hides their depth', async () => {
        const { isDepthMeaningful, splitFeaturesForTable } = await import('../utils/starDisplayFormat');
        expect(isDepthMeaningful('detected')).toBe(true);
        expect(isDepthMeaningful('possible')).toBe(true);
        expect(isDepthMeaningful('inconclusive')).toBe(true);
        expect(isDepthMeaningful('not_detected')).toBe(false);
        expect(isDepthMeaningful('not_covered')).toBe(false);
        expect(isDepthMeaningful(undefined)).toBe(false);

        const features = [
            { feature: 'H-beta', wavelength_angstrom: 4861, verdict: 'detected' as const },
            { feature: 'Mg b', wavelength_angstrom: 5175, verdict: 'inconclusive' as const },
            { feature: 'H-alpha', wavelength_angstrom: 6563, verdict: 'not_detected' as const },
            { feature: 'G band', wavelength_angstrom: 4300, verdict: 'not_detected' as const },
            { feature: 'Na D', wavelength_angstrom: 5893, verdict: 'not_covered' as const },
        ];
        const split = splitFeaturesForTable(features);
        expect(split.shown.map((feature) => feature.feature)).toEqual(['H-beta', 'Mg b']);
        expect(split.hidden.map((feature) => feature.feature)).toEqual(['H-alpha', 'G band', 'Na D']);
        expect(split.hiddenSummary).toBe('2 tested, nothing found, 1 outside the spectrum');
        expect(splitFeaturesForTable(null)).toEqual({ shown: [], hidden: [], hiddenSummary: '' });
        expect(splitFeaturesForTable(features.slice(0, 2)).hiddenSummary).toBe('');
    });

    it('keeps labels on one row when they are far apart and stacks close ones', async () => {
        const { assignLabelRows } = await import('../utils/starDisplayFormat');
        // 4600 A across 700 px is about 0.15 px per Angstrom; a 7-character label at 7 px is 49 px wide.
        const far = [
            { x: 4000, text: 'H-delta' },
            { x: 5000, text: 'H-beta' },
            { x: 6000, text: 'Na D' },
        ];
        expect(assignLabelRows(far, 4600, 700, 7, 6)).toEqual([0, 0, 0]);

        // Ca H & K (3965) and H-delta (4117) are 152 A = 23 px apart: the second must move up.
        const close = [
            { x: 3965, text: 'Ca H & K' },
            { x: 4117, text: 'H-delta' },
        ];
        expect(assignLabelRows(close, 4600, 700, 7, 6)).toEqual([0, 1]);
    });

    it('reuses the lower row once a label is clear of the one before it', async () => {
        const { assignLabelRows } = await import('../utils/starDisplayFormat');
        const labels = [
            { x: 4000, text: 'Ca H & K' },
            { x: 4100, text: 'H-delta' },
            { x: 5000, text: 'H-beta' },
        ];
        expect(assignLabelRows(labels, 4600, 700, 7, 6)).toEqual([0, 1, 0]);
    });

    it('returns rows in the order the labels were given, however they are sorted on the axis', async () => {
        const { assignLabelRows } = await import('../utils/starDisplayFormat');
        const labels = [
            { x: 4117, text: 'H-delta' },
            { x: 3965, text: 'Ca H & K' },
        ];
        // The left-most label (index 1) takes row 0.
        expect(assignLabelRows(labels, 4600, 700, 7, 6)).toEqual([1, 0]);
        expect(assignLabelRows([], 4600, 700, 7, 6)).toEqual([]);
        // With no known axis span every label is treated as overlapping, so they are all stacked.
        expect(assignLabelRows(labels, 0, 700, 7, 6)).toEqual([1, 0]);
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

describe('emissionLineMarks', () => {
    const line = (verdict: string, wavelengths: number[]) =>
        ({
            line: 'blend',
            members: [],
            rest_wavelengths_angstrom: wavelengths,
            is_blend: true,
            second_order_ghost_angstrom: 0,
            verdict,
            significance: 6,
        }) as never;

    it('marks only detected and unclear lines, at the mean rest wavelength', () => {
        const marks = emissionLineMarks([line('detected', [4959, 5007]), line('unclear', [6563]), line('not_seen', [7136])]);
        expect(marks.map((mark) => mark.verdict)).toEqual(['detected', 'possible']);
        expect(marks[0].wavelength_angstrom).toBeCloseTo(4983);
    });

    it('copes with no lines', () => {
        expect(emissionLineMarks(undefined)).toEqual([]);
    });
});

describe('describeEmissionLineVerdict', () => {
    it('labels each verdict', () => {
        expect(describeEmissionLineVerdict('detected').label).toBe('Detected');
        expect(describeEmissionLineVerdict('not_seen').label).toBe('Not seen');
        expect(describeEmissionLineVerdict('odd').label).toBe('odd');
    });
});
