import { EventEmitter } from 'events';
import { getBackendBase, withSessionToken } from '../services/backendApi';

class SocketClient extends EventEmitter {
    private socket: WebSocket | null = null;
    private reconnectTimer: any = null;
    private pingInterval: any = null;

    constructor() {
        super();
    }

    private getWsUrl(): string {
        let base = getBackendBase();
        if (!base) base = 'http://localhost:5000';
        return base.replace(/^http/, 'ws').replace(/\/$/, '') + '/ws/events';
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

            this.socket.onopen = () => {
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
