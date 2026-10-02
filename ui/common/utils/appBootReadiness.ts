import { useEffect } from 'react';

/**
 * @fileoverview Tracks each top-level mode's "initial data loaded" signal so
 * the Electron main process can be told once every mode has actually
 * finished loading, not just mounted, and dismiss the splash screen only
 * then (see electron/main.js and ui/App.tsx). Module state is naturally
 * scoped per window (each BrowserWindow's renderer is its own JS context),
 * so this only ever completes in the main window, which is the only window
 * App.tsx force-mounts every mode in.
 */

/**
 * Every mode App.tsx's MODE_PANELS force-mounts in the main window at boot.
 * Keep this in sync with MODE_PANELS in ui/App.tsx.
 */
const APP_MODE_NAMES = [
    'Image Viewer',
    'Astronomy Manager',
    'Planetarium',
    'Image Processing',
    'Observatory Manager',
    'Observation Manager',
    'Command Console',
] as const;

const pendingModes = new Set<string>(APP_MODE_NAMES);
let reported = false;

/**
 * Called by a mode's top-level component once its initial/critical data has
 * loaded. Once every mode has reported, tells the main process so it can
 * dismiss the splash screen and reveal the main window. Safe to call more
 * than once, from an aux window (which only ever mounts one mode, so this
 * never completes there), or before the preload API is available.
 */
export function reportModeReady(mode: string): void {
    if (reported) return;
    pendingModes.delete(mode);
    if (pendingModes.size === 0) {
        reported = true;
        window.astrometrics?.app?.reportAppFullyLoaded?.();
    }
}

/**
 * Convenience hook for a mode's top-level component: reports the mode ready
 * as soon as `isReady` becomes true. Pass the same boolean that already
 * gates that view's own "Loading…" placeholder vs. real content.
 */
export function useReportModeReady(mode: string, isReady: boolean): void {
    useEffect(() => {
        if (isReady) reportModeReady(mode);
    }, [mode, isReady]);
}
