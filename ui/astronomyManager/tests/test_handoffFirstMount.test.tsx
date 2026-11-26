/**
 * @file test_handoffFirstMount.test.tsx
 * @description Checks that a star handed to the Astronomy Manager through the hand-off store
 * (the Planetarium's "Open in Astronomy Manager") is the star it opens when it first mounts,
 * with no local storage key involved, and that the first target's default star does not
 * replace it.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { postHandoff } from '../../common/utils/displayHandoffStore';

vi.mock('../hooks/useTargetBrowserItems', () => ({
    useTargetBrowserItems: () => ({
        items: [{ id: 'M 31', value: 'M 31', label: 'M 31' }],
        filterText: '', setFilterText: () => undefined, isLoading: false, isError: false, retry: () => undefined,
    }),
}));

vi.mock('../hooks/useSpectralClassBrowserItems', () => ({
    ALL_SPECTRAL_CLASSES_VALUE: '__all__',
    useSpectralClassBrowserItems: () => ({
        items: [], filterText: '', setFilterText: () => undefined, isLoading: false, isError: false, retry: () => undefined,
    }),
}));

vi.mock('../hooks/useStarsBySpectralClassList', () => ({
    useStarsBySpectralClassList: () => ({
        items: [], filterText: '', setFilterText: () => undefined, page: 1, setPage: () => undefined,
        selectStar: () => undefined, rememberPickedStar: () => undefined, hasMore: false, totalPages: 1,
        isLoading: false, isError: false, retry: () => undefined,
    }),
}));

/** The target's star list: the default star (it has a spectrum) comes first, the handed-off star second. */
const starRows = [
    { id: 'default-star', name: 'Default Star', hasSpectra: true },
    { id: 'handed-star', name: 'Handed Star' },
];

vi.mock('../../common/queries/useAstronomyListQuery', () => ({
    useAstronomyListQuery: () => ({ data: starRows, isLoading: false, isError: false, error: null, refetch: () => undefined }),
}));
vi.mock('../../common/queries/useAstronomyCountQuery', () => ({
    useAstronomyCountQuery: () => ({ data: starRows.length }),
}));

/** Every star id the display asked to load, in order. */
const requestedStars: string[] = [];
vi.mock('../hooks/useSpectrumData', () => ({
    useSpectrumData: (pendingId: string) => {
        if (pendingId) requestedStars.push(pendingId);
        return { astronomyData: null, loading: false, error: null, replaceAstronomyData: () => undefined };
    },
}));

import { AstronomyManager } from '../AstronomyManager';

describe('Astronomy Manager star hand-off on first mount', () => {
    beforeEach(() => {
        requestedStars.length = 0;
        localStorage.clear();
    });

    /** The hand-off alone opens the star, and the target's default star never replaces it. */
    it('opens the handed-off star, not the default star', async () => {
        postHandoff('Astronomy Manager', 'astronomySelectStar', 'handed-star');
        render(<AstronomyManager />);
        await waitFor(() => expect(requestedStars).toContain('handed-star'));
        expect(requestedStars).not.toContain('default-star');
        expect(requestedStars[requestedStars.length - 1]).toBe('handed-star');
    });

    /** With no hand-off, the first target's default star opens. */
    it('opens the default star when nothing was handed off', async () => {
        render(<AstronomyManager />);
        await waitFor(() => expect(requestedStars).toContain('default-star'));
    });
});
