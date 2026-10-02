/**
 * @fileoverview Centralized error reporter for the application.
 * Logs errors to the console and emits notifications to the UI via emitToast,
 * including emitToast's native-OS-notification path.
 *
 * Convention across the codebase (so this doesn't drift into three parallel
 * error-reporting mechanisms again):
 * - `callBackend` (see `backendApi.ts`) already reports failures itself —
 *   every RPC call toasts on failure unless the caller passes `silent: true`.
 *   Most `catch` blocks around a `callBackend`/service call therefore don't
 *   need to call `reportError` too; a `console.error` there is just
 *   supplementary devtools logging, not the user's only signal.
 * - Call `reportError` for failures `callBackend` can't see: local/non-RPC
 *   errors (e.g. WebGL context creation, parsing, a rejected non-RPC
 *   promise), or a `silent: true` RPC call whose failure you've decided
 *   *should* surface to the user after all.
 * - Don't call `useToast().show(msg, 'error')` directly for a caught error —
 *   it renders the toast but skips emitToast's native-notification path, so
 *   the failure won't produce a GNOME notification when the window is
 *   unfocused, unlike every other error path in the app. Use `reportError`
 *   (or `emitToast` for a non-error toast) instead; `useToast` remains fine
 *   for a component's own deliberate, non-error UI toasts.
 * - Don't call this (or `emitToast`) from inside a render loop or anything
 *   that can fire many times a second (e.g. a per-frame draw error) — there's
 *   no de-duplication for a tight loop, and it will spam toasts. Log to
 *   console there instead until a throttled reporting helper exists.
 */

import { emitToast } from './emitToast';

/**
 * Reports an error: logs the full error object to the console for debugging,
 * then notifies the user via {@link emitToast} (in-app toast, plus a native
 * OS notification when the window is hidden or unfocused).
 * @param err The error object or message to report.
 * @param source Optional string identifying the origin of the error.
 */
export function reportError(err: unknown, source?: string): void {
  try {
    // Keep developer-visible console output for debugging, with the full
    // error object (and its stack) rather than just the message emitToast logs.
    console.error(source ? `${source}:` : 'Error:', err);
  } catch {
    // Ignore console failures.
  }

  const text = err instanceof Error ? err.message : String(err ?? 'Unknown error');
  emitToast(text, 'error', source ?? 'app');
}
