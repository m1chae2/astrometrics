/**
 * @file test_jobNights.test.ts
 * @description Tests for the observing-night helpers: the local-noon boundary, a session that
 * crosses midnight, the before-noon case, and grouping.
 */
import { describe, expect, it } from 'vitest';
import {
    formatNightLabel, getNightStart, groupJobsByNight, isFromCurrentNight, toLocalIsoTime,
} from '../utils/jobNights';

/** Builds a minimal job created at a local time. */
const jobAt = (id: string, createdAt: string) => ({
    id, targetId: 'M 31', jobType: 'stacking', status: 'completed', createdAt,
});

describe('getNightStart', () => {
    /** After noon, the night began at noon today. */
    it('uses noon today when it is after noon', () => {
        expect(toLocalIsoTime(getNightStart(new Date(2026, 10, 5, 21, 30)))).toBe('2026-11-05T12:00:00');
    });

    /** Exactly at noon counts as the new night. */
    it('treats exactly noon as the start of a new night', () => {
        expect(toLocalIsoTime(getNightStart(new Date(2026, 10, 5, 12, 0, 0)))).toBe('2026-11-05T12:00:00');
    });

    /** Before noon, the night began at noon yesterday. */
    it('uses noon yesterday when it is before noon', () => {
        expect(toLocalIsoTime(getNightStart(new Date(2026, 10, 5, 11, 59, 59)))).toBe('2026-11-04T12:00:00');
    });

    /** A session after midnight still belongs to the evening before. */
    it('keeps a session that crosses midnight together', () => {
        const evening = getNightStart(new Date(2026, 10, 4, 23, 0));
        const afterMidnight = getNightStart(new Date(2026, 10, 5, 1, 30));
        expect(evening.getTime()).toBe(afterMidnight.getTime());
    });

    /** Month boundaries roll back correctly. */
    it('rolls back across a month boundary', () => {
        expect(toLocalIsoTime(getNightStart(new Date(2026, 11, 1, 3, 0)))).toBe('2026-11-30T12:00:00');
    });
});

describe('isFromCurrentNight', () => {
    /** Before noon, last evening's jobs are still tonight's. */
    it('includes last evening before noon and excludes the night before', () => {
        const now = new Date(2026, 10, 5, 9, 0);
        expect(isFromCurrentNight(jobAt('a', '2026-11-04T22:00:00'), now)).toBe(true);
        expect(isFromCurrentNight(jobAt('b', '2026-11-04T11:00:00'), now)).toBe(false);
    });

    /** Jobs without a time cannot be placed. */
    it('excludes jobs with no creation time', () => {
        expect(isFromCurrentNight({ ...jobAt('c', ''), createdAt: null }, new Date())).toBe(false);
    });
});

describe('formatNightLabel', () => {
    /** The label names the weekday, month and day. */
    it('reads like Wed Nov 4', () => {
        expect(formatNightLabel(new Date(2026, 10, 4, 12))).toBe('Wed Nov 4');
    });
});

describe('groupJobsByNight', () => {
    /** Jobs split on the noon boundary, newest night first, newest job first. */
    it('groups jobs by night with the newest first', () => {
        const nights = groupJobsByNight([
            jobAt('late', '2026-11-04T23:30:00'),
            jobAt('after-midnight', '2026-11-05T01:00:00'),
            jobAt('day-before', '2026-11-03T20:00:00'),
            jobAt('noon-gap', '2026-11-04T11:00:00'),
        ]);
        expect(nights.map((night) => night.label)).toEqual(['Wed Nov 4', 'Tue Nov 3']);
        expect(nights[0].jobs.map((job) => job.id)).toEqual(['after-midnight', 'late']);
        // 11:00 on Nov 4 is before noon, so it still belongs to the night of Nov 3.
        expect(nights[1].jobs.map((job) => job.id)).toEqual(['noon-gap', 'day-before']);
    });

    /** Jobs with no time are skipped. */
    it('skips jobs with no usable time', () => {
        expect(groupJobsByNight([{ ...jobAt('x', ''), createdAt: null }, jobAt('y', 'not a date')])).toEqual([]);
    });
});
