/**
 * @fileoverview Runs the display flow inside the real Electron app (the default target of the TPM harness) and
 * collects its process metrics.
 *
 * Starts the Electron app (development mode, `electron .`) pointed at the
 * throwaway backend, with `ASTROMETRICS_PERF_METRICS` set so `electron/perf_metrics.js`
 * writes the CPU and memory of every Electron process to a file. With `--visible 1` (the default of
 * `run_tpm.sh`) Electron runs on your real display (keeping `DISPLAY` and `WAYLAND_DISPLAY`), so the page uses the
 * real graphics card; a window opens for the run, without taking focus. With `--visible 0` (`run_tpm.sh --headless`)
 * it runs inside `xvfb-run` (an invisible screen, software graphics) with `WAYLAND_DISPLAY` removed and
 * `--ozone-platform=x11` forced, so no window can appear on your desktop. It then attaches to the Electron window over the Chrome DevTools port, lets
 * the app sit idle for a number of minutes, and runs `flow.mjs` against that
 * window with `--attach-url` (puppeteer.connect). The run is not throttled: `ASTROMETRICS_TPM_HARNESS=1` makes
 * `electron/harness_mode.js` turn off background throttling and show the window without taking focus, and the
 * Chromium switches below keep a hidden or covered window at full speed. From the moment the DevTools port opens until the idle time ends, a second
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
 *        [--pan-seconds 6] [--idle-seconds 30] [--flow-out <dir>] [--ui-dist <built UI folder>] [--visible 0|1] [--ozone auto|x11|wayland] [--gpu auto|intel|nvidia] [--expect-simulator-chip 0|1]
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
 * The size of the invisible screen and window of a `--headless` run. The app's own window is 1280 x 800, and at that
 * width the header's session chip covers the display menu button while a camera is exposing, so the flow's clicks on
 * the menu land on the chip and no display after the first is ever visited. A real laptop panel is wider.
 */
const HEADLESS_WIDTH = 1920;
const HEADLESS_HEIGHT = 1080;

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

    // The real app serves the built UI from its private `app://.` address (electron/app_protocol.js), whose origin the
    // backend's WebSocket check accepts. `--ui-dist` names the production build, and `ASTROMETRICS_UI_DIST` makes
    // main.js serve it the same way. Without `--ui-dist` it falls back to the preview server over HTTP.
    const uiDist = options['ui-dist'] ? path.resolve(options['ui-dist']) : null;
    const flowUiUrl = uiDist ? 'app://.' : options['ui-url'];
    if (uiDist && !fs.existsSync(path.join(uiDist, 'index.html'))) throw new Error(`No built UI at ${uiDist}.`);

    const electronBinary = path.join(repoRoot, 'node_modules', '.bin', 'electron');
    const visible = options.visible === '1';
    const electronArguments = [
        '.',
        `--user-data-dir=${profile}`,
        // Loopback only: the DevTools port is never open to other computers.
        `--remote-debugging-address=127.0.0.1`,
        `--remote-debugging-port=${debugPort}`,
        '--no-sandbox',
        // A hidden or covered window keeps its timers and frames at full speed (see electron/harness_mode.js).
        '--disable-renderer-backgrounding',
        '--disable-backgrounding-occluded-windows',
        '--disable-background-timer-throttling',
    ];
    // A chosen graphics card needs the X11 (XWayland) backend and ANGLE on the system driver (see electron/gpu_choice.js),
    // so it replaces the --ozone hint.
    const gpuChoice = options.gpu ?? 'auto';
    const gpuPlan = gpuLaunchPlan(gpuChoice, process.env, 'electron');
    if (gpuChoice !== 'auto') electronArguments.push(...gpuPlan.switches);
    else if (visible) electronArguments.push(`--ozone-platform-hint=${options.ozone ?? 'auto'}`);
    // Headless (xvfb-run): Electron picks its Wayland backend from WAYLAND_DISPLAY and would then ignore xvfb's
    // DISPLAY and draw on the real desktop, so the variable is removed below and the X11 backend is forced here.
    if (!visible && !electronArguments.includes('--ozone-platform=x11')) electronArguments.push('--ozone-platform=x11');
    const launchEnvironment = { ...gpuPlan.environment };
    if (!visible) {
        delete launchEnvironment.WAYLAND_DISPLAY;
        launchEnvironment.XDG_SESSION_TYPE = 'x11';
    }
    const electron = spawn(
        visible ? electronBinary : 'xvfb-run',
        visible ? electronArguments : ['-a', '-s', `-screen 0 ${HEADLESS_WIDTH}x${HEADLESS_HEIGHT}x24`, electronBinary, ...electronArguments],
        {
            cwd: repoRoot,
            // Its own process group, so the whole tree (xvfb-run, Electron and its helpers) can be stopped together.
            detached: true,
            stdio: ['ignore', 'pipe', 'pipe'],
            env: {
                ...launchEnvironment,
                // Tells the app's start-up check (electron/gpu_startup_check.js) which card to verify.
                ...(gpuChoice === 'auto' ? {} : { ASTROMETRICS_GPU_CHOICE: gpuChoice }),
                ASTROMETRICS_PERF_METRICS: metricsFile,
                // Turns off page throttling and shows the window without focus (electron/harness_mode.js).
                ASTROMETRICS_TPM_HARNESS: '1',
                // Headless only: the main window opens at the size of the invisible screen (see HEADLESS_WIDTH).
                ...(visible ? {} : { ASTROMETRICS_TPM_WINDOW_SIZE: `${HEADLESS_WIDTH}x${HEADLESS_HEIGHT}` }),
                ASTROMETRICS_PORT: backendPort,
                ASTROMETRICS_CONFIG: path.join(sandbox, 'astrometrics.config.toml'),
                ASTROMETRICS_CONFIG_PATH: path.join(sandbox, 'astrometrics.config.toml'),
                ASTROMETRICS_TESTING: '1',
                SKIP_BACKEND: '1',
                // With a built UI folder, main.js serves it from app://. exactly as the packaged app does.
                ...(uiDist ? { ASTROMETRICS_UI_DIST: uiDist } : { ELECTRON_RENDERER_URL: options['ui-url'] }),
                NODE_ENV: 'development',
                XDG_DATA_HOME: path.join(sandbox, 'xdg_data'),
                XDG_CACHE_HOME: path.join(sandbox, 'xdg_cache'),
                XDG_STATE_HOME: path.join(sandbox, 'xdg_state'),
                XDG_CONFIG_HOME: path.join(sandbox, 'xdg_config'),
            },
        },
    );
    let electronLog = '';
    // Everything Electron prints is kept in the results folder, so a failed start explains itself.
    const electronLogFile = path.join(options.out, 'electron-output.log');
    fs.writeFileSync(electronLogFile, '');
    const keepOutput = (chunk) => {
        electronLog += chunk;
        fs.appendFileSync(electronLogFile, chunk);
    };
    electron.stdout.on('data', keepOutput);
    electron.stderr.on('data', keepOutput);
    electron.on('exit', (code, signal) => {
        fs.appendFileSync(electronLogFile, `\n[electron_run] the launched process exited (code ${code}, signal ${signal})\n`);
    });
    fs.writeFileSync(path.join(options.out, 'electron.pid'), String(electron.pid));

    try {
        // Wait for the DevTools port, then for the window to load the UI.
        let ready = false;
        const portWaitSeconds = 60;
        for (let attempt = 0; attempt < portWaitSeconds * 2 && !ready && electron.exitCode === null; attempt += 1) {
            try {
                const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`);
                ready = response.ok;
            } catch {
                await sleep(500);
            }
        }
        if (!ready) {
            const reason = electron.exitCode === null
                ? `did not open its DevTools port (127.0.0.1:${debugPort}) within ${portWaitSeconds} s`
                : `exited with code ${electron.exitCode} before opening its DevTools port`;
            throw new Error(`Electron ${reason}. Its output is saved in ${electronLogFile}. Last lines:\n${electronLog.slice(-3000) || '(no output)'}`);
        }
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
            '--ui-url', flowUiUrl,
            '--backend-url', options['backend-url'],
            // The default target writes the flow's files straight into the results folder; the extra lap that
            // follows a browser flow (--browser with --electron) keeps its own folder.
            '--out', options['flow-out'] ? path.resolve(options['flow-out']) : path.join(options.out, 'electron-flow'),
            '--laps', options.laps ?? '3',
            '--idle-seconds', options['idle-seconds'] ?? '30',
            '--pan-seconds', options['pan-seconds'] ?? '6',
            '--gpu', gpuChoice,
            '--expect-simulator-chip', options['expect-simulator-chip'] ?? '0',
            '--attach-url', `http://127.0.0.1:${debugPort}`,
        ];
        const flow = spawnSync('node', flowArguments, { stdio: 'inherit' });
        if (flow.status !== 0) throw new Error('The flow inside Electron failed.');
    } finally {
        // Stop the whole group: killing only xvfb-run left Electron running, and its open pipes kept this script alive
        // after the flow had finished.
        const stopGroup = (signal) => {
            try {
                process.kill(-electron.pid, signal);
            } catch {
                // The group is already gone.
            }
        };
        stopGroup('SIGTERM');
        await sleep(1500);
        stopGroup('SIGKILL');
    }
    console.log(`Electron metrics: ${metricsFile}`);
}

main().then(() => process.exit(0)).catch((error) => {
    console.error(error);
    process.exit(1);
});
