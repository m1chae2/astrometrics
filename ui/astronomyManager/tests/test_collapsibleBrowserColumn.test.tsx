/**
 * @file test_collapsibleBrowserColumn.test.tsx
 * @description Checks that the Astronomy Manager's left column (the browse tabs, the Targets list and
 * the star list) folds to the same thin strip as Image Processing's, names the selected target on
 * the strip, opens again, and remembers the choice.
 */

import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';

const STORAGE_KEY = 'astrometrics:astronomyManager:targetListCollapsed';

vi.mock('../hooks/useTargetBrowserItems', () => ({
    useTargetBrowserItems: () => ({
        items: [{ id: 'M 31', value: 'M 31', label: 'M 31' }],
        filterText: '',
        setFilterText: () => undefined,
        isLoading: false,
        isError: false,
        retry: () => undefined,
    }),
}));

vi.mock('../hooks/useSpectralClassBrowserItems', () => ({
    ALL_SPECTRAL_CLASSES_VALUE: '__all__',
    useSpectralClassBrowserItems: () => ({
        items: [], filterText: '', setFilterText: () => undefined, isLoading: false, isError: false, retry: () => undefined,
    }),
}));

/** An empty star list, shared by the by-target and by-class lists. */
const emptyStarList = {
    items: [], filterText: '', setFilterText: () => undefined, page: 1, setPage: () => undefined,
    selectStar: () => undefined, rememberPickedStar: () => undefined, hasMore: false, totalPages: 1,
    isLoading: false, isError: false, retry: () => undefined,
};

vi.mock('../hooks/useSpectrumList', () => ({ useSpectrumList: () => emptyStarList }));
vi.mock('../hooks/useStarsBySpectralClassList', () => ({ useStarsBySpectralClassList: () => emptyStarList }));
vi.mock('../hooks/useSpectrumData', () => ({
    useSpectrumData: () => ({ astronomyData: null, loading: false, error: null, replaceAstronomyData: () => undefined }),
}));

import { AstronomyManager } from '../AstronomyManager';

describe('Astronomy Manager left column fold', () => {
    beforeEach(() => {
        localStorage.clear();
    });

    it('folds to a strip naming the selected target, and opens again', async () => {
        render(<AstronomyManager />);
        const hide = await screen.findByTitle('Hide target list');
        expect(hide.textContent).toBe('Targets');
        expect(hide.getAttribute('aria-expanded')).toBe('true');
        await userEvent.click(hide);

        const strip = screen.getByRole('button', { name: 'Targets, M 31' });
        expect(strip.getAttribute('aria-expanded')).toBe('false');
        expect(screen.getByText('M 31').className).toContain('panel__collapsed-note');
        expect(screen.queryByText('By spectral class')).toBeNull();
        expect(document.querySelector('.collapsible-column--collapsed')).not.toBeNull();

        await userEvent.click(strip);
        expect(screen.getByText('By spectral class')).toBeTruthy();
        expect(document.querySelector('.collapsible-column--collapsed')).toBeNull();
    });

    it('remembers the folded state under its own key', async () => {
        const first = render(<AstronomyManager />);
        await userEvent.click(await screen.findByTitle('Hide target list'));
        expect(localStorage.getItem(STORAGE_KEY)).not.toBeNull();
        first.unmount();
        render(<AstronomyManager />);
        expect(screen.getByRole('button', { name: 'Targets, M 31' })).toBeTruthy();
    });
});
