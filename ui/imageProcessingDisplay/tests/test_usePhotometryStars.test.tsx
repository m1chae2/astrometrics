/**
 * @fileoverview Tests for `usePhotometryStars`: on a stack the Astrometry overlay keeps only the named
 * stars whose library id has photometry, found with one request, and says how many it shows. A target
 * with no photometry shows no stars and a note.
 */
import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NO_PHOTOMETRY_NOTE, usePhotometryStars } from '../hooks/usePhotometryStars';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => callBackendMock(...args),
}));

const stars = [
    { id: 'detection-0', name: 'HD 214181', x: 1, y: 1, gaiaSourceId: '11', libraryId: 'Gaia DR3 11', matchKind: 'matched' as const },
    { id: 'detection-1', name: 'TYC 1-2-3', x: 2, y: 2, gaiaSourceId: '22', libraryId: 'Gaia DR3 22', matchKind: 'matched' as const },
    { id: 'detection-2', name: 'Unnamed star', x: 3, y: 3, matchKind: 'unmatched' as const },
];

/** Wraps a hook in a fresh query client. */
function wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe('usePhotometryStars', () => {
    beforeEach(() => callBackendMock.mockReset());

    it('keeps only the stars with photometry, from one request', async () => {
        callBackendMock.mockResolvedValue([{ id: 'Gaia DR3 22' }, { id: 'Some other star' }]);
        const { result } = renderHook(() => usePhotometryStars(stars, 'NGC 7331', true), { wrapper });
        await waitFor(() => expect(result.current.stars).toHaveLength(1));
        expect(result.current.stars[0].name).toBe('TYC 1-2-3');
        expect(result.current.stars[0].libraryId).toBe('Gaia DR3 22');
        expect(result.current.countText).toBe('Showing 1 star with photometry');
        expect(callBackendMock).toHaveBeenCalledTimes(1);
        expect(callBackendMock).toHaveBeenCalledWith('astronomy:list', {
            target_id: 'NGC 7331', filter_type: 'photometry', limit: 0,
        });
    });

    it('matches a star by its library id only, never by guessing from its name', async () => {
        callBackendMock.mockResolvedValue([{ id: 'HD 214181' }]);
        const { result } = renderHook(() => usePhotometryStars(stars, 'NGC 7331', true), { wrapper });
        await waitFor(() => expect(result.current.countText).toBe('Showing 0 stars with photometry'));
        expect(result.current.stars).toEqual([]);
    });

    it('shows no stars and a note when the target has no photometry', async () => {
        callBackendMock.mockResolvedValue([]);
        const { result } = renderHook(() => usePhotometryStars(stars, 'NGC 7331', true), { wrapper });
        await waitFor(() => expect(result.current.countText).toBe(NO_PHOTOMETRY_NOTE));
        expect(result.current.stars).toEqual([]);
    });

    it('asks nothing while switched off', () => {
        const { result } = renderHook(() => usePhotometryStars(stars, 'NGC 7331', false), { wrapper });
        expect(result.current.stars).toEqual([]);
        expect(result.current.countText).toBeNull();
        expect(callBackendMock).not.toHaveBeenCalled();
    });
});
