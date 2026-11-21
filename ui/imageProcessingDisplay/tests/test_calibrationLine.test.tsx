/**
 * @file test_calibrationLine.test.tsx
 * @description Tests the Calibration block of the Data tab: the report built from the stacker's
 * per-group match, the complete view, the missing and check lists, and the all-groups toggle.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { buildCalibrationReport } from '../utils/calibrationLine';
import { CalibrationLine } from '../components/CalibrationLine';
import type { CalibrationGroupMatch, GroupedFrameStat } from '../../common/types/backendTypes';

const found = (count: number, fallbacks: string[] = []): CalibrationGroupMatch => ({ count, fallbacks, problem: '' });
const missing = (problem: string): CalibrationGroupMatch => ({ count: 0, fallbacks: [], problem });
const group = (
    filter: string, exposure: string, darkMatch: CalibrationGroupMatch,
    flatMatch: CalibrationGroupMatch, biasMatch: CalibrationGroupMatch,
    iso = '100', offset = '10',
): GroupedFrameStat => ({
    filter, iso, offset, binning: '1x1', exposure, count: 10, camera: 'ASI',
    darkMatch, flatMatch, biasMatch,
});

describe('buildCalibrationReport', () => {
    it('finds nothing missing when everything matches', () => {
        const report = buildCalibrationReport([group('L', '120', found(70), found(30), found(20))]);
        expect(report?.missing).toHaveLength(0);
        expect(report?.nearMatches).toHaveLength(0);
        expect(report?.groups[0].label).toBe('L 120 s');
        expect(report?.groups[0].settings).toBe('gain 100, offset 10, 1×1');
    });

    it('lists a group with a missing kind and keeps the reason', () => {
        const report = buildCalibrationReport([group('L', '20', missing('no dark frames at 20 s'), found(3), found(2))]);
        expect(report?.missing).toHaveLength(1);
        expect(report?.missing[0].parts[0].status).toBe('missing');
        expect(report?.missing[0].parts[0].reason).toContain('no dark frames');
    });

    it('lists a near match apart from the missing groups', () => {
        const report = buildCalibrationReport([
            group('L', '30', found(30, ['darks are at another temperature']), found(3), found(2)),
            group('L', '60', missing('no darks'), found(3), found(2)),
        ]);
        expect(report?.nearMatches.map((g) => g.label)).toEqual(['L 30 s']);
        expect(report?.missing.map((g) => g.label)).toEqual(['L 60 s']);
    });

    it('returns nothing without light frames', () => {
        expect(buildCalibrationReport([])).toBeNull();
    });
});

describe('CalibrationLine', () => {
    it('says complete when nothing is missing', () => {
        render(<CalibrationLine lightFrames={[
            group('L', '120', found(7), found(1), found(1)),
            group('L', '60', found(7), found(1), found(1)),
        ]} />);
        const block = screen.getByTestId('calibration-line');
        expect(block.textContent).toContain('Complete');
        expect(block.textContent).toContain('all 2 exposure groups');
        expect(screen.queryByTestId('calibration-missing')).toBeNull();
    });

    it('lists one row per group with missing frames and the count in the heading', () => {
        render(<CalibrationLine lightFrames={[
            group('L', '20', missing('no darks'), found(1), found(1), '0.0', '10.0'),
            group('', '120', missing('no darks'), missing('no flats'), found(1), '800', '0'),
            group('L', '60', found(7), found(1), found(1)),
        ]} />);
        const block = screen.getByTestId('calibration-line');
        expect(block.textContent).toContain('Missing calibration (2)');
        const rows = screen.getByTestId('calibration-missing').querySelectorAll('li');
        expect(rows).toHaveLength(2);
        expect(rows[0].textContent).toContain('No darks and flats for 120 s');
        expect(rows[1].textContent).toContain('No darks for L 20 s');
        expect(rows[1].textContent).toContain('gain 0, offset 10, 1×1');
    });

    it('shows a near match as a check item that is not counted as missing', () => {
        render(<CalibrationLine lightFrames={[
            group('L', '30', found(30, ['darks are at another temperature']), found(1), found(1)),
        ]} />);
        const block = screen.getByTestId('calibration-line');
        expect(block.textContent).toContain('Complete');
        const check = screen.getByTestId('calibration-check');
        expect(check.textContent).toContain('darks for L 30 s');
        expect(check.textContent).toContain('another temperature');
    });

    it('opens and closes the table of every group', () => {
        render(<CalibrationLine lightFrames={[
            group('L', '20', missing('no darks'), found(3), found(2)),
            group('L', '60', found(7), found(1), found(1)),
        ]} />);
        expect(screen.queryByTestId('calibration-all-groups')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Show all groups' }));
        const rows = screen.getByTestId('calibration-all-groups').querySelectorAll('tbody tr');
        expect(rows).toHaveLength(2);
        expect(rows[1].textContent).toContain('L 60 s');
        fireEvent.click(screen.getByRole('button', { name: 'Hide all groups' }));
        expect(screen.queryByTestId('calibration-all-groups')).toBeNull();
    });
});
