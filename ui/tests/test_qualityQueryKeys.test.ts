/**
 * @fileoverview Tests that one prefix call refreshes every quality query.
 */
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import {
    isAffectedByMeasurement,
    PLAN_QUERY_KEY_PREFIX,
    QUALITY_QUERY_KEY_PREFIX,
    qualityQueryKeys,
} from '../common/queries/qualityQueryKeys';
import { UI_EVENTS } from '../common/types/backendTypes';

describe('qualityQueryKeys', () => {
    it('starts every key with the shared prefix', () => {
        expect(qualityQueryKeys.query({ target_id: 'M 13' }).slice(0, 1)).toEqual([...QUALITY_QUERY_KEY_PREFIX]);
        expect(qualityQueryKeys.metrics().slice(0, 1)).toEqual([...QUALITY_QUERY_KEY_PREFIX]);
    });

    it('marks every quality query stale with one prefix call and leaves other queries alone', async () => {
        const client = new QueryClient();
        client.setQueryData(qualityQueryKeys.query({ target_id: 'M 13' }), []);
        client.setQueryData(qualityQueryKeys.metrics('frame.read_noise_e'), []);
        client.setQueryData(['targetDataAvailability'], {});

        await client.invalidateQueries({ queryKey: QUALITY_QUERY_KEY_PREFIX });

        const staleByKey = client
            .getQueryCache()
            .getAll()
            .map((query) => [query.queryKey[0], query.state.isInvalidated]);
        expect(staleByKey).toContainEqual(['quality', true]);
        expect(staleByKey).toContainEqual(['targetDataAvailability', false]);
        expect(staleByKey.filter(([key, stale]) => key === 'quality' && stale)).toHaveLength(2);
    });

    it('refreshes only the queries for the measured target and nights', () => {
        const payload = { targetId: 'M 13', observingNightIds: ['2026-10-13'] };
        expect(isAffectedByMeasurement(qualityQueryKeys.query({ target_id: 'M 13' }), payload)).toBe(true);
        expect(isAffectedByMeasurement(qualityQueryKeys.query({ target_id: 'M 57' }), payload)).toBe(false);
        expect(isAffectedByMeasurement(qualityQueryKeys.query({ observing_night_id: '2026-10-13' }), payload)).toBe(true);
        expect(isAffectedByMeasurement(qualityQueryKeys.query({ observing_night_id: '2026-10-01' }), payload)).toBe(false);
        expect(isAffectedByMeasurement(qualityQueryKeys.query({ metric: 'frame.read_noise_e' }), payload)).toBe(true);
        expect(isAffectedByMeasurement(qualityQueryKeys.metrics(), payload)).toBe(true);
        expect(isAffectedByMeasurement(['targetDataAvailability'], payload)).toBe(false);
    });

    it('names the event the backend sends when rows were stored', () => {
        expect(UI_EVENTS['quality:measured']).toBe('quality:measured');
    });
});

describe('advisory, history and plan key builders', () => {
    const payload = { targetId: 'M 13', observingNightIds: ['2026-10-13'] };

    it('refreshes target keys only for the measured target', () => {
        expect(isAffectedByMeasurement(qualityQueryKeys.targetReport('M 13'), payload)).toBe(true);
        expect(isAffectedByMeasurement(qualityQueryKeys.targetReport('M 57'), payload)).toBe(false);
        expect(isAffectedByMeasurement(qualityQueryKeys.resultStatements('M 13', 'star-1'), payload)).toBe(true);
        expect(isAffectedByMeasurement(qualityQueryKeys.resultStatements('M 57', 'star-1'), payload)).toBe(false);
    });

    it('refreshes night keys only for the measured nights', () => {
        expect(isAffectedByMeasurement(qualityQueryKeys.nightQuality('2026-10-13'), payload)).toBe(true);
        expect(isAffectedByMeasurement(qualityQueryKeys.nightQuality('2026-10-01'), payload)).toBe(false);
        expect(isAffectedByMeasurement(qualityQueryKeys.targetChanges('2026-10-01'), payload)).toBe(false);
        expect(isAffectedByMeasurement(qualityQueryKeys.targetChanges(), payload)).toBe(true);
        expect(isAffectedByMeasurement(qualityQueryKeys.nights(), payload)).toBe(true);
        expect(isAffectedByMeasurement(qualityQueryKeys.liveSessionStatus(), payload)).toBe(true);
    });

    it('keeps the tonight ranking and plan reads out of the measurement refresh', () => {
        expect(isAffectedByMeasurement(qualityQueryKeys.tonightRanking(null), payload)).toBe(false);
        expect(isAffectedByMeasurement([...PLAN_QUERY_KEY_PREFIX, 'list'], payload)).toBe(false);
        expect(qualityQueryKeys.tonightRanking('2026-10-24T22:00:00-06:00')).not.toEqual(qualityQueryKeys.tonightRanking(null));
    });
});
