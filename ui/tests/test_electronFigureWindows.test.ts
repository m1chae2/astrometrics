/**
 * @fileoverview Unit tests for electron/figure_windows.js. A figure that is
 * asked for twice must give one window, which is reloaded and brought to the
 * front, and a closed window must be forgotten.
 */

import { describe, it, expect, vi } from 'vitest';
import { createFigureWindowRegistry } from '../../electron/figure_windows.js';

/** A stand-in for an Electron window that records what is done to it. */
function makeFakeWindow() {
  const handlers: Record<string, () => void> = {};
  let destroyed = false;
  return {
    isDestroyed: () => destroyed,
    isMinimized: () => false,
    restore: vi.fn(),
    focus: vi.fn(),
    on: (event: string, handler: () => void) => {
      handlers[event] = handler;
    },
    close() {
      destroyed = true;
      handlers.closed?.();
    },
  };
}

/** The stand-in window type. */
type FakeWindow = ReturnType<typeof makeFakeWindow>;

describe('createFigureWindowRegistry', () => {
  /** Two requests for one figure make one window, which is reloaded and focused. */
  it('gives one window for two requests for the same figure', () => {
    const registry = createFigureWindowRegistry();
    const create = vi.fn(makeFakeWindow);
    const reload = vi.fn();
    const first = registry.openOrReuse('http://127.0.0.1:5000/figure/1', create, reload) as FakeWindow;
    const second = registry.openOrReuse('http://127.0.0.1:5000/figure/1', create, reload);
    expect(second).toBe(first);
    expect(create).toHaveBeenCalledTimes(1);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(first.focus).toHaveBeenCalledTimes(1);
  });

  /** Different figures keep separate windows. */
  it('gives separate windows for different figures', () => {
    const registry = createFigureWindowRegistry();
    const a = registry.openOrReuse('/figure/1', makeFakeWindow, vi.fn());
    const b = registry.openOrReuse('/figure/2', makeFakeWindow, vi.fn());
    expect(a).not.toBe(b);
    expect(registry.size()).toBe(2);
  });

  /** After a window closes, the next request opens a new one. */
  it('forgets a window when it closes', () => {
    const registry = createFigureWindowRegistry();
    const first = registry.openOrReuse('/figure/1', makeFakeWindow, vi.fn()) as FakeWindow;
    first.close();
    expect(registry.size()).toBe(0);
    const reload = vi.fn();
    const second = registry.openOrReuse('/figure/1', makeFakeWindow, reload);
    expect(second).not.toBe(first);
    expect(reload).not.toHaveBeenCalled();
  });
});
