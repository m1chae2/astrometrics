/**
 * @fileoverview Tests for the astrometry overlay in Image Processing: the displayed file's path
 * is sent to the backend, and the reason is shown in the viewer header when an image has no sky
 * solution, instead of markers.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchAstrometryOverlayStars } from '../../common/services/astronomyService';
import { ViewerPanelContainer } from '../components/ViewerPanelContainer';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/fitsViewer/FitsViewerManager', () => ({
    FitsViewerManager: () => <div data-testid="viewer" />,
}));
vi.mock('../fileBrowser/FileBrowser', () => ({
    FileBrowser: () => <div />,
    FileBrowserToolbar: () => <div />,
}));
vi.mock('../../common/components/SwitchDisplayButton', () => ({
    SwitchDisplayButton: () => <span />,
}));

/** Renders the viewer panel with the given overlay props. */
function renderPanel(overlayReason: string | null, showAstrometryOverlay = true) {
    return render(
        <ViewerPanelContainer
            fitsRendererRef={{ current: null }}
            imageUrl={null}
            imageBlob={null}
            imageFrameInfo={null}
            loading={false}
            error={null}
            selectedTarget="M 31"
            autoPanTrigger={0}
            disableStretch={false}
            stretch={true}
            toggleStretch={() => {}}
            showAstrometryOverlay={showAstrometryOverlay}
            overlayReason={overlayReason}
            allFiles={[]}
            filteredFiles={[]}
            fileFilterText=""
            setFileFilterText={() => {}}
            fileBrowserCamera="All"
            setFileBrowserCamera={() => {}}
            checkedFiles={new Set()}
            handleRequestDeleteFiles={() => {}}
            onOpenHeaderModal={() => {}}
            onFileClick={() => {}}
            selectedFile={null}
            toggleFile={() => {}}
            toggleAllFiles={() => {}}
            analysisResults={null}
        />
    );
}

describe('astrometry overlay for the displayed image', () => {
    beforeEach(() => callBackendMock.mockReset());

    it('sends the displayed file path with the target', async () => {
        callBackendMock.mockResolvedValue({ stars: [], reason: null });
        await fetchAstrometryOverlayStars('M 31', 35, '/lib/stacks/M31_nikon.fits');
        expect(callBackendMock).toHaveBeenCalledWith('astronomy:get_overlay_stars', {
            target_id: 'M 31',
            limit: 35,
            image_path: '/lib/stacks/M31_nikon.fits',
        });
    });

    it('omits the path when no file is shown, and returns the reason', async () => {
        callBackendMock.mockResolvedValue({ stars: [], reason: 'No sky solution for this image yet' });
        const result = await fetchAstrometryOverlayStars('M 31');
        expect(callBackendMock.mock.calls[0][1]).toEqual({ target_id: 'M 31', limit: 0 });
        expect(result.reason).toBe('No sky solution for this image yet');
    });

    it('shows the reason in the viewer header when the overlay is on', () => {
        renderPanel('No sky solution for this image yet');
        expect(screen.getByTestId('astrometry-overlay-reason').textContent).toBe(
            'No sky solution for this image yet'
        );
    });

    it('shows nothing when there is no reason or the overlay is off', () => {
        const { unmount } = renderPanel(null);
        expect(screen.queryByTestId('astrometry-overlay-reason')).toBeNull();
        unmount();
        renderPanel('No sky solution for this image yet', false);
        expect(screen.queryByTestId('astrometry-overlay-reason')).toBeNull();
    });
});
