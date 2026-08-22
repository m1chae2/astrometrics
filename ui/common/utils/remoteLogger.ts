/**
 * @fileoverview Remote logger utility for the Astrometrics frontend.
 * Captures global JavaScript errors and unhandled Promise rejections,
 * forwarding them to the backend application logger via the unified
 * JSON-RPC callBackend interface (system:frontend_log).
 */

import { callBackend } from '../services/backendApi';

/** Payload shape for a remote log entry. */
interface LogPayload {
    level: 'info' | 'warn' | 'error';
    message: string;
    stack?: string;
    componentStack?: string;
}

/**
 * Sends a structured log payload to the backend via JSON-RPC.
 * Fire-and-forget: failures are swallowed to avoid cascading UI errors.
 *
 * @param payload - The log entry to forward to the server.
 */
export const logRemote = async (payload: LogPayload): Promise<void> => {
    try {
        await callBackend('system:frontend_log', payload);
    } catch {
        // Fallback to console if the backend is unreachable — do not throw.
        console.error('[remoteLogger] Failed to send log to backend:', payload.message);
    }
};

/**
 * Installs global window error and unhandled rejection listeners that forward
 * captured exceptions to the backend log via logRemote.
 * Should be called once at application startup.
 */
export const setupGlobalErrorListener = (): void => {
    window.onerror = (message, source, lineno, colno, error) => {
        logRemote({
            level: 'error',
            message: String(message),
            stack: error?.stack || `${source}:${lineno}:${colno}`,
        });
    };

    window.onunhandledrejection = (event) => {
        logRemote({
            level: 'error',
            message: `Unhandled Promise Rejection: ${event.reason}`,
            stack: String(event.reason),
        });
    };

    console.info('[remoteLogger] Remote logger initialized.');
};
