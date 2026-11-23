/**
 * @file test_calibrationLine.test.tsx
 * @description Tests the Calibration block of the Data tab: the report built from the stacker's
 * per-group match, the complete and warning chips, the missing list, and the all-groups table shown
 * when the chip is hovered or focused.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { buildCalibrationReport, chooseCalibrationScope, filterGroupsByScope } from '../utils/calibrationLine';
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
        expect(block.querySelector('[data-status="ok"]')?.textContent).toBe('Complete');
        expect(block.textContent).not.toContain('exposure groups');
        expect(screen.queryByTestId('calibration-check')).toBeNull();
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

    it('turns the chip into a warning when a near match remains, with no Check list', () => {
        render(<CalibrationLine lightFrames={[
            group('L', '30', found(30, ['darks are at another temperature']), found(1), found(1)),
            group('L', '60', found(7), found(1), found(1)),
        ]} />);
        const block = screen.getByTestId('calibration-line');
        const chip = block.querySelector('.calibration-report__heading [data-status]') as HTMLElement;
        expect(chip.getAttribute('data-status')).toBe('warning');
        expect(chip.textContent).toBe('Complete, 1 to check');
        expect(screen.queryByTestId('calibration-check')).toBeNull();
        expect(block.textContent).not.toContain('Check');
    });

    it('has no Show all groups button and no table until the chip is hovered', () => {
        render(<CalibrationLine lightFrames={[group('L', '60', found(7), found(1), found(1))]} />);
        expect(screen.queryByRole('button', { name: /all groups/i })).toBeNull();
        expect(screen.queryByTestId('calibration-all-groups')).toBeNull();
    });

    it('shows the table of every group while the chip is hovered and hides it again', () => {
        render(<CalibrationLine lightFrames={[
            group('L', '20', missing('no darks'), found(3), found(2)),
            group('L', '60', found(7), found(1), found(1)),
        ]} />);
        const trigger = screen.getByTestId('calibration-line').querySelector('[tabindex="0"]') as HTMLElement;
        fireEvent.mouseEnter(trigger);
        const rows = screen.getByTestId('calibration-all-groups').querySelectorAll('tbody tr');
        expect(rows).toHaveLength(2);
        expect(rows[1].textContent).toContain('L 60 s');
        fireEvent.mouseLeave(trigger);
        expect(screen.queryByTestId('calibration-all-groups')).toBeNull();
    });

    it('shows the table on keyboard focus, keeps the near-match sentence in its cell and closes on Escape', () => {
        const sentence = 'No flat frames matched gain 0 and offset 0, so flat frames taken at gain 0.0, offset 10 '
            + 'were applied to the light frames.';
        render(<CalibrationLine lightFrames={[group('L', '30', found(1), found(5, [sentence]), found(1))]} />);
        const trigger = screen.getByTestId('calibration-line').querySelector('[tabindex="0"]') as HTMLElement;
        fireEvent.focus(trigger);
        const table = screen.getByTestId('calibration-all-groups');
        expect(table.querySelector(`[title="${sentence}"]`)).not.toBeNull();
        fireEvent.keyDown(trigger, { key: 'Escape' });
        expect(screen.queryByTestId('calibration-all-groups')).toBeNull();
    });
});

describe('calibration scope (M 31-like target with two cameras)', () => {
    const asi = (exposure: string, darks: CalibrationGroupMatch): GroupedFrameStat => ({
        ...group('L', exposure, darks, found(3), found(2)), camera: 'ZWO ASI 533MM Pro', focalLengthMm: 405,
    });
    const nikon = (exposure: string): GroupedFrameStat => ({
        ...group('', exposure, missing('no darks'), found(3), found(2)), camera: 'Nikon D5300', focalLengthMm: 300,
    });
    const groups = [asi('20', missing('no darks')), asi('120', found(5)), nikon('10'), nikon('20')];

    it('follows the stack configuration and leaves other cameras out', () => {
        const scope = chooseCalibrationScope('All', '', { camera: 'ZWO ASI 533MM Pro', focalLengthMm: 405 });
        expect(scope?.label).toBe('ZWO ASI 533MM Pro@405mm');
        render(<CalibrationLine lightFrames={groups} scope={scope} />);
        expect(screen.getByTestId('calibration-scope').textContent).toBe('ZWO ASI 533MM Pro@405mm');
        expect(screen.getByTestId('calibration-line').textContent).toContain('Missing calibration (1)');
        const rows = screen.getByTestId('calibration-missing').querySelectorAll('li');
        expect(rows).toHaveLength(1);
        expect(rows[0].textContent).toContain('L 20 s');
        fireEvent.mouseEnter(screen.getByTestId('calibration-line').querySelector('[tabindex="0"]') as HTMLElement);
        expect(screen.getByTestId('calibration-all-groups').querySelectorAll('tbody tr')).toHaveLength(2);
    });

    it('follows the File Browser when it is filtered to a camera', () => {
        const scope = chooseCalibrationScope('Nikon D5300', '300', { camera: 'ZWO ASI 533MM Pro', focalLengthMm: 405 });
        expect(filterGroupsByScope(groups, scope).map((g) => g.camera)).toEqual(['Nikon D5300', 'Nikon D5300']);
    });

    it('shows every group when nothing narrows the scope', () => {
        expect(chooseCalibrationScope('All', '', null)).toBeNull();
        expect(filterGroupsByScope(groups, null)).toHaveLength(4);
    });
});
