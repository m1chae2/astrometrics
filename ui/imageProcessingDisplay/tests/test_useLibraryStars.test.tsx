/**
 * @fileoverview Tests for `useLibraryStars`: one call asks which overlay stars the library has, and
 * only those get a `libraryId` (so only those can open in the Astronomy Manager).
 */
import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useLibraryStars } from '../hooks/useLibraryStars';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => callBackendMock(...args),
}));

const stars = [
    { id: 'detection-0', name: 'HD 214181', x: 1, y: 1, gaiaSourceId: '11', matchKind: 'matched' as const },
    { id: 'detection-1', name: 'Gaia DR3 22', x: 2, y: 2, gaiaSourceId: '22', matchKind: 'matched' as const },
    { id: 'detection-2', name: 'Unnamed star', x: 3, y: 3, matchKind: 'unmatched' as const },
];

/** Wraps a hook in a fresh query client. */
function wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe('useLibraryStars', () => {
    beforeEach(() => callBackendMock.mockReset());

    it('asks once and marks only the stars the library has', async () => {
        callBackendMock.mockResolvedValue(['Gaia DR3 22']);
        const { result } = renderHook(() => useLibraryStars(stars, 'NGC 7331|stack.fits'), { wrapper });
        await waitFor(() => expect(result.current[1].libraryId).toBe('Gaia DR3 22'));
        expect(result.current[0].libraryId).toBeUndefined();
        expect(result.current[2].libraryId).toBeUndefined();
        expect(callBackendMock).toHaveBeenCalledTimes(1);
        expect(callBackendMock).toHaveBeenCalledWith('astronomy:existing_ids', {
            star_ids: ['Gaia DR3 11', 'HD 214181', 'Gaia DR3 22'],
        });
    });

    it('asks nothing when there are no stars', () => {
        const { result } = renderHook(() => useLibraryStars([], 'x'), { wrapper });
        expect(result.current).toEqual([]);
        expect(callBackendMock).not.toHaveBeenCalled();
    });
});
