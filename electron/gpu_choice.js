/**
 * @fileoverview The graphics card choice (`auto`, `intel` or `nvidia`) for Chromium-based programs.
 *
 * This is the one place that says how to start the Astrometrics app, or a TPM test run, on a chosen card. It is used by
 * `build/linux/run_astrometrics.sh` (through the command line at the bottom of this file), by `electron/main.js`
 * (through `gpu_startup_check.js`) and by the TPM harness (`build/tpm/gpu_choice.mjs`, which re-exports it).
 *
 * On a laptop with two graphics cards (an Intel iGPU that drives every screen and an NVIDIA dGPU that idles), the
 * card that draws the app changes the speed a lot. The app picks the card BEFORE Electron starts, because Chromium
 * cannot change cards while it runs. So plugging in or unplugging the charger while the app is open changes nothing
 * until the next start.
 *
 * Where the choice comes from, strongest first:
 *  1. the environment variable `ASTROMETRICS_GPU`;
 *  2. the `gpu` key in the `[Frontend]` section of `astrometrics.config.toml`;
 *  3. `auto`.
 * `auto` means NVIDIA when the laptop is on AC power and an NVIDIA card with the PRIME offload driver is present,
 * and Intel otherwise.
 *
 * The way to start on NVIDIA was found by trial on this computer (Wayland session, XWayland on `DISPLAY`):
 *  - `--ozone-platform=x11`: use the X11 (XWayland) backend, because NVIDIA offload works there;
 *  - `--use-gl=angle` with `--use-angle=gl-egl` for Brave/Chrome: draw through ANGLE on top of the system's OpenGL ES/EGL
 *    driver (ANGLE's Vulkan backend gave no WebGL here, and the default is software SwiftShader). Electron's own
 *    Chromium cannot find an EGL configuration on the NVIDIA card this way and quietly draws on Intel, so for Electron
 *    the switch is `--use-angle=gl` (desktop OpenGL through GLX) instead, which does reach the NVIDIA card;
 *  - the PRIME render offload variables `__NV_PRIME_RENDER_OFFLOAD=1`, `__GLX_VENDOR_LIBRARY_NAME=nvidia` and
 *    `__VK_LAYER_NV_optimus=NVIDIA_only`.
 * The WebGL renderer string is the proof of which card was used (see `verifyRenderer`).
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { pathToFileURL } from 'url';

/** The variables that send an OpenGL or Vulkan program to the NVIDIA card. */
export const NVIDIA_OFFLOAD_ENVIRONMENT = {
    __NV_PRIME_RENDER_OFFLOAD: '1',
    __GLX_VENDOR_LIBRARY_NAME: 'nvidia',
    __VK_LAYER_NV_optimus: 'NVIDIA_only',
};

/** The Chromium switches that make it draw on a real card through the system's driver. */
export const HARDWARE_GL_SWITCHES = ['--ozone-platform=x11', '--use-gl=angle', '--use-angle=gl-egl'];

/** The same for Electron's Chromium (see the file notes for why it differs). */
export const ELECTRON_HARDWARE_GL_SWITCHES = ['--ozone-platform=x11', '--use-gl=angle', '--use-angle=gl'];

/** Every hardware switch either kind of program may have been given (used to strip them when restarting). */
export const ALL_HARDWARE_GL_SWITCHES = [...new Set([...HARDWARE_GL_SWITCHES, ...ELECTRON_HARDWARE_GL_SWITCHES])];

/** The values the setting may have. */
export const GPU_CHOICES = ['auto', 'intel', 'nvidia'];

/**
 * Says how to start a program on the chosen card, for the TPM test runs.
 *
 * @param {string} choice - `auto`, `intel` or `nvidia`.
 * @param {NodeJS.ProcessEnv} baseEnvironment - The environment to start from.
 * @param {'chrome' | 'electron'} [program] - Which Chromium the plan is for.
 * @return {{environment: NodeJS.ProcessEnv, switches: string[]}} The environment to pass and the Chromium switches to add.
 *   For `auto` the environment is unchanged and there are no switches.
 */
export function gpuLaunchPlan(choice, baseEnvironment = process.env, program = 'chrome') {
    if (!GPU_CHOICES.includes(choice)) throw new Error('--gpu must be auto, intel or nvidia.');
    const environment = { ...baseEnvironment };
    if (choice === 'auto') return { environment, switches: [] };
    for (const name of Object.keys(NVIDIA_OFFLOAD_ENVIRONMENT)) delete environment[name];
    if (choice === 'nvidia') Object.assign(environment, NVIDIA_OFFLOAD_ENVIRONMENT);
    return { environment, switches: [...(program === 'electron' ? ELECTRON_HARDWARE_GL_SWITCHES : HARDWARE_GL_SWITCHES)] };
}

/**
 * Says how the app starts on a card that is already decided (`intel` or `nvidia`).
 *
 * NVIDIA uses the same plan as the TPM runs. Intel keeps the app's normal start (native Wayland, no extra switches)
 * and only removes the NVIDIA offload variables, so the app looks exactly as it did before this setting existed.
 *
 * @param {'intel' | 'nvidia'} choice - The card.
 * @param {NodeJS.ProcessEnv} baseEnvironment - The environment to start from.
 * @return {{environment: NodeJS.ProcessEnv, switches: string[]}} The environment to pass and the Chromium switches to add.
 */
export function appLaunchPlan(choice, baseEnvironment = process.env) {
    if (choice === 'nvidia') return gpuLaunchPlan('nvidia', baseEnvironment, 'electron');
    const environment = { ...baseEnvironment };
    for (const name of Object.keys(NVIDIA_OFFLOAD_ENVIRONMENT)) delete environment[name];
    return { environment, switches: [] };
}

/**
 * Names the kind of card a WebGL renderer string shows.
 *
 * @param {string | null} renderer - The unmasked renderer.
 * @return {'nvidia' | 'intel' | 'amd' | 'software' | 'unknown'} The kind.
 */
export function classifyRenderer(renderer) {
    const text = (renderer ?? '').toLowerCase();
    if (/swiftshader|llvmpipe|softpipe|software|basic render/.test(text)) return 'software';
    if (text.includes('nvidia')) return 'nvidia';
    if (text.includes('intel')) return 'intel';
    if (text.includes('amd') || text.includes('radeon')) return 'amd';
    return 'unknown';
}

/**
 * Stops the run if the page did not draw on the card that was asked for.
 *
 * @param {string} choice - `auto`, `intel` or `nvidia`.
 * @param {string | null} renderer - The unmasked WebGL renderer the page reported.
 * @param {string} where - Words for the message, for example `the Chrome flow`.
 * @return {void}
 */
export function verifyRenderer(choice, renderer, where) {
    if (choice === 'auto') return;
    const kind = classifyRenderer(renderer);
    if (kind !== choice) {
        throw new Error(
            `GPU CHECK FAILED: --gpu ${choice} was asked for, but WebGL in ${where} reports "${renderer}" (${kind}). ` +
                'The run is stopped so that a different card is not measured by mistake. ' +
                (choice === 'nvidia'
                    ? 'Check that prime-select is not intel-only, that nvidia-smi works and that DISPLAY (XWayland) is set.'
                    : 'Check that a graphical session (DISPLAY) is available; an invisible xvfb screen has no graphics card.'),
        );
    }
}

/**
 * Tells whether the computer is running on AC (mains) power.
 *
 * Reads `/sys/class/power_supply/*`. A supply whose `type` is `Mains` and whose `online` is `1` means the charger is
 * plugged in. A computer with no battery and no mains entry (a desktop) counts as on AC power.
 *
 * @param {string} [root] - The folder to read, `/sys/class/power_supply` by default.
 * @param {typeof fs} [fileSystem] - The file system to read, so tests can use a fake one.
 * @return {boolean} True when on AC power.
 */
export function isAcPowerOnline(root = '/sys/class/power_supply', fileSystem = fs) {
    let names;
    try {
        names = fileSystem.readdirSync(root);
    } catch {
        return false;
    }
    let sawBattery = false;
    let sawMains = false;
    for (const name of names) {
        let type;
        try {
            type = String(fileSystem.readFileSync(path.join(root, name, 'type'), 'utf8')).trim();
        } catch {
            continue;
        }
        if (type === 'Battery') sawBattery = true;
        if (type !== 'Mains') continue;
        sawMains = true;
        try {
            if (String(fileSystem.readFileSync(path.join(root, name, 'online'), 'utf8')).trim() === '1') return true;
        } catch {
            // An unreadable adapter is treated as not plugged in.
        }
    }
    return !sawBattery && !sawMains;
}

/**
 * Tells whether an NVIDIA card with the driver that PRIME render offload needs is present.
 *
 * Needs the kernel driver (`/proc/driver/nvidia/version`) and the NVIDIA EGL entry for the GL vendor library
 * (a `*nvidia*.json` file in `/usr/share/glvnd/egl_vendor.d` or `/etc/glvnd/egl_vendor.d`).
 *
 * @param {typeof fs} [fileSystem] - The file system to read, so tests can use a fake one.
 * @return {boolean} True when NVIDIA offload can work.
 */
export function hasNvidiaOffload(fileSystem = fs) {
    if (!fileSystem.existsSync('/proc/driver/nvidia/version')) return false;
    for (const folder of ['/usr/share/glvnd/egl_vendor.d', '/etc/glvnd/egl_vendor.d']) {
        try {
            if (fileSystem.readdirSync(folder).some((name) => /nvidia/i.test(name))) return true;
        } catch {
            // The folder may not exist.
        }
    }
    return false;
}

/**
 * Finds the settings file the same way the Python side does (see `find_config_file` in astrometricslib).
 *
 * @param {NodeJS.ProcessEnv} environment - The environment.
 * @return {string} The path of `astrometrics.config.toml` in the settings folder (it may not exist).
 */
export function configFilePath(environment = process.env) {
    const fromEnvironment = environment.ASTROMETRICS_CONFIG_PATH || environment.ASTROMETRICS_CONFIG;
    if (fromEnvironment) return fromEnvironment;
    const root = environment.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
    return path.join(root, 'astrometrics', 'astrometrics.config.toml');
}

/**
 * Reads the `gpu` key of the `[Frontend]` section of the settings file. Only reads; never writes the file.
 *
 * @param {NodeJS.ProcessEnv} environment - The environment, used to find the file.
 * @param {typeof fs} [fileSystem] - The file system to read, so tests can use a fake one.
 * @return {string | null} The value, or null when the file, the section or the key is missing.
 */
export function readGpuSettingFromConfig(environment = process.env, fileSystem = fs) {
    let text;
    try {
        text = fileSystem.readFileSync(configFilePath(environment), 'utf8');
    } catch {
        return null;
    }
    let inFrontend = false;
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (line.startsWith('[')) {
            inFrontend = /^\[\s*["']?Frontend["']?\s*\]/.test(line);
            continue;
        }
        if (!inFrontend) continue;
        const match = /^gpu\s*=\s*["']?([A-Za-z]+)["']?\s*(#.*)?$/.exec(line);
        if (match) return match[1];
    }
    return null;
}

/**
 * Decides the card to use and why.
 *
 * @param {object} [options] - What to read; every part can be replaced in tests.
 * @param {NodeJS.ProcessEnv} [options.environment] - The environment (`ASTROMETRICS_GPU` is read from it).
 * @param {typeof fs} [options.fileSystem] - The file system.
 * @param {string} [options.powerSupplyRoot] - The power supply folder.
 * @return {{setting: string, choice: 'intel' | 'nvidia', reason: string}} The setting that was read, the card to use,
 *   and a sentence for the log.
 */
export function resolveGpuChoice({ environment = process.env, fileSystem = fs, powerSupplyRoot } = {}) {
    let setting = 'auto';
    let source = 'default';
    const fromEnvironment = (environment.ASTROMETRICS_GPU ?? '').trim().toLowerCase();
    const fromConfig = fromEnvironment ? null : readGpuSettingFromConfig(environment, fileSystem);
    const requested = fromEnvironment || (fromConfig ?? '').toLowerCase();
    if (requested) {
        if (GPU_CHOICES.includes(requested)) {
            setting = requested;
            source = fromEnvironment ? 'ASTROMETRICS_GPU' : 'config [Frontend] gpu';
        } else {
            source = `ignored invalid value "${requested}"`;
        }
    }
    if (setting === 'intel' || setting === 'nvidia') {
        const nvidiaPresent = setting === 'intel' || hasNvidiaOffload(fileSystem);
        if (setting === 'nvidia' && !nvidiaPresent) {
            return { setting, choice: 'intel', reason: `gpu=nvidia (${source}) but no NVIDIA offload driver found, using Intel` };
        }
        return { setting, choice: setting, reason: `gpu=${setting} (${source})` };
    }
    if (!hasNvidiaOffload(fileSystem)) return { setting, choice: 'intel', reason: `gpu=auto (${source}): no NVIDIA offload driver, using Intel` };
    const onAc = isAcPowerOnline(powerSupplyRoot, fileSystem);
    return {
        setting,
        choice: onAc ? 'nvidia' : 'intel',
        reason: `gpu=auto (${source}): ${onAc ? 'on AC power with NVIDIA present, using NVIDIA' : 'on battery, using Intel'}`,
    };
}

/**
 * Wraps a value in single quotes for a shell.
 *
 * @param {string} value - The text.
 * @return {string} The quoted text.
 */
function shellQuote(value) {
    return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

/**
 * Builds the shell lines the app launcher evaluates before it starts Electron.
 *
 * @param {{choice: 'intel' | 'nvidia', reason: string}} decision - The result of `resolveGpuChoice`.
 * @return {string} Lines that set `ASTROMETRICS_GPU_CHOICE` and `ELECTRON_GPU_SWITCHES`, and set or unset the offload variables.
 */
export function shellPlanLines(decision) {
    const plan = appLaunchPlan(decision.choice, {});
    const lines = [`export ASTROMETRICS_GPU_CHOICE=${shellQuote(decision.choice)}`];
    for (const name of Object.keys(NVIDIA_OFFLOAD_ENVIRONMENT)) {
        lines.push(name in plan.environment ? `export ${name}=${shellQuote(plan.environment[name])}` : `unset ${name}`);
    }
    lines.push(`ELECTRON_GPU_SWITCHES=${shellQuote(plan.switches.join(' '))}`);
    lines.push(`ELECTRON_GPU_REASON=${shellQuote(decision.reason)}`);
    return lines.join('\n');
}

// Command line: `node electron/gpu_choice.js shell-plan` prints the lines for the launcher to `eval`;
// `node electron/gpu_choice.js decide` prints the decision as JSON.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    const decision = resolveGpuChoice();
    console.log(process.argv[2] === 'decide' ? JSON.stringify(decision) : shellPlanLines(decision));
}
