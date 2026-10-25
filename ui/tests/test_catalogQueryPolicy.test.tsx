/**
 * @fileoverview Tests that the star-catalog queries never poll on a timer,
 * stay quiet while hidden, fetch the count once per scope, and that App.tsx
 * invalidates the same keys the queries use.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { DisplayActiveContext } from '../common/context/DisplayActiveContext';
import { CATALOG_STALE_TIME_MS, catalogRetryDelay } from '../common/queries/catalogQueryPolicy';
import { useAstronomyListQuery } from '../common/queries/useAstronomyListQuery';
import { useAstronomyCountQuery } from '../common/queries/useAstronomyCountQuery';
import { useSpectralClassSummaryQuery } from '../common/queries/useSpectralClassSummaryQuery';
import { useTargetDataAvailabilityQuery } from '../common/queries/useTargetDataAvailabilityQuery';
import { useStarsBySpectralClassQuery } from '../common/queries/useStarsBySpectralClassQuery';

const service = vi.hoisted(() => ({
    fetchAstronomyList: vi.fn(async () => []),
    fetchAstronomyCount: vi.fn(async () => 5),
    fetchSpectralClassSummary: vi.fn(async () => []),
    fetchTargetDataAvailability: vi.fn(async () => ({})),
    fetchStarsBySpectralClass: vi.fn(async () => []),
}));
vi.mock('../common/services/astronomyService', () => service);

const makeWrapper = (client: QueryClient, active = true) =>
    ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={client}>
            <DisplayActiveContext.Provider value={active}>{children}</DisplayActiveContext.Provider>
        </QueryClientProvider>
    );

const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

/** Reads the options React Query holds for the only query in the cache. */
const onlyQueryOptions = (client: QueryClient) => client.getQueryCache().getAll()[0].options as any;

describe('catalog query policy', () => {
    beforeEach(() => vi.clearAllMocks());

    it('has no refetch interval, a 5 minute stale time and no focus refetch', () => {
        const hooks: Array<() => unknown> = [
            () => useAstronomyListQuery(),
            () => useAstronomyCountQuery(undefined, ''),
            () => useSpectralClassSummaryQuery(),
            () => useTargetDataAvailabilityQuery(),
            () => useStarsBySpectralClassQuery('G'),
        ];
        for (const hook of hooks) {
            const client = newClient();
            renderHook(hook, { wrapper: makeWrapper(client) });
            const options = onlyQueryOptions(client);
            expect(options.refetchInterval).toBeUndefined();
            expect(options.refetchOnWindowFocus).toBe(false);
            expect(options.staleTime).toBe(CATALOG_STALE_TIME_MS);
        }
    });

    it('does not fetch while the display is hidden', async () => {
        const client = newClient();
        renderHook(() => {
            useAstronomyListQuery();
            useAstronomyCountQuery(undefined, '');
            useSpectralClassSummaryQuery();
            useTargetDataAvailabilityQuery();
            useStarsBySpectralClassQuery('G');
        }, { wrapper: makeWrapper(client, false) });
        await new Promise((r) => setTimeout(r, 20));
        for (const fn of Object.values(service)) expect(fn).not.toHaveBeenCalled();
    });

    it('fetches the count once per scope change, not on re-render', async () => {
        const client = newClient();
        const { rerender } = renderHook(
            ({ target }: { target?: string }) => useAstronomyCountQuery(target, ''),
            { wrapper: makeWrapper(client), initialProps: { target: undefined as string | undefined } },
        );
        await waitFor(() => expect(service.fetchAstronomyCount).toHaveBeenCalledTimes(1));
        rerender({ target: undefined });
        rerender({ target: undefined });
        expect(service.fetchAstronomyCount).toHaveBeenCalledTimes(1);
        rerender({ target: 'M31' });
        await waitFor(() => expect(service.fetchAstronomyCount).toHaveBeenCalledTimes(2));
    });

    it('App.tsx invalidates every catalog query key on catalog:changed', () => {
        const source = readFileSync(resolve(__dirname, '../App.tsx'), 'utf8');
        for (const key of ['astronomyList', 'astronomyCount', 'targetDataAvailability', 'spectralClassSummary', 'starsBySpectralClass']) {
            expect(source).toContain(`invalidateQueries({ queryKey: ['${key}'] })`);
        }
        const client = newClient();
        renderHook(() => {
            useAstronomyListQuery();
            useAstronomyCountQuery(undefined, '');
            useSpectralClassSummaryQuery();
            useTargetDataAvailabilityQuery();
            useStarsBySpectralClassQuery('G');
        }, { wrapper: makeWrapper(client) });
        const keys = client.getQueryCache().getAll().map((q) => q.queryKey[0]);
        expect(keys.sort()).toEqual(
            ['astronomyCount', 'astronomyList', 'spectralClassSummary', 'starsBySpectralClass', 'targetDataAvailability'],
        );
    });

    it('retry waits grow and stop at 10 seconds', () => {
        expect([0, 1, 2, 3, 4].map(catalogRetryDelay)).toEqual([2000, 4000, 8000, 10000, 10000]);
    });

    describe('a failed request', () => {
        beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
        afterEach(() => vi.useRealTimers());

        it('is retried and then shows an error, never an empty result', async () => {
            service.fetchAstronomyList.mockRejectedValue(new Error('busy, try again'));
            const client = newClient();
            const { result } = renderHook(() => useAstronomyListQuery(), { wrapper: makeWrapper(client) });
            // Initial call plus two retries (retry: 2), with the backoff waits between them.
            await vi.advanceTimersByTimeAsync(2000);
            await vi.advanceTimersByTimeAsync(4000);
            await waitFor(() => expect(result.current.isError).toBe(true));
            expect(service.fetchAstronomyList).toHaveBeenCalledTimes(3);
            expect(result.current.data).toBeUndefined();
        });

        it('keeps the previous data when a refetch fails', async () => {
            service.fetchAstronomyList.mockResolvedValueOnce([{ id: 'vega' }] as never);
            const client = newClient();
            const { result } = renderHook(() => useAstronomyListQuery(), { wrapper: makeWrapper(client) });
            await waitFor(() => expect(result.current.data).toEqual([{ id: 'vega' }]));
            service.fetchAstronomyList.mockRejectedValue(new Error('busy, try again'));
            void client.invalidateQueries({ queryKey: ['astronomyList'] });
            for (let step = 0; step < 8; step += 1) await vi.advanceTimersByTimeAsync(2000);
            const cached = client.getQueryCache().getAll()[0];
            expect(cached.state.status).toBe('error');
            expect(cached.state.data).toEqual([{ id: 'vega' }]);
        });
    });
});
