/**
 * @fileoverview Runs the same lap as flow.mjs inside the real Electron app and collects its process metrics.
 *
 * Starts the Electron app (development mode, `electron .`) pointed at the
 * throwaway backend, with `ASTROMETRICS_PERF_METRICS` set so `electron/perf_metrics.js`
 * writes the CPU and memory of every Electron process to a file. Electron runs
 * inside `xvfb-run` (an invisible screen, so software graphics), so no window
 * appears on your desktop. With `--visible 1` it runs on your real display
 * instead (keeping `DISPLAY` and `WAYLAND_DISPLAY`), so the page can use the
 * real graphics card; a window opens for the run. It then attaches to the Electron window over the Chrome DevTools port, lets
 * the app sit idle for a number of minutes, and runs `flow.mjs` against that
 * window with `--attach-url`. From the moment the DevTools port opens until the idle time ends, a second
 * connection watches every window for console errors, failed requests and toasts (`electron-startup-errors.json`),
 * so the errors of the app's own start-up are counted, not only those of the flow.
 *
 * SAFETY (all of these must hold, or the script stops before starting Electron):
 *  - the backend address is the throwaway backend: on this computer, never port 5000;
 *  - Electron gets its own `--user-data-dir` inside the throwaway folder, so its
 *    single-instance lock is separate and a running copy of the real app is never
 *    handed our launch;
 *  - `SKIP_BACKEND=1`, so Electron starts no backend of its own;
 *  - the XDG folders point inside the throwaway folder, so the app's protocol
 *    registration (`setAsDefaultProtocolClient`) cannot change your desktop settings;
 *  - the settings file and every data folder are the throwaway ones;
 *  - in visible mode the window shows only the throwaway UI, and Electron keeps its own
 *    user-data folder, lock and XDG folders inside the throwaway folder.
 *
 * Usage (normally started by run_tpm.sh with TPM_ELECTRON=1):
 *   node build/tpm/electron_run.mjs --sandbox <throwaway dir> --ui-url http://127.0.0.1:PORT \
 *        --backend-url http://127.0.0.1:PORT --out <results dir> [--idle-minutes 3] [--laps 3] \
 *        [--pan-seconds 6] [--visible 0|1] [--ozone auto|x11|wayland] [--gpu auto|intel|nvidia]
 */

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import { gpuLaunchPlan } from '../../electron/gpu_choice.js';
import { installToastListener, readToasts, watchPage } from './error_watch.mjs';

const repoRoot = path.resolve(import.meta.dirname, '..', '..');
const FORBIDDEN_BACKEND_PORT = 5000;

/**
 * Reads `--name value` pairs from the command line.
 *
 * @param {string[]} argv - The arguments after the script name.
 * @return {Record<string, string>} The values by name.
 */
function parseArguments(argv) {
    const values = {};
    for (let index = 0; index < argv.length; index += 2) values[argv[index].replace(/^--/, '')] = argv[index + 1];
    return values;
}

/**
 * Asks the operating system for an unused port.
 *
 * @return {Promise<number>} The port.
 */
function freePort() {
    return new Promise((resolve, reject) => {
        const probe = net.createServer();
        probe.on('error', reject);
        probe.listen(0, '127.0.0.1', () => {
            const { port } = probe.address();
            probe.close(() => resolve(port));
        });
    });
}

/** Waits a number of milliseconds. */
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

/**
 * Stops the run unless every safety rule holds.
 *
 * @param {Record<string, string>} options - The command-line values.
 * @return {void}
 */
function assertSafe(options) {
    const backend = new URL(options['backend-url']);
    if (!['127.0.0.1', 'localhost'].includes(backend.hostname) || !backend.port || Number(backend.port) === FORBIDDEN_BACKEND_PORT) {
        throw new Error(`Refusing to run: ${options['backend-url']} is not a throwaway backend.`);
    }
    const sandbox = path.resolve(options.sandbox);
    if (!sandbox.startsWith(path.resolve('/tmp')) && !sandbox.startsWith(path.resolve(process.env.TMPDIR ?? '/tmp'))) {
        throw new Error(`Refusing to run: sandbox ${sandbox} is not under the temporary folder.`);
    }
    if (options.visible === '1') {
        if (!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
            throw new Error('--visible needs a graphical session (DISPLAY or WAYLAND_DISPLAY is not set).');
        }
        if (!['auto', 'x11', 'wayland'].includes(options.ozone ?? 'auto')) throw new Error('--ozone must be auto, x11 or wayland.');
    } else if (spawnSync('which', ['xvfb-run']).status !== 0) {
        throw new Error('xvfb-run is needed so Electron does not open a window on your desktop (install the xvfb package).');
    }
}

/**
 * Runs the whole Electron measurement.
 *
 * @return {Promise<void>}
 */
async function main() {
    const options = parseArguments(process.argv.slice(2));
    for (const required of ['sandbox', 'ui-url', 'backend-url', 'out']) {
        if (!options[required]) throw new Error(`Needs --${required}.`);
    }
    assertSafe(options);
    const sandbox = path.resolve(options.sandbox);
    const backendPort = new URL(options['backend-url']).port;
    const debugPort = await freePort();
    const metricsFile = path.join(options.out, 'electron-metrics.jsonl');
    fs.mkdirSync(options.out, { recursive: true });
    const profile = path.join(sandbox, 'electron_profile');
    fs.mkdirSync(profile, { recursive: true });

    const electronBinary = path.join(repoRoot, 'node_modules', '.bin', 'electron');
    const visible = options.visible === '1';
    const electronArguments = ['.', `--user-data-dir=${profile}`, `--remote-debugging-port=${debugPort}`, '--no-sandbox'];
    // A chosen graphics card needs the X11 (XWayland) backend and ANGLE on the system driver (see electron/gpu_choice.js),
    // so it replaces the --ozone hint.
    const gpuChoice = options.gpu ?? 'auto';
    const gpuPlan = gpuLaunchPlan(gpuChoice, process.env, 'electron');
    if (gpuChoice !== 'auto') electronArguments.push(...gpuPlan.switches);
    else if (visible) electronArguments.push(`--ozone-platform-hint=${options.ozone ?? 'auto'}`);
    const electron = spawn(
        visible ? electronBinary : 'xvfb-run',
        visible ? electronArguments : ['-a', electronBinary, ...electronArguments],
        {
            cwd: repoRoot,
            stdio: ['ignore', 'pipe', 'pipe'],
            env: {
                ...gpuPlan.environment,
                // Tells the app's start-up check (electron/gpu_startup_check.js) which card to verify.
                ...(gpuChoice === 'auto' ? {} : { ASTROMETRICS_GPU_CHOICE: gpuChoice }),
                ASTROMETRICS_PERF_METRICS: metricsFile,
                ASTROMETRICS_PORT: backendPort,
                ASTROMETRICS_CONFIG: path.join(sandbox, 'astrometrics.config.toml'),
                ASTROMETRICS_CONFIG_PATH: path.join(sandbox, 'astrometrics.config.toml'),
                ASTROMETRICS_TESTING: '1',
                SKIP_BACKEND: '1',
                ELECTRON_RENDERER_URL: options['ui-url'],
                NODE_ENV: 'development',
                XDG_DATA_HOME: path.join(sandbox, 'xdg_data'),
                XDG_CACHE_HOME: path.join(sandbox, 'xdg_cache'),
                XDG_STATE_HOME: path.join(sandbox, 'xdg_state'),
                XDG_CONFIG_HOME: path.join(sandbox, 'xdg_config'),
            },
        },
    );
    let electronLog = '';
    electron.stdout.on('data', (chunk) => (electronLog += chunk));
    electron.stderr.on('data', (chunk) => (electronLog += chunk));
    fs.writeFileSync(path.join(options.out, 'electron.pid'), String(electron.pid));

    try {
        // Wait for the DevTools port, then for the window to load the UI.
        let ready = false;
        for (let attempt = 0; attempt < 120 && !ready && electron.exitCode === null; attempt += 1) {
            try {
                const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`);
                ready = response.ok;
            } catch {
                await sleep(500);
            }
        }
        if (!ready) throw new Error(`Electron did not open its DevTools port.\n${electronLog.slice(-3000)}`);
        // Watch the start-up (and the idle time) for errors, in every window the app opens.
        const startupRecords = { consoleErrors: [], failedRequests: [], abortedRequests: [] };
        const startupBrowser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${debugPort}`, defaultViewport: null });
        const watchedPages = [];
        const watch = async (page) => {
            watchedPages.push(page);
            watchPage(page, 'electron-startup', startupRecords);
            try {
                await page.evaluate(installToastListener);
            } catch {
                // The page may be between documents; the console and network records still work.
            }
        };
        for (const page of await startupBrowser.pages()) await watch(page);
        startupBrowser.on('targetcreated', async (target) => {
            const page = target.type() === 'page' ? await target.page().catch(() => null) : null;
            if (page) await watch(page);
        });
        await sleep(8000);

        // Idle first, so the sampler records what the app costs when nobody is using it.
        const idleMinutes = Number(options['idle-minutes'] ?? 3);
        await sleep(idleMinutes * 60 * 1000);
        const startupToasts = [];
        for (const page of watchedPages) startupToasts.push(...(await readToasts(page, 'electron-startup')));
        fs.writeFileSync(
            path.join(options.out, 'electron-startup-errors.json'),
            JSON.stringify({ ...startupRecords, toasts: startupToasts }, null, 2),
        );
        await startupBrowser.disconnect();

        const flowArguments = [
            path.join(import.meta.dirname, 'flow.mjs'),
            '--ui-url', options['ui-url'],
            '--backend-url', options['backend-url'],
            '--out', path.join(options.out, 'electron-flow'),
            '--laps', options.laps ?? '3',
            '--idle-seconds', '30',
            '--pan-seconds', options['pan-seconds'] ?? '6',
            '--gpu', gpuChoice,
            '--attach-url', `http://127.0.0.1:${debugPort}`,
        ];
        const flow = spawnSync('node', flowArguments, { stdio: 'inherit' });
        if (flow.status !== 0) throw new Error('The flow inside Electron failed.');
    } finally {
        electron.kill('SIGTERM');
        await sleep(1500);
        if (electron.exitCode === null) electron.kill('SIGKILL');
    }
    console.log(`Electron metrics: ${metricsFile}`);
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
