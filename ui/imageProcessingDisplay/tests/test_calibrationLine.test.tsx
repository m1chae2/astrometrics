/**
 * @file test_calibrationLine.test.tsx
 * @description Tests the Calibration line of the Data tab: the text built from the stacker's
 * per-group match, the warning style and the hover detail.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { buildCalibrationLine } from '../utils/calibrationLine';
import { CalibrationLine } from '../components/CalibrationLine';
import type { CalibrationGroupMatch, GroupedFrameStat } from '../../common/types/backendTypes';

const found = (count: number, fallbacks: string[] = []): CalibrationGroupMatch => ({ count, fallbacks, problem: '' });
const missing = (problem: string): CalibrationGroupMatch => ({ count: 0, fallbacks: [], problem });
const group = (
    filter: string, exposure: string, darkMatch: CalibrationGroupMatch,
    flatMatch: CalibrationGroupMatch, biasMatch: CalibrationGroupMatch,
): GroupedFrameStat => ({
    filter, iso: '100', offset: '10', binning: '1x1', exposure, count: 10, camera: 'ASI',
    darkMatch, flatMatch, biasMatch,
});

describe('buildCalibrationLine', () => {
    it('writes one line when everything matches', () => {
        const line = buildCalibrationLine([group('L', '120', found(70), found(30), found(20))]);
        expect(line?.text).toBe('L 120 s: 70 darks ✓ · 30 flats ✓ · 20 bias ✓');
        expect(line?.hasWarning).toBe(false);
    });

    it('names the failing rule when a bias is missing at the exact setting', () => {
        const line = buildCalibrationLine([group('L', '120', found(70), found(30),
            missing('no bias at gain 100, offset 10'))]);
        expect(line?.text).toContain('no bias at gain 100, offset 10 ✗');
        expect(line?.hasWarning).toBe(true);
    });

    it('marks a near match with a warning and keeps the reason in the detail', () => {
        const line = buildCalibrationLine([group('L', '120', found(70), found(30),
            found(20, ['bias frames are at another offset']))]);
        expect(line?.text).toContain('20 bias ⚠');
        expect(line?.hasWarning).toBe(true);
        expect(line?.detail).toContain('another offset');
    });

    it('lists each group when there are several', () => {
        const line = buildCalibrationLine([
            group('L', '120', found(70), found(5), found(2)),
            group('Ha', '300', missing('no darks'), found(5), found(2)),
        ]);
        expect(line?.text.split('  |  ')).toHaveLength(2);
        expect(line?.text).toContain('Ha 300 s: no darks ✗');
        expect(line?.detail.split('\n')).toHaveLength(2);
    });

    it('returns nothing without light frames', () => {
        expect(buildCalibrationLine([])).toBeNull();
    });
});

describe('CalibrationLine', () => {
    it('shows the line with the hover detail and the warning style', () => {
        render(<CalibrationLine lightFrames={[group('L', '120', missing('no darks'), found(1), found(1))]} />);
        const row = screen.getByTestId('calibration-line');
        expect(row.textContent).toContain('Calibration:');
        expect(row.getAttribute('title')).toContain('10 lights');
        expect(row.querySelector('.stack-summary__flag')).toBeTruthy();
    });

    it('shows a clean line without the warning style', () => {
        render(<CalibrationLine lightFrames={[group('L', '120', found(7), found(1), found(1))]} />);
        expect(screen.getByTestId('calibration-line').querySelector('.stack-summary__flag')).toBeNull();
    });
});
