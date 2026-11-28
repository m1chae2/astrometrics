/**
 * @file useMountedModes.ts
 * @description Decides which displays (app modes) are mounted, and in what order at start-up.
 *
 * The main window mounts every display at start-up, so the splash screen covers the whole app
 * (see `ui/common/utils/appBootReadiness.ts`). Mounting all of them in one go held up the display
 * the user sees: it waited for every display's code to load, and then the browser was busy building
 * all of them at once. So the shown display mounts first. Once it is on the page, the hidden
 * displays mount when the browser is next idle, as low-priority work that React can pause to keep
 * the shown display responsive. The splash still waits for every display to report its data loaded.
 *
 * An extra window (opened with "Open in new window") only mounts the display it shows, plus any
 * display the user opens later.
 *
 * A display stays mounted once it has been mounted, so going back to it keeps its state.
 */

import { startTransition, useCallback, useEffect, useState } from 'react';
import { runWhenIdle } from '../utils/runWhenIdle';

/**
 * Longest wait, in milliseconds, for the browser to be idle before the hidden displays mount
 * anyway. It is short so the splash screen, which waits for every display, is not held up.
 */
export const HIDDEN_DISPLAYS_IDLE_TIMEOUT_MS = 300;

/** Wait, in milliseconds, before the hidden displays mount where the browser has no idle callback. */
export const HIDDEN_DISPLAYS_FALLBACK_DELAY_MS = 50;

/**
 * Longest wait, in milliseconds, for the shown display to reach the page. If its code fails to
 * load or it crashes, the hidden displays still mount after this, so the splash can finish.
 */
export const SHOWN_DISPLAY_WAIT_LIMIT_MS = 3000;

/** What {@link useMountedModes} gives back. */
export interface MountedModes {
  /** The displays to mount now. */
  mountedModes: ReadonlySet<string>;
  /** Call when a display's content is on the page. The first call lets the hidden displays mount. */
  reportDisplayMounted: () => void;
}

/**
 * Keeps the set of mounted displays.
 *
 * @param allModes Every display, in screen order. Keep the same array between renders.
 * @param shownMode The display the user is looking at.
 * @param mountsEveryMode True in the main window, which mounts every display at start-up.
 * @return The displays to mount, and the function a display calls once it is on the page.
 */
export function useMountedModes(
  allModes: readonly string[],
  shownMode: string,
  mountsEveryMode: boolean,
): MountedModes {
  const [mountedModes, setMountedModes] = useState<ReadonlySet<string>>(() => new Set([shownMode]));
  const [isFirstDisplayMounted, setIsFirstDisplayMounted] = useState(false);

  // A display the user opens is mounted at once and stays mounted.
  useEffect(() => {
    setMountedModes((previousModes) => {
      if (previousModes.has(shownMode)) return previousModes;
      const nextModes = new Set(previousModes);
      nextModes.add(shownMode);
      return nextModes;
    });
  }, [shownMode]);

  const isEveryModeMounted = allModes.every((mode) => mountedModes.has(mode));

  // In the main window, mount the hidden displays once the shown one is on the page.
  useEffect(() => {
    if (!mountsEveryMode || isEveryModeMounted) return undefined;
    /** Adds every display, as low-priority work so React can pause it for the shown display. */
    const mountEveryMode = (): void => {
      startTransition(() => {
        setMountedModes((previousModes) => new Set([...previousModes, ...allModes]));
      });
    };
    if (isFirstDisplayMounted) {
      return runWhenIdle(mountEveryMode, {
        timeoutMs: HIDDEN_DISPLAYS_IDLE_TIMEOUT_MS,
        fallbackDelayMs: HIDDEN_DISPLAYS_FALLBACK_DELAY_MS,
      });
    }
    const waitLimitTimer = window.setTimeout(mountEveryMode, SHOWN_DISPLAY_WAIT_LIMIT_MS);
    return () => window.clearTimeout(waitLimitTimer);
  }, [allModes, mountsEveryMode, isEveryModeMounted, isFirstDisplayMounted]);

  const reportDisplayMounted = useCallback(() => setIsFirstDisplayMounted(true), []);

  return { mountedModes, reportDisplayMounted };
}
