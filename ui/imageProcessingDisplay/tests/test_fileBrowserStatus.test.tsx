/**
 * @file test_fileBrowserStatus.test.tsx
 * @description Tests of the File Browser's Status column: a chip for each status with the reason on hover,
 * set-aside frames listed after the target's own frames and out of reach of the checkboxes, the `status:`
 * filter, and the Put back flow with its confirm dialog.
 */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FileBrowser, type FileItem } from '../fileBrowser/FileBrowser';
import { buildFrameStatusLookup } from '../utils/frameStatus';
import type { FrameStatusReport } from '../../common/types/backendTypes';

const item = (name: string, date = '2026-11-01 21:00:00'): FileItem => ({
    path: `/lib/${name}`, name, camera: 'ZWO', iso: '', exposure: '60', filter: 'L', date,
});

const report: FrameStatusReport = {
    stackedAt: 100,
    frames: [
        { path: '/lib/a.fits', name: 'a.fits', state: 'included' },
        { path: '/lib/b.fits', name: 'b.fits', state: 'included' },
        { path: '/lib/c.fits', name: 'c.fits', state: 'not_stacked' },
        { path: '/lib/_excluded/d.fits', name: 'd.fits', state: 'set_aside', kind: 'clouded', reason: '203 stars against a typical 3348', restorable: true },
    ],
};
const review = {
    targetId: 'M 1', scope: 'all', frameCount: 3,
    roundness: { state: 'round', sentence: '' }, direction: { state: 'round', sentence: '' },
    standout: { state: 'found', sentence: '' }, corners: { state: 'not_measured', sentence: '' },
    series: [{ path: '/lib/a.fits', elongation: 1.0 }, { path: '/lib/b.fits', elongation: 1.5 }],
    standoutFrames: [{ path: '/lib/b.fits', elongation: 1.5, reason: 'stars stretched' }],
};

function renderBrowser(overrides: Partial<React.ComponentProps<typeof FileBrowser>> = {}) {
    const onPutBackFrame = vi.fn();
    const onSelectFile = vi.fn();
    render(
        <FileBrowser
            files={[item('a.fits'), item('b.fits'), item('c.fits')]}
            rejectedFiles={[]}
            onSelectFile={onSelectFile}
            selectedFile={null}
            searchTerm=""
            onSearchChange={() => {}}
            checkedFiles={new Set()}
            onToggleFile={() => {}}
            onToggleAll={() => {}}
            selectedCamera="All"
            frameStatuses={buildFrameStatusLookup(report, review)}
            setAsideFiles={[{ ...item('d.fits'), path: '/lib/_excluded/d.fits', camera: '' }]}
            onPutBackFrame={onPutBackFrame}
            {...overrides}
        />,
    );
    return { onPutBackFrame, onSelectFile };
}

describe('File Browser Status column', () => {
    it('shows a chip for each status, with the reason on hover', () => {
        renderBrowser();
        expect(screen.getAllByText('Included')).toHaveLength(1);
        expect(screen.getByText('Flagged').closest('[title]')?.getAttribute('title'))
            .toBe('Stars stretched: elongation 1.50, session typical 1.25');
        expect(screen.getByText('Not stacked yet').closest('[title]')?.getAttribute('title')).toMatch(/after the last stack/);
        expect(screen.getByText('Set aside: cloud').closest('[title]')?.getAttribute('title'))
            .toBe('203 stars against a typical 3348');
    });

    it('lists a set-aside frame, and its checkbox cannot be ticked', () => {
        renderBrowser();
        expect(screen.getByText('d.fits')).toBeTruthy();
        expect((screen.getByLabelText('Select d.fits') as HTMLInputElement).disabled).toBe(true);
        expect((screen.getByLabelText('Select a.fits') as HTMLInputElement).disabled).toBe(false);
    });

    it('selects a set-aside frame like any other row', () => {
        const { onSelectFile } = renderBrowser();
        fireEvent.click(screen.getByText('d.fits'));
        expect(onSelectFile).toHaveBeenCalledWith('/lib/_excluded/d.fits');
    });

    it('filters with status:set-aside and status:flagged', () => {
        renderBrowser({ searchTerm: 'status:set-aside' });
        expect(screen.getByText('d.fits')).toBeTruthy();
        expect(screen.queryByText('a.fits')).toBeNull();
    });

    it('filters with status:flagged', () => {
        renderBrowser({ searchTerm: 'status:flagged' });
        expect(screen.getByText('b.fits')).toBeTruthy();
        expect(screen.queryByText('d.fits')).toBeNull();
        expect(screen.queryByText('a.fits')).toBeNull();
    });
});

describe('Put back', () => {
    it('asks first, then puts the frame back', () => {
        const { onPutBackFrame, onSelectFile } = renderBrowser();
        fireEvent.click(screen.getByRole('button', { name: 'Put back' }));
        expect(onPutBackFrame).not.toHaveBeenCalled();
        expect(onSelectFile).not.toHaveBeenCalled();
        expect(screen.getByRole('dialog')).toBeTruthy();
        fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Put back' }));
        expect(onPutBackFrame).toHaveBeenCalledWith('/lib/_excluded/d.fits');
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('does nothing when the person cancels', () => {
        const { onPutBackFrame } = renderBrowser();
        fireEvent.click(screen.getByRole('button', { name: 'Put back' }));
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(onPutBackFrame).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('offers no button for a frame that cannot be put back', () => {
        const limited: FrameStatusReport = {
            frames: [{ path: '/lib/_excluded/d.fits', name: 'd.fits', state: 'set_aside', kind: 'trailed', restorable: false, restoreNote: 'No record of where it came from.' }],
        };
        renderBrowser({ frameStatuses: buildFrameStatusLookup(limited, null) });
        expect(screen.queryByRole('button', { name: 'Put back' })).toBeNull();
        expect(screen.getByText('Set aside: trailed').closest('[title]')?.getAttribute('title')).toMatch(/No record of where it came from/);
    });
});
