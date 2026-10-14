/**
 * @fileoverview The error that `callBackend` throws when the backend reports a failure.
 *
 * The backend sends every failure as an `ErrorInfo` record inside the JSON-RPC
 * `error.data` field. `BackendError` keeps that record so the UI can decide
 * what to show from the error's code instead of reading its message text.
 */

import type { ErrorInfo } from '../types/backendTypes';

/** The error codes the backend sends. */
export type BackendErrorCode =
    | 'invalid_argument'
    | 'not_found'
    | 'conflict'
    | 'permission_denied'
    | 'configuration'
    | 'storage'
    | 'hardware'
    | 'external_service'
    | 'processing'
    | 'internal';

/** A failure reported by the backend. */
export class BackendError extends Error {
    /** The error code, such as `not_found` or `hardware`. */
    readonly code: string;
    /** Facts about the error, such as the name of the target. */
    readonly details: Record<string, unknown>;
    /** Whether the same call may succeed if tried again later. */
    readonly retryable: boolean;
    /** The id of the failed call. The backend writes it on every log line of the call. */
    readonly requestId: string | null;

    /**
     * @param info The error record from the backend.
     */
    constructor(info: Pick<ErrorInfo, 'code' | 'message'> & Partial<ErrorInfo>) {
        super(info.message);
        this.name = 'BackendError';
        this.code = info.code;
        this.details = info.details ?? {};
        this.retryable = info.retryable ?? false;
        this.requestId = info.requestId ?? null;
    }
}

/** How the UI shows an error: the text and the toast kind. */
export interface ErrorPresentation {
    text: string;
    kind: 'error' | 'warning';
}

/**
 * Decides how a backend error is shown. This is the one place that does so.
 *
 * - A bad argument is a warning that shows the backend's sentence, because the
 *   user can fix it.
 * - A retryable error says that trying again may work.
 * - An internal error shows the backend's generic sentence, which carries the
 *   request id a developer can look up in the log.
 *
 * @param error The error to show.
 * @return The text and kind of the message.
 */
export function presentBackendError(error: BackendError): ErrorPresentation {
    if (error.code === 'invalid_argument') {
        return { text: error.message, kind: 'warning' };
    }
    if (error.retryable) {
        return { text: `${error.message} Trying again may work.`, kind: 'error' };
    }
    return { text: error.message, kind: 'error' };
}
