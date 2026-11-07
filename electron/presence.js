/**
 * @fileoverview Works out whether the user is looking at the app ("present") or not ("away").
 * Only desktop notifications use this answer. Nothing else in the app slows down when the user is away.
 *
 * The user is present when the main window has focus, is not minimized, the screen is not locked,
 * the computer is not asleep, and the user has touched the keyboard or mouse recently.
 * Focus is the main signal. A window that is covered by another one may still report
 * "visible", so visibility alone is not enough. On Wayland and XWayland we only know about
 * our own window's focus, and that is all we need.
 */

/**
 * How long the keyboard and mouse may sit untouched before the user counts as away, in seconds.
 * Two minutes is long enough that reading a chart without touching anything does not count as away,
 * and short enough that a walk to the kitchen does.
 */
export const IDLE_THRESHOLD_SECONDS = 120;

/** How often the idle time is checked, in milliseconds. The check reads one number, so it costs almost nothing. */
export const IDLE_POLL_INTERVAL_MS = 10000;

/**
 * Starts watching the window and the power state.
 * @param {Object} options
 * @param {Object} options.window - The main BrowserWindow (needs on, isFocused, isMinimized, isDestroyed).
 * @param {Object} options.powerMonitor - Electron's powerMonitor (needs on, getSystemIdleTime).
 * @param {Function} options.onChange - Called with 'present' or 'away' each time the answer changes.
 * @param {number} [options.idleThresholdSeconds] - Idle time after which the user counts as away.
 * @param {number} [options.pollIntervalMs] - How often to check the idle time.
 * @returns {{getState: function(): ('present'|'away'), refresh: function(): void, stop: function(): void}}
 *   Handle to read the current answer, force a re-check, or stop watching.
 */
export function startPresenceTracker({
  window: watchedWindow,
  powerMonitor,
  onChange,
  idleThresholdSeconds = IDLE_THRESHOLD_SECONDS,
  pollIntervalMs = IDLE_POLL_INTERVAL_MS,
}) {
  let locked = false;
  let suspended = false;
  let lastState = null;

  /** Reads the window state and returns true if the window is in front of the user. */
  const windowInFront = () => {
    if (!watchedWindow || watchedWindow.isDestroyed()) return false;
    return watchedWindow.isFocused() && !watchedWindow.isMinimized();
  };

  /** Reads the idle time, treating a failed read as "not idle". */
  const idleSeconds = () => {
    try {
      return powerMonitor.getSystemIdleTime();
    } catch {
      return 0;
    }
  };

  /** Combines all signals into 'present' or 'away'. */
  const compute = () => {
    if (locked || suspended) return 'away';
    if (!windowInFront()) return 'away';
    if (idleSeconds() >= idleThresholdSeconds) return 'away';
    return 'present';
  };

  /** Re-checks everything and tells the listener if the answer changed. */
  const refresh = () => {
    const next = compute();
    if (next !== lastState) {
      lastState = next;
      onChange(next);
    }
  };

  const windowEvents = ['focus', 'blur', 'minimize', 'restore', 'show', 'hide'];
  for (const name of windowEvents) watchedWindow.on(name, refresh);

  /** Remembers a power event and re-checks. */
  const onPower = (field, value) => () => {
    if (field === 'locked') locked = value;
    else suspended = value;
    refresh();
  };
  const powerHandlers = {
    'lock-screen': onPower('locked', true),
    'unlock-screen': onPower('locked', false),
    suspend: onPower('suspended', true),
    resume: onPower('suspended', false),
  };
  for (const [name, handler] of Object.entries(powerHandlers)) powerMonitor.on(name, handler);

  const timer = setInterval(refresh, pollIntervalMs);
  if (typeof timer.unref === 'function') timer.unref();
  lastState = compute();

  return {
    getState: () => lastState,
    refresh,
    /** Stops all watching. */
    stop: () => {
      clearInterval(timer);
      for (const [name, handler] of Object.entries(powerHandlers)) powerMonitor.removeListener?.(name, handler);
      if (!watchedWindow.isDestroyed()) {
        for (const name of windowEvents) watchedWindow.removeListener?.(name, refresh);
      }
    },
  };
}
