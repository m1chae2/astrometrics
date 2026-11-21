/**
 * @file test_fileBrowserArrowKeys.test.tsx
 * @description Tests of the File Browser's arrow-key movement: Up and Down open the previous or next file in the
 * order the list shows, stop at the ends, and leave keys alone when a text box or checkbox has the focus.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FileBrowser, FileBrowserToolbar, type FileItem } from '../fileBrowser/FileBrowser';

const item = (name: string): FileItem => ({
    path: `/lib/${name}`, name, camera: 'ZWO', iso: '', exposure: '60', filter: 'L', date: '2026-11-01 21:00:00',
});

/** Renders the browser (and the search box) with the given selection; returns the selection spy. */
function renderBrowser(selectedFile: string | null, files = [item('c.fits'), item('a.fits'), item('b.fits')]) {
    const onSelectFile = vi.fn();
    render(
        <>
            <FileBrowserToolbar
                files={files} searchTerm="" onSearchChange={() => {}} selectedCamera="All"
                onCameraChange={() => {}} checkedFiles={new Set()}
            />
            <FileBrowser
                files={files} rejectedFiles={[]} onSelectFile={onSelectFile} selectedFile={selectedFile}
                searchTerm="" onSearchChange={() => {}} checkedFiles={new Set()} onToggleFile={() => {}}
                onToggleAll={() => {}} selectedCamera="All"
            />
        </>,
    );
    // Sort by name so the list order (a, b, c) differs from the order the files were given in.
    fireEvent.click(screen.getByText(/^Filename/));
    return onSelectFile;
}

const list = () => screen.getByRole('group', { name: /Files/ });

describe('File Browser arrow keys', () => {
    it('moves down and up through the list in its sorted order', () => {
        const onSelectFile = renderBrowser('/lib/a.fits');
        fireEvent.keyDown(list(), { key: 'ArrowDown' });
        expect(onSelectFile).toHaveBeenLastCalledWith('/lib/b.fits');
    });

    it('moves up to the previous file', () => {
        const onSelectFile = renderBrowser('/lib/b.fits');
        fireEvent.keyDown(list(), { key: 'ArrowUp' });
        expect(onSelectFile).toHaveBeenLastCalledWith('/lib/a.fits');
    });

    it('stops at the first and last file', () => {
        const top = renderBrowser('/lib/a.fits');
        fireEvent.keyDown(list(), { key: 'ArrowUp' });
        expect(top).not.toHaveBeenCalled();
    });

    it('stops at the last file', () => {
        const bottom = renderBrowser('/lib/c.fits');
        fireEvent.keyDown(list(), { key: 'ArrowDown' });
        expect(bottom).not.toHaveBeenCalled();
    });

    it('does nothing in the search box or from a checkbox', () => {
        const onSelectFile = renderBrowser('/lib/a.fits');
        fireEvent.keyDown(screen.getByPlaceholderText(/Search files/), { key: 'ArrowDown' });
        fireEvent.keyDown(screen.getByLabelText('Select a.fits'), { key: 'ArrowDown' });
        expect(onSelectFile).not.toHaveBeenCalled();
    });

    it('does nothing when no file is selected', () => {
        const onSelectFile = renderBrowser(null);
        fireEvent.keyDown(list(), { key: 'ArrowDown' });
        expect(onSelectFile).not.toHaveBeenCalled();
    });
});
