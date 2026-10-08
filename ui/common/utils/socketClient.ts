/**
 * @fileoverview The app's WebSocket connections to the backend.
 *
 * This file and backendApi.ts are the only places the app may open a
 * WebSocket or call `fetch` (an ESLint rule enforces it). `socketClient`
 * keeps the `/ws/events` connection open and turns its messages into events.
 * `openTerminalSocket` opens the plain Python terminal's `/ws/terminal`
 * connection.
 */

import { EventEmitter } from 'events';
import { getBackendBase, resetSessionToken, withSessionToken } from '../services/backendApi';
import { BACKEND_ROUTES } from '../types/backendTypes';

/**
 * Builds the `ws://` address of a backend WebSocket route.
 * @param path The route's path, from `BACKEND_ROUTES`.
 * @return The full address, without the session token.
 */
function backendSocketUrl(path: string): string {
    let base = getBackendBase();
    if (!base) base = 'http://localhost:5000';
    return base.replace(/^http/, 'ws').replace(/\/$/, '') + path;
}

class SocketClient extends EventEmitter {
    private socket: WebSocket | null = null;
    private reconnectTimer: any = null;
    private pingInterval: any = null;

    constructor() {
        super();
    }

    private getWsUrl(): string {
        return backendSocketUrl(BACKEND_ROUTES.eventsSocket);
    }

    public async connect() {
        if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
            return;
        }

        try {
            // The backend rejects handshakes without the session token, so it
            // must be resolved before the socket is opened.
            const url = await withSessionToken(this.getWsUrl());

            // Re-check: an await point means another connect() may have raced
            // us to an open socket while the token was being fetched.
            if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
                return;
            }

            this.socket = new WebSocket(url);
            let hasOpened = false;

            this.socket.onopen = () => {
                hasOpened = true;
                console.log("[Socket] Connected");
                this.emit('connected');
                if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
                this.startHeartbeat();
            };

            this.socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'UI_EVENT') {
                        this.emit('action', data.action, data.payload);
                    }
                } catch (e) {
                    console.error("[Socket] Failed to parse message:", e);
                }
            };

            this.socket.onclose = () => {
                console.log("[Socket] Disconnected");
                // Closing without ever opening means the handshake was refused,
                // most likely because the backend restarted and now holds a
                // different session token. Drop the cached one so the next
                // attempt fetches the current token instead of retrying a dead one.
                if (!hasOpened) resetSessionToken();
                this.cleanup();
                this.scheduleReconnect();
            };

            this.socket.onerror = (err) => {
                console.error("[Socket] Error:", err);
            };

        } catch (e) {
            console.error("[Socket] Connection failed:", e);
            this.scheduleReconnect();
        }
    }

    private startHeartbeat() {
        if (this.pingInterval) clearInterval(this.pingInterval);
        this.pingInterval = setInterval(() => {
            if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                this.socket.send('ping');
            }
        }, 30000); // 30s heartbeat
    }

    private cleanup() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    private scheduleReconnect() {
        this.cleanup();
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => {
            console.log("[Socket] Reconnecting...");
            this.connect();
        }, 5000);
    }
}

export const socketClient = new SocketClient();

/** What the terminal screen does when its socket opens, gets text, or closes. */
export interface TerminalSocketHandlers {
    /** Called once the connection is open. */
    onOpen: () => void;
    /** Called with each text message the backend sends. */
    onMessage: (text: string) => void;
    /** Called when the connection closes, with the WebSocket close code. */
    onClose: (code: number) => void;
    /** Called on a connection error, but not while the socket is closing. */
    onError: () => void;
}

/** An open terminal connection. */
export interface TerminalSocket {
    /**
     * Sends a line of Python to the backend.
     * @return False if the connection is not open, so nothing was sent.
     */
    send: (text: string) => boolean;
    /** Closes the connection and stops calling the handlers. */
    close: () => void;
}

/**
 * Opens the plain Python terminal's WebSocket (`/ws/terminal`).
 *
 * The session token is added to the address first, because the backend
 * refuses a handshake without it.
 * @param handlers What to do when the socket opens, gets text, or closes.
 * @param isCancelled Checked after the token is looked up; when it returns
 *     true, no socket is opened and `null` is returned.
 * @return The open connection, or `null` if it was cancelled.
 */
export async function openTerminalSocket(
    handlers: TerminalSocketHandlers,
    isCancelled: () => boolean = () => false,
): Promise<TerminalSocket | null> {
    const url = await withSessionToken(backendSocketUrl(BACKEND_ROUTES.terminalSocket));
    if (isCancelled()) return null;

    const ws = new WebSocket(url);
    ws.onopen = () => handlers.onOpen();
    ws.onmessage = (event) => handlers.onMessage(event.data);
    ws.onclose = (event) => handlers.onClose(event.code);
    ws.onerror = () => {
        if (ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED) {
            return;
        }
        handlers.onError();
    };

    return {
        send: (text: string) => {
            if (ws.readyState !== WebSocket.OPEN) return false;
            ws.send(text);
            return true;
        },
        close: () => {
            ws.onopen = null;
            ws.onmessage = null;
            ws.onclose = null;
            ws.onerror = null;
            if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                ws.close();
            }
        },
    };
}
