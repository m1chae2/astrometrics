/**
 * @fileoverview Tests for the shared controls that replaced one-off ones: the camera filter of the
 * file browser (a PopoverSelect), the sort order of the target list (compact Tabs), the "Hide Feature
 * Lines" toggle of the spectrum chart (a pressed-state button) and the console sidebar tabs.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';
import { FileBrowserToolbar } from '../imageProcessingDisplay/fileBrowser/FileBrowser';
import { TargetListFilterPanel } from '../common/radioList/TargetListFilterPanel';
import { SpectrumViewer } from '../astronomyManager/components/SpectrumViewer';
import { ConsoleSidebar } from '../commandConsole/ConsoleSidebar';

vi.mock('../common/components/plotting/BasePlot', () => ({ BasePlot: () => <div data-testid="plot" /> }));
vi.mock('../common/services/backendApi', () => ({ callBackend: vi.fn().mockResolvedValue([]) }));


const frame = (camera: string) => ({
    path: `/lib/${camera}.fits`, name: `${camera}.fits`, camera, iso: '', exposure: '60.0', filter: 'L', date: '2026-11-04',
});

describe('FileBrowser camera filter', () => {
    /** Choosing a camera reports its name; choosing "All Cameras" reports "All", as the old select did. */
    it('reports the chosen camera, and All for the empty choice', async () => {
        const user = userEvent.setup();
        const onCameraChange = vi.fn();
        render(
            <FileBrowserToolbar
                files={[frame('ASI533'), frame('Nikon')]}
                searchTerm=""
                onSearchChange={() => {}}
                selectedCamera="All"
                onCameraChange={onCameraChange}
                checkedFiles={new Set()}
            />,
        );
        await user.click(screen.getByRole('button', { name: 'Camera: All Cameras' }));
        await user.click(screen.getByRole('button', { name: 'Nikon' }));
        expect(onCameraChange).toHaveBeenCalledWith('Nikon');
        await user.click(screen.getByRole('button', { name: 'Camera: All Cameras' }));
        await user.click(screen.getByRole('button', { name: 'All Cameras' }));
        expect(onCameraChange).toHaveBeenLastCalledWith('All');
    });
});

describe('TargetListFilterPanel sort order', () => {
    /** The two sort choices are tabs, and picking Newest calls the same handler as before. */
    it('offers A-Z and Newest as tabs', async () => {
        const user = userEvent.setup();
        const onSortChange = vi.fn();
        render(
            <TargetListFilterPanel
                catalogOptions={['All Targets']}
                selectedCatalog="All Targets"
                onCatalogChange={() => {}}
                cameras={[]}
                selectedCamera="All"
                onCameraChange={() => {}}
                sort="A–Z"
                onSortChange={onSortChange}
            />,
        );
        const sortTabs = screen.getByRole('tablist', { name: 'Sort order' });
        expect(sortTabs.classList.contains('tabs--compact')).toBe(true);
        expect(screen.getByRole('tab', { name: 'A–Z' }).getAttribute('aria-selected')).toBe('true');
        await user.click(screen.getByRole('tab', { name: 'Newest' }));
        expect(onSortChange).toHaveBeenCalledWith('Newest');
    });
});

describe('SpectrumViewer Hide Feature Lines', () => {
    /** It is a toggle button: not pressed at first, pressed after a click, and not a tab. */
    it('is a toggle button with a pressed state', async () => {
        const user = userEvent.setup();
        const astronomyData = {
            wavelength: [4000, 5000, 6000, 7000],
            spectrumFlux: [1, 0.8, 0.9, 1.1],
            spectroscopy: {
                probableSpectralFeatures: [{ feature: 'H-alpha', wavelength_angstrom: 6563, verdict: 'detected', depth: 0.3 }],
                emissionLines: [],
            },
        } as any;
        render(<SpectrumViewer astronomyData={astronomyData} loading={false} />);
        const toggle = screen.getByRole('button', { name: 'Hide Feature Lines' });
        expect(toggle.getAttribute('aria-pressed')).toBe('false');
        expect(screen.queryByRole('tab', { name: 'Hide Feature Lines' })).toBeNull();
        await user.click(toggle);
        expect(toggle.getAttribute('aria-pressed')).toBe('true');
    });
});

describe('ConsoleSidebar tabs', () => {
    /** The sidebar tabs are shared Tabs, and the Docs tab shows its count. */
    it('shows the Scripts and Docs tabs with the Docs count', () => {
        render(<ConsoleSidebar
                recipes={[]}
                userScripts={[]}
                docTopics={[{ id: 'a', title: 'A', path: 'a', category: 'General' }, { id: 'b', title: 'B', path: 'b', category: 'General' }]}
                onSelectRecipe={() => {}}
                onSelectUserScript={() => {}}
                onNewScript={() => {}}
            />);
        expect(screen.getByRole('tab', { name: 'Scripts & Recipes' }).getAttribute('aria-selected')).toBe('true');
        expect(screen.getByRole('tab', { name: 'Docs (2)' })).toBeTruthy();
    });
});
