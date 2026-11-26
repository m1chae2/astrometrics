/**
 * @file test_starShapeStats.test.ts
 * @description Tests of the session's typical elongation, read from the star-shape review.
 */
import { describe, expect, it } from 'vitest';
import { sessionTypicalElongation } from '../utils/starShapeStats';

describe('sessionTypicalElongation', () => {
    it('reads the review\'s own typical elongation', () => {
        expect(sessionTypicalElongation({ typicalElongation: 1.25 })).toBe(1.25);
        expect(sessionTypicalElongation({ typicalElongation: null })).toBeNull();
        expect(sessionTypicalElongation({})).toBeNull();
        expect(sessionTypicalElongation(null)).toBeNull();
        expect(sessionTypicalElongation(undefined)).toBeNull();
    });

    it('gives the median of a per-frame series from a caller that still passes one', () => {
        expect(sessionTypicalElongation([{ path: 'a', elongation: 1.0 }, { path: 'b', elongation: 1.2 }, { path: 'c', elongation: 3 }])).toBe(1.2);
        expect(sessionTypicalElongation([{ path: 'a' }])).toBeNull();
    });
});
