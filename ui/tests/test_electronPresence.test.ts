/**
 * @fileoverview Tests for electron/presence.js, which decides whether the user is looking at the app.
 * The window and the power monitor are fakes, so no Electron is needed.
 */

import { EventEmitter } from 'events';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDLE_THRESHOLD_SECONDS, startPresenceTracker } from '../../electron/presence.js';

/** A fake window that can be focused, blurred and minimized. */
class FakeWindow extends EventEmitter {
  focused = true;
  minimized = false;
  destroyed = false;
  isFocused() { return this.focused; }
  isMinimized() { return this.minimized; }
  isDestroyed() { return this.destroyed; }
}

/** A fake power monitor with a settable idle time. */
class FakePowerMonitor extends EventEmitter {
  idle = 0;
  getSystemIdleTime() { return this.idle; }
}

describe('presence tracker', () => {
  let win: FakeWindow;
  let power: FakePowerMonitor;
  let changes: string[];
  let tracker: ReturnType<typeof startPresenceTracker>;

  beforeEach(() => {
    vi.useFakeTimers();
    win = new FakeWindow();
    power = new FakePowerMonitor();
    changes = [];
    tracker = startPresenceTracker({ window: win, powerMonitor: power, onChange: (s: string) => changes.push(s) });
  });

  afterEach(() => {
    tracker.stop();
    vi.useRealTimers();
  });

  /** A focused window with a recent keystroke is present (the start-up answer is read, not announced). */
  it('starts present when the window is focused', () => {
    expect(tracker.getState()).toBe('present');
    expect(changes).toEqual([]);
  });

  /** Losing and regaining focus flips the answer. */
  it('goes away on blur and returns on focus', () => {
    win.focused = false;
    win.emit('blur');
    expect(tracker.getState()).toBe('away');
    win.focused = true;
    win.emit('focus');
    expect(tracker.getState()).toBe('present');
    expect(changes).toEqual(['away', 'present']);
  });

  /** A minimized window is away even if it still says it has focus. */
  it('is away when minimized', () => {
    win.minimized = true;
    win.emit('minimize');
    expect(tracker.getState()).toBe('away');
    win.minimized = false;
    win.emit('restore');
    expect(tracker.getState()).toBe('present');
  });

  /** Idle time is checked on a timer; under the limit stays present, at the limit is away. */
  it('is away once the idle time reaches the threshold', () => {
    power.idle = IDLE_THRESHOLD_SECONDS - 1;
    vi.advanceTimersByTime(10000);
    expect(tracker.getState()).toBe('present');
    power.idle = IDLE_THRESHOLD_SECONDS;
    vi.advanceTimersByTime(10000);
    expect(tracker.getState()).toBe('away');
    power.idle = 0;
    vi.advanceTimersByTime(10000);
    expect(tracker.getState()).toBe('present');
  });

  /** A locked screen is away; unlocking returns to present. */
  it('is away while the screen is locked', () => {
    power.emit('lock-screen');
    expect(tracker.getState()).toBe('away');
    power.emit('unlock-screen');
    expect(tracker.getState()).toBe('present');
  });

  /** A sleeping computer is away until it resumes. */
  it('is away while suspended', () => {
    power.emit('suspend');
    expect(tracker.getState()).toBe('away');
    power.emit('resume');
    expect(tracker.getState()).toBe('present');
  });

  /** The listener hears only real changes. */
  it('does not repeat an unchanged answer', () => {
    win.emit('focus');
    win.emit('show');
    expect(changes).toEqual([]);
  });
});
