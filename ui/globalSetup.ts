/**
 * @fileoverview Vitest global setup: starts one shared test backend for the whole run.
 *
 * Starting a Python backend is slow (it imports astropy) and heavy. It used to
 * happen once per test file, so a run started up to a dozen at once and
 * loaded the whole machine, including any app running alongside. Now one
 * backend serves the run, and it starts only if a test file that is going to
 * run contains the marker `@requires-test-backend`. The backend uses a
 * throwaway library, a random port and the INDI simulator.
 */

import { spawn, ChildProcess } from 'child_process';
import fs from 'fs';
import net from 'net';
import os from 'os';
import path from 'path';
import type { TestProject } from 'vitest/node';
import { TEST_BACKEND_MARKER } from './testBackendMarker';

declare module 'vitest' {
    export interface ProvidedContext {
        /** Origin of the shared test backend, or an empty string if none was started. */
        testBackendOrigin: string;
    }
}

const repoRoot = path.resolve(__dirname, '..');

/**
 * Lists the test files this run will use, narrowed by any file filters on the command line.
 *
 * @return {string[]} Absolute paths of candidate test files.
 */
function findTestFiles(): string[] {
    const found: string[] = [];
    const walk = (directory: string): void => {
        for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
            if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
            const fullPath = path.join(directory, entry.name);
            if (entry.isDirectory()) walk(fullPath);
            else if (/^test_.*\.(ts|tsx)$/.test(entry.name)) found.push(fullPath);
        }
    };
    for (const root of ['ui', 'tests']) {
        if (fs.existsSync(path.join(repoRoot, root))) walk(path.join(repoRoot, root));
    }
    const filters = process.argv.slice(2).filter((argument) => !argument.startsWith('-') && argument !== 'run');
    return filters.length ? found.filter((file) => filters.some((filter) => file.includes(filter))) : found;
}

/**
 * Reserves an unused TCP port from the OS.
 *
 * A fixed port made this suite flaky: consecutive runs raced each other for it
 * while a previous backend was still releasing the socket. Binding port 0 lets
 * the kernel pick a free one, which is then handed to the backend.
 *
 * @return {Promise<number>} A port number that was free at time of checking.
 */
function reserveFreePort(): Promise<number> {
    return new Promise((resolve, reject) => {
        const probe = net.createServer();
        probe.on('error', reject);
        probe.listen(0, '127.0.0.1', () => {
            const { port } = probe.address() as net.AddressInfo;
            // Avoid TIME_WAIT on Linux by offsetting by 1 from the just-closed socket.
            probe.close(() => resolve(port + 1));
        });
    });
}

/**
 * Starts the backend on a fresh port and waits for it to answer an RPC probe.
 *
 * @param {string} configPath - Sandbox config file to pass via ASTROMETRICS_CONFIG_PATH.
 * @return {Promise<{ process: ChildProcess; port: number; addressInUse: boolean }>} The process, the
 *   port tried, and whether startup failed only because that port was already taken (worth a retry).
 */
async function trySpawnBackend(
    configPath: string,
): Promise<{ process: ChildProcess; port: number; addressInUse: boolean }> {
    const port = await reserveFreePort();
    const venvPythonPath = path.join(repoRoot, '.venv', 'bin', 'python3');
    const pythonPath = fs.existsSync(venvPythonPath) ? venvPythonPath : process.env.PYTHON_BIN || 'python3';

    const backend = spawn(pythonPath, ['-m', 'backend.main_backend'], {
        cwd: repoRoot,
        env: {
            ...process.env,
            ASTROMETRICS_CONFIG_PATH: configPath,
            ASTROMETRICS_TESTING: '1',
            ASTROMETRICS_PORT: String(port),
        },
        stdio: 'pipe',
    });

    // Keep stderr so a backend that dies during startup says why.
    let backendStderr = '';
    backend.stderr?.on('data', (chunk) => {
        backendStderr += String(chunk);
    });
    let exitInfo: string | null = null;
    backend.on('exit', (code, signal) => {
        exitInfo = `backend exited early (code=${code}, signal=${signal})`;
    });

    const readinessTimeoutMs = Number(process.env.ASTROMETRICS_TEST_BACKEND_TIMEOUT_MS ?? 60000);
    const start = Date.now();
    let ready = false;
    while (Date.now() - start < readinessTimeoutMs && !exitInfo) {
        try {
            const response = await fetch(`http://127.0.0.1:${port}/api/rpc`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ jsonrpc: '2.0', method: 'system:save', params: {}, id: 'probe' }),
            });
            if (response.ok) {
                ready = true;
                break;
            }
        } catch {
            await new Promise((resolve) => setTimeout(resolve, 200));
        }
    }

    if (!ready) {
        const addressInUse = backendStderr.includes('address already in use');
        if (!addressInUse) {
            backend.kill('SIGTERM');
            const seconds = ((Date.now() - start) / 1000).toFixed(1);
            throw new Error(
                `Test backend server failed to start on port ${port}: ${exitInfo ?? `no successful probe within ${seconds}s`}.\n` +
                    `Backend stderr:\n${backendStderr.slice(-4000) || '(none captured)'}`,
            );
        }
        backend.kill('SIGTERM');
        return { process: backend, port, addressInUse: true };
    }
    return { process: backend, port, addressInUse: false };
}

/**
 * Starts the shared test backend if any test file in this run needs it.
 *
 * @param {TestProject} project - Vitest project, used to hand the backend's origin to the tests.
 * @return {Promise<() => void>} Teardown that stops the backend and removes its sandbox.
 */
export default async function setup(project: TestProject): Promise<() => void> {
    const needsBackend = findTestFiles().some((file) => fs.readFileSync(file, 'utf8').includes(TEST_BACKEND_MARKER));
    if (!needsBackend || process.env.NO_TEST_BACKEND) {
        project.provide('testBackendOrigin', '');
        return () => {};
    }

    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-test-ui-'));
    const libraryDirectory = path.join(tempDir, 'library');
    const framesDirectory = path.join(libraryDirectory, 'frames');
    fs.mkdirSync(framesDirectory, { recursive: true });
    const configPath = path.join(tempDir, 'astrometrics.config.toml');
    fs.writeFileSync(configPath, `["Image Library"]\npath = "${libraryDirectory}"\nframes_path = "${framesDirectory}"\n`);

    let backend: ChildProcess | null = null;
    let port = -1;
    const maxAttempts = 3;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        const result = await trySpawnBackend(configPath);
        backend = result.process;
        port = result.port;
        if (!result.addressInUse) break;
        if (attempt === maxAttempts) {
            throw new Error(`Test backend could not bind a free port after ${maxAttempts} attempts.`);
        }
    }

    project.provide('testBackendOrigin', `http://127.0.0.1:${port}`);
    return () => {
        backend?.kill('SIGTERM');
        fs.rmSync(tempDir, { recursive: true, force: true });
    };
}
