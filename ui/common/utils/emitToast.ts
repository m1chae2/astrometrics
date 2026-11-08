/**
 * @fileoverview Helper utility to emit toast notifications from anywhere in the app.
 * Supports both internal event bus and window CustomEvents.
 */

import { emit } from './eventBus';
import { isAppReady } from './appBootReadiness';
import { getPresence, startPresenceWatch } from './presence';

/** Seconds after the page loads during which no desktop notification is sent. */
export const NOTIFICATION_STARTUP_GRACE_MS = 15000;

/** The time this file was loaded, used for the start-up grace period. */
const loadedAtMs = Date.now();

// Learn the user's presence early so the first alert has an answer.
startPresenceWatch();

/** Allowed types for toast notifications. */
export type ToastKind = 'success' | 'error' | 'info' | 'warning';

/** Extra choices for `emitToast`. */
export interface EmitToastOptions {
  /**
   * True if the user must act on this. It then goes to the desktop only when the user
   * is away from the app; when they are looking at it, the toast is enough.
   * The toast also stays on screen until the user closes it, so a short glance away
   * does not lose it.
   */
  notify?: boolean;
  /** A button on the toast, for a follow-up the user can take (for example "Open"). */
  action?: ToastAction;
}

/** A button shown on a toast. */
export interface ToastAction {
  /** The button text. */
  label: string;
  /** Runs when the user presses the button. */
  run: () => void;
}

/**
 * Emits a toast notification.
 * @param text The message content of the toast.
 * @param kind The severity/type of the toast. Defaults to 'success'.
 * @param source Optional identifier for the origin of the toast.
 * @param options Set `notify` to also send a desktop notification. Do this only
 *   for something the user must act on (guiding lost, a download failed, the mount
 *   could not be parked). A toast never reaches the desktop on its own, and none
 *   does during start-up, or while the user is looking at the app. A device that is offline is a status, not a toast.
 */
export function emitToast(
  text: string,
  kind: ToastKind = 'success',
  source?: string,
  options?: EmitToastOptions
): void {
  try {
    if (kind === 'error') {
      try {
        // A failed user action is expected, so it is logged as a warning, not a console error.
        console.warn(source ? `${source}: ${text}` : text);
      } catch {
        // Ignore console failures.
      }
    }

    // Try the internal event bus.
    try {
      emit('toast', { text, kind, source, sticky: options?.notify === true, action: options?.action });
    } catch {
      // Ignore emission failures.
    }

    // Also dispatch a window CustomEvent for non-module script consumption.
    try {
      window.dispatchEvent(
        new CustomEvent('astrometrics:toast', {
          detail: { text, kind, source, sticky: options?.notify === true, action: options?.action },
        })
      );
    } catch {
      // Ignore event dispatch failures.
    }

    // Send a desktop notification only when the caller says the user must act,
    // never while the app is still starting up, and only when the user is away.
    // An away alert stays until it is dismissed or the user comes back (the main
    // process then closes it). The in-app toast above is shown either way.
    try {
      const started =
        isAppReady() && Date.now() - loadedAtMs >= NOTIFICATION_STARTUP_GRACE_MS;
      if (options?.notify && started && getPresence() === 'away' && typeof window !== 'undefined' && window.astrometrics?.app?.showNotification) {
        const capitalizedSource = source ? source.charAt(0).toUpperCase() + source.slice(1) : 'Observatory Alert';
        const tag = source ? `${source.toLowerCase()}-status` : 'app-alert';
        const isCritical = kind === 'error' || kind === 'warning';
        window.astrometrics.app.showNotification(capitalizedSource, text, {
          urgency: isCritical ? 'critical' : 'normal',
          tag,
          timeoutType: isCritical ? 'never' : 'default',
        });
      }
    } catch {
      // Ignore notification failures.
    }
  } catch {
    // Ignore any failures in the notification path.
  }
}
