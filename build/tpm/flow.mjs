/**
 * @fileoverview Renderer flow for the technical performance measures (TPMs).
 *
 * Drives the production UI build through one lap of the app (load, Image
 * Viewer, Image Processing, Astronomy Manager, Planetarium, Observatory
 * Manager) several times, with Lighthouse user flows
 * recording one navigation step and one timespan step per display and lap.
 * It also measures what Lighthouse does not: how long each display switch and
 * click takes to settle, the Planetarium frame rate while panning, the
 * JavaScript heap after each lap, and every RPC call the page makes. It also records every console error, failed
 * request and in-app toast (see `error_watch.mjs`), which `compile_tpm.py` turns into the error TPMs.
 *
 * The displays and the actions on each come from `tpm_catalog.json`. Observation Manager and Command Console
 * are prototype displays, so the catalog excludes them and the flow leaves them out. On each display the flow picks a seeded target that is not the
 * selected one (the selection is shared between displays), so every click makes
 * the display load something new.
 *
 * SAFETY: this script talks only to the throwaway backend that `run_tpm.sh`
 * started. It refuses to run if the backend address is port 5000 or is not on
 * this computer. It sends no hardware command; every control it clicks only
 * selects or displays data.
 *
 * Usage (normally started by run_tpm.sh):
 *   node build/tpm/flow.mjs --ui-url http://127.0.0.1:PORT --backend-url http://127.0.0.1:PORT \
 *        --out build/tpm/results/<timestamp> [--laps 3] [--idle-seconds 30] [--pan-seconds 6] [--zoom-seconds 4] [--attach-url http://127.0.0.1:CDP] [--gpu auto|intel|nvidia]
 *
 * With `--attach-url` the script attaches (puppeteer.connect) to an already running app, the real Electron window
 * started by `electron_run.mjs`, which is the default target of the harness. Without it the script launches a browser
 * itself: `--browser brave|chrome` picks which (default: any Chromium found). On an attached Electron page the
 * Lighthouse navigation audit of the load cannot run (the page is `app://`, not http), so the load is a timespan
 * around a plain navigation; the browser-only audits are listed in README.md. Every other measurement is the same
 * for both targets.
 *
 * After the laps it also records a Chrome DevTools trace of a fixed-length
 * Planetarium pan and zoom (`planetarium-trace.json`, summary in
 * `planetarium-profile.json`) and asks the page which graphics renderer WebGL
 * uses, so every run says whether it drew on real hardware.
 */

import fs from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import { startFlow } from 'lighthouse';
import { gpuLaunchPlan, verifyRenderer } from '../../electron/gpu_choice.js';
import {
    IDLE_END_MARK,
    IDLE_START_MARK,
    PAN_END_MARK,
    PAN_START_MARK,
    WHEEL_TICK_MARK,
    ZOOM_END_MARK,
    ZOOM_START_MARK,
    summarizeIdle,
    summarizeTrace,
} from './planetarium_profile.mjs';
import { installToastListener, readToasts, watchPage } from './error_watch.mjs';

/** The one port that is the user's real backend. The harness never touches it. */
const FORBIDDEN_BACKEND_PORT = 5000;

/**
 * Reads `--name value` pairs from the command line.
 *
 * @param {string[]} argv - The arguments after the script name.
 * @return {Record<string, string>} The values by name.
 */
function parseArguments(argv) {
    const values = {};
    for (let index = 0; index < argv.length; index += 2) {
        values[argv[index].replace(/^--/, '')] = argv[index + 1];
    }
    return values;
}

/**
 * Refuses a backend address that could be the user's real one.
 *
 * @param {string} backendUrl - The address of the throwaway backend.
 * @return {void}
 */
function assertSandboxBackend(backendUrl) {
    const parsed = new URL(backendUrl);
    if (!['127.0.0.1', 'localhost'].includes(parsed.hostname)) {
        throw new Error(`Refusing to run: backend ${backendUrl} is not on this computer.`);
    }
    if (Number(parsed.port) === FORBIDDEN_BACKEND_PORT || !parsed.port) {
        throw new Error(`Refusing to run: backend ${backendUrl} is the real backend's port (5000) or has no port.`);
    }
}

/**
 * Finds a Chromium-based browser to launch.
 *
 * @param {string} [name] - `brave`, `chrome` (Chrome or Chromium) or `any`. `CHROME_PATH` always wins.
 * @return {string} The path of the executable.
 */
function findBrowser(name = 'any') {
    const byName = {
        brave: ['/usr/bin/brave-browser', '/usr/bin/brave', '/opt/brave.com/brave/brave-browser'],
        chrome: ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium'],
        any: ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/brave-browser'],
    };
    if (!byName[name]) throw new Error(`--browser must be brave or chrome, not ${name}.`);
    const candidates = [process.env.CHROME_PATH, ...byName[name]].filter(Boolean);
    const found = candidates.find((candidate) => fs.existsSync(candidate));
    if (!found) throw new Error('No Chrome, Chromium or Brave found. Set CHROME_PATH to one.');
    return found;
}

/**
 * RPC method names that would move or change hardware. The flow never clicks
 * anything that sends them; if the page makes one anyway, the run fails.
 */
const HARDWARE_RPC_PATTERN = /(slew|park|promot|capabilit|abort|set_tracking|jog|pulse|move|expose|capture_start|connect_device|focus_move)/i;

/** When the flow started, for the step log. */
const flowStartedAt = Date.now();

/**
 * Prints one line that names the step being started and the seconds since the flow began, so a stalled run shows
 * where it stopped.
 *
 * @param {string} name - The step.
 * @return {void}
 */
function logStep(name) {
    console.log(`[flow +${((Date.now() - flowStartedAt) / 1000).toFixed(1)}s] ${name}`);
}

/**
 * Waits for a promise, but fails the run with a clear message if it takes longer than a limit, so no wait can hang
 * for ever.
 *
 * @param {string} name - What is awaited, for the message.
 * @param {number} limitMs - The longest wait, in milliseconds.
 * @param {Promise<T>} promise - The wait.
 * @return {Promise<T>} The promise's value.
 * @template T
 */
function bounded(name, limitMs, promise) {
    let timer;
    const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`The flow stalled: "${name}" took longer than ${limitMs / 1000} s.`)), limitMs);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/** Waits a number of milliseconds. */
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

/**
 * The code injected into every page before the app starts. It wraps `fetch` to
 * record each RPC call (method, duration, which step of the flow it belongs
 * to), counts calls still in flight, and exposes helpers that time a click
 * until the page settles. It runs in the page, so it uses only browser APIs.
 */
function pageRecorder() {
    const recorder = {
        phase: 'load',
        inFlight: 0,
        rpc: [],
        layoutShift: 0,
        // How many stars each Planetarium source call returned (what the sky map has to draw).
        sourceCounts: [],
    };
    window.__tpm = recorder;
    // Window focus and visibility events, with the step they fell in: React Query refetches stale data on
    // `focus` and `visibilitychange`, so a refetch that follows one of these is not the display's own doing.
    recorder.events = [];
    for (const type of ['focus', 'blur', 'visibilitychange', 'online']) {
        window.addEventListener(type, () => recorder.events.push({ type, at: performance.now(), phase: recorder.phase, state: document.visibilityState }), true);
    }
    // Time to ready: the first moment a target list is on screen and no RPC call is waiting.
    // Measured here, inside the page, from the start of the navigation, so Lighthouse's own
    // wait for a quiet network does not count.
    recorder.readyAt = null;
    const readyTimer = setInterval(() => {
        const listShown = [...document.querySelectorAll('.selectable-list__item')].some((element) => element.offsetParent !== null);
        if (listShown && recorder.inFlight === 0) {
            recorder.readyAt = performance.now();
            clearInterval(readyTimer);
        }
    }, 25);
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
        const url = typeof input === 'string' ? input : (input.url ?? String(input));
        const isRpc = url.includes('/api/rpc');
        let method = '';
        let parameters = '';
        if (isRpc) {
            try {
                const body = JSON.parse(init?.body ?? '{}');
                method = body.method ?? '';
                // Kept short: the parameters tell repeated calls (same method, same parameters) from different ones.
                parameters = JSON.stringify(body.params ?? body.payload ?? {}).slice(0, 160);
            } catch {
                method = '';
            }
            recorder.inFlight += 1;
        }
        const startedAt = performance.now();
        let outcome = 'ok';
        try {
            const response = await realFetch(input, init);
            if (isRpc && /^planetarium:get_(sources|catalog_sources)$/.test(method)) {
                // Count the stars in the reply (a clone, so the page still reads its own copy).
                response
                    .clone()
                    .json()
                    .then((body) => {
                        // The reply is a JSON-RPC envelope; its result may hold the list or an object with `sources`.
                        let payload = body?.result ?? body?.data ?? body;
                        for (let depth = 0; depth < 3 && payload && !Array.isArray(payload) && !payload.sources; depth += 1) {
                            payload = payload.result ?? payload.data ?? payload;
                        }
                        const list = Array.isArray(payload) ? payload : payload?.sources;
                        recorder.sourceCounts.push({
                            method,
                            params: parameters,
                            phase: recorder.phase,
                            outcome: response.ok ? 'ok' : 'http-error',
                            httpStatus: response.status,
                            count: Array.isArray(list) ? list.length : null,
                            bodyKeys: payload && typeof payload === 'object' ? Object.keys(payload).slice(0, 6) : [],
                            // The star catalog drivers the backend could not read (null: the reply has no such field).
                            failedCatalogs: Array.isArray(payload?.failedCatalogs) ? payload.failedCatalogs : null,
                        });
                    })
                    .catch(() => {});
            }
            return response;
        } catch (error) {
            outcome = error?.name === 'AbortError' ? 'aborted' : 'failed';
            // A source call that never got a reply (the page cancelled it, or the request failed) is recorded too, so
            // it does not vanish from the list: a cancelled call carries no `failedCatalogs`, so it is not a failure.
            if (isRpc && /^planetarium:get_(sources|catalog_sources)$/.test(method)) {
                recorder.sourceCounts.push({
                    method,
                    params: parameters,
                    phase: recorder.phase,
                    outcome,
                    httpStatus: null,
                    count: null,
                    bodyKeys: [],
                    failedCatalogs: null,
                });
            }
            throw error;
        } finally {
            if (isRpc) {
                recorder.inFlight -= 1;
                recorder.rpc.push({ method, params: parameters, outcome, phase: recorder.phase, at: startedAt, ms: performance.now() - startedAt });
            }
        }
    };
    new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
            if (!entry.hadRecentInput) recorder.layoutShift += entry.value;
        }
    }).observe({ type: 'layout-shift', buffered: true });

    /** Waits two animation frames, so the time includes the next paint. */
    const nextPaint = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    /**
     * Waits for the next real pointer press, then times it until a condition is true, the next paint has
     * happened and no RPC call has been in flight for 150 ms (the screen has settled). The press comes from a
     * real mouse event, so the browser also counts it for Interaction to Next Paint.
     *
     * @param {{kind: string, text?: string}} done - The condition that says the click took effect.
     * @param {number} timeoutMs - Give up after this long.
     * @return {Promise<{firstPaintMs: number, settledMs: number, timedOut: boolean, noPress?: boolean}>} The timings;
     *   `noPress` is true when no press arrived within `PRESS_WAIT_MS`.
     */
    recorder.timeNextPress = async (done, timeoutMs = 10000) => {
        /** How long to wait for the press itself, in milliseconds (this function runs in the page, so no outer constants). */
        const PRESS_WAIT_MS = 5000;
        // Without a limit a click that never reaches the page (the element moved or was covered while the page
        // re-rendered) would leave this promise waiting until the browser's protocol timeout ends the whole run.
        const start = await new Promise((resolve) => {
            const onPress = () => resolve(performance.now());
            window.addEventListener('pointerdown', onPress, { capture: true, once: true });
            setTimeout(() => {
                window.removeEventListener('pointerdown', onPress, { capture: true });
                resolve(null);
            }, PRESS_WAIT_MS);
        });
        if (start === null) return { firstPaintMs: 0, settledMs: 0, timedOut: true, noPress: true };
        let timedOut = false;
        while (!recorder.conditionMet(done)) {
            if (performance.now() - start > timeoutMs) {
                timedOut = true;
                break;
            }
            await new Promise((resolve) => setTimeout(resolve, 10));
        }
        await nextPaint();
        const firstPaintMs = performance.now() - start;
        let quietSince = performance.now();
        while (performance.now() - start < timeoutMs) {
            if (recorder.inFlight > 0) quietSince = performance.now();
            if (performance.now() - quietSince >= 150) break;
            await new Promise((resolve) => setTimeout(resolve, 20));
        }
        return { firstPaintMs, settledMs: performance.now() - start - 150, timedOut };
    };
    /** Counts animation frames for a number of milliseconds and returns the frame rate. */
    recorder.countFrames = (durationMs) =>
        new Promise((resolve) => {
            let frames = 0;
            const start = performance.now();
            const tick = () => {
                frames += 1;
                if (performance.now() - start >= durationMs) resolve((frames * 1000) / (performance.now() - start));
                else requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        });
    /**
     * Tests a declarative condition (the page's CSP forbids evaluating strings).
     *
     * @param {{kind: string, text?: string}} done - `selected` (a selected list row), `tab` (a selected tab),
     *   `mode` (the header shows a display), `button` (a popover button shows the text) or `always`.
     * @return {boolean} Whether the condition holds.
     */
    recorder.conditionMet = (done) => {
        const visible = (selector) => [...document.querySelectorAll(selector)].filter((element) => element.offsetParent !== null);
        const has = (elements, text) => elements.some((element) => (element.textContent ?? '').includes(text));
        switch (done.kind) {
            case 'selected':
                return has(visible('.selectable-list__item--selected'), done.text);
            case 'tab':
                return visible('[role=tab]').some((element) => element.textContent.trim() === done.text && element.getAttribute('aria-selected') === 'true');
            case 'mode':
                return (document.querySelector('.mode-selector__button')?.textContent ?? '').includes(done.text);
            case 'button':
                return has(visible('.popover-select__button'), done.text);
            default:
                return true;
        }
    };
    /**
     * Picks the first candidate name that has a visible row in a list and is not the selected row.
     *
     * @param {string[]} candidates - Target names to choose from, in order of preference.
     * @return {string | null} The name to click, or `null` if no candidate is shown unselected.
     */
    recorder.pickUnselected = (candidates) => {
        const rows = [...document.querySelectorAll('.selectable-list__item')].filter((element) => element.offsetParent !== null);
        const isSelected = (row) => row.classList.contains('selectable-list__item--selected');
        for (const name of candidates) {
            const row = rows.find((element) => (element.textContent ?? '').includes(name));
            if (row && !isSelected(row)) return name;
        }
        return null;
    };
    /** Finds the first visible element matching a selector whose text contains a string. */
    recorder.find = (selector, text) =>
        [...document.querySelectorAll(selector)].find((element) => element.offsetParent !== null && (!text || (element.textContent ?? '').includes(text))) ?? null;
}

/**
 * Finds the first visible element that matches and gives the point at its centre, scrolling it into view.
 *
 * @param {import('puppeteer-core').Page} page - The page.
 * @param {string} selector - The CSS selector.
 * @param {string} text - Text the element must contain (empty for any).
 * @return {Promise<{x: number, y: number} | null>} The point, or null when no element matches.
 */
function findPoint(page, selector, text) {
    return page.evaluate(
        (selectorArgument, textArgument) => {
            const element = window.__tpm.find(selectorArgument, textArgument);
            if (!element) return null;
            element.scrollIntoView({ block: 'nearest' });
            const rectangle = element.getBoundingClientRect();
            return { x: rectangle.x + rectangle.width / 2, y: rectangle.y + rectangle.height / 2 };
        },
        selector,
        text,
    );
}

/**
 * Times one click on a visible element.
 *
 * @param {import('puppeteer-core').Page} page - The page.
 * @param {{selector: string, text?: string, done: {kind: string, text?: string}, pick?: string[]}} spec - The
 *   element to click, found by CSS selector and optional text, and the condition that is true once the click
 *   took effect. With `pick` (target names) the click goes to the first of those names whose row is shown and
 *   is not the selected one, and the condition becomes "that row is selected"; the call waits up to 5 s for
 *   such a row to appear.
 * @return {Promise<{firstPaintMs: number, settledMs: number, timedOut: boolean, picked?: string}>} The timings.
 */
async function timedClick(page, { selector, text = '', done, pick }) {
    let picked;
    if (pick) {
        const waitStarted = Date.now();
        while (!picked && Date.now() - waitStarted < 5000) {
            picked = await page.evaluate((candidates) => window.__tpm.pickUnselected(candidates), pick);
            if (!picked) await sleep(100);
        }
        if (!picked) return { firstPaintMs: 0, settledMs: 0, timedOut: true, missing: `${selector} one of ${pick.join(', ')} not selected` };
        text = picked;
        done = { kind: 'selected', text: picked };
    }
    const point = await findPoint(page, selector, text);
    if (!point) return { firstPaintMs: 0, settledMs: 0, timedOut: true, missing: `${selector} ${text}` };
    let timing;
    // A click can miss when the page re-renders under it; find the element again and click once more.
    for (let attempt = 0; attempt < 2; attempt += 1) {
        const target = attempt === 0 ? point : await findPoint(page, selector, text);
        if (!target) return { firstPaintMs: 0, settledMs: 0, timedOut: true, missing: `${selector} ${text}` };
        const pending = page.evaluate((condition) => window.__tpm.timeNextPress(condition), done);
        await sleep(30);
        await page.mouse.click(target.x, target.y);
        // A page that is not painting (a covered window) never settles; fail with a clear message instead of waiting.
        timing = await bounded(`click ${selector} ${text}`, 60000, pending);
        if (!timing.noPress) break;
    }
    return picked ? { ...timing, picked } : timing;
}

/** The seeded imaging targets, and the seeded stars (the Astronomy Manager lists stars as well). */
const TARGET_POOLS = {
    imaging: ['M 31', 'M 13', 'M 81'],
    stars: ['Vega', 'Altair'],
};

/**
 * Reads the TPM catalog, which says which displays the flow visits and which actions it performs on each.
 *
 * @return {{displays: Array<{name: string, excluded?: string, actions?: Array<{action: string, pool?: string, tab?: string}>}>}}
 *   The parsed `tpm_catalog.json`.
 */
function loadCatalog() {
    return JSON.parse(fs.readFileSync(path.join(import.meta.dirname, 'tpm_catalog.json'), 'utf8'));
}

/**
 * The displays in lap order, built from the catalog, with what to do inside each. The catalog names the displays and
 * the actions; the code here knows how to perform each action. Every action only selects or shows data: no button
 * that moves hardware is ever clicked.
 *
 * Actions: `select_target` (click a seeded target other than the selected one, from the catalog's `pool`),
 * `change_tab` (click the tab named `tab`), `pan`, `zoom` and `change_time` (the Planetarium; they run together in
 * the order of the first of them), `open_night` and `back_to_live` (the Observatory Manager's session menu).
 *
 * @return {Array<{display: string, actions: object[], modeIs: object}>} The steps.
 */
function lapSteps() {
    const measured = loadCatalog().displays.filter((display) => !display.excluded);
    return measured.map((display) => ({
        display: display.name,
        actions: display.actions ?? [],
        modeIs: { kind: 'mode', text: display.name },
    }));
}

/** The seeded observing night that the Observatory Manager opens. */
const SEEDED_NIGHT = '2026-10-12';

/** The catalog actions that the Planetarium exercise performs. */
const PLANETARIUM_ACTIONS = ['pan', 'zoom', 'change_time'];

/**
 * Turns one catalog action into the click to time.
 *
 * @param {{action: string, pool?: string, tab?: string}} action - The catalog action.
 * @return {{name: string, click: object}} The interaction name and the click specification for `timedClick`.
 */
function clickFor(action) {
    if (action.action === 'select_target') {
        const candidates = TARGET_POOLS[action.pool];
        if (!candidates) throw new Error(`Unknown target pool in the catalog: ${action.pool}`);
        return { name: 'select_target', click: { selector: '.selectable-list__item', pick: candidates } };
    }
    if (action.action === 'change_tab') {
        return { name: 'change_tab', click: { selector: '[role=tab]', text: action.tab, done: { kind: 'tab', text: action.tab } } };
    }
    throw new Error(`Unknown action in the catalog: ${action.action}`);
}

/**
 * Switches to a display through the header's mode menu and times it.
 *
 * @param {import('puppeteer-core').Page} page - The page.
 * @param {string} display - The display name, as the menu shows it.
 * @param {{kind: string, text: string}} modeIsCondition - Condition that is true once the header shows the display.
 * @return {Promise<object>} The timings.
 */
async function switchDisplay(page, display, modeIsCondition) {
    await timedClick(page, { selector: '.mode-selector__button', done: { kind: 'always' } });
    await sleep(150);
    return timedClick(page, { selector: '.mode-selector__item', text: display, done: modeIsCondition });
}

/** Wheel ticks in one direction of the zoom. Each tick scales the field of view by 0.85 or 1.15 (`useSkyMapInput`). */
const ZOOM_TICKS = 12;
/**
 * In the traced zoom, the ticks come in bursts of this many, with a pause after each burst, so that the easing of
 * the last tick of a burst settles before the next tick (the settle-time TPMs need ticks that settle).
 */
const TICKS_PER_BURST = 4;
/** The pause after a burst, in milliseconds: longer than the zoom easing, so the sky is still again. */
const BURST_PAUSE_MS = 600;
/** How long the traced zoom waits after its last tick for the easing to settle, in milliseconds. */
const FINAL_SETTLE_MS = 800;
/** How long the page is left alone in the traced idle window, in milliseconds. */
const IDLE_WINDOW_MS = 3000;

/**
 * Zooms the sky in with the mouse wheel, then back out, spread evenly over a number of seconds.
 *
 * The sky map listens to the wheel on its canvas (`onWheel` in `useSkyMapInput.ts`): a negative `deltaY` zooms in.
 *
 * @param {import('puppeteer-core').Page} page - The page.
 * @param {{x: number, y: number}} box - The canvas centre, where the pointer rests.
 * @param {number} seconds - How long the ticks themselves take (in and out), not counting the burst pauses.
 * @param {boolean} [withBursts] - Pause for `BURST_PAUSE_MS` after every `TICKS_PER_BURST` ticks, so the easing
 *   settles between bursts (the traced zoom). The pauses add to the total time.
 * @return {Promise<void>}
 */
async function zoomSky(page, box, seconds, withBursts = false) {
    const gapMs = (seconds * 1000) / (ZOOM_TICKS * 2);
    await page.mouse.move(box.x, box.y);
    for (const deltaY of [-120, 120]) {
        for (let tick = 0; tick < ZOOM_TICKS; tick += 1) {
            await page.mouse.wheel({ deltaY });
            await sleep(withBursts && (tick + 1) % TICKS_PER_BURST === 0 ? BURST_PAUSE_MS : gapMs);
        }
    }
}

/**
 * Pans and zooms the Planetarium and changes its time, returning the frame rate while panning.
 *
 * @param {import('puppeteer-core').Page} page - The page.
 * @param {number} zoomSeconds - How long the zoom lasts.
 * @param {Set<string>} actions - Which of `pan`, `zoom` and `change_time` the catalog asks for.
 * @return {Promise<{fps: number, timeChange: object}>} Frames per second while panning, and the time-change timing (`null` for an action not asked for).
 */
async function exercisePlanetarium(page, zoomSeconds, actions) {
    const box = await page.evaluate(() => {
        const canvas = [...document.querySelectorAll('canvas')].find((element) => element.offsetParent);
        const rectangle = canvas?.getBoundingClientRect();
        return rectangle ? { x: rectangle.x + rectangle.width / 2, y: rectangle.y + rectangle.height / 2 } : null;
    });
    let fps = null;
    if (box && actions.has('pan')) {
        await page.mouse.move(box.x, box.y);
        const framesPromise = page.evaluate(() => window.__tpm.countFrames(3000));
        await page.mouse.down();
        for (let step = 0; step < 90; step += 1) {
            await page.mouse.move(box.x + Math.sin(step / 10) * 200 + step, box.y + Math.cos(step / 10) * 120, { steps: 1 });
            await sleep(16);
        }
        await page.mouse.up();
        fps = await bounded('frame count while panning', 60000, framesPromise);
    }
    if (box && actions.has('zoom')) {
        // No animation-frame counting here: the sky map draws on demand, and a counting loop would ask the page for
        // frames it would not draw. The zoom is measured in the traced profile instead (`profilePlanetarium`).
        await zoomSky(page, box, zoomSeconds);
    }
    let timeChange = null;
    if (actions.has('change_time')) {
        await timedClick(page, { selector: 'button', text: 'Date & Time', done: { kind: 'always' } });
        await sleep(300);
        timeChange = await timedClick(page, {
            selector: '.planetarium-datetime-modal__arrow-btn',
            text: '▲',
            done: { kind: 'always' },
        });
        await timedClick(page, { selector: '.planetarium-datetime-modal__close', done: { kind: 'always' } });
    }
    return { fps, timeChange };
}

/** Trace categories for the pan profile: main-thread tasks, frames, the GPU process, Blink and user timing. */
const TRACE_CATEGORIES = [
    'devtools.timeline',
    'disabled-by-default-devtools.timeline',
    'disabled-by-default-devtools.timeline.frame',
    'gpu',
    'blink',
    'blink.user_timing',
    'toplevel',
];

/** Renderer names that mean WebGL is drawing in software, not on a graphics card. */
const SOFTWARE_RENDERER_PATTERN = /swiftshader|llvmpipe|softpipe|software|basic render/i;

/**
 * Asks the page which graphics renderer WebGL2 uses.
 *
 * @param {import('puppeteer-core').Page} page - The page.
 * @return {Promise<{vendor: string | null, renderer: string | null, webgl2: boolean, hardware: boolean | null}>} The strings
 *   from the WEBGL_debug_renderer_info extension and whether they look like a real graphics card.
 */
async function probeWebGl(page) {
    const probe = await page.evaluate(() => {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl2');
        if (!gl) return { vendor: null, renderer: null, webgl2: false };
        const extension = gl.getExtension('WEBGL_debug_renderer_info');
        return {
            vendor: extension ? gl.getParameter(extension.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
            renderer: extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
            webgl2: true,
        };
    });
    const hardware = probe.renderer === null ? null : !SOFTWARE_RENDERER_PATTERN.test(`${probe.renderer} ${probe.vendor ?? ''}`);
    return { ...probe, hardware };
}

/**
 * Drags the sky around for a fixed time, then zooms it in and out, while one DevTools trace records, and reduces
 * the trace to a pan profile and a zoom profile.
 *
 * Runs outside any Lighthouse timespan, because only one trace can run at a time. The Planetarium records
 * `performance.measure` entries for its star projection and overlay drawing only while
 * `window.__ASTROMETRICS_PERF__` is true, so the flag is on for this pan alone.
 *
 * @param {import('puppeteer-core').Page} page - The page, already on the Planetarium.
 * @param {string} outputDirectory - Where `planetarium-trace.json` goes.
 * @param {number} seconds - How long to pan.
 * @param {number} zoomSeconds - How long the zoom (in and back out) lasts.
 * @return {Promise<object | null>} The pan summary from `summarizeTrace`, with the zoom summary under `zoom`, or
 *   `null` if there is no sky canvas.
 */
async function profilePlanetarium(page, outputDirectory, seconds, zoomSeconds) {
    const box = await page.evaluate(() => {
        const canvas = [...document.querySelectorAll('canvas')].find((element) => element.offsetParent);
        const rectangle = canvas?.getBoundingClientRect();
        return rectangle ? { x: rectangle.x + rectangle.width / 2, y: rectangle.y + rectangle.height / 2 } : null;
    });
    if (!box) return null;
    const tracePath = path.join(outputDirectory, 'planetarium-trace.json');
    await page.evaluate(() => {
        performance.clearMeasures();
        window.__ASTROMETRICS_PERF__ = true;
    });
    await page.tracing.start({ path: tracePath, categories: TRACE_CATEGORIES });
    await page.evaluate((name) => performance.mark(name), PAN_START_MARK);
    const framesPromise = page.evaluate((durationMs) => window.__tpm.countFrames(durationMs), seconds * 1000);
    await page.mouse.move(box.x, box.y);
    await page.mouse.down();
    const startedAt = Date.now();
    for (let step = 0; Date.now() - startedAt < seconds * 1000; step += 1) {
        await page.mouse.move(box.x + Math.sin(step / 10) * 200 + (step % 90), box.y + Math.cos(step / 10) * 120, { steps: 1 });
        await sleep(16);
    }
    await page.mouse.up();
    const animationFps = await bounded('frame count during the traced pan', 60000, framesPromise);
    await page.evaluate((name) => performance.mark(name), PAN_END_MARK);
    /** Reads the page's own timings recorded since the last read and clears them. */
    const takeMeasures = () =>
        page.evaluate(() => {
            const durations = (name) => performance.getEntriesByName(name, 'measure').map((entry) => entry.duration);
            const result = {
                starMeasuresMs: durations('planetarium:star_project_upload'),
                overlayMeasuresMs: durations('planetarium:overlay_draw'),
            };
            performance.clearMeasures();
            return result;
        });
    const panMeasures = await takeMeasures();
    // The zoom, in the same trace, with its own marks.
    await sleep(300);
    await page.evaluate((name) => performance.mark(name), ZOOM_START_MARK);
    // Each wheel input event leaves a mark in the trace, timed from the event's own timestamp.
    await page.evaluate((name) => {
        window.addEventListener('wheel', (event) => performance.mark(name, { startTime: event.timeStamp }), { capture: true, passive: true });
    }, WHEEL_TICK_MARK);
    await zoomSky(page, box, zoomSeconds, true);
    // Let the last tick's easing settle, then end the zoom window.
    await sleep(FINAL_SETTLE_MS);
    await page.evaluate((name) => performance.mark(name), ZOOM_END_MARK);
    const zoomMeasures = await takeMeasures();
    // The idle window: the page is left alone (the pointer rests, nothing is asked), and the trace shows whether
    // the sky map keeps drawing.
    await page.evaluate((name) => performance.mark(name), IDLE_START_MARK);
    await sleep(IDLE_WINDOW_MS);
    await page.evaluate((name) => performance.mark(name), IDLE_END_MARK);
    await page.tracing.stop();
    await page.evaluate(() => {
        window.__ASTROMETRICS_PERF__ = false;
    });
    const trace = JSON.parse(fs.readFileSync(tracePath, 'utf8'));
    const events = Array.isArray(trace) ? trace : (trace.traceEvents ?? []);
    return {
        ...summarizeTrace(events, panMeasures),
        animationFpsWhileTraced: animationFps,
        traceEventCount: events.length,
        zoom: {
            ...summarizeTrace(events, zoomMeasures, { start: ZOOM_START_MARK, end: ZOOM_END_MARK, tick: WHEEL_TICK_MARK }),
        },
        idle: summarizeIdle(events),
    };
}

/**
 * Forces a garbage collection and reads the JavaScript heap size.
 *
 * @param {import('puppeteer-core').Page} page - The page.
 * @return {Promise<number>} Used heap in bytes.
 */
async function measureHeap(page) {
    const session = await page.createCDPSession();
    await session.send('HeapProfiler.collectGarbage');
    await session.detach();
    return (await page.metrics()).JSHeapUsedSize;
}

/**
 * Runs the whole flow and writes the report files.
 *
 * @return {Promise<void>}
 */
async function main() {
    const options = parseArguments(process.argv.slice(2));
    const { 'ui-url': uiUrl, 'backend-url': backendUrl, out: outputDirectory } = options;
    if (!uiUrl || !backendUrl || !outputDirectory) throw new Error('Needs --ui-url, --backend-url and --out.');
    assertSandboxBackend(backendUrl);
    const laps = Number(options.laps ?? 3);
    const panSeconds = Number(options['pan-seconds'] ?? 6);
    const zoomSeconds = Number(options['zoom-seconds'] ?? 4);
    const idleSeconds = Number(options['idle-seconds'] ?? 30);
    fs.mkdirSync(outputDirectory, { recursive: true });

    const gpuChoice = options.gpu ?? 'auto';
    const gpuPlan = gpuLaunchPlan(gpuChoice);
    const browser = options['attach-url']
        ? await puppeteer.connect({ browserURL: options['attach-url'], defaultViewport: null })
        : await puppeteer.launch({
              executablePath: findBrowser(options.browser ?? 'any'),
              headless: true,
              args: ['--no-sandbox', '--window-size=1440,900', '--disable-background-timer-throttling', ...gpuPlan.switches],
              env: gpuPlan.environment,
              defaultViewport: { width: 1440, height: 900 },
          });
    // The resource sampler (resource_usage.py) follows the browser's processes through this file.
    if (!options['attach-url']) fs.writeFileSync(path.join(outputDirectory, 'browser.pid'), String(browser.process().pid));
    const label = options['attach-url'] ? 'electron' : 'chrome';
    const page = options['attach-url'] ? (await browser.pages()).find((candidate) => candidate.url().startsWith(uiUrl)) ?? (await browser.pages())[0] : await browser.newPage();
    // The UI reaches the backend through the preview server, which forwards to the throwaway backend.
    await page.evaluateOnNewDocument((address) => {
        try {
            localStorage.setItem('backendUrl', address);
            localStorage.setItem('appMode', 'Image Viewer');
        } catch {
            // Storage can be blocked; the page then uses its default address, which the run would notice.
        }
    // A page opened from a file has no server to forward its calls, so it talks to the backend directly.
    }, !/^https?:/.test(uiUrl) ? backendUrl : uiUrl);
    await page.evaluateOnNewDocument(pageRecorder);
    await page.evaluateOnNewDocument(installToastListener);
    const errorRecords = watchPage(page, label);

    const lighthouseConfig = {
        extends: 'lighthouse:default',
        settings: {
            onlyCategories: ['performance'],
            formFactor: 'desktop',
            screenEmulation: { disabled: true },
            throttlingMethod: 'provided',
            disableStorageReset: true,
            budgets: JSON.parse(fs.readFileSync(path.join(import.meta.dirname, 'budgets.json'), 'utf8')),
        },
    };
    logStep('start the Lighthouse flow');
    const flow = await startFlow(page, { name: 'Astrometrics UI lap', config: lighthouseConfig });

    // Step 1: load the app, and time how long until the first display is usable.
    const loadStarted = Date.now();
    if (!/^https?:/.test(uiUrl)) {
        // Lighthouse accepts only http(s) addresses for a navigation, so the load of a file:// page (the Electron
        // window, as the real app loads it) is a timespan around a plain navigation.
        await flow.startTimespan({ name: 'Load app' });
        await page.goto(`${uiUrl.replace(/\/$/, '')}/index.html`, { waitUntil: 'load' });
        await flow.endTimespan();
    } else {
        await flow.navigate(`${uiUrl.replace(/\/$/, '')}/index.html`, { name: 'Load app' });
    }
    await page.waitForFunction(() => window.__tpm.find('.selectable-list__item') && window.__tpm.inFlight === 0, { timeout: 60000 });
    const timeToReadyMs = await page.evaluate(() => window.__tpm.readyAt);
    const loadWallMs = Date.now() - loadStarted;
    logStep('loaded; measuring the heap');
    const initialHeap = await bounded('heap measurement after load', 60000, measureHeap(page));
    const gpu = await probeWebGl(page);
    try {
        verifyRenderer(gpuChoice, gpu.renderer, options['attach-url'] ? 'the Electron window' : 'the Chrome flow');
    } catch (error) {
        // Do not leave the browser running when the wrong graphics card is in use.
        if (options['attach-url']) await browser.disconnect();
        else await browser.close();
        throw error;
    }

    const steps = lapSteps();
    const measurements = { switches: [], interactions: [], planetarium: [], heapAfterLap: [], stepNames: [], lapWindows: [] };

    for (let lap = 1; lap <= laps; lap += 1) {
        // Wall-clock start and end of the lap (seconds since the epoch), for the disk-use-per-lap TPMs.
        const lapStartedAt = Date.now() / 1000;
        for (const step of steps) {
            const stepName = `lap ${lap}: ${step.display}`;
            measurements.stepNames.push({ lap, display: step.display, stepName });
            await page.evaluate((phase) => {
                window.__tpm.phase = phase;
            }, step.display);
            logStep(stepName);
            await bounded(`start timespan ${stepName}`, 120000, flow.startTimespan({ name: stepName }));
            const switched = await switchDisplay(page, step.display, step.modeIs);
            measurements.switches.push({ lap, display: step.display, ...switched });
            await sleep(400);
            let planetariumDone = false;
            for (const action of step.actions) {
                if (PLANETARIUM_ACTIONS.includes(action.action)) {
                    // The pan, zoom and time change run together, once, at the first of them.
                    if (planetariumDone) continue;
                    planetariumDone = true;
                    const wanted = new Set(step.actions.map((item) => item.action).filter((name) => PLANETARIUM_ACTIONS.includes(name)));
                    const result = await exercisePlanetarium(page, zoomSeconds, wanted);
                    measurements.planetarium.push({ lap, ...result });
                } else if (action.action === 'open_night') {
                    // Open the session menu, then pick the seeded night.
                    await timedClick(page, { selector: '.popover-select__button', text: 'Live', done: { kind: 'always' } });
                    await sleep(250);
                    const openNight = await timedClick(page, {
                        selector: '.popover-select__option',
                        text: SEEDED_NIGHT,
                        done: { kind: 'button', text: SEEDED_NIGHT },
                    });
                    measurements.interactions.push({ lap, display: step.display, name: 'open_night', ...openNight });
                    await sleep(500);
                } else if (action.action === 'back_to_live') {
                    // Needs `open_night` before it: opens the menu on the night and picks Live.
                    await timedClick(page, { selector: '.popover-select__button', text: SEEDED_NIGHT, done: { kind: 'always' } });
                    await sleep(250);
                    const backToLive = await timedClick(page, {
                        selector: '.popover-select__option',
                        text: 'Live',
                        done: { kind: 'button', text: 'Live' },
                    });
                    measurements.interactions.push({ lap, display: step.display, name: 'back_to_live', ...backToLive });
                } else {
                    const interaction = clickFor(action);
                    const timing = await timedClick(page, interaction.click);
                    measurements.interactions.push({ lap, display: step.display, name: interaction.name, ...timing });
                    await sleep(250);
                }
            }
            await bounded(`end timespan ${stepName}`, 180000, flow.endTimespan());
        }
        measurements.lapWindows.push({ lap, start: lapStartedAt, end: Date.now() / 1000 });
        measurements.heapAfterLap.push({ lap, bytes: await bounded(`heap after lap ${lap}`, 60000, measureHeap(page)) });
    }

    // Planetarium pan profile: a fixed-length pan under a DevTools trace, outside any Lighthouse step.
    await page.evaluate(() => {
        window.__tpm.phase = 'profile';
    });
    const planetariumStep = steps.find((step) => step.actions.some((action) => PLANETARIUM_ACTIONS.includes(action.action)));
    await switchDisplay(page, planetariumStep.display, planetariumStep.modeIs);
    await sleep(1500);
    await sleep(2500);
    await page.screenshot({ path: path.join(outputDirectory, 'planetarium-sky.png') });
    logStep('Planetarium trace profile');
    const planetariumProfile = await bounded('Planetarium trace profile', 240000, profilePlanetarium(page, outputDirectory, panSeconds, zoomSeconds));
    fs.writeFileSync(path.join(outputDirectory, 'planetarium-profile.json'), JSON.stringify({ gpu, profile: planetariumProfile }, null, 2));

    // Idle: go back to the first display and count what the page polls on its own.
    await page.evaluate(() => {
        window.__tpm.phase = 'idle';
    });
    logStep('idle: back to Image Viewer');
    await bounded('switch back to Image Viewer', 60000, switchDisplay(page, 'Image Viewer', steps[0].modeIs));
    logStep(`idle for ${idleSeconds} s`);
    const idleStart = await page.evaluate(() => performance.now());
    await sleep(idleSeconds * 1000);
    const idleEnd = await page.evaluate(() => performance.now());

    logStep('read toasts and the recorder');
    const toasts = await bounded('read toasts', 30000, readToasts(page, label));
    const recorder = await page.evaluate(() => ({ sourceCounts: window.__tpm.sourceCounts, rpc: window.__tpm.rpc, events: window.__tpm.events, layoutShift: window.__tpm.layoutShift }));
    const idleCalls = recorder.rpc.filter((call) => call.at >= idleStart && call.at <= idleEnd);

    const unsafeRpc = [...new Set(recorder.rpc.map((call) => call.method).filter((method) => HARDWARE_RPC_PATTERN.test(method)))];
    logStep('Lighthouse createFlowResult');
    const flowResult = await bounded('Lighthouse createFlowResult', 120000, flow.createFlowResult());
    logStep('Lighthouse generateReport');
    fs.writeFileSync(path.join(outputDirectory, 'flow-report.html'), await bounded('Lighthouse generateReport', 120000, flow.generateReport()));
    fs.writeFileSync(path.join(outputDirectory, 'flow-report.json'), JSON.stringify(flowResult, null, 2));

    const lighthouseSteps = flowResult.steps.map((step) => {
        const audit = (id) => step.lhr.audits[id]?.numericValue ?? null;
        const summary = step.lhr.audits['resource-summary']?.details?.items ?? [];
        return {
            name: step.name,
            totalBlockingTimeMs: audit('total-blocking-time'),
            interactionToNextPaintMs: audit('interaction-to-next-paint'),
            cumulativeLayoutShift: audit('cumulative-layout-shift'),
            scriptBytes: summary.find((item) => item.resourceType === 'script')?.transferSize ?? null,
            requestCount: summary.find((item) => item.resourceType === 'total')?.requestCount ?? null,
        };
    });

    const output = {
        generatedAt: new Date().toISOString(),
        // What the page ran in: the real Electron app, or the named browser.
        target: options['attach-url'] ? 'electron' : (options.browser ?? 'chrome'),
        laps,
        uiUrl,
        backendUrl,
        timeToReadyMs,
        loadWallMs,
        initialHeapBytes: initialHeap,
        gpu,
        planetariumProfile,
        ...measurements,
        lighthouseSteps,
        planetariumSources: recorder.sourceCounts,
        rpc: recorder.rpc,
        events: recorder.events,
        idle: { seconds: (idleEnd - idleStart) / 1000, rpcCalls: idleCalls.length, methods: idleCalls.map((call) => call.method) },
        pageLayoutShift: recorder.layoutShift,
        unsafeRpc,
        errors: { ...errorRecords, toasts },
    };
    logStep('write flow-metrics.json');
    fs.writeFileSync(path.join(outputDirectory, 'flow-metrics.json'), JSON.stringify(output, null, 2));
    if (options['attach-url']) await browser.disconnect();
    else await browser.close();
    if (unsafeRpc.length > 0) throw new Error(`The page sent hardware-looking RPC calls: ${unsafeRpc.join(', ')}`);
    console.log(`Flow finished: ${laps} laps, ${recorder.rpc.length} RPC calls. Report in ${outputDirectory}`);
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
