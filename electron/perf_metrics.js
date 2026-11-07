/**
 * @fileoverview Optional process-metrics sampler for performance runs.
 *
 * When the environment variable `ASTROMETRICS_PERF_METRICS` names a file, the
 * main process calls `startPerfMetrics` at start-up. Every two seconds the
 * sampler asks Electron for the CPU and memory use of each of its processes
 * (main, renderer, GPU and so on) and appends one JSON line per process to
 * that file. It also writes one `gpu_report` line (what Electron says about
 * the graphics card and which GPU features are on), so a run can say whether
 * it drew on real hardware. The first report is written at start-up, before any
 * window has rendered, and is repeated once after `GPU_REPORT_REPEAT_DELAY_MS`
 * so that the later one shows the features the window really used. It also
 * writes one `desktop_notification` line each time the page asks for a native
 * notification, so a run can count them. Without the variable nothing is
 * started and nothing is written.
 * The technical performance measures (TPM) runner in `build/tpm/` reads the
 * file after a run.
 */

import fs from 'fs';

/** How often the sampler reads the metrics, in milliseconds. */
export const PERF_SAMPLE_INTERVAL_MS = 2000;

/** How long after start-up the graphics report is written a second time, in milliseconds (the window has rendered by then). */
export const GPU_REPORT_REPEAT_DELAY_MS = 20000;

/**
 * Turns the list Electron returns into one JSON line per process.
 *
 * @param {Array<object>} metrics - The result of `app.getAppMetrics()`.
 * @param {number} timestampMs - Sample time in milliseconds since the Unix epoch.
 * @return {string[]} One JSON string per process, without trailing newlines.
 */
export function formatMetricLines(metrics, timestampMs) {
  return metrics.map((processMetrics) =>
    JSON.stringify({
      timestamp: timestampMs,
      type: processMetrics.type,
      pid: processMetrics.pid,
      cpuPercent: processMetrics.cpu?.percentCPUUsage ?? null,
      workingSetKb: processMetrics.memory?.workingSetSize ?? null,
      peakWorkingSetKb: processMetrics.memory?.peakWorkingSetSize ?? null,
    }),
  );
}

/**
 * Writes one line that says what graphics hardware Electron found.
 *
 * Electron's `getGPUFeatureStatus()` says which features (WebGL, rasterization
 * and so on) run on the GPU or fall back to software. `getGPUInfo('basic')`
 * names the card and driver. Both are read once; a failure is ignored.
 *
 * @param {object} electronApp - Electron's `app`.
 * @param {string} metricsFile - The file to append the line to.
 * @param {Function} now - Clock returning milliseconds.
 * @return {Promise<void>} Resolves when the line is written or the read failed.
 */
export async function writeGpuReport(electronApp, metricsFile, now) {
  try {
    if (typeof electronApp.getGPUFeatureStatus !== 'function' || typeof electronApp.getGPUInfo !== 'function') return;
    const featureStatus = electronApp.getGPUFeatureStatus();
    const gpuInfo = await electronApp.getGPUInfo('basic');
    const line = JSON.stringify({ timestamp: now(), type: 'gpu_report', featureStatus, gpuInfo });
    fs.appendFileSync(metricsFile, `${line}\n`);
  } catch {
    // The report is optional; a failed read must never disturb the app.
  }
}

/**
 * Turns one native-notification request into a JSON line.
 *
 * @param {{title?: string, body?: string}} request - What the page sent to the `show-notification` channel.
 * @param {number} timestampMs - Time of the request in milliseconds since the Unix epoch.
 * @return {string} One JSON string, without a trailing newline.
 */
export function formatNotificationLine(request, timestampMs) {
  return JSON.stringify({
    timestamp: timestampMs,
    type: 'desktop_notification',
    title: String(request?.title ?? '').slice(0, 300),
    body: String(request?.body ?? '').slice(0, 500),
  });
}

/**
 * Appends a line to the notification file for every `show-notification` request the page sends.
 *
 * This listens next to the app's own handler (Electron allows several listeners on one channel), so it sees every
 * request, including one the app then drops (for example because of its cooldown). It never changes what the app does.
 *
 * @param {object} ipcMain - Electron's `ipcMain`.
 * @param {string} metricsFile - The file to append the lines to.
 * @param {Function} now - Clock returning milliseconds.
 * @return {void}
 */
export function watchNotifications(ipcMain, metricsFile, now) {
  ipcMain.on('show-notification', (_event, request) => {
    try {
      fs.appendFileSync(metricsFile, `${formatNotificationLine(request, now())}\n`);
    } catch {
      // Counting is optional; a failed write must never disturb the app.
    }
  });
}

/**
 * Starts the sampler if `ASTROMETRICS_PERF_METRICS` is set.
 *
 * @param {object} electronApp - Electron's `app`, which gives `getAppMetrics()`.
 * @param {object} [options] - Options.
 * @param {NodeJS.ProcessEnv} [options.environment] - Environment to read; defaults to `process.env`.
 * @param {Function} [options.now] - Clock returning milliseconds; defaults to `Date.now`.
 * @param {object} [options.ipcMain] - Electron's `ipcMain`. Native-notification requests are counted through it. When it is
 *   left out, notifications are not counted.
 * @return {(() => void) | null} A function that stops the sampler, or `null` if the variable is not set.
 */
export function startPerfMetrics(electronApp, { environment = process.env, now = Date.now, ipcMain = null } = {}) {
  const metricsFile = environment.ASTROMETRICS_PERF_METRICS;
  if (!metricsFile) return null;
  const sample = () => {
    try {
      const lines = formatMetricLines(electronApp.getAppMetrics(), now());
      if (lines.length > 0) fs.appendFileSync(metricsFile, `${lines.join('\n')}\n`);
    } catch {
      // A failed sample must never disturb the app; the next one tries again.
    }
  };
  sample();
  void writeGpuReport(electronApp, metricsFile, now);
  const repeatTimer = setTimeout(() => void writeGpuReport(electronApp, metricsFile, now), GPU_REPORT_REPEAT_DELAY_MS);
  repeatTimer.unref();
  if (ipcMain) watchNotifications(ipcMain, metricsFile, now);
  const timer = setInterval(sample, PERF_SAMPLE_INTERVAL_MS);
  timer.unref();
  return () => {
    clearInterval(timer);
    clearTimeout(repeatTimer);
  };
}
