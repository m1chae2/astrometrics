
/**
 * @fileoverview Global test setup configuration for Vitest.
 * Extends matchers and defines a global fetch mock that simulates JSON-RPC 2.0
 * backend calls during testing.
 */

import '@testing-library/jest-dom';
import { afterEach, expect } from 'vitest';
import { resetHandoffsForTests } from './common/utils/displayHandoffStore';
import * as matchers from '@testing-library/jest-dom/matchers';
import type { TestingLibraryMatchers } from '@testing-library/jest-dom/matchers';

// Extend Vitest's expect with jest-dom matchers
expect.extend(matchers);

/**
 * Tell TypeScript about the jest-dom matchers (`toBeInTheDocument` and so on).
 *
 * Vitest 5 reads custom matcher types only from its own `Matchers<R, T>`
 * interface. jest-dom 7 still declares its types on the old global
 * `jest.Matchers` (and its `/vitest` entry on the old one-parameter
 * `Assertion<T>`), so without this block every jest-dom matcher is a type
 * error. `R` is what the matcher returns, which is what jest-dom expects as
 * its second type parameter.
 */
declare module 'vitest' {
    // eslint-disable-next-line @typescript-eslint/no-empty-object-type
    interface Matchers<R extends void | Promise<void> = void | Promise<void>, T = unknown>
        extends TestingLibraryMatchers<unknown, R> {}
}

// Mock scrollIntoView for jsdom environment compatibility
if (typeof window !== 'undefined') {
  window.Element.prototype.scrollIntoView = vi.fn();
}

/**
 * Stand-in for the browser's ResizeObserver, which jsdom does not have.
 * It never reports a size change; tests that need sizes mock the element sizes.
 * It is installed once here, as a plain global (not a `vi.stubGlobal`), so
 * `vi.unstubAllGlobals()` in a test file cannot remove it.
 */
class ResizeObserverStub {
    /** Starts watching an element; does nothing. */
    observe(): void { /* no-op */ }
    /** Stops watching an element; does nothing. */
    unobserve(): void { /* no-op */ }
    /** Stops watching everything; does nothing. */
    disconnect(): void { /* no-op */ }
}
if (typeof globalThis.ResizeObserver === 'undefined') {
    globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

// We can add custom matchers here later if needed
// expect.extend({
//   toBeProcessing(received) { ... }
// });

import { vi, beforeAll, afterAll, inject } from 'vitest';
import fs from 'fs';
import { TEST_BACKEND_MARKER } from './testBackendMarker';

const nativeFetch = global.fetch;

/**
 * Origin of the shared test backend, e.g. `http://127.0.0.1:38979`, or an empty
 * string for test files that did not ask for one.
 *
 * `ui/globalSetup.ts` starts one backend for the whole run, and only when a test
 * file contains the marker `@requires-test-backend`. The fetch mock below uses
 * this to decide which requests to pass through to the real backend instead of
 * answering from its canned responses. It is read at call time because the mock
 * is installed at module scope, before `beforeAll` runs.
 */
let testBackendOrigin = '';

beforeAll(() => {
    const origin = inject('testBackendOrigin');
    const testPath = expect.getState().testPath;
    if (origin && testPath && fs.readFileSync(testPath, 'utf8').includes(TEST_BACKEND_MARKER)) {
        testBackendOrigin = origin;
        process.env.BACKEND_URL = origin;
    }
});

afterAll(() => {
    testBackendOrigin = '';
    delete process.env.BACKEND_URL;
});

/**
 * Global mock implementation for the fetch API.
 * Answers structured JSON-RPC 2.0 requests by parsing request payloads.
 * @param url Request target URL.
 * @param init Optional request options including HTTP method and request body.
 * @return Mocked fetch response.
 */
global.fetch = vi.fn((url: string | Request | URL, init?: RequestInit) => {
    const urlString = typeof url === 'string' ? url : url.toString();

    // If calling the test backend, route to the real native fetch. Matched
    // against the dynamically reserved origin: hardcoding a port here meant
    // that changing the backend's port silently diverted every request into
    // the canned responses below, which fail only for methods the mock has
    // no case for.
    if (testBackendOrigin && urlString.startsWith(testBackendOrigin)) {
        return nativeFetch(url, init);
    }

    // Handle unified JSON-RPC 2.0 requests
    if (urlString.includes('/api/rpc') && init && init.body) {
        try {
            const body = JSON.parse(init.body as string);
            const method = body.method;

            let rpcData: any = null;
            if (method === 'ingestion:scan') {
                rpcData = { folders: [] };
            } else if (method === 'ingestion:start') {
                rpcData = { jobId: 'mock-job-id' };
            } else if (method === 'ingestion:status') {
                rpcData = { status: 'idle', progress: '0%', logs: [] };
            } else if (method === 'ingestion:stats') {
                rpcData = { fileCount: 0 };
            } else if (method === 'target:list') {
                rpcData = [];
            } else if (method === 'target:get_frames_grouped') {
                rpcData = [];
            } else if (method === 'astronomy:visible') {
                rpcData = [];
            } else if (method === 'telescope:status') {
                rpcData = {
                    ra: 'Unknown',
                    dec: 'Unknown',
                    altitude: 'Unknown',
                    azimuth: 'Unknown',
                    temperature: 'Unknown',
                    humidity: 'Unknown',
                    connectionStatus: 'Disconnected',
                    trackingStatus: 'Not Tracking',
                    focuserPosition: 0,
                    filter: '',
                    guidingHistory: [],
                    alignmentAttempts: [],
                };
            } else if (method === 'guiding:status') {
                rpcData = { status: 'idle', history: [] };
            }

            return Promise.resolve({
                json: () => Promise.resolve({
                    jsonrpc: '2.0',
                    result: {
                        status: 'success',
                        data: rpcData
                    },
                    id: body.id
                }),
                ok: true,
                status: 200,
                statusText: 'OK',
            } as Response);
        } catch (e) {
            // Fall back to general mock logic if parsing fails
        }
    }

    // Anything that is not a JSON-RPC call or a test-backend call gets an empty list.
    const jsonResponse: any = [];

    return Promise.resolve({
        json: () => Promise.resolve(jsonResponse),
        ok: true,
        status: 200,
        statusText: 'OK',
    } as Response);
});

/**
 * Origin of the backend that tests reach by default (`getBackendBase()` falls
 * back to it), which is where a developer's real backend usually runs.
 */
const DEFAULT_BACKEND_PORTS = ['5000'];

/**
 * Tells whether a URL points at a backend port that no test may reach for real.
 *
 * A request is allowed only when it goes to the throwaway backend this run
 * started (`testBackendOrigin`). Everything else aimed at a default backend
 * port on this machine is blocked.
 *
 * @param {string} urlString - The URL a test is trying to open.
 * @return {boolean} True if the request must be refused.
 */
function isForbiddenBackendUrl(urlString: string): boolean {
    if (testBackendOrigin && urlString.startsWith(testBackendOrigin)) return false;
    let parsed: URL;
    try {
        parsed = new URL(urlString);
    } catch {
        return false;
    }
    const isLoopback = ['127.0.0.1', 'localhost', '[::1]', '::1', '0.0.0.0'].includes(parsed.hostname);
    return isLoopback && DEFAULT_BACKEND_PORTS.includes(parsed.port);
}

/**
 * Throws the error used by every guard below.
 *
 * @param {string} urlString - The refused URL.
 */
function refuseBackendAccess(urlString: string): never {
    throw new Error(
        `Test tried to reach a real backend at ${urlString}. Unit tests must mock the network. ` +
            `Tests that need a backend are opt-in: set ASTROMETRICS_LIVE_BACKEND_TESTS=1 and mark the file with ` +
            `${TEST_BACKEND_MARKER}; they then use a throwaway backend started by the run, never a real one.`,
    );
}

// Guard 1: tests that replace fetch with their own mock are fine, but a test that
// restores the real fetch must still not be able to reach the default backend port.
const guardedFetch = global.fetch;
global.fetch = vi.fn((url: string | Request | URL, init?: RequestInit) => {
    const urlString = typeof url === 'string' ? url : url.toString();
    if (isForbiddenBackendUrl(urlString)) refuseBackendAccess(urlString);
    return guardedFetch(url, init);
}) as typeof global.fetch;

// Guard 2: XMLHttpRequest and WebSocket in jsdom do real network I/O.
if (typeof XMLHttpRequest !== 'undefined') {
    const nativeOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (this: XMLHttpRequest, method: string, url: string | URL, ...rest: unknown[]) {
        if (isForbiddenBackendUrl(String(url))) refuseBackendAccess(String(url));
        return (nativeOpen as (...args: unknown[]) => void).call(this, method, url, ...rest);
    } as typeof XMLHttpRequest.prototype.open;
}
if (typeof WebSocket !== 'undefined') {
    const NativeWebSocket = WebSocket;
    class GuardedWebSocket extends NativeWebSocket {
        /** Refuses sockets aimed at a real backend, otherwise behaves like WebSocket. */
        constructor(url: string | URL, protocols?: string | string[]) {
            if (isForbiddenBackendUrl(String(url).replace(/^ws/, 'http'))) refuseBackendAccess(String(url));
            super(url, protocols);
        }
    }
    global.WebSocket = GuardedWebSocket as typeof WebSocket;
}

// A hand-off nobody picked up in one test must not reach the next test.
afterEach(() => {
  resetHandoffsForTests();
});
