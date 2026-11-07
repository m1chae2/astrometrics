/**
 * @fileoverview Unit tests for electron/perf_metrics.js, the optional sampler
 * that writes Electron process metrics to a file for the TPM runs. Checks the
 * JSON line format, that nothing starts without the environment variable, and
 * that a sample reaches the file when it is set.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { formatMetricLines, formatNotificationLine, startPerfMetrics, watchNotifications, writeGpuReport } from '../../electron/perf_metrics.js';

const sampleMetrics = [
  { type: 'Browser', pid: 11, cpu: { percentCPUUsage: 1.5 }, memory: { workingSetSize: 100, peakWorkingSetSize: 120 } },
  { type: 'GPU', pid: 12, cpu: { percentCPUUsage: 0 }, memory: { workingSetSize: 50, peakWorkingSetSize: 60 } },
];

describe('perf_metrics', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('writes one JSON line per process with the named fields', () => {
    const lines = formatMetricLines(sampleMetrics, 1000).map((line) => JSON.parse(line));
    expect(lines).toEqual([
      { timestamp: 1000, type: 'Browser', pid: 11, cpuPercent: 1.5, workingSetKb: 100, peakWorkingSetKb: 120 },
      { timestamp: 1000, type: 'GPU', pid: 12, cpuPercent: 0, workingSetKb: 50, peakWorkingSetKb: 60 },
    ]);
  });

  it('does nothing without ASTROMETRICS_PERF_METRICS', () => {
    const fakeApp = { getAppMetrics: vi.fn(() => sampleMetrics) };
    expect(startPerfMetrics(fakeApp, { environment: {} })).toBeNull();
    expect(fakeApp.getAppMetrics).not.toHaveBeenCalled();
  });

  it('appends samples to the named file and stops on request', () => {
    vi.useFakeTimers();
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-perf-test-'));
    const file = path.join(folder, 'metrics.jsonl');
    const fakeApp = { getAppMetrics: vi.fn(() => sampleMetrics) };
    const stop = startPerfMetrics(fakeApp, { environment: { ASTROMETRICS_PERF_METRICS: file }, now: () => 5 });
    vi.advanceTimersByTime(2000);
    stop?.();
    vi.advanceTimersByTime(6000);
    const rows = fs.readFileSync(file, 'utf8').trim().split('\n');
    expect(rows).toHaveLength(4);
    fs.rmSync(folder, { recursive: true, force: true });
  });

  it('writes one gpu_report line with the feature status and the basic GPU info', async () => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-perf-gpu-'));
    const file = path.join(folder, 'metrics.jsonl');
    const fakeApp = {
      getGPUFeatureStatus: vi.fn(() => ({ webgl2: 'enabled_on' })),
      getGPUInfo: vi.fn(async () => ({ auxAttributes: { glRenderer: 'NVIDIA GeForce GTX 1650' } })),
    };
    await writeGpuReport(fakeApp, file, () => 7);
    const row = JSON.parse(fs.readFileSync(file, 'utf8').trim());
    expect(row).toMatchObject({ timestamp: 7, type: 'gpu_report', featureStatus: { webgl2: 'enabled_on' } });
    expect(fakeApp.getGPUInfo).toHaveBeenCalledWith('basic');
    fs.rmSync(folder, { recursive: true, force: true });
  });

  it('skips the gpu report quietly when the app cannot give one', async () => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-perf-nogpu-'));
    const file = path.join(folder, 'metrics.jsonl');
    await writeGpuReport({}, file, () => 7);
    expect(fs.existsSync(file)).toBe(false);
    fs.rmSync(folder, { recursive: true, force: true });
  });

  it('writes one desktop_notification line for each request the page sends', () => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-perf-notify-'));
    const file = path.join(folder, 'metrics.jsonl');
    let handler: ((event: unknown, request: unknown) => void) | undefined;
    const fakeIpc = { on: vi.fn((_channel: string, listener: typeof handler) => { handler = listener; }) };
    watchNotifications(fakeIpc, file, () => 9);
    expect(fakeIpc.on).toHaveBeenCalledWith('show-notification', expect.any(Function));
    handler?.({}, { title: 'Guiding lost', body: 'RMS 3 px' });
    const row = JSON.parse(fs.readFileSync(file, 'utf8').trim());
    expect(row).toEqual(JSON.parse(formatNotificationLine({ title: 'Guiding lost', body: 'RMS 3 px' }, 9)));
    expect(row).toMatchObject({ type: 'desktop_notification', title: 'Guiding lost' });
    fs.rmSync(folder, { recursive: true, force: true });
  });

  it('writes the gpu report a second time after the window has rendered', async () => {
    vi.useFakeTimers();
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-perf-gpu2-'));
    const file = path.join(folder, 'metrics.jsonl');
    const fakeApp = {
      getAppMetrics: vi.fn(() => []),
      getGPUFeatureStatus: vi.fn(() => ({})),
      getGPUInfo: vi.fn(async () => ({})),
    };
    const stop = startPerfMetrics(fakeApp, { environment: { ASTROMETRICS_PERF_METRICS: file }, ipcMain: { on: vi.fn() } });
    await vi.advanceTimersByTimeAsync(25000);
    stop?.();
    const reports = fs.readFileSync(file, 'utf8').trim().split('\n').filter((line) => JSON.parse(line).type === 'gpu_report');
    expect(reports).toHaveLength(2);
    fs.rmSync(folder, { recursive: true, force: true });
  });
});
