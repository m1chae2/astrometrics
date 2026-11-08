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

/** A file path in a message: slash-separated parts (spaces allowed) that end in a file name with an extension. */
const FILE_PATH_PATTERN = /(?:\/[^/\n]+)+\/([^/\n]*\.[A-Za-z0-9]{1,5})\b/g;

/**
 * Makes a backend message short for a toast. A "not found" message about a file
 * becomes "Couldn't open <file name>", and any other full file path in a message
 * is cut down to its file name. The full text goes to the console.
 *
 * @param error The error whose message is shortened.
 * @return The short text; the message unchanged when it holds no file path.
 */
function shortenFilePaths(error: BackendError): string {
    const message = error.message;
    FILE_PATH_PATTERN.lastIndex = 0;
    const match = FILE_PATH_PATTERN.exec(message);
    FILE_PATH_PATTERN.lastIndex = 0;
    if (!match) {
        return message;
    }
    console.warn(`Backend error (${error.code}): ${message}`, error.details);
    if (error.code === 'not_found' || /not found/i.test(message)) {
        return `Couldn't open ${match[1]}`;
    }
    return message.replace(FILE_PATH_PATTERN, '$1');
}

/**
 * Decides how a backend error is shown. This is the one place that does so.
 *
 * - A bad argument is a warning that shows the backend's sentence, because the
 *   user can fix it.
 * - A retryable error says that trying again may work.
 * - A message that holds a full file path shows only the file name; the full
 *   path goes to the console.
 * - An internal error shows the backend's generic sentence, which carries the
 *   request id a developer can look up in the log.
 *
 * @param error The error to show.
 * @return The text and kind of the message.
 */
export function presentBackendError(error: BackendError): ErrorPresentation {
    const message = shortenFilePaths(error);
    if (error.code === 'invalid_argument') {
        return { text: message, kind: 'warning' };
    }
    if (error.retryable) {
        return { text: `${message} Trying again may work.`, kind: 'error' };
    }
    return { text: message, kind: 'error' };
}
