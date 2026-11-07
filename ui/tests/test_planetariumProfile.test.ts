/**
 * @fileoverview Unit tests for the Planetarium pan-profile helpers: the trace
 * reducer in build/tpm/planetarium_profile.mjs and the optional timing marks in
 * ui/planetariumDisplay/utils/perfMarks.ts. A small made-up trace checks the
 * frame rate, the main-thread split and the GPU busy time; the marks must record
 * nothing unless the perf flag is set.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { PAN_END_MARK, PAN_START_MARK, WHEEL_TICK_MARK, ZOOM_END_MARK, ZOOM_START_MARK, activeRuns, summarizeTrace as summarizeTraceUntyped } from '../../build/tpm/planetarium_profile.mjs';
import { perfEnd, perfStart } from '../planetariumDisplay/utils/perfMarks';

/** The reducer is plain JavaScript, so its result is read field by field in these tests. */
const summarizeTrace = summarizeTraceUntyped as (...args: unknown[]) => any;

describe('summarizeTrace', () => {
  it('reduces a small trace to frame rate, main-thread split and GPU busy time', () => {
    const events = [
      { ph: 'M', name: 'process_name', pid: 2, tid: 0, args: { name: 'GPU Process' } },
      { ph: 'R', name: PAN_START_MARK, pid: 1, tid: 1, ts: 1_000_000 },
      { ph: 'R', name: PAN_END_MARK, pid: 1, tid: 1, ts: 2_000_000 },
      { ph: 'X', name: 'RunTask', pid: 1, tid: 1, ts: 1_100_000, dur: 400_000 },
      { ph: 'X', name: 'RunTask', pid: 2, tid: 5, ts: 1_100_000, dur: 100_000 },
      ...[1_100_000, 1_300_000, 1_500_000, 1_700_000, 1_900_000].map((ts) => ({ ph: 'I', name: 'DrawFrame', pid: 1, tid: 2, ts })),
    ];
    const summary = summarizeTrace(events, { starMeasuresMs: [4, 6], overlayMeasuresMs: [10, 10] });
    expect(summary.windowSeconds).toBeCloseTo(1);
    expect(summary.fps).toBeCloseTo(5);
    expect(summary.frameMsP50).toBeCloseTo(200);
    expect(summary.mainThread.busyMs).toBeCloseTo(400);
    expect(summary.mainThread.starProjectUploadMsPerFrame).toBeCloseTo(2);
    expect(summary.mainThread.otherMsPerFrame).toBeCloseTo((400 - 10 - 20) / 5);
    expect(summary.gpuProcess.busyMs).toBeCloseTo(100);
    expect(summary.starProjectUpload.msP50).toBeCloseTo(5);
  });
});

describe('summarizeTrace with the zoom marks', () => {
  it('reduces the zoom window of the same trace on its own', () => {
    const events = [
      { ph: 'R', name: PAN_START_MARK, pid: 1, tid: 1, ts: 1_000_000 },
      { ph: 'R', name: PAN_END_MARK, pid: 1, tid: 1, ts: 2_000_000 },
      { ph: 'R', name: ZOOM_START_MARK, pid: 1, tid: 1, ts: 3_000_000 },
      { ph: 'R', name: ZOOM_END_MARK, pid: 1, tid: 1, ts: 4_000_000 },
      { ph: 'X', name: 'RunTask', pid: 1, tid: 1, ts: 3_100_000, dur: 200_000 },
      ...[1_100_000, 1_900_000].map((ts) => ({ ph: 'I', name: 'DrawFrame', pid: 1, tid: 2, ts })),
      ...[3_100_000, 3_400_000, 3_700_000, 3_900_000].map((ts) => ({ ph: 'I', name: 'DrawFrame', pid: 1, tid: 2, ts })),
    ];
    const measures = { starMeasuresMs: [], overlayMeasuresMs: [] };
    const zoom = summarizeTrace(events, measures, { start: ZOOM_START_MARK, end: ZOOM_END_MARK });
    const pan = summarizeTrace(events, measures);
    expect(zoom.windowSeconds).toBeCloseTo(1);
    expect(zoom.fps).toBeCloseTo(4);
    expect(zoom.mainThread.busyMs).toBeCloseTo(200);
    expect(pan.fps).toBeCloseTo(2);
    expect(pan.mainThread.busyMs).toBeCloseTo(0);
  });
});

describe('summarizeTrace for an on-demand renderer', () => {
  // Three wheel ticks 200 ms apart. Each one draws frames every 16 ms for a while, then the renderer idles.
  const draw = (ts: number) => ({ ph: 'I', name: 'DrawFrame', pid: 1, tid: 2, ts });
  const burst = (start: number, count: number) => Array.from({ length: count }, (_, index) => draw(start + index * 16_000));
  const events = [
    { ph: 'R', name: ZOOM_START_MARK, pid: 1, tid: 1, ts: 1_000_000 },
    { ph: 'R', name: ZOOM_END_MARK, pid: 1, tid: 1, ts: 2_000_000 },
    { ph: 'R', name: WHEEL_TICK_MARK, pid: 1, tid: 1, ts: 1_100_000 },
    { ph: 'R', name: WHEEL_TICK_MARK, pid: 1, tid: 1, ts: 1_300_000 },
    { ph: 'R', name: WHEEL_TICK_MARK, pid: 1, tid: 1, ts: 1_500_000 },
    // Latency 8 ms, easing of 5 frames (64 ms after the first frame).
    ...burst(1_108_000, 5),
    // Latency 10 ms, one slow frame (30 ms) in the middle.
    ...[1_310_000, 1_326_000, 1_356_000, 1_372_000].map(draw),
    // Latency 12 ms, 4 frames.
    ...burst(1_512_000, 4),
    { ph: 'X', name: 'RunTask', pid: 1, tid: 1, ts: 1_200_000, dur: 80_000 },
    { ph: 'X', name: 'RunTask', pid: 1, tid: 1, ts: 1_400_000, dur: 20_000 },
  ];
  const summary = summarizeTrace(events, { starMeasuresMs: [], overlayMeasuresMs: [] }, { start: ZOOM_START_MARK, end: ZOOM_END_MARK, tick: WHEEL_TICK_MARK });

  it('leaves the idle gaps between ticks out of the frame times', () => {
    expect(summary.active.runCount).toBe(3);
    expect(summary.active.idleGapCount).toBe(2);
    expect(summary.active.frameCount).toBe(13);
    expect(summary.active.frameMsP50).toBeCloseTo(16);
    expect(summary.active.frameMsP95).toBeLessThan(30.1);
    expect(summary.active.slowFrameCount).toBe(1);
    // The old measure over all intervals sees the 190 ms idle gaps.
    expect(summary.frameMsP95).toBeGreaterThan(100);
  });

  it('measures each tick from the wheel event to the first frame and to the last frame of its easing', () => {
    expect(summary.ticks.count).toBe(3);
    expect(summary.ticks.withoutFrame).toBe(0);
    expect(summary.ticks.overlapped).toBe(0);
    expect(summary.ticks.latencyMsP50).toBeCloseTo(10);
    expect(summary.ticks.settleMsP50).toBeCloseTo(72, 0);
  });

  it('counts main-thread tasks over 50 ms', () => {
    expect(summary.longTasks.count).toBe(1);
    expect(summary.longTasks.maxMs).toBeCloseTo(80);
  });

  it('splits frames into runs at gaps of 100 ms or more', () => {
    const { runs, intervalsMs } = activeRuns([0, 16_000, 32_000, 200_000, 216_000]);
    expect(runs.map((run: { frames: number }) => run.frames)).toEqual([3, 2]);
    expect(intervalsMs).toEqual([16, 16, 16]);
  });
});

describe('perfMarks', () => {
  afterEach(() => {
    delete window.__ASTROMETRICS_PERF__;
    vi.restoreAllMocks();
  });

  it('does nothing without the flag', () => {
    const measure = vi.spyOn(performance, 'measure');
    expect(perfStart()).toBeNull();
    perfEnd('planetarium:overlay_draw', perfStart());
    expect(measure).not.toHaveBeenCalled();
  });

  it('records a measure when the flag is set', () => {
    window.__ASTROMETRICS_PERF__ = true;
    const measure = vi.spyOn(performance, 'measure').mockImplementation(() => ({}) as PerformanceMeasure);
    perfEnd('planetarium:overlay_draw', perfStart());
    expect(measure).toHaveBeenCalledWith('planetarium:overlay_draw', expect.objectContaining({ start: expect.any(Number) }));
  });
});
