/**
 * @fileoverview Tests that the renderer uses the backend address Electron
 * provides, and that calls wait for the backend to be ready before they start.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { getBackendBase, waitForBackendReady, resetBackendReadiness, callBackend } from '../common/services/backendApi';
import * as toastModule from '../common/utils/emitToast';

afterEach(() => {
    delete (window as any).astrometrics;
    window.localStorage.removeItem('backendUrl');
    resetBackendReadiness();
    vi.unstubAllGlobals();
});

describe('getBackendBase', () => {
    it('uses the address Electron provides, not port 5000', () => {
        (window as any).astrometrics = { backend: { baseUrl: 'http://127.0.0.1:43409/' } };
        expect(getBackendBase()).toBe('http://127.0.0.1:43409');
    });

    /** Electron knows the real port, so it beats a stale local-storage override. */
    it('prefers the Electron address over the localStorage override', () => {
        window.localStorage.setItem('backendUrl', 'http://127.0.0.1:5000');
        (window as any).astrometrics = { backend: { baseUrl: 'http://127.0.0.1:43409' } };
        expect(getBackendBase()).toBe('http://127.0.0.1:43409');
    });

    /** Without the bridge, the localStorage override is still used. */
    it('uses the localStorage override when there is no Electron bridge', () => {
        window.localStorage.setItem('backendUrl', 'example.test:8080');
        expect(getBackendBase()).toBe('http://example.test:8080');
    });
});

describe('waitForBackendReady', () => {
    it('does nothing outside Electron', async () => {
        const fetchMock = vi.fn();
        vi.stubGlobal('fetch', fetchMock);
        await waitForBackendReady();
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('polls /api/ready on the provided address until it answers', async () => {
        (window as any).astrometrics = { backend: { baseUrl: 'http://127.0.0.1:43409' } };
        const fetchMock = vi.fn()
            .mockRejectedValueOnce(new TypeError('Failed to fetch'))
            .mockResolvedValue({ ok: true });
        vi.stubGlobal('fetch', fetchMock);
        await waitForBackendReady();
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:43409/api/ready');
    });
});

describe('callBackend before the first answer', () => {
    /** A failed user action still shows its error; a failed read stays quiet. */
    it('toasts a failed write but not a failed read when the backend cannot be reached', async () => {
        const toast = vi.spyOn(toastModule, 'emitToast').mockImplementation(() => {});
        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
        await callBackend('target:get' as any, {} as any, { maxRetries: 0 }).catch(() => {});
        expect(toast).not.toHaveBeenCalled();
        await callBackend('config:update' as any, {} as any).catch(() => {});
        expect(toast).toHaveBeenCalledTimes(1);
        toast.mockRestore();
    });
});
