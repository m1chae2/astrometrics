/**
 * @fileoverview Start-up check of which graphics card Electron really got.
 *
 * The launcher (`build/linux/run_astrometrics.sh`) or the TPM harness asks for a card by setting the environment
 * variable `ASTROMETRICS_GPU_CHOICE` (`intel` or `nvidia`) and the matching switches. This file asks Electron what it
 * got, writes that to the main-process log, and warns when the answer is wrong. If NVIDIA was asked for and the app
 * did not get it (or the GPU process crashed), it restarts the app once on Intel so the window never stays on
 * broken or software graphics.
 */

import { appLaunchPlan, classifyRenderer, ALL_HARDWARE_GL_SWITCHES } from './gpu_choice.js';

/** Set in the environment of the restarted app so that it never restarts a second time. */
export const FALLBACK_MARKER = 'ASTROMETRICS_GPU_FALLBACK';

/**
 * Picks the best renderer text out of what Electron reports.
 *
 * @param {object} info - The result of `app.getGPUInfo('complete')`.
 * @return {string | null} The OpenGL renderer string when there is one, otherwise the description of the active device.
 */
export function rendererFromGpuInfo(info) {
    const glRenderer = info?.auxAttributes?.glRenderer;
    if (glRenderer) return String(glRenderer);
    const devices = info?.gpuDevice ?? [];
    const active = devices.find((device) => device.active) ?? devices[0];
    if (!active) return null;
    if (active.vendorId === 0x10de) return `NVIDIA (device ${active.deviceString ?? active.deviceId})`;
    if (active.vendorId === 0x8086) return `Intel (device ${active.deviceString ?? active.deviceId})`;
    return active.deviceString ?? null;
}

/**
 * Asks a hidden page which card WebGL really draws on. This is the proof that counts: Electron's own GPU
 * description can name the NVIDIA card as "active" while the page is in fact drawn on Intel.
 *
 * @param {typeof Electron.BrowserWindow} BrowserWindow - Electron's window class.
 * @return {Promise<string | null>} The unmasked WebGL renderer, or null when WebGL is not available.
 */
export async function probeWebglRenderer(BrowserWindow) {
    const probeWindow = new BrowserWindow({ show: false, webPreferences: { sandbox: true } });
    try {
        await probeWindow.loadURL('data:text/html,<canvas></canvas>');
        return await probeWindow.webContents.executeJavaScript(`(() => {
            const gl = document.createElement('canvas').getContext('webgl2') || document.createElement('canvas').getContext('webgl');
            if (!gl) return null;
            const info = gl.getExtension('WEBGL_debug_renderer_info');
            return String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
        })()`);
    } finally {
        probeWindow.destroy();
    }
}

/**
 * Restarts the app on Intel, once.
 *
 * @param {Electron.App} app - The Electron app.
 * @param {NodeJS.ProcessEnv} environment - The environment of this process; it is changed so the new process inherits it.
 * @param {string[]} argv - The command-line arguments of this process.
 * @param {{warn: Function, info: Function}} logger - Where to write.
 * @return {void}
 */
function relaunchOnIntel(app, environment, argv, logger) {
    logger.warn('GPU: restarting once on the Intel graphics card.');
    const intelPlan = appLaunchPlan('intel', environment);
    for (const name of Object.keys(environment)) {
        if (!(name in intelPlan.environment)) delete environment[name];
    }
    environment.ASTROMETRICS_GPU = 'intel';
    environment.ASTROMETRICS_GPU_CHOICE = 'intel';
    environment[FALLBACK_MARKER] = '1';
    const args = argv.slice(1).filter((argument) => !ALL_HARDWARE_GL_SWITCHES.includes(argument));
    app.relaunch({ args });
    app.quit();
    // If a quit handler holds the app open, leave anyway: the new copy is already queued.
    setTimeout(() => app.exit(0), 5000).unref();
}

/**
 * Checks the card and logs the result. Call once, after the app is ready.
 *
 * @param {Electron.App} app - The Electron app.
 * @param {object} [options] - Replaceable parts, for tests.
 * @param {NodeJS.ProcessEnv} [options.environment] - The environment.
 * @param {string[]} [options.argv] - The command-line arguments.
 * @param {{warn: Function, info: Function}} [options.logger] - Where to write.
 * @param {typeof Electron.BrowserWindow} [options.BrowserWindow] - Electron's window class; when given, a hidden page
 *   asks WebGL which card it uses (the most reliable answer).
 * @return {Promise<{requested: string | null, kind: string, renderer: string | null, relaunched: boolean}>} What was found.
 */
export async function checkGpuAtStartup(app, { environment = process.env, argv = process.argv, logger = console, BrowserWindow = null } = {}) {
    const requested = environment.ASTROMETRICS_GPU_CHOICE || null;
    let renderer = null;
    let softwareCompositing = false;
    try {
        renderer = BrowserWindow ? await probeWebglRenderer(BrowserWindow) : null;
        // Without WebGL (or without a window class) fall back to what Electron says about its devices.
        renderer = renderer ?? rendererFromGpuInfo(await app.getGPUInfo('complete'));
        const status = typeof app.getGPUFeatureStatus === 'function' ? app.getGPUFeatureStatus() : {};
        softwareCompositing = /software/.test(String(status.gpu_compositing ?? '')) || /software/.test(String(status.webgl ?? ''));
    } catch (error) {
        logger.warn(`GPU: could not read the graphics information: ${error?.message ?? error}`);
    }
    const kind = classifyRenderer(renderer);
    logger.info(`GPU: requested ${requested ?? 'nothing (Chromium default)'}, renderer "${renderer}" (${kind}).`);

    let relaunched = false;
    if (requested && kind !== requested) {
        logger.warn(`GPU WARNING: ${requested} was requested, but Electron is drawing on "${renderer}" (${kind}).`);
        if (requested === 'nvidia' && environment[FALLBACK_MARKER] !== '1') {
            relaunchOnIntel(app, environment, argv, logger);
            relaunched = true;
        }
    } else if (requested && softwareCompositing) {
        logger.warn('GPU WARNING: Electron reports software compositing or WebGL on the requested card.');
    }

    // A crashed GPU process also means broken graphics: restart on Intel when NVIDIA was the choice.
    if (typeof app.on === 'function') {
        app.on('child-process-gone', (_event, details) => {
            if (details?.type !== 'GPU') return;
            logger.warn(`GPU WARNING: the GPU process ended (${details.reason}).`);
            if (requested === 'nvidia' && environment[FALLBACK_MARKER] !== '1') relaunchOnIntel(app, environment, argv, logger);
        });
    }
    return { requested, kind, renderer, relaunched };
}
