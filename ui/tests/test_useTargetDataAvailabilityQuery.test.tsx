/**
 * @fileoverview Checks that the target availability query asks again while its map is empty
 * (the library was not filled yet at start-up) and stops once a target has an entry.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';

const fetchTargetDataAvailability = vi.fn();
vi.mock('../common/services/astronomyService', () => ({
  fetchTargetDataAvailability: () => fetchTargetDataAvailability(),
}));

import { EMPTY_AVAILABILITY_RETRY_MS, useTargetDataAvailabilityQuery } from '../common/queries/useTargetDataAvailabilityQuery';

describe('useTargetDataAvailabilityQuery', () => {
  /** An empty map is fetched again after the retry wait, then kept once it has data. */
  it('refetches an empty map and stops when it has targets', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchTargetDataAvailability
      .mockResolvedValueOnce({})
      .mockResolvedValue({ Albireo: { starCount: 1436, hasSpectra: true, hasPhotometry: true } });
    const client = new QueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useTargetDataAvailabilityQuery(), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual({}));
    await vi.advanceTimersByTimeAsync(EMPTY_AVAILABILITY_RETRY_MS + 100);
    await waitFor(() => expect(result.current.data?.Albireo.starCount).toBe(1436));
    const callsWhenFilled = fetchTargetDataAvailability.mock.calls.length;
    await vi.advanceTimersByTimeAsync(EMPTY_AVAILABILITY_RETRY_MS * 3);
    expect(fetchTargetDataAvailability.mock.calls.length).toBe(callsWhenFilled);
    vi.useRealTimers();
  });
});
