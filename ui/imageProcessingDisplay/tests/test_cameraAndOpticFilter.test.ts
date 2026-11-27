/**
 * @fileoverview Tests that the frames the Stack button uses match the frames
 * the File Browser shows: camera, optic and search text all apply together.
 */

import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

/** Make a frame record the backend would send for M 27. */
const frame = (name: string, camera: string, focalLengthMm: number) => ({
    path: `/lib/${name}.fits`, name: `${name}.fits`, camera, gain: null, exposureS: 60, filter: 'L',
    date: '2026-11-04', focalLengthMm,
});

// M 27: the Nikon shot 3 frames at 300 mm and 5 at 405 mm; the ZWO shot 2 at 405 mm.
const m27Files = [
    ...[1, 2, 3].map(n => frame(`nikon300_${n}`, 'Nikon DSLR DSC D5300', 300)),
    ...[1, 2, 3, 4, 5].map(n => frame(`nikon405_${n}`, 'Nikon DSLR DSC D5300', 405)),
    ...[1, 2].map(n => frame(`zwo405_${n}`, 'ZWO ASI 533MM Pro', 405)),
];

vi.mock('../../common/services/targetService', () => ({
    fetchTargetFiles: vi.fn(async () => ({ files: m27Files })),
}));
vi.mock('../../common/services/imaging/imageService', () => ({ deleteFiles: vi.fn() }));
vi.mock('../../common/utils/emitToast', () => ({ emitToast: vi.fn() }));

import { useTargetFilesLogic } from '../hooks/useTargetFilesLogic';
import { getMultiOpticStatus } from '../utils/opticSelection';

/** Render the hook for M 27 and wait for its files to load. */
const renderLoaded = async () => {
    const rendered = renderHook(() => useTargetFilesLogic('M 27', 0));
    await waitFor(() => expect(rendered.result.current.allFiles).toHaveLength(10));
    return rendered;
};

describe('useTargetFilesLogic camera and optic filters', () => {
    /** With every camera shown, the frames are not limited by camera. */
    it('keeps every camera for All, defaulting to the optic with most frames', async () => {
        const { result } = await renderLoaded();
        expect(result.current.selectedCamera).toBe('All');
        expect(result.current.selectedOptic).toBe('405');
        expect(result.current.filteredFiles).toHaveLength(7);
    });

    /** Camera Nikon with the 405 mm optic gives the five Nikon 405 mm frames, one optic. */
    it('sends only the chosen camera and optic', async () => {
        const { result } = await renderLoaded();
        act(() => { result.current.setSelectedCamera('Nikon DSLR DSC D5300'); });
        expect(result.current.selectedOptic).toBe('405');
        expect(result.current.filteredFiles).toHaveLength(5);
        expect(new Set(result.current.filteredFiles.map(f => f.camera)).size).toBe(1);
        expect(getMultiOpticStatus(result.current.filteredFiles).hasMultiple).toBe(false);
    });

    /** Option counts are for the selected camera. */
    it('counts optic frames for the selected camera only', async () => {
        const { result } = await renderLoaded();
        act(() => { result.current.setSelectedCamera('Nikon DSLR DSC D5300'); });
        expect(result.current.opticOptions.map(option => option.label))
            .toEqual(['300 mm (3 frames)', '405 mm (5 frames)']);
    });

    /** Choosing All optics for the Nikon leaves two optics, so Stack must ask for one. */
    it('reports two optics for the Nikon when All optics is chosen', async () => {
        const { result } = await renderLoaded();
        act(() => { result.current.setSelectedCamera('Nikon DSLR DSC D5300'); });
        act(() => { result.current.setSelectedOptic(''); });
        expect(result.current.filteredFiles).toHaveLength(8);
        expect(getMultiOpticStatus(result.current.filteredFiles).hasMultiple).toBe(true);
    });

    /** A camera with one optic has no picker and an empty optic choice. */
    it('has no optic choice for a camera with one optic', async () => {
        const { result } = await renderLoaded();
        act(() => { result.current.setSelectedCamera('ZWO ASI 533MM Pro'); });
        expect(result.current.opticOptions).toEqual([]);
        expect(result.current.selectedOptic).toBe('');
        expect(result.current.filteredFiles).toHaveLength(2);
    });

    /** Changing camera forgets an optic picked for the old camera. */
    it('returns to the default optic when the camera changes', async () => {
        const { result } = await renderLoaded();
        act(() => { result.current.setSelectedCamera('Nikon DSLR DSC D5300'); });
        act(() => { result.current.setSelectedOptic('300'); });
        expect(result.current.filteredFiles).toHaveLength(3);
        act(() => { result.current.setSelectedCamera('All'); });
        expect(result.current.selectedOptic).toBe('405');
    });
});
