/**
 * @fileoverview Tests that a preview remade at the same path is read again: the image viewer
 * hook reloads the open file when it hears the "picture remade" event, and a download of a
 * processed picture asks the server whether the file changed.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useViewerState } from '../hooks/useViewerState';
import { STACK_PICTURE_REMADE_EVENT } from '../../common/services/imaging/stackReviewService';
import { emit } from '../../common/utils/eventBus';
import { fetchImageFile } from '../../common/services/backendApi';

const fetchImageByPathMock = vi.fn();
vi.mock('../../common/services/imaging/imageService', () => ({
    fetchLightFrame: vi.fn(),
    fetchImageByPath: (...args: unknown[]) => fetchImageByPathMock(...args),
}));

describe('a picture remade at the path on screen', () => {
    afterEach(() => {
        fetchImageByPathMock.mockReset();
        vi.restoreAllMocks();
    });

    it('is read again by the viewer when the remade event arrives', async () => {
        fetchImageByPathMock.mockResolvedValue({ imageData: 'data:image/jpeg;base64,AAAA', min: 0, max: 1 });
        const { result } = renderHook(() => useViewerState('M 81', [], '/s/M_81_Stacked.fits'));
        await waitFor(() => expect(result.current.imageUrl).toBe('data:image/jpeg;base64,AAAA'));
        expect(fetchImageByPathMock).toHaveBeenCalledTimes(1);

        fetchImageByPathMock.mockResolvedValue({ imageData: 'data:image/jpeg;base64,BBBB', min: 0, max: 1 });
        act(() => emit(STACK_PICTURE_REMADE_EVENT, { targetId: 'M 81', stackPath: '/s/M_81_Stacked.fits' }));

        await waitFor(() => expect(result.current.imageUrl).toBe('data:image/jpeg;base64,BBBB'));
        expect(fetchImageByPathMock).toHaveBeenCalledTimes(2);
    });
});

describe('fetchImageFile', () => {
    it('lets the browser reuse its copy by default and asks the server when told to revalidate', async () => {
        const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('x'));
        await fetchImageFile('http://x/a.jpg');
        await fetchImageFile('http://x/a.jpg', undefined, true);
        expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: 'default' });
        expect(fetchMock.mock.calls[1][1]).toMatchObject({ cache: 'no-cache' });
    });
});
