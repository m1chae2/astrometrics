/**
 * @file test_displayMode.test.ts
 * @description Tests that the viewer state follows the Display choice: Auto Stretch shows the
 * backend's picture, and Linear reads the FITS file itself so the viewer holds the pixel values.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useViewerState } from '../hooks/useViewerState';

const fetchImageByPathMock = vi.fn();
const fetchFitsFileByPathMock = vi.fn();
vi.mock('../../common/services/imaging/imageService', () => ({
    fetchLightFrame: vi.fn(),
    fetchTargetFrameAsFits: vi.fn(),
    fetchImageByPath: (...args: unknown[]) => fetchImageByPathMock(...args),
    fetchFitsFileByPath: (...args: unknown[]) => fetchFitsFileByPathMock(...args),
}));

describe('the display mode of the viewer state', () => {
    afterEach(() => {
        fetchImageByPathMock.mockReset();
        fetchFitsFileByPathMock.mockReset();
    });

    it('starts as Auto Stretch and shows the backend picture', async () => {
        fetchImageByPathMock.mockResolvedValue({ imageData: 'data:image/jpeg;base64,AAAA', min: 0, max: 1 });
        const { result } = renderHook(() => useViewerState('M 31', [], '/s/M_31_Stacked.fits'));
        expect(result.current.displayMode).toBe('auto-stretch');
        await waitFor(() => expect(result.current.imageUrl).toBe('data:image/jpeg;base64,AAAA'));
        expect(fetchImageByPathMock).toHaveBeenCalledWith('/s/M_31_Stacked.fits', true);
        expect(result.current.imageBlob).toBeNull();
    });

    it('reads the FITS file itself in Linear, and goes back to the picture in Auto Stretch', async () => {
        fetchImageByPathMock.mockResolvedValue({ imageData: 'data:image/jpeg;base64,AAAA', min: 0, max: 1 });
        const fitsFile = new Blob(['SIMPLE'], { type: 'application/fits' });
        fetchFitsFileByPathMock.mockResolvedValue(fitsFile);
        const { result } = renderHook(() => useViewerState('M 31', [], '/s/M_31_Stacked.fits'));
        await waitFor(() => expect(result.current.imageUrl).not.toBeNull());

        act(() => result.current.setDisplayMode('linear'));
        await waitFor(() => expect(result.current.imageBlob).toBe(fitsFile));
        expect(result.current.imageUrl).toBeNull();
        expect(fetchFitsFileByPathMock).toHaveBeenCalledWith('/s/M_31_Stacked.fits');

        act(() => result.current.setDisplayMode('auto-stretch'));
        await waitFor(() => expect(result.current.imageUrl).toBe('data:image/jpeg;base64,AAAA'));
        expect(result.current.imageBlob).toBeNull();
    });
});
