/**
 * @file useBlinkPair.ts
 * @description The state behind the Blink button. When the picked frame is Flagged or Set aside, it asks
 * the backend for a typical frame of the same session and one star in both frames, keeps both frames
 * loaded, and says which frame the viewer should show and which star to center. Blink switches the shown
 * frame between the two. The B key does the same. Nothing is asked for a frame that is neither flagged
 * nor set aside, and a frame that cannot be paired is a status (the button is off and says why), not an
 * error.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchBlinkPair } from '../../common/services/imaging/frameReviewService';
import { preloadFrames, releaseFrames } from '../utils/frameLoadCache';
import { BLINK_START_ZOOM, blinkNote, focusForLoadedFrame, opensBlink } from '../utils/blinkView';
import type { BlinkFocus } from '../utils/blinkView';
import type { FrameStatusInfo } from '../utils/frameStatus';
import type { BlinkPair } from '../../common/types/backendTypes';

/** What {@link useBlinkPair} gives back. */
export interface BlinkState {
    /** True when the picked frame is one Blink applies to (flagged or set aside). */
    applies: boolean;
    /** True while the backend is choosing the typical frame and the star. */
    loading: boolean;
    /** True when a typical frame and a star were found, so the button works. */
    ready: boolean;
    /** Why Blink is off for a frame it applies to, or an empty string. */
    unavailableReason: string;
    /** True while the typical frame is shown. */
    blinkOn: boolean;
    /** Switches between the frame and the typical frame. */
    toggle: () => void;
    /** The file the viewer should show: the picked frame, or the typical frame while blinking. */
    shownPath: string | null;
    /**
     * Finds the star to center in the frame the viewer holds now.
     * @param loadedPath The file whose pixels the viewer holds.
     * @return The star, or `null` when that file is not one of the pair.
     */
    focusFor: (loadedPath: string | null) => BlinkFocus | null;
    /** The two files to keep loaded (the frame and the typical frame), or `null` when there is no pair. */
    framePaths: readonly [string, string] | null;
    /** The note that says which frame is showing, or an empty string. */
    note: string;
}

/**
 * Tells whether a key press belongs to a text field, where B must be typed and not taken as Blink.
 * @param target The element that had the focus.
 * @return True for a field a person types in.
 */
function isTypingTarget(target: EventTarget | null): boolean {
    const element = target as HTMLElement | null;
    if (!element || typeof element.tagName !== 'string') return false;
    return element.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName);
}

/**
 * Prepares the Blink comparison for the picked frame.
 * @param targetId The selected target ("" for none).
 * @param pickedPath The frame picked in the File Browser or the timeline, or `null`.
 * @param status The picked frame's status, or `undefined` when it has none.
 * @return The Blink state.
 */
export function useBlinkPair(
    targetId: string,
    pickedPath: string | null,
    status: FrameStatusInfo | undefined,
): BlinkState {
    const applies = Boolean(targetId && pickedPath && opensBlink(status?.kind));
    // The pair is kept with the frame it was made for, so a different pick never shows the last pair.
    const [made, setMade] = useState<{ path: string; pair: BlinkPair | null } | null>(null);
    const [blink, setBlink] = useState<{ path: string; on: boolean; used: boolean }>({ path: '', on: false, used: false });
    const pair = made !== null && made.path === pickedPath ? made.pair : null;
    const loading = applies && (made === null || made.path !== pickedPath);

    useEffect(() => {
        if (!applies || !pickedPath) return undefined;
        let stale = false;
        fetchBlinkPair(targetId, pickedPath).then((answer) => { if (!stale) setMade({ path: pickedPath, pair: answer }); });
        return () => { stale = true; };
    }, [applies, targetId, pickedPath]);

    const foundPair = pair !== null && pair.found && pair.typicalPath ? pair : null;
    const typicalPath = foundPair?.typicalPath ?? null;
    const foundPath = foundPair?.path ?? null;
    const framePaths = useMemo<readonly [string, string] | null>(
        () => (foundPath && typicalPath ? [foundPath, typicalPath] : null),
        [foundPath, typicalPath],
    );

    const blinkOn = foundPair !== null && blink.path === pickedPath && blink.on;
    const toggle = useCallback(() => {
        if (!pickedPath) return;
        setBlink((current) => ({ path: pickedPath, on: current.path === pickedPath ? !current.on : true, used: true }));
    }, [pickedPath]);

    useEffect(() => {
        if (!foundPair) return undefined;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key.toLowerCase() !== 'b' || event.ctrlKey || event.metaKey || event.altKey || isTypingTarget(event.target)) return;
            event.preventDefault();
            toggle();
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [foundPair, toggle]);

    const used = blink.path === pickedPath && blink.used;
    const focusFor = useCallback(
        (loadedPath: string | null) => (foundPair ? focusForLoadedFrame(foundPair, loadedPath, used ? undefined : BLINK_START_ZOOM) : null),
        [foundPair, used],
    );
    const unavailableReason = applies && !loading && foundPair === null
        ? (pair?.reason || 'Blink could not pick a typical frame for this frame.')
        : '';
    return {
        applies,
        loading,
        ready: foundPair !== null,
        unavailableReason,
        blinkOn,
        toggle,
        framePaths,
        shownPath: blinkOn && typicalPath ? typicalPath : pickedPath,
        focusFor,
        note: foundPair ? blinkNote(foundPair, blinkOn, status?.label ?? '') : '',
    };
}

/**
 * Keeps the two frames of a Blink pair loaded, so switching between them is instant. The frames are
 * read again when the display mode changes, because Linear reads the FITS file and Auto Stretch reads the
 * backend's picture. They are let go when the pair goes away.
 * @param framePaths The frame and the typical frame, or `null`.
 * @param stretch True when the viewer is in Auto Stretch.
 */
export function useBlinkPreload(framePaths: readonly [string, string] | null, stretch: boolean): void {
    useEffect(() => {
        if (!framePaths) return undefined;
        const controller = new AbortController();
        preloadFrames(framePaths, stretch, controller.signal);
        return () => { controller.abort(); releaseFrames(); };
    }, [framePaths, stretch]);
}
