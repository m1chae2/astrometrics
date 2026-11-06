/**
 * @fileoverview Reduces a Chrome DevTools trace of a Planetarium pan to a few numbers.
 *
 * `flow.mjs` records a trace while it drags the sky around for a fixed time.
 * The trace is a list of events. Each event has a name, a process id, a thread
 * id, a start time `ts` and (for work that took time) a duration `dur`, both in
 * microseconds. This file finds the pan in the trace, then works out:
 *  - how many frames were drawn and how long each took;
 *  - how busy the page's main thread was, per frame;
 *  - how busy the GPU process was, per frame.
 *
 * The split of the main thread into "star projection and upload" and "overlay
 * drawing" does not come from the trace. The Planetarium records those with
 * `performance.measure` while the flag `window.__ASTROMETRICS_PERF__` is set
 * (see `ui/planetariumDisplay/utils/perfMarks.ts`), and the flow passes the
 * measured durations in. Everything else on the main thread is "other".
 *
 * The sky map draws on demand: it asks for a frame only while something changes
 * (a drag, or the zoom easing toward its target) and draws nothing while the
 * picture is still. So the time between two drawn frames is meaningless across
 * an idle gap. The zoom is therefore measured per wheel tick (the flow marks
 * each tick in the trace) and per stretch of active animation:
 *  - tick latency: from the wheel input event to the first frame drawn after it;
 *  - settle time: from the tick to the last frame of its easing;
 *  - frame time and slow-frame count, over the intervals inside active animation only;
 *  - long main-thread tasks (over 50 ms) in the window.
 *
 * After the zoom the flow waits for the easing to settle and then leaves the page alone for a few seconds (the
 * idle window). A sky map that draws on demand draws nothing in that window; `summarizeIdle` counts the frames it
 * did draw.
 *
 * Pure functions only, so the file can be tested without a browser.
 */

/** Names of the two marks the flow places in the trace around the pan. */
export const PAN_START_MARK = 'tpm:pan_start';
export const PAN_END_MARK = 'tpm:pan_end';
/** Names of the two marks the flow places in the same trace around the zoom (wheel in, then back out). */
export const ZOOM_START_MARK = 'tpm:zoom_start';
export const ZOOM_END_MARK = 'tpm:zoom_end';
/** Names of the two marks the flow places in the same trace around the idle window (the sky is still, nothing moves). */
export const IDLE_START_MARK = 'tpm:idle_start';
export const IDLE_END_MARK = 'tpm:idle_end';
/** Name of the mark the flow places in the trace at each wheel input event of the zoom. */
export const WHEEL_TICK_MARK = 'tpm:wheel_tick';

/**
 * Two drawn frames closer than this belong to one stretch of animation; a longer gap is the renderer being idle.
 * An animating frame is at most a few tens of milliseconds apart even on slow software graphics, and the flow's
 * wheel ticks are about 167 ms apart, so 100 ms separates the two.
 */
export const ACTIVE_GAP_MS = 100;

/**
 * A frame that took longer than this missed a 60 Hz screen's refresh. One refresh is 16.7 ms, but vsync timing
 * jitters by a few tenths of a millisecond, so an exact 16.7 ms limit would flag about two frames in five on a
 * healthy 60 Hz run. The limit is 1.2 refreshes.
 */
export const SLOW_FRAME_MS = 20;

/** A main-thread task longer than this blocks the page noticeably (the Lighthouse long-task limit). */
export const LONG_TASK_MS = 50;

/**
 * Gives a percentile by linear interpolation.
 *
 * @param {number[]} values - The values.
 * @param {number} fraction - 0.5 for the median, 0.95 for the 95th percentile.
 * @return {number | null} The percentile, or `null` if there are no values.
 */
export function percentile(values, fraction) {
    if (values.length === 0) return null;
    const ordered = [...values].sort((a, b) => a - b);
    const position = (ordered.length - 1) * fraction;
    const lower = Math.floor(position);
    const upper = Math.min(lower + 1, ordered.length - 1);
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower);
}

/**
 * Adds up how much of each task fell inside a time window.
 *
 * @param {Array<{ts: number, dur?: number}>} tasks - Events with a start and a duration, in microseconds.
 * @param {number} windowStart - Window start, in microseconds.
 * @param {number} windowEnd - Window end, in microseconds.
 * @return {number} Busy time inside the window, in milliseconds.
 */
export function busyMilliseconds(tasks, windowStart, windowEnd) {
    let total = 0;
    for (const task of tasks) {
        const start = Math.max(task.ts, windowStart);
        const end = Math.min(task.ts + (task.dur ?? 0), windowEnd);
        if (end > start) total += end - start;
    }
    return total / 1000;
}

/**
 * Splits drawn frames into stretches of active animation and measures the frame times inside them.
 *
 * @param {number[]} frames - Draw times in microseconds, sorted.
 * @return {{runs: Array<{start: number, end: number, frames: number}>, intervalsMs: number[], idleGapCount: number}}
 *   The stretches (first draw, last draw and frame count of each), the intervals between consecutive frames of the
 *   same stretch, and how many idle gaps (intervals of `ACTIVE_GAP_MS` or more) separate the stretches.
 */
export function activeRuns(frames) {
    const runs = [];
    const intervalsMs = [];
    let idleGapCount = 0;
    for (let index = 0; index < frames.length; index += 1) {
        const gapMs = index === 0 ? Infinity : (frames[index] - frames[index - 1]) / 1000;
        if (gapMs < ACTIVE_GAP_MS) {
            const run = runs[runs.length - 1];
            run.end = frames[index];
            run.frames += 1;
            intervalsMs.push(gapMs);
        } else {
            if (index > 0) idleGapCount += 1;
            runs.push({ start: frames[index], end: frames[index], frames: 1 });
        }
    }
    return { runs, intervalsMs, idleGapCount };
}

/**
 * Measures what each wheel tick did: how long until the first frame, and how long until the easing settled.
 *
 * @param {number[]} ticks - Times of the wheel input events in microseconds, sorted.
 * @param {number[]} frames - Draw times in microseconds, sorted.
 * @param {Array<{start: number, end: number}>} runs - The stretches of active animation from `activeRuns`.
 * @param {number} windowEnd - End of the window in microseconds.
 * @return {{count: number, withoutFrame: number, overlapped: number, latencyMs: number[], settleMs: number[]}}
 *   The number of ticks, the ticks with no frame after them, the ticks whose animation ran into the next tick
 *   (no settle time of their own), and the two lists of milliseconds.
 */
export function tickTimings(ticks, frames, runs, windowEnd) {
    const latencyMs = [];
    const settleMs = [];
    let withoutFrame = 0;
    let overlapped = 0;
    ticks.forEach((tick, index) => {
        const first = frames.find((time) => time >= tick);
        if (first === undefined) {
            withoutFrame += 1;
            return;
        }
        latencyMs.push((first - tick) / 1000);
        const run = runs.find((candidate) => candidate.start <= first && first <= candidate.end);
        const nextTick = ticks[index + 1] ?? windowEnd;
        if (!run || run.end >= nextTick) {
            // The animation did not stop before the next tick: it belongs to a burst, and only the burst's last tick settles.
            if (index < ticks.length - 1) overlapped += 1;
            else if (run) settleMs.push((run.end - tick) / 1000);
            return;
        }
        settleMs.push((run.end - tick) / 1000);
    });
    return { count: ticks.length, withoutFrame, overlapped, latencyMs, settleMs };
}

/**
 * Reduces a trace and the page's own measures to the profile of one phase (pan or zoom).
 *
 * @param {Array<object>} events - The trace's `traceEvents`.
 * @param {{starMeasuresMs: number[], overlayMeasuresMs: number[]}} measures - The durations the page recorded.
 * @param {{start: string, end: string, tick?: string}} [marks] - The names of the marks that bound the window to
 *   reduce. The default is the pan; pass the zoom marks to reduce the zoom phase of the same trace. With `tick`, the
 *   window's wheel-tick marks are reduced to tick latency and settle time.
 * @return {object} The profile: frame counts and rates, main-thread split and GPU busy time per frame, the frame times
 *   inside active animation (`active`), the wheel-tick timings (`ticks`) and the long main-thread tasks (`longTasks`).
 */
export function summarizeTrace(events, measures, marks = { start: PAN_START_MARK, end: PAN_END_MARK }) {
    const threadNames = new Map();
    const processNames = new Map();
    for (const event of events) {
        if (event.ph !== 'M') continue;
        if (event.name === 'thread_name') threadNames.set(`${event.pid}:${event.tid}`, event.args?.name ?? '');
        if (event.name === 'process_name') processNames.set(event.pid, event.args?.name ?? '');
    }
    const startMark = events.find((event) => event.name === marks.start);
    const endMark = events.find((event) => event.name === marks.end);
    const timed = events.filter((event) => typeof event.ts === 'number' && event.ts > 0);
    const windowStart = startMark?.ts ?? Math.min(...timed.map((event) => event.ts));
    const windowEnd = endMark?.ts ?? Math.max(...timed.map((event) => event.ts));
    const windowSeconds = (windowEnd - windowStart) / 1e6;

    // The page's main thread is the one that logged the pan marks.
    const mainKey = startMark ? `${startMark.pid}:${startMark.tid}` : null;
    const tasks = events.filter((event) => event.ph === 'X' && event.name === 'RunTask');
    const mainTasks = tasks.filter((event) => `${event.pid}:${event.tid}` === mainKey);
    const gpuProcessIds = new Set([...processNames].filter(([, name]) => /gpu/i.test(name)).map(([pid]) => pid));
    const gpuTasks = tasks.filter((event) => gpuProcessIds.has(event.pid));

    // A "frame" is one compositor draw. If a build records none, use the page's animation-frame callbacks.
    const drawFrames = events.filter((event) => event.name === 'DrawFrame' && event.ts >= windowStart && event.ts <= windowEnd);
    const animationFrames = events.filter(
        (event) => event.name === 'FireAnimationFrame' && `${event.pid}:${event.tid}` === mainKey && event.ts >= windowStart && event.ts <= windowEnd,
    );
    const frames = (drawFrames.length > 0 ? drawFrames : animationFrames).map((event) => event.ts).sort((a, b) => a - b);
    const intervalsMs = frames.slice(1).map((time, index) => (time - frames[index]) / 1000);

    // GPU busy time in each frame interval.
    const gpuPerFrameMs = frames.slice(1).map((end, index) => busyMilliseconds(gpuTasks, frames[index], end));

    const animation = activeRuns(frames);
    const activeTotalMs = animation.runs.reduce((sum, run) => sum + (run.end - run.start) / 1000, 0);
    const active = {
        frameCount: animation.runs.reduce((sum, run) => sum + run.frames, 0),
        runCount: animation.runs.length,
        idleGapCount: animation.idleGapCount,
        fps: activeTotalMs > 0 ? (animation.intervalsMs.length * 1000) / activeTotalMs : null,
        frameMsP50: percentile(animation.intervalsMs, 0.5),
        frameMsP95: percentile(animation.intervalsMs, 0.95),
        slowFrameCount: animation.intervalsMs.filter((interval) => interval > SLOW_FRAME_MS).length,
    };
    const tickTimes = marks.tick
        ? events.filter((event) => event.name === marks.tick && event.ts >= windowStart && event.ts <= windowEnd).map((event) => event.ts).sort((a, b) => a - b)
        : [];
    const timings = tickTimings(tickTimes, frames, animation.runs, windowEnd);
    const longTaskDurations = mainTasks
        .filter((task) => task.ts >= windowStart && task.ts <= windowEnd && (task.dur ?? 0) / 1000 > LONG_TASK_MS)
        .map((task) => (task.dur ?? 0) / 1000);
    const mainBusyMs = busyMilliseconds(mainTasks, windowStart, windowEnd);
    const starTotalMs = measures.starMeasuresMs.reduce((sum, value) => sum + value, 0);
    const overlayTotalMs = measures.overlayMeasuresMs.reduce((sum, value) => sum + value, 0);
    const frameCount = frames.length;
    const perFrame = (total) => (frameCount > 0 ? total / frameCount : null);
    const gpuBusyMs = busyMilliseconds(gpuTasks, windowStart, windowEnd);

    return {
        windowSeconds,
        frameSource: drawFrames.length > 0 ? 'DrawFrame' : 'FireAnimationFrame',
        drawFrameCount: drawFrames.length,
        animationFrameCount: animationFrames.length,
        fps: windowSeconds > 0 ? frameCount / windowSeconds : null,
        frameMsP50: percentile(intervalsMs, 0.5),
        frameMsP95: percentile(intervalsMs, 0.95),
        active,
        ticks: {
            count: timings.count,
            withoutFrame: timings.withoutFrame,
            overlapped: timings.overlapped,
            latencyMsP50: percentile(timings.latencyMs, 0.5),
            latencyMsP95: percentile(timings.latencyMs, 0.95),
            settleMsP50: percentile(timings.settleMs, 0.5),
            settleMsP95: percentile(timings.settleMs, 0.95),
        },
        longTasks: { count: longTaskDurations.length, maxMs: longTaskDurations.length > 0 ? Math.max(...longTaskDurations) : 0 },
        mainThread: {
            found: mainKey !== null && mainTasks.length > 0,
            busyMs: mainBusyMs,
            busyMsPerFrame: perFrame(mainBusyMs),
            starProjectUploadMsPerFrame: perFrame(starTotalMs),
            overlayDrawMsPerFrame: perFrame(overlayTotalMs),
            otherMsPerFrame: perFrame(Math.max(0, mainBusyMs - starTotalMs - overlayTotalMs)),
        },
        starProjectUpload: { calls: measures.starMeasuresMs.length, msP50: percentile(measures.starMeasuresMs, 0.5), msP95: percentile(measures.starMeasuresMs, 0.95) },
        overlayDraw: { calls: measures.overlayMeasuresMs.length, msP50: percentile(measures.overlayMeasuresMs, 0.5), msP95: percentile(measures.overlayMeasuresMs, 0.95) },
        gpuProcess: {
            found: gpuProcessIds.size > 0,
            busyMs: gpuBusyMs,
            busyMsPerFrame: perFrame(gpuBusyMs),
            msP50: percentile(gpuPerFrameMs, 0.5),
            msP95: percentile(gpuPerFrameMs, 0.95),
        },
    };
}

/**
 * Counts the frames drawn while the page was left alone.
 *
 * @param {Array<object>} events - The trace's `traceEvents`.
 * @param {{start?: string, end?: string}} [marks] - The names of the marks that bound the idle window.
 * @return {{windowSeconds: number, drawFrameCount: number, drawsPerSecond: number | null}} The window length, the
 *   `DrawFrame` events inside it and the draws per second (`null` if a mark is missing or the window is empty).
 */
export function summarizeIdle(events, marks = { start: IDLE_START_MARK, end: IDLE_END_MARK }) {
    const startMark = events.find((event) => event.name === marks.start);
    const endMark = events.find((event) => event.name === marks.end);
    if (!startMark || !endMark || endMark.ts <= startMark.ts) return { windowSeconds: 0, drawFrameCount: 0, drawsPerSecond: null };
    const windowSeconds = (endMark.ts - startMark.ts) / 1e6;
    const drawFrameCount = events.filter((event) => event.name === 'DrawFrame' && event.ts >= startMark.ts && event.ts <= endMark.ts).length;
    return { windowSeconds, drawFrameCount, drawsPerSecond: drawFrameCount / windowSeconds };
}
