/**
 * @file test_blinkButton.test.tsx
 * @description Tests of the Blink button in the viewer toolbar: it shows only for a frame Blink applies
 * to, is off (with the reason) until a pair is found, switches the shown frame when pressed, names the
 * frame on screen in a note, and points the star profile at the frame that is shown.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ViewerPanelContainer } from '../components/ViewerPanelContainer';
import type { BlinkState } from '../hooks/useBlinkPair';

const viewerProps = vi.fn();
vi.mock('../../common/fitsViewer/FitsViewerManager', () => ({
    FitsViewerManager: (props: Record<string, unknown>) => { viewerProps(props); return <div data-testid="viewer" />; },
}));
vi.mock('../fileBrowser/FileBrowser', () => ({
    FileBrowser: () => <div />,
    FileBrowserToolbar: () => <div />,
}));
vi.mock('../../common/components/SwitchDisplayButton', () => ({
    SwitchDisplayButton: () => <span />,
}));

const toggle = vi.fn();
const baseBlink: BlinkState = {
    applies: true, loading: false, ready: true, unavailableReason: '', blinkOn: false, toggle,
    framePaths: ['/lib/bad.fits', '/lib/typical.fits'], shownPath: '/lib/bad.fits', focusFor: () => null,
    note: 'Frame 123 (set aside: trailed)',
};

/** Renders the viewer panel with the given Blink state. */
function renderPanel(blink: BlinkState | undefined, shownFile: string | null = '/lib/bad.fits') {
    return render(
        <ViewerPanelContainer
            fitsRendererRef={{ current: null }}
            detectionsEnabled={false}
            onDetectionsChange={() => undefined}
            imageUrl={null}
            imageBlob={null}
            imageFrameInfo={null}
            loading={false}
            error={null}
            selectedTarget="M 81"
            autoPanTrigger={0}
            disableStretch={false}
            displayMode="linear"
            onDisplayModeChange={() => {}}
            blink={blink}
            shownFile={shownFile}
            focusStar={{ x: 10, y: 20, width: 800, height: 600, key: 'k' }}
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
            selectedFile="/lib/bad.fits"
            toggleFile={() => {}}
            toggleAllFiles={() => {}}
            analysisResults={null}
        />
    );
}

describe('the Blink button', () => {
    beforeEach(() => { toggle.mockReset(); viewerProps.mockReset(); });

    it('is not shown for a frame Blink does not apply to', () => {
        renderPanel({ ...baseBlink, applies: false, note: '' });
        expect(screen.queryByRole('button', { name: 'Blink' })).toBeNull();
        expect(screen.queryByTestId('blink-note')).toBeNull();
    });

    it('switches the frame when pressed and says which frame is showing', () => {
        renderPanel(baseBlink);
        expect(screen.getByTestId('blink-note').textContent).toBe('Frame 123 (set aside: trailed)');
        fireEvent.click(screen.getByRole('button', { name: 'Blink' }));
        expect(toggle).toHaveBeenCalledTimes(1);
    });

    it('names the typical frame while blinking', () => {
        renderPanel({ ...baseBlink, blinkOn: true, note: 'Typical: frame 079' });
        expect(screen.getByTestId('blink-note').textContent).toBe('Typical: frame 079');
    });

    it('is off, with the reason as its hint, when no pair was found', () => {
        renderPanel({ ...baseBlink, ready: false, note: '', unavailableReason: 'No other frame of this session is in the stack.' });
        const button = screen.getByRole('button', { name: 'Blink' }) as HTMLButtonElement;
        expect(button.disabled).toBe(true);
        expect(button.title).toContain('No other frame');
    });

    it('hands the star to the viewer and keeps the Blink frames decoded ahead of time', () => {
        renderPanel(baseBlink);
        const props = viewerProps.mock.calls[viewerProps.mock.calls.length - 1][0] as Record<string, unknown>;
        expect(props.focusStar).toMatchObject({ x: 10, y: 20, key: 'k' });
        expect(props.acceptPrefetch).toBe(true);
    });
});
