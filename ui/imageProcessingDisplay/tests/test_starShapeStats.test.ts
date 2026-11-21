/**
 * @file test_starShapeStats.test.ts
 * @description Tests of the small numbers drawn from the star-shape review: the median and the
 * session's typical elongation.
 */
import { describe, expect, it } from 'vitest';
import { median, sessionTypicalElongation } from '../utils/starShapeStats';

describe('median', () => {
    it('takes the middle value, or the mean of the middle two', () => {
        expect(median([3, 1, 2])).toBe(2);
        expect(median([4, 1, 2, 3])).toBe(2.5);
        expect(median([])).toBeNull();
    });
});

describe('sessionTypicalElongation', () => {
    it('is the median of the measured frames', () => {
        expect(sessionTypicalElongation([{ path: 'a', elongation: 1.0 }, { path: 'b', elongation: 1.2 }, { path: 'c', elongation: 3 }])).toBe(1.2);
        expect(sessionTypicalElongation([{ path: 'a' }])).toBeNull();
        expect(sessionTypicalElongation(undefined)).toBeNull();
    });
});
