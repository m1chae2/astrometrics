/**
 * @fileoverview Tells the app whether the user is looking at it ("present") or not ("away").
 * Only desktop notifications use this. Nothing else slows down when the user is away.
 * In the desktop app the answer comes from the Electron main process (window focus, idle time,
 * screen lock). In a plain browser it comes from the page's own focus and visibility.
 */

/** Whether the user is looking at the app. */
export type Presence = 'present' | 'away';

/** The latest answer from the desktop app, or null until it has answered. */
let desktopPresence: Presence | null = null;

/** True once the desktop subscription has been set up. */
let subscribed = false;

/** Sets up the desktop subscription the first time it is needed. */
function subscribeOnce(): void {
  if (subscribed) return;
  const app = typeof window !== 'undefined' ? window.astrometrics?.app : undefined;
  if (!app?.getPresence) return;
  subscribed = true;
  try {
    app.onPresenceChange?.((state) => {
      desktopPresence = state;
    });
    void app.getPresence().then((state) => {
      desktopPresence = state;
    }).catch(() => {});
  } catch {
    // Without an answer we fall back to "away" below, which is the safe side for alerts.
  }
}

/**
 * Says whether the user is looking at the app right now.
 * @returns 'present' or 'away'. Before the desktop app has answered, 'away', so an alert is never lost.
 */
export function getPresence(): Presence {
  if (typeof window === 'undefined') return 'away';
  subscribeOnce();
  if (window.astrometrics?.app?.getPresence) return desktopPresence ?? 'away';
  // Plain browser: no idle information, only focus and visibility.
  const looking = typeof document !== 'undefined' && !document.hidden && document.hasFocus();
  return looking ? 'present' : 'away';
}

/** Starts the desktop subscription early so the first answer is ready. Safe to call many times. */
export function startPresenceWatch(): void {
  if (typeof window !== 'undefined') subscribeOnce();
}

/** Forgets the saved answer. Used by tests. */
export function resetPresenceForTests(): void {
  desktopPresence = null;
  subscribed = false;
}
