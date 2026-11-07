/**
 * @fileoverview Checks the test-setup guard that refuses real network calls to the default backend port.
 */

import { describe, it, expect } from 'vitest';

describe('backend network guard (setupTests)', () => {
    it('refuses fetch to the default backend port', () => {
        /** A real-fetch call to 127.0.0.1:5000 must throw, not reach a backend. */
        expect(() => fetch('http://127.0.0.1:5000/api/rpc', { method: 'POST', body: '{}' })).toThrow(/real backend/);
    });

    it('refuses localhost spelling too', () => {
        /** The guard matches every loopback host name. */
        expect(() => fetch('http://localhost:5000/api/rpc')).toThrow(/real backend/);
    });

    it('refuses WebSocket to the default backend port', () => {
        /** Sockets are guarded like fetch. */
        expect(() => new WebSocket('ws://127.0.0.1:5000/ws/events')).toThrow(/real backend/);
    });

    it('still answers other URLs from the canned mock', async () => {
        /** Non-backend URLs keep the existing mocked behaviour. */
        const response = await fetch('http://example.invalid/x');
        expect(response.ok).toBe(true);
    });
});
