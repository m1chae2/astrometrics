/**
 * @file test_frameStatus.test.ts
 * @description Tests of the File Browser's Status wording: the set-aside kinds, the hover text of a
 * flagged frame, "not stacked yet", the session's typical elongation, and the `status:` search filter.
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
import type { FileItem, FrameStatusReport, StarShapeReview } from '../../common/types/backendTypes';

const verdict = { state: 'round', sentence: 'x' };
const review = (series: Array<{ path: string; elongation: number }>, standout: string[]): StarShapeReview => ({
    targetId: 'M 1', scope: 'all', frameCount: series.length,
    roundness: verdict, direction: verdict, standout: verdict, corners: verdict,
    series, standoutFrames: standout.map((path) => ({
        path, elongation: series.find((frame) => frame.path === path)!.elongation, reason: 'stars stretched',
    })),
});

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
            null, null, 10,
        );
        expect(info.label).toBe('Set aside: cloud');
        expect(info.tooltip).toBe('203 stars against a typical 3348');
        expect(info.filterText).toBe('set-aside cloud');
        expect(info.restorable).toBe(true);
    });

    it('says when no reason is on record', () => {
        const info = describeFrameStatus({ path: '/e/a.fits', name: 'a.fits', state: 'set_aside' }, null, null, 10);
        expect(info.tooltip).toBe(NO_REASON_ON_RECORD);
        expect(info.label).toBe('Set aside: other');
    });

    it('flags an included frame the review marks, naming its elongation and the typical one', () => {
        const info = describeFrameStatus({ path: '/a.fits', name: 'a.fits', state: 'included' }, 1.4321, 1.08, 10);
        expect(info.label).toBe('Flagged');
        expect(info.tooltip).toBe('Stars stretched: elongation 1.43, session typical 1.08');
    });

    it('says not stacked yet, with the reason', () => {
        const withStack = describeFrameStatus({ path: '/a.fits', name: 'a.fits', state: 'not_stacked' }, null, null, 10);
        expect(withStack.label).toBe('Not stacked yet');
        expect(withStack.tooltip).toMatch(/after the last stack/);
        const noStack = describeFrameStatus({ path: '/a.fits', name: 'a.fits', state: 'not_stacked' }, null, null, null);
        expect(noStack.tooltip).toMatch(/no stack yet/);
    });
});

describe('buildFrameStatusLookup', () => {
    it('adds the review flags to the included frames only', () => {
        const report: FrameStatusReport = {
            frames: [
                { path: '/a.fits', name: 'a.fits', state: 'included' },
                { path: '/b.fits', name: 'b.fits', state: 'included' },
                { path: '/c.fits', name: 'c.fits', state: 'not_stacked' },
            ],
            stackedAt: 5,
        };
        const lookup = buildFrameStatusLookup(report, review([
            { path: '/a.fits', elongation: 1.0 }, { path: '/b.fits', elongation: 1.5 }, { path: '/c.fits', elongation: 1.6 },
        ], ['/b.fits', '/c.fits']));
        expect(lookup.get('/a.fits')?.kind).toBe('included');
        expect(lookup.get('/b.fits')?.kind).toBe('flagged');
        expect(lookup.get('/c.fits')?.kind).toBe('not-stacked');
    });

    it('is empty before the report is read', () => {
        expect(buildFrameStatusLookup(null, null).size).toBe(0);
    });
});

describe('the status search filter', () => {
    const file = { path: '/a.fits', name: 'a.fits', camera: 'ZWO', iso: '', exposure: '60', filter: 'L', date: '' } as unknown as FileItem;

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
