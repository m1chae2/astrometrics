/**
 * @fileoverview Tests for the shared tracking status check.
 */

import { describe, expect, it } from 'vitest';
import { isTrackingStatus } from '../utils/isTrackingStatus';

describe('isTrackingStatus', () => {
    it('is true for Tracking in any letter case', () => {
        expect(isTrackingStatus('Tracking')).toBe(true);
        expect(isTrackingStatus('tracking')).toBe(true);
    });

    it('is false for Not Tracking', () => {
        expect(isTrackingStatus('Not Tracking')).toBe(false);
        expect(isTrackingStatus('not tracking')).toBe(false);
    });

    it('is false for the other statuses the backend sends', () => {
        for (const text of ['Slewing', 'Parked', 'Idle', 'Unknown', '']) {
            expect(isTrackingStatus(text)).toBe(false);
        }
    });

    it('is false when there is no text', () => {
        expect(isTrackingStatus(null)).toBe(false);
        expect(isTrackingStatus(undefined)).toBe(false);
    });
});
