import { useEffect, useSyncExternalStore } from 'react';

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
/** Functions to call when a mode becomes ready, so `useIsModeReady` can update. */
const readyListeners = new Set<() => void>();

/** Lets React watch the ready flags. Returns the function that stops watching. */
function subscribeToReadiness(listener: () => void): () => void {
    readyListeners.add(listener);
    return () => {
        readyListeners.delete(listener);
    };
}

/**
 * Tells whether a mode has reported that its first data is loaded.
 *
 * @param mode The mode's name.
 * @return True once the mode reported (or once the whole app is ready).
 */
export function isModeReady(mode: string): boolean {
    return reported || !pendingModes.has(mode);
}

/**
 * Tells whether every mode has reported that its first data is loaded, that is,
 * whether start-up is over. Toasts and desktop notifications about changing
 * status wait for this, so the first readings after launch are not shown as news.
 *
 * @return True once the whole app has reported ready.
 */
export function isAppReady(): boolean {
    return reported;
}

/**
 * Hook form of `isModeReady`: re-renders when the mode becomes ready.
 * App.tsx uses it to keep a display awake until it has loaded once.
 *
 * @param mode The mode's name.
 * @return True once the mode has reported ready.
 */
export function useIsModeReady(mode: string): boolean {
    return useSyncExternalStore(subscribeToReadiness, () => isModeReady(mode));
}

/**
 * Called by a mode's top-level component once its initial/critical data has
 * loaded. Once every mode has reported, tells the main process so it can
 * dismiss the splash screen and reveal the main window. Safe to call more
 * than once, from an aux window (which only ever mounts one mode, so this
 * never completes there), or before the preload API is available.
 */
export function reportModeReady(mode: string): void {
    if (reported) return;
    const wasPending = pendingModes.delete(mode);
    if (wasPending) readyListeners.forEach((listener) => listener());
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
