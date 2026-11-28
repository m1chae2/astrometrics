/**
 * @file test_mountedModes.test.tsx
 * @description Tests the start-up mount order of the displays (useMountedModes). The main window
 * mounts the shown display first and the hidden ones only once the shown one is on the page and the
 * browser is idle, or after a time limit if the shown one never gets there. An extra window mounts
 * only the displays it shows.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  HIDDEN_DISPLAYS_FALLBACK_DELAY_MS,
  HIDDEN_DISPLAYS_IDLE_TIMEOUT_MS,
  SHOWN_DISPLAY_WAIT_LIMIT_MS,
  useMountedModes,
} from '../common/hooks/useMountedModes';

/** Every display, in screen order, as App.tsx passes them. */
const ALL_MODES: readonly string[] = ['Image Viewer', 'Planetarium', 'Image Processing'];

/** The idle callback the browser had before a test replaced it. */
const originalIdleCallback = window.requestIdleCallback;
const originalCancelIdleCallback = window.cancelIdleCallback;

beforeEach(() => {
  vi.useFakeTimers();
  // Tests use the timer path unless they put in their own idle callback.
  (window as unknown as { requestIdleCallback?: unknown }).requestIdleCallback = undefined;
});

afterEach(() => {
  vi.useRealTimers();
  window.requestIdleCallback = originalIdleCallback;
  window.cancelIdleCallback = originalCancelIdleCallback;
});

describe('useMountedModes in the main window', () => {
  /** Only the shown display mounts first; the others mount once it is on the page. */
  it('mounts the shown display first and the hidden ones after it is on the page', () => {
    const { result } = renderHook(() => useMountedModes(ALL_MODES, 'Planetarium', true));
    expect([...result.current.mountedModes]).toEqual(['Planetarium']);

    act(() => result.current.reportDisplayMounted());
    expect([...result.current.mountedModes]).toEqual(['Planetarium']);

    act(() => { vi.advanceTimersByTime(HIDDEN_DISPLAYS_FALLBACK_DELAY_MS); });
    expect([...result.current.mountedModes].sort()).toEqual([...ALL_MODES].sort());
  });

  /** A shown display that never reaches the page (its code failed to load) does not keep the others out. */
  it('mounts the hidden displays after the time limit when the shown one never gets on the page', () => {
    const { result } = renderHook(() => useMountedModes(ALL_MODES, 'Image Viewer', true));
    act(() => { vi.advanceTimersByTime(SHOWN_DISPLAY_WAIT_LIMIT_MS - 1); });
    expect([...result.current.mountedModes]).toEqual(['Image Viewer']);
    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current.mountedModes.size).toBe(ALL_MODES.length);
  });

  /** Where the browser has idle callbacks, the hidden displays wait for one, with a short time limit. */
  it('waits for the browser to be idle', () => {
    const idleCallbacks: { callback: () => void; timeout?: number }[] = [];
    window.requestIdleCallback = ((callback: () => void, options?: { timeout?: number }) => {
      idleCallbacks.push({ callback, timeout: options?.timeout });
      return idleCallbacks.length;
    }) as unknown as typeof window.requestIdleCallback;
    window.cancelIdleCallback = vi.fn();

    const { result } = renderHook(() => useMountedModes(ALL_MODES, 'Image Viewer', true));
    act(() => result.current.reportDisplayMounted());
    expect(idleCallbacks).toHaveLength(1);
    expect(idleCallbacks[0].timeout).toBe(HIDDEN_DISPLAYS_IDLE_TIMEOUT_MS);
    expect(result.current.mountedModes.size).toBe(1);

    act(() => idleCallbacks[0].callback());
    expect(result.current.mountedModes.size).toBe(ALL_MODES.length);
  });
});

describe('useMountedModes in an extra window', () => {
  /** An extra window mounts only the display it shows, and keeps one the user opened. */
  it('mounts only the displays it shows', () => {
    const { result, rerender } = renderHook(
      ({ shownMode }) => useMountedModes(ALL_MODES, shownMode, false),
      { initialProps: { shownMode: 'Image Processing' } },
    );
    act(() => result.current.reportDisplayMounted());
    act(() => { vi.advanceTimersByTime(SHOWN_DISPLAY_WAIT_LIMIT_MS); });
    expect([...result.current.mountedModes]).toEqual(['Image Processing']);

    rerender({ shownMode: 'Planetarium' });
    expect([...result.current.mountedModes]).toEqual(['Image Processing', 'Planetarium']);
  });
});
