/**
 * @file test_stackCheckReport.test.tsx
 * @description Tests the Check Frames report: the counts, the list of set-aside and flagged frames
 * with their reasons, the calibration table with its flags, the message for a stack with no frame
 * left, and the helpers that shorten a calibration folder and name a reason.
 */
import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { StackCheckReport, NOTHING_LEFT_MESSAGE } from '../components/StackCheckReport';
import { calibrationFit, shortCalibrationFolder, summarizeSetAside } from '../utils/stackCheck';
import type { PredictedCalibration, StackPrediction } from '../../common/types/backendTypes';

/** Builds one calibration choice with the given count and flags. */
const part = (kind: string, frame_count: number, flags: string[] = [], blocking_flags: string[] = []): PredictedCalibration =>
    ({ kind, frame_count, folders: frame_count ? [`/lib/${kind}s/Camera/0.0`] : [], flags, blocking_flags });

/** A prediction with one group whose flats are only a near match. */
const prediction: StackPrediction = {
    target_id: 'M 52',
    kind: 'imaging',
    camera: 'ZWO ASI 533MM Pro',
    frames_selected: 52,
    frames_kept: 44,
    set_aside: [
        { path: '/l/001.fits', file: '001.fits', kind: 'off_field', reason: 'Other field (8 frames)' },
        { path: '/l/003.fits', file: '003.fits', kind: 'trailed', reason: 'Stars trailed: elongation 1.38, limit 1.32' },
    ],
    flagged: [{ path: '/l/044.fits', file: '044.fits', kind: 'uneven_sky', reason: 'Uneven sky: 0.024, limit 0.010' }],
    groups: [{
        exposure_s: 120, frames_kept: 44, first_file: '009.fits', last_file: '052.fits', filter_name: 'L',
        gain: '0', offset: '10', binning: '1x1',
        dark: part('dark', 30),
        bias: part('bias', 20),
        flat: part('flat', 30, ['Flats at another offset were used.']),
        warnings: ['flat: Flats at another offset were used.'],
    }],
    warnings: ['2 of 52 frames have an uneven sky.'],
    gates: [],
    unreadable: [],
    nothing_left: false,
    note: 'Nothing was stacked, moved, stored or saved.',
};

describe('StackCheckReport', () => {
    /** The counts say how many frames would be stacked, set aside and kept with a warning. */
    it('shows the counts', () => {
        render(<StackCheckReport prediction={prediction} />);
        const counts = screen.getByRole('status');
        expect(counts.textContent).toMatch(/44 of 52 imaging frames would be stacked/);
        expect(counts.textContent).toMatch(/2 set aside/);
        expect(counts.textContent).toMatch(/1 kept with a warning/);
    });

    /** Each set-aside and flagged frame is listed with its reason. */
    it('lists the set-aside and flagged frames with their reasons', () => {
        render(<StackCheckReport prediction={prediction} />);
        const setAside = screen.getByRole('list', { name: 'Frames stacking would set aside', hidden: true });
        expect(within(setAside).getAllByRole('listitem', { hidden: true })).toHaveLength(2);
        expect(setAside.textContent).toMatch(/Trailed stars: 003\.fits.*elongation 1\.38, limit 1\.32/);
        const flagged = screen.getByRole('list', { name: 'Frames stacking would keep with a warning', hidden: true });
        expect(flagged.textContent).toMatch(/Uneven sky, kept: 044\.fits/);
    });

    /** The table names the folders the library chose, and a near match carries its flag. */
    it('shows the calibration of each group and its flags', () => {
        render(<StackCheckReport prediction={prediction} />);
        const table = screen.getByRole('table', { name: 'Calibration frames for each exposure group' });
        expect(table.textContent).toMatch(/120 s, 44 frames, L/);
        expect(table.textContent).toMatch(/darks\/Camera\/0\.0/);
        expect(screen.getByRole('list', { name: 'Warnings' }).textContent)
            .toMatch(/120 s, 44 frames, L: flat: Flats at another offset/);
    });

    /** A stack with no frame left says so as an alert. */
    it('says stacking would fail when nothing is left', () => {
        render(<StackCheckReport prediction={{ ...prediction, frames_kept: 0, groups: [], nothing_left: true }} />);
        expect(screen.getByRole('alert').textContent).toBe(NOTHING_LEFT_MESSAGE);
    });
});

describe('stackCheck helpers', () => {
    /** The folder is shortened to the part from the library's darks, biases or flats folder. */
    it('shortens a calibration folder', () => {
        expect(shortCalibrationFolder('/home/me/Library/darks/ZWO ASI 533MM Pro/0.0/30.0')).toBe('darks/ZWO ASI 533MM Pro/0.0/30.0');
        expect(shortCalibrationFolder('C:\\lib\\flats\\T\\Cam\\L\\0.0')).toBe('flats/T/Cam/L/0.0');
        expect(shortCalibrationFolder('/a/b/c/d/e/f')).toBe('c/d/e/f');
    });

    /** No frames or a blocking flag is an error, a soft flag a warning, else ok. */
    it('rates how well the calibration fits', () => {
        expect(calibrationFit(part('dark', 0))).toBe('error');
        expect(calibrationFit(part('dark', 3, [], ['Binning differs.']))).toBe('error');
        expect(calibrationFit(part('dark', 3, ['Another temperature.']))).toBe('warning');
        expect(calibrationFit(part('dark', 3))).toBe('ok');
    });

    /** The summary counts each kind of reason. */
    it('summarizes why frames are set aside', () => {
        expect(summarizeSetAside(prediction.set_aside ?? [])).toBe('1 off the field, 1 trailed stars');
    });
});
