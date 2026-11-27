/**
 * @file test_frameStatus.test.ts
 * @description Tests of the File Browser's Status wording: the set-aside kinds, the hover text of a
 * flagged frame (the library's reason word for word), "not stacked yet", and the `status:` search filter.
 */
import { describe, expect, it } from 'vitest';
import {
    buildFrameStatusLookup,
    describeFrameStatus,
    normalizeStatusFilter,
    setAsideCategory,
    NO_REASON_ON_RECORD,
} from '../utils/frameStatus';
import { matchesQuery } from '../../common/utils/searchLogic';
import type { FileItem, FrameStatusReport } from '../../common/types/backendTypes';

describe('setAsideCategory', () => {
    it('names trailed, cloud, off field and other', () => {
        expect(setAsideCategory('trailed')).toBe('trailed');
        expect(setAsideCategory('clouded')).toBe('cloud');
        expect(setAsideCategory('off_field')).toBe('off field');
        expect(setAsideCategory('something else')).toBe('other');
        expect(setAsideCategory(null)).toBe('other');
    });
});

describe('describeFrameStatus', () => {
    it('gives a set-aside frame the stacker reason word for word', () => {
        const info = describeFrameStatus(
            { path: '/e/a.fits', name: 'a.fits', state: 'set_aside', kind: 'clouded', reason: '203 stars against a typical 3348', restorable: true },
            10,
        );
        expect(info.label).toBe('Set aside: cloud');
        expect(info.tooltip).toBe('203 stars against a typical 3348');
        expect(info.filterText).toBe('set-aside cloud');
        expect(info.restorable).toBe(true);
    });

    it('says when no reason is on record', () => {
        const info = describeFrameStatus({ path: '/e/a.fits', name: 'a.fits', state: 'set_aside' }, 10);
        expect(info.tooltip).toBe(NO_REASON_ON_RECORD);
        expect(info.label).toBe('Set aside: other');
    });

    it('shows the library reason for a flagged frame word for word', () => {
        const reason = 'Stacked, but its stars are stretched: elongation 1.25, warning level 1.20, set-aside threshold 1.32.';
        const info = describeFrameStatus({ path: '/a.fits', name: 'a.fits', state: 'flagged', elongation: 1.25, reason }, 10);
        expect(info.label).toBe('Flagged');
        expect(info.tone).toBe('warning');
        expect(info.tooltip).toBe(reason);
    });

    it('gives a flagged frame with no reason a plain sentence', () => {
        const info = describeFrameStatus({ path: '/a.fits', name: 'a.fits', state: 'flagged', elongation: 1.25 }, 10);
        expect(info.tooltip).toBe('Stacked, but its stars are stretched.');
    });

    it('says not stacked yet, with the reason', () => {
        const withStack = describeFrameStatus({ path: '/a.fits', name: 'a.fits', state: 'not_stacked' }, 10);
        expect(withStack.label).toBe('Not stacked yet');
        expect(withStack.tooltip).toMatch(/after the last stack/);
        const noStack = describeFrameStatus({ path: '/a.fits', name: 'a.fits', state: 'not_stacked' }, null);
        expect(noStack.tooltip).toMatch(/no stack yet/);
    });
});

describe('buildFrameStatusLookup', () => {
    it('words each state the backend gives, with the flagged reason', () => {
        const report: FrameStatusReport = {
            frames: [
                { path: '/a.fits', name: 'a.fits', state: 'included' },
                { path: '/b.fits', name: 'b.fits', state: 'flagged', elongation: 1.25, reason: 'Stacked, but its stars are stretched: elongation 1.25, warning level 1.20, set-aside threshold 1.32.' },
                { path: '/c.fits', name: 'c.fits', state: 'not_stacked' },
            ],
            stackedAt: 5,
        };
        const lookup = buildFrameStatusLookup(report);
        expect(lookup.get('/a.fits')?.kind).toBe('included');
        expect(lookup.get('/b.fits')?.kind).toBe('flagged');
        expect(lookup.get('/b.fits')?.tooltip).toBe('Stacked, but its stars are stretched: elongation 1.25, warning level 1.20, set-aside threshold 1.32.');
        expect(lookup.get('/c.fits')?.kind).toBe('not-stacked');
    });

    it('is empty before the report is read', () => {
        expect(buildFrameStatusLookup(null).size).toBe(0);
    });
});

describe('the status search filter', () => {
    const file = { path: '/a.fits', name: 'a.fits', camera: 'ZWO', gain: null, exposureS: 60, filter: 'L', date: '' } as unknown as FileItem;

    it('matches status:set-aside and status:flagged on the status text', () => {
        expect(matchesQuery({ ...file, status: 'set-aside cloud' }, 'status:set-aside')).toBe(true);
        expect(matchesQuery({ ...file, status: 'set-aside cloud' }, 'status:cloud')).toBe(true);
        expect(matchesQuery({ ...file, status: 'included' }, 'status:set-aside')).toBe(false);
        expect(matchesQuery({ ...file, status: 'flagged' }, 'status:flagged')).toBe(true);
    });

    it('treats a space like a hyphen and combines with other filters', () => {
        expect(matchesQuery({ ...file, status: 'set-aside trailed' }, 'status:set aside')).toBe(true);
        expect(matchesQuery({ ...file, status: 'flagged' }, 'status:flagged && exp > 30')).toBe(true);
        expect(matchesQuery({ ...file, status: 'flagged' }, 'status:flagged && exp > 90')).toBe(false);
    });

    it('can ask for exactly one status', () => {
        expect(matchesQuery({ ...file, status: 'not-stacked' }, 'status=included')).toBe(false);
        expect(matchesQuery({ ...file, status: 'included' }, 'status=included')).toBe(true);
    });

    it('normalizes what a person types', () => {
        expect(normalizeStatusFilter(' Set Aside ')).toBe('set-aside');
    });
});
