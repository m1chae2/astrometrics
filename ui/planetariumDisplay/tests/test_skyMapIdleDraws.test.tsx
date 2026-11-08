/**
 * @fileoverview Counts how many times the sky map draws in each situation: none while the
 * view is idle, until the pan or zoom settles after a gesture, and every frame while time
 * plays. The browser's frame callback and clock are driven by hand, and the overlay layers
 * and the WebGL star field are replaced by fakes, so the count is exactly the number of
 * times the render loop decided the picture had changed.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render } from '@testing-library/react';

const drawCount = { value: 0 };

vi.mock('../utils/perfMarks', () => ({
  perfStart: () => 0,
  perfEnd: (name: string) => {
    if (name === 'planetarium:overlay_draw') drawCount.value += 1;
  },
}));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
  useTelescopeStatus: () => ({ telemetry: { ra: '-', dec: '-' }, telescopeConnection: null }),
}));
vi.mock('../webgl/StarFieldRenderer', () => ({
  StarFieldRenderer: class {
    render() {}
    dispose() {}
  },
}));
vi.mock('../../common/utils/emitToast', () => ({ emitToast: vi.fn() }));

import { CelestialSkyMap } from '../components/CelestialSkyMap';

const FRAME_MS = 1000 / 60;
let pendingFrame: FrameRequestCallback | null = null;

/** Runs the given number of 60 Hz animation frames, advancing the fake clock for each. */
function runFrames(count: number): void {
  for (let frame = 0; frame < count; frame += 1) {
    vi.advanceTimersByTime(FRAME_MS);
    const callback = pendingFrame;
    pendingFrame = null;
    if (callback) act(() => callback(performance.now()));
  }
}

/** A stand-in for a canvas context: any method can be called and returns another stand-in. */
function fakeContext(): any {
  const target = () => undefined;
  return new Proxy(target, { get: (_t, key) => (key === 'measureText' ? () => ({ width: 10 }) : fakeContext()), apply: () => fakeContext() });
}

/** Renders the sky map with plain props and returns the render result. */
function renderMap(simulationDate?: Date) {
  const view = render(
    <CelestialSkyMap
      center={null} sources={[]} targets={[]} location={{ latitude: 45.76, longitude: -110.74 } as any}
      showStars showFOV showEnvironment showGrid showCatalog showConstellations constellationLines={[]}
      showTelescope simulationDate={simulationDate} fov={60} onFOVChange={vi.fn()} onSelectSource={vi.fn()}
      onRightClickSource={vi.fn()} selectedTargetId="" slewRequestId={0} onCenterChange={vi.fn()}
    />,
  );
  return view;
}

describe('sky map draws', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-06T06:00:00Z'));
    drawCount.value = 0;
    pendingFrame = null;
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      pendingFrame = callback;
      return 1;
    });
    vi.stubGlobal('cancelAnimationFrame', () => {
      pendingFrame = null;
    });
    Object.defineProperty(HTMLElement.prototype, 'offsetParent', { configurable: true, get: () => document.body });
    HTMLCanvasElement.prototype.getBoundingClientRect = () =>
      ({ width: 1000, height: 600, left: 0, top: 0, right: 1000, bottom: 600, x: 0, y: 0, toJSON() {} }) as DOMRect;
    HTMLCanvasElement.prototype.getContext = (() => fakeContext()) as any;
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  /** An untouched live map draws its first frames, then almost nothing for ten seconds. */
  it('draws almost nothing while idle', () => {
    renderMap();
    runFrames(10);
    drawCount.value = 0;
    runFrames(600);
    expect(drawCount.value).toBeLessThanOrEqual(2);
  });

  /** A wheel zoom draws while easing, then stops drawing once it settles. */
  it('draws during a zoom and stops when it settles', () => {
    const canvas = renderMap().container.querySelector('canvas') as HTMLCanvasElement;
    runFrames(10);
    drawCount.value = 0;
    fireEvent.wheel(canvas, { deltaY: -100 });
    runFrames(120);
    const afterSettle = drawCount.value;
    expect(afterSettle).toBeGreaterThan(5);
    expect(afterSettle).toBeLessThan(60);
    runFrames(600);
    // Only the slow sidereal drift (one draw every few seconds at this zoom) is left.
    expect(drawCount.value - afterSettle).toBeLessThanOrEqual(2);
  });

  /** A drag draws while the camera catches up with the pointer, then stops. */
  it('draws during a pan and stops when the camera reaches its target', () => {
    const canvas = renderMap().container.querySelector('canvas') as HTMLCanvasElement;
    runFrames(10);
    drawCount.value = 0;
    fireEvent.pointerDown(canvas, { clientX: 500, clientY: 300, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 400, clientY: 280, pointerId: 1 });
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    runFrames(180);
    const afterSettle = drawCount.value;
    expect(afterSettle).toBeGreaterThan(5);
    runFrames(600);
    // Only the slow sidereal drift (one draw every few seconds at this zoom) is left.
    expect(drawCount.value - afterSettle).toBeLessThanOrEqual(2);
  });

  /** A simulated date that moves every frame (time playback) draws every frame. */
  it('draws every frame while time plays', () => {
    const view = renderMap(new Date('2026-11-06T06:00:00Z'));
    runFrames(5);
    drawCount.value = 0;
    // The time controller moves the offset; here the date prop itself steps by 10 minutes a frame.
    for (let frame = 0; frame < 30; frame += 1) {
      view.rerender(
        <CelestialSkyMap
          center={null} sources={[]} targets={[]} location={{ latitude: 45.76, longitude: -110.74 } as any}
          showStars showFOV showEnvironment showGrid showCatalog showConstellations constellationLines={[]}
          showTelescope simulationDate={new Date(Date.UTC(2026, 10, 6, 6, 10 * (frame + 1)))} fov={60}
          onFOVChange={vi.fn()} onSelectSource={vi.fn()} onRightClickSource={vi.fn()} selectedTargetId=""
          slewRequestId={0} onCenterChange={vi.fn()}
        />,
      );
      runFrames(1);
    }
    expect(drawCount.value).toBeGreaterThanOrEqual(30);
  });
});
