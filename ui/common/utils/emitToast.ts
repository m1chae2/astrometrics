/**
 * @fileoverview Helper utility to emit toast notifications from anywhere in the app.
 * Supports both internal event bus and window CustomEvents.
 */

import { emit } from './eventBus';

/** Allowed types for toast notifications. */
export type ToastKind = 'success' | 'error' | 'info' | 'warning';

/**
 * Emits a toast notification.
 * @param text The message content of the toast.
 * @param kind The severity/type of the toast. Defaults to 'success'.
 * @param source Optional identifier for the origin of the toast.
 */
export function emitToast(
  text: string,
  kind: ToastKind = 'success',
  source?: string
): void {
  try {
    if (kind === 'error') {
      try {
        console.error(source ? `${source}: ${text}` : text);
      } catch {
        // Ignore console errors.
      }
    }

    // Try the internal event bus.
    try {
      emit('toast', { text, kind, source });
    } catch {
      // Ignore emission failures.
    }

    // Also dispatch a window CustomEvent for non-module script consumption.
    try {
      window.dispatchEvent(
        new CustomEvent('astrometrics:toast', {
          detail: { text, kind, source },
        })
      );
    } catch {
      // Ignore event dispatch failures.
    }
  } catch {
    // Ignore any failures in the notification path.
  }
}
