/**
 * @fileoverview Centralized error reporter for the application.
 * Logs errors to the console and emits notifications to the UI via the event bus.
 */

import { emit } from './eventBus';

/**
 * Reports an error by logging it and notifying the UI layer.
 * @param err The error object or message to report.
 * @param source Optional string identifying the origin of the error.
 */
export function reportError(err: unknown, source?: string): void {
  try {
    // Keep developer-visible console output for debugging.
    console.error(source ? `${source}:` : 'Error:', err);
  } catch {
    // Ignore console failures.
  }

  try {
    const text = err instanceof Error ? err.message : String(err ?? 'Unknown error');

    // Notify React-side listeners via the eventBus.
    try {
      emit('toast', { text, kind: 'error', source: source ?? 'app' });
    } catch {
      // Ignore emission failures.
    }

    // Also dispatch a window CustomEvent so non-module code can trigger toasts.
    try {
      window.dispatchEvent(
        new CustomEvent('astrometrics:toast', {
          detail: { text, kind: 'error', source },
        })
      );
    } catch {
      // Ignore event dispatch failures.
    }
  } catch {
    // Ignore any reporting failures.
  }
}
