
/**
 * @fileoverview Global test setup configuration for Vitest.
 * Extends matchers and defines global fetch mocks to simulate both JSON-RPC 2.0
 * and legacy REST API endpoints during testing.
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

import { vi, beforeAll, afterAll } from 'vitest';
import { spawn, ChildProcess } from 'child_process';
import fs from 'fs';
import net from 'net';
import path from 'path';
import os from 'os';

const nativeFetch = global.fetch;
let backendProcess: ChildProcess | null = null;
let tempDir: string = '';

/**
 * Origin of the spawned test backend, e.g. `http://127.0.0.1:38979`.
 *
 * The fetch mock below uses this to decide which requests to pass through to
 * the real backend instead of answering from its canned responses. It is
 * assigned in `beforeAll` once a port has been reserved; the mock must read
 * it at call time rather than capturing it, since the mock is installed at
 * module scope before the port is known.
 */
let testBackendOrigin = '';

/**
 * Reserves an unused TCP port from the OS.
 *
 * A fixed port made this suite flaky: consecutive runs raced each other for
 * it while a previous backend was still releasing the socket, and parallel
 * CI jobs collided outright. Binding port 0 lets the kernel allocate a free
 * one, which is then handed to the backend via ASTROMETRICS_PORT.
 *
 * @return {Promise<number>} A port number that was free at time of checking.
 */
function reserveFreePort(): Promise<number> {
    return new Promise((resolve, reject) => {
        const probe = net.createServer();
        probe.on('error', reject);
        probe.listen(0, '127.0.0.1', () => {
            const { port } = probe.address() as net.AddressInfo;
            probe.close(() => resolve(port));
        });
    });
}

beforeAll(async () => {
    // 1. Create sandbox directory
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-test-ui-'));

    const libDir = path.join(tempDir, 'libraryIndex');
    const framesDir = path.join(libDir, 'frames');
    fs.mkdirSync(libDir, { recursive: true });
    fs.mkdirSync(framesDir, { recursive: true });

    // Write sandbox config file
    const configPath = path.join(tempDir, 'astrometrics.config');
    fs.writeFileSync(configPath, `[Image Library]
path = ${libDir}
frames_path = ${framesDir}
`);

    // Configure test environment variables
    const backendPort = await reserveFreePort();
    testBackendOrigin = `http://127.0.0.1:${backendPort}`;
    process.env.BACKEND_URL = testBackendOrigin;
    process.env.ASTROMETRICS_CONFIG_PATH = configPath;
    process.env.ASTROMETRICS_TESTING = '1';

    // 2. Spawn the backend process
    const repoRoot = path.resolve(__dirname, '..');
    const venvPythonPath = path.join(repoRoot, '.venv', 'bin', 'python3');
    const pythonPath = fs.existsSync(venvPythonPath) ? venvPythonPath : (process.env.PYTHON_BIN || 'python3');

    backendProcess = spawn(pythonPath, ['-m', 'backend.main_backend'], {
        cwd: repoRoot,
        env: {
            ...process.env,
            ASTROMETRICS_PORT: String(backendPort),
        },
        stdio: 'pipe'
    });

    // Retain stderr so a backend that dies during startup reports *why*,
    // instead of surfacing as an indistinguishable readiness timeout.
    let backendStderr = '';
    backendProcess.stderr?.on('data', (chunk) => {
        backendStderr += String(chunk);
    });

    let exitInfo: string | null = null;
    backendProcess.on('exit', (code, signal) => {
        exitInfo = `backend exited early (code=${code}, signal=${signal})`;
    });

    // Importing astropy/astroquery is slow, and slower still on a loaded CI
    // runner or alongside a parallel pytest run, so allow generous headroom.
    const readinessTimeoutMs = Number(process.env.ASTROMETRICS_TEST_BACKEND_TIMEOUT_MS ?? 60000);

    let ready = false;
    const start = Date.now();
    while (Date.now() - start < readinessTimeoutMs) {
        if (exitInfo) break;
        try {
            const res = await nativeFetch(`http://127.0.0.1:${backendPort}/api/rpc`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonrpc: '2.0',
                    method: 'system:save',
                    params: {},
                    id: 'probe'
                })
            });
            if (res.ok) {
                ready = true;
                break;
            }
        } catch {
            await new Promise(resolve => setTimeout(resolve, 200));
        }
    }

    if (!ready) {
        const elapsedSeconds = ((Date.now() - start) / 1000).toFixed(1);
        const reason = exitInfo ?? `no successful probe within ${elapsedSeconds}s`;
        throw new Error(
            `Test backend server failed to start on port ${backendPort}: ${reason}.\n` +
            `Backend stderr:\n${backendStderr.slice(-4000) || '(none captured)'}`
        );
    }
}, 120000);

afterAll(() => {
    if (backendProcess) {
        backendProcess.kill('SIGTERM');
    }
    try {
        fs.rmSync(tempDir, { recursive: true, force: true });
    } catch {
        // ignore
    }
});

/**
 * Global mock implementation for the fetch API.
 * Supports legacy REST URLs and structured JSON-RPC 2.0 endpoints by parsing request payloads.
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

    // Legacy REST routing fallback
    let jsonResponse: any = [];

    if (urlString.includes('/remote/scan')) {
        jsonResponse = { folders: [] };
    } else if (urlString.includes('/ingest')) {
        jsonResponse = { jobId: 'mock-job-id', status: 'idle', progress: '0%', logs: [] };
    } else if (urlString.includes('/status')) {
        // Generic status or specific
        jsonResponse = { status: 'idle', history: [] };
    } else if (urlString.includes('/targets')) {
        jsonResponse = []; // List of targets
    }

    return Promise.resolve({
        json: () => Promise.resolve(jsonResponse),
        ok: true,
        status: 200,
        statusText: 'OK',
    } as Response);
});
