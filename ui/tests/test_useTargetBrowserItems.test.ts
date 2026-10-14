/**
 * @fileoverview Test suite for useTargetBrowserItems, the "browse by
 * target" primary list used in the Astronomy Manager's split target/star
 * navigation.
 */

import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useTargetBrowserItems } from '../astronomyManager/hooks/useTargetBrowserItems';

vi.mock('../common/queries/useTargetListQuery', () => ({
    useTargetListQuery: vi.fn(),
}));

vi.mock('../common/queries/useTargetDataAvailabilityQuery', () => ({
    useTargetDataAvailabilityQuery: vi.fn(),
}));

import { useTargetListQuery } from '../common/queries/useTargetListQuery';
import { useTargetDataAvailabilityQuery } from '../common/queries/useTargetDataAvailabilityQuery';

describe('useTargetBrowserItems', () => {
    it('lists each target showing its star count and data badges, with no unscoped All entry', () => {
        vi.mocked(useTargetListQuery).mockReturnValue({
            data: ['M 81', 'M 13', 'NGC 2403'],
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);
        vi.mocked(useTargetDataAvailabilityQuery).mockReturnValue({
            data: {
                'M 81': { hasSpectra: true, hasPhotometry: false, starCount: 6 },
                'M 13': { hasSpectra: true, hasPhotometry: true, starCount: 14 },
            },
            isLoading: false,
            error: null,
        } as any);

        const { result } = renderHook(() => useTargetBrowserItems());

        expect(result.current.items.map((i) => i.id)).toEqual(['M 13', 'M 81', 'NGC 2403']);
        const byId = Object.fromEntries(result.current.items.map((i) => [i.id, i]));
        expect(byId['M 13']).toMatchObject({ subtitle: '14 stars', hasSpectra: true, hasPhotometry: true });
        expect(byId['M 81']).toMatchObject({ subtitle: '6 stars', hasSpectra: true, hasPhotometry: false });
        expect(byId['NGC 2403']).toMatchObject({ subtitle: '0 stars', hasSpectra: false, hasPhotometry: false });
    });

    it('shows a loading subtitle instead of a misleading "0 stars" while availability is still loading', () => {
        vi.mocked(useTargetListQuery).mockReturnValue({
            data: ['Albireo'],
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);
        vi.mocked(useTargetDataAvailabilityQuery).mockReturnValue({
            data: undefined,
            isLoading: true,
            error: null,
        } as any);

        const { result } = renderHook(() => useTargetBrowserItems());

        expect(result.current.items).toEqual([
            expect.objectContaining({ id: 'Albireo', subtitle: 'Loading…' }),
        ]);
    });

    it('filters targets by name, case-insensitively', () => {
        vi.mocked(useTargetListQuery).mockReturnValue({
            data: ['M 81', 'M 13', 'NGC 2403'],
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);
        vi.mocked(useTargetDataAvailabilityQuery).mockReturnValue({
            data: {},
            isLoading: false,
            error: null,
        } as any);

        const { result } = renderHook(() => useTargetBrowserItems());

        act(() => {
            result.current.setFilterText('ngc');
        });

        expect(result.current.items.map((i) => i.id)).toEqual(['NGC 2403']);
    });
});
