/**
 * @fileoverview Tests for the Optic picker in the frame list: the choices it
 * offers, the filtering, the default choice and the mixed-optics warning.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
    buildOpticOptions, filterFilesByOptic, chooseDefaultOptic, getMultiOpticStatus,
} from '../utils/opticSelection';
import { FileBrowserToolbar } from '../fileBrowser/FileBrowser';

/** Make a frame record with a camera and a focal length. */
const frame = (name: string, camera: string, focalLengthMm: number | null) => ({
    path: `/lib/${name}.fits`, camera, focalLengthMm,
});

// M 27: a Nikon through a 300 mm lens and a 405 mm telescope, plus a frame with no focal length.
const m27Frames = [
    frame('a', 'Nikon D5300', 300),
    frame('b', 'Nikon D5300', 300.2),
    frame('c', 'Nikon D5300', 404.6),
    frame('d', 'Nikon D5300', 405),
    frame('e', 'Nikon D5300', 405),
    frame('f', 'ASI533', 405),
    frame('g', 'ASI533', null),
];

describe('buildOpticOptions', () => {
    /** Each distinct whole-mm focal length gets one option with its frame count. */
    it('lists each rounded focal length with its frame count', () => {
        expect(buildOpticOptions(m27Frames)).toEqual([
            { id: '300', label: '300 mm (2 frames)' },
            { id: '405', label: '405 mm (4 frames)' },
        ]);
    });

    /** One optic, or none, means there is nothing to pick. */
    it('returns no options for one optic or none', () => {
        expect(buildOpticOptions([frame('a', 'X', 405), frame('b', 'X', 405.3)])).toEqual([]);
        expect(buildOpticOptions([frame('a', 'X', null)])).toEqual([]);
    });
});

describe('filterFilesByOptic', () => {
    /** An empty choice keeps every frame, including the ones with no focal length. */
    it('keeps all frames for All optics', () => {
        expect(filterFilesByOptic(m27Frames, '')).toHaveLength(7);
    });

    /** A chosen optic keeps only the frames that round to it. */
    it('keeps only the frames of the chosen optic', () => {
        const kept = filterFilesByOptic(m27Frames, '405');
        expect(kept.map(f => f.path)).toEqual([
            '/lib/c.fits', '/lib/d.fits', '/lib/e.fits', '/lib/f.fits',
        ]);
    });
});

describe('chooseDefaultOptic', () => {
    /** With all cameras counted, the optic with the most frames wins. */
    it('picks the optic with the most frames', () => {
        expect(chooseDefaultOptic(m27Frames)).toBe('405');
    });

    /** Only the selected camera's frames are counted. */
    it('counts only the selected camera', () => {
        const frames = [
            frame('a', 'Nikon', 300), frame('b', 'Nikon', 300), frame('c', 'Nikon', 405),
            frame('d', 'ASI', 405), frame('e', 'ASI', 405), frame('f', 'ASI', 405),
        ];
        expect(chooseDefaultOptic(frames, 'Nikon')).toBe('300');
        expect(chooseDefaultOptic(frames, 'All')).toBe('405');
    });

    /** A target with one optic needs no default. */
    it('returns an empty choice when there is only one optic', () => {
        expect(chooseDefaultOptic([frame('a', 'X', 405)])).toBe('');
    });
});

describe('getMultiOpticStatus', () => {
    /** Two optics among the files to stack is flagged. */
    it('flags files from more than one optic', () => {
        expect(getMultiOpticStatus(m27Frames)).toEqual({ hasMultiple: true, focalLengths: [300, 405] });
    });

    /** After picking one optic the status is clear. */
    it('is clear for a single optic', () => {
        expect(getMultiOpticStatus(filterFilesByOptic(m27Frames, '405')))
            .toEqual({ hasMultiple: false, focalLengths: [405] });
    });
});

describe('FileBrowserToolbar optic picker', () => {
    /** The picker offers the optics and reports the choice; All optics reports an empty string. */
    it('reports the chosen optic', async () => {
        const user = userEvent.setup();
        const onOpticChange = vi.fn();
        const files = m27Frames.map(f => ({
            ...f, name: `${f.path}`, camera: f.camera, iso: '', exposure: '60', filter: 'L', date: '2026-11-04',
        }));
        render(
            <FileBrowserToolbar
                files={files}
                searchTerm=""
                onSearchChange={() => {}}
                selectedCamera="All"
                onCameraChange={() => {}}
                selectedOptic="405"
                onOpticChange={onOpticChange}
                checkedFiles={new Set()}
            />,
        );
        await user.click(screen.getByRole('button', { name: 'Optic: 405 mm (4 frames)' }));
        await user.click(screen.getByRole('button', { name: '300 mm (2 frames)' }));
        expect(onOpticChange).toHaveBeenCalledWith('300');
        await user.click(screen.getByRole('button', { name: 'Optic: 405 mm (4 frames)' }));
        await user.click(screen.getByRole('button', { name: 'All optics' }));
        expect(onOpticChange).toHaveBeenLastCalledWith('');
    });

    /** With one optic the picker is hidden. */
    it('hides the picker for one optic', () => {
        render(
            <FileBrowserToolbar
                files={[{ path: '/a', name: 'a', camera: 'X', iso: '', exposure: '1', filter: 'L', date: '', focalLengthMm: 405 }]}
                searchTerm=""
                onSearchChange={() => {}}
                selectedCamera="All"
                onCameraChange={() => {}}
                onOpticChange={() => {}}
                checkedFiles={new Set()}
            />,
        );
        expect(screen.queryByText(/Optic/)).toBeNull();
    });
});
