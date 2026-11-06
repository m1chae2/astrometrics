/**
 * @fileoverview Tests for the Planetarium zoom easing: target accumulation, the
 * per-frame ease, and how often the parent is told about the field of view.
 */

import { describe, it, expect } from 'vitest';
import {
  FOV_NOTIFY_INTERVAL_MS,
  FovNotifyGate,
  MAX_FOV_DEG,
  MIN_FOV_DEG,
  easeFovTowardTarget,
  nextZoomTarget,
} from '../planetariumDisplay/utils/zoomEasing';

/** Runs the ease frame by frame at 60 frames per second and counts parent notifications. */
function runToSettle(startFov: number, targetFov: number, gate: FovNotifyGate) {
  let fov = startFov;
  let nowMs = 0;
  let notifications = 0;
  let frames = 0;
  for (; frames < 1000; frames++) {
    nowMs += 1000 / 60;
    const step = easeFovTowardTarget(fov, targetFov);
    fov = step.fov;
    if (gate.update(fov, step.settled, nowMs) !== null) notifications += 1;
    if (step.settled) break;
  }
  return { fov, notifications, frames, durationMs: nowMs };
}

describe('nextZoomTarget', () => {
  it('accumulates several wheel ticks that arrive before a frame', () => {
    let target = 60;
    for (let tick = 0; tick < 3; tick++) target = nextZoomTarget(target, true);
    expect(target).toBeCloseTo(60 * 0.85 ** 3, 10);
  });

  it('stays inside the zoom range', () => {
    expect(nextZoomTarget(MIN_FOV_DEG, true)).toBe(MIN_FOV_DEG);
    expect(nextZoomTarget(MAX_FOV_DEG, false)).toBe(MAX_FOV_DEG);
  });
});

describe('easeFovTowardTarget', () => {
  it('moves part of the way each frame instead of jumping', () => {
    const step = easeFovTowardTarget(60, 30);
    expect(step.settled).toBe(false);
    expect(step.fov).toBeLessThan(60);
    expect(step.fov).toBeGreaterThan(30);
  });

  it('snaps to the target when close enough', () => {
    expect(easeFovTowardTarget(30.01, 30)).toEqual({ fov: 30, settled: true });
  });
});

describe('FovNotifyGate', () => {
  it('tells the parent once when a short zoom settles', () => {
    const { fov, notifications, durationMs } = runToSettle(60, 51, new FovNotifyGate(60));
    expect(fov).toBe(51);
    expect(durationMs).toBeLessThan(FOV_NOTIFY_INTERVAL_MS * 2);
    expect(notifications).toBeLessThanOrEqual(2);
    expect(notifications).toBeGreaterThanOrEqual(1);
  });

  it('is quiet between frames that change nothing', () => {
    const gate = new FovNotifyGate(40);
    expect(gate.update(40, true, 0)).toBeNull();
  });

  it('reports at most once per interval during a long gesture, then once on settle', () => {
    const gate = new FovNotifyGate(100);
    const reports: number[] = [];
    let nowMs = 0;
    // 3 seconds of frames with the FOV still moving, then a settled frame.
    for (let frame = 0; frame < 180; frame++) {
      nowMs += 1000 / 60;
      const reported = gate.update(100 - frame * 0.1, false, nowMs);
      if (reported !== null) reports.push(reported);
    }
    expect(reports.length).toBeLessThanOrEqual(Math.ceil(3000 / FOV_NOTIFY_INTERVAL_MS) + 1);
    expect(reports.length).toBeGreaterThan(1);
    const settled = gate.update(70, true, nowMs + 1);
    expect(settled).toBe(70);
    expect(gate.lastNotified).toBe(70);
  });

  it('does not echo a value the parent set from outside', () => {
    const gate = new FovNotifyGate(90);
    gate.adoptExternal(1);
    expect(gate.update(1, true, 0)).toBeNull();
  });
});
