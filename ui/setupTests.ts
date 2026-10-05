
/**
 * @fileoverview Global test setup configuration for Vitest.
 * Extends matchers and defines a global fetch mock that simulates JSON-RPC 2.0
 * backend calls during testing.
 */

import '@testing-library/jest-dom';
import { expect } from 'vitest';
import * as matchers from '@testing-library/jest-dom/matchers';

// Extend Vitest's expect with jest-dom matchers
expect.extend(matchers);

// Mock scrollIntoView for jsdom environment compatibility
if (typeof window !== 'undefined') {
  window.Element.prototype.scrollIntoView = vi.fn();
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
