/**
 * @file test_blinkPair.test.tsx
 * @description Tests of the Blink comparison: which frame statuses open it, how the star is found in
 * whichever frame is on screen, the note that names the frame, the backend call, and the toggle that
 * switches the shown frame (and answers the B key) without losing the star.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useBlinkPair, useBlinkPreload } from '../hooks/useBlinkPair';
import { BLINK_START_ZOOM, blinkNote, focusForLoadedFrame, frameName, opensBlink } from '../utils/blinkView';
import type { BlinkPair } from '../../common/types/backendTypes';
import type { FrameStatusInfo } from '../utils/frameStatus';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: async (...args: unknown[]) => callBackendMock(...args),
}));
const preloadMock = vi.fn();
const releaseMock = vi.fn();
vi.mock('../utils/frameLoadCache', () => ({
    preloadFrames: (...args: unknown[]) => preloadMock(...args),
    releaseFrames: () => releaseMock(),
}));

const BAD = '/lib/_excluded/Light_M81_0123.fits';
const TYPICAL = '/lib/Light_M81_0079.fits';

const pair: BlinkPair = {
    found: true, path: BAD, typicalPath: TYPICAL, starX: 400, starY: 300, typicalStarX: 410, typicalStarY: 296,
    turned: true, votes: 40, sessionFrameCount: 90, width: 800, height: 600,
};

const setAside: FrameStatusInfo = {
    kind: 'set-aside', label: 'Set aside: trailed', tone: 'neutral', tooltip: '', filterText: '', restorable: true, restoreNote: '',
};
const included: FrameStatusInfo = { ...setAside, kind: 'included', label: 'Included' };

beforeEach(() => {
    callBackendMock.mockReset();
    preloadMock.mockReset();
    releaseMock.mockReset();
    callBackendMock.mockImplementation(async (method: string) => (method === 'quality:choose_blink_pair' ? pair : null));
});

describe('which frames open Blink', () => {
    it('opens for flagged and set-aside frames only', () => {
        expect(opensBlink('flagged')).toBe(true);
        expect(opensBlink('set-aside')).toBe(true);
        expect(opensBlink('included')).toBe(false);
        expect(opensBlink('not-stacked')).toBe(false);
        expect(opensBlink(undefined)).toBe(false);
    });
});

describe('frameName and the note', () => {
    it('names a frame by the number in its file name', () => {
        expect(frameName(TYPICAL)).toBe('frame 0079');
        expect(frameName('/lib/a.fits')).toBe('a.fits');
    });

    it('says which frame is showing', () => {
        expect(blinkNote(pair, false, 'Set aside: trailed')).toBe('Frame 0123 (set aside: trailed)');
        expect(blinkNote(pair, true, 'Set aside: trailed')).toBe('Typical: frame 0079');
    });
});

describe('the star in the frame on screen', () => {
    it('gives the star of the frame that is loaded, and the start zoom only when asked', () => {
        const first = focusForLoadedFrame(pair, BAD, BLINK_START_ZOOM);
        expect(first).toMatchObject({ x: 400, y: 300, width: 800, height: 600, scale: BLINK_START_ZOOM });
        const typical = focusForLoadedFrame(pair, TYPICAL, undefined);
        expect(typical).toMatchObject({ x: 410, y: 296 });
        expect(typical?.scale).toBeUndefined();
        expect(typical?.key).not.toBe(first?.key);
    });

    it('gives nothing for another file or a pair that was not found', () => {
        expect(focusForLoadedFrame(pair, '/lib/other.fits', undefined)).toBeNull();
        expect(focusForLoadedFrame(pair, null, undefined)).toBeNull();
        expect(focusForLoadedFrame({ ...pair, found: false }, BAD, undefined)).toBeNull();
    });
});

describe('useBlinkPair', () => {
    it('asks nothing for an included frame', async () => {
        const { result } = renderHook(() => useBlinkPair('M 81', TYPICAL, included));
        expect(result.current.applies).toBe(false);
        expect(result.current.shownPath).toBe(TYPICAL);
        expect(callBackendMock).not.toHaveBeenCalled();
    });

    it('asks for the pair of a set-aside frame, keeps both frames loaded and centers the star', async () => {
        const { result } = renderHook(() => useBlinkPair('M 81', BAD, setAside));
        await waitFor(() => expect(result.current.ready).toBe(true));
        expect(callBackendMock).toHaveBeenCalledWith('quality:choose_blink_pair', { target: 'M 81', path: BAD }, { silent: true });
        expect(result.current.framePaths).toEqual([BAD, TYPICAL]);
        expect(result.current.shownPath).toBe(BAD);
        expect(result.current.focusFor(BAD)).toMatchObject({ x: 400, y: 300, scale: BLINK_START_ZOOM });
        expect(result.current.note).toBe('Frame 0123 (set aside: trailed)');
    });

    it('Blink switches the shown frame and keeps the zoom the person has', async () => {
        const { result } = renderHook(() => useBlinkPair('M 81', BAD, setAside));
        await waitFor(() => expect(result.current.ready).toBe(true));

        act(() => result.current.toggle());
        expect(result.current.blinkOn).toBe(true);
        expect(result.current.shownPath).toBe(TYPICAL);
        expect(result.current.note).toBe('Typical: frame 0079');
        // The viewer has not finished loading the typical frame yet: the star of the frame it holds stays, with no new zoom.
        expect(result.current.focusFor(BAD)?.x).toBe(400);
        expect(result.current.focusFor(BAD)?.scale).toBeUndefined();

        expect(result.current.focusFor(TYPICAL)).toMatchObject({ x: 410, y: 296 });
        expect(result.current.focusFor(TYPICAL)?.scale).toBeUndefined();

        act(() => result.current.toggle());
        expect(result.current.blinkOn).toBe(false);
        expect(result.current.shownPath).toBe(BAD);
    });

    it('answers the B key but not while typing in a field', async () => {
        const { result } = renderHook(() => useBlinkPair('M 81', BAD, setAside));
        await waitFor(() => expect(result.current.ready).toBe(true));

        act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b' })); });
        expect(result.current.blinkOn).toBe(true);

        const input = document.createElement('input');
        document.body.appendChild(input);
        act(() => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', bubbles: true })); });
        expect(result.current.blinkOn).toBe(true);
        document.body.removeChild(input);

        act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'B' })); });
        expect(result.current.blinkOn).toBe(false);
    });

    it('says why Blink is off when no pair was found', async () => {
        callBackendMock.mockImplementation(async () => ({ found: false, path: BAD, reason: 'No other frame of this session is in the stack.' }));
        const { result } = renderHook(() => useBlinkPair('M 81', BAD, setAside));
        await waitFor(() => expect(result.current.unavailableReason).toContain('No other frame'));
        expect(result.current.ready).toBe(false);
        expect(result.current.shownPath).toBe(BAD);
        act(() => result.current.toggle());
        expect(result.current.blinkOn).toBe(false);
    });

    it('starts again for a different frame', async () => {
        let picked = BAD;
        const { result, rerender } = renderHook(() => useBlinkPair('M 81', picked, setAside));
        await waitFor(() => expect(result.current.ready).toBe(true));
        act(() => result.current.toggle());
        picked = '/lib/_excluded/Light_M81_0124.fits';
        rerender();
        expect(result.current.blinkOn).toBe(false);
        expect(result.current.ready).toBe(false);
        await waitFor(() => expect(result.current.ready).toBe(true));
    });
});

describe('useBlinkPreload', () => {
    it('keeps both frames loaded for the display mode and lets them go with the pair', () => {
        const { rerender, unmount } = renderHook(({ stretch }) => useBlinkPreload([BAD, TYPICAL], stretch), { initialProps: { stretch: false } });
        expect(preloadMock).toHaveBeenCalledWith([BAD, TYPICAL], false, expect.anything());
        rerender({ stretch: true });
        expect(preloadMock).toHaveBeenLastCalledWith([BAD, TYPICAL], true, expect.anything());
        unmount();
        expect(releaseMock).toHaveBeenCalled();
    });

    it('does nothing without a pair', () => {
        renderHook(() => useBlinkPreload(null, false));
        expect(preloadMock).not.toHaveBeenCalled();
    });
});
