/**
 * @file test_planetariumPause.test.tsx
 * @description Tests that the Planetarium's moving parts stop while the display is
 * paused (hidden by React `Activity`) and resume, with their state kept, when it
 * is shown again: the time controller's playback and the arrow-key listener.
 */

import React, { Activity } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render } from '@testing-library/react';
import { useTimeController, type TimeController } from '../hooks/useTimeController';
import { useSkyMapInput } from '../hooks/useSkyMapInput';

const latest: { time: TimeController | null } = { time: null };
/** The newest time controller the hooks gave. */
const time = (): TimeController => latest.time as TimeController;
const pan = {
  canvasRef: { current: null } as React.RefObject<HTMLCanvasElement | null>,
  centerAzRef: { current: 0 },
  centerAltRef: { current: 0 },
  targetAzRef: { current: 100 },
  targetAltRef: { current: 0 },
  localFOVRef: { current: 60 },
  targetFovRef: { current: 60 },
  setTrackingMode: vi.fn(),
};

/** Runs the Planetarium hooks that own timers and key listeners. */
const SkyHooks: React.FC = () => {
  const controller = useTimeController();
  React.useEffect(() => {
    latest.time = controller;
  });
  useSkyMapInput(pan as unknown as Parameters<typeof useSkyMapInput>[0]);
  return null;
};

describe('Planetarium while paused', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    pan.targetAzRef.current = 100;
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** Time playback does not advance while paused, and continues from the same offset when shown. */
  it('stops time playback while paused and resumes it from the same offset', () => {
    const { rerender } = render(
      <Activity mode="visible">
        <SkyHooks />
      </Activity>
    );
    act(() => time().setIsTimePlaying(true));
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    const offsetWhenHidden = time().timeOffsetMinutes;
    expect(offsetWhenHidden).toBeGreaterThan(0);

    rerender(
      <Activity mode="hidden">
        <SkyHooks />
      </Activity>
    );
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(time().timeOffsetMinutes).toBe(offsetWhenHidden);

    rerender(
      <Activity mode="visible">
        <SkyHooks />
      </Activity>
    );
    expect(time().isTimePlaying).toBe(true);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(time().timeOffsetMinutes).toBeGreaterThan(offsetWhenHidden);
  });

  /** Arrow keys pan the sky only while the Planetarium is shown. */
  it('ignores arrow keys while paused and pans again when shown', () => {
    const { rerender } = render(
      <Activity mode="hidden">
        <SkyHooks />
      </Activity>
    );
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    expect(pan.targetAzRef.current).toBe(100);

    rerender(
      <Activity mode="visible">
        <SkyHooks />
      </Activity>
    );
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    expect(pan.targetAzRef.current).toBeGreaterThan(100);
  });

  /** While time plays, the offset read per frame grows smoothly between the 100 ms steps. */
  it('carries the playback offset forward between steps', () => {
    render(<SkyHooks />);
    act(() => time().setIsTimePlaying(true));
    const samples: number[] = [];
    for (let frame = 0; frame < 6; frame += 1) {
      act(() => {
        vi.advanceTimersByTime(16);
      });
      samples.push(time().getOffsetMinutesAt(performance.now()));
    }
    for (let index = 1; index < samples.length; index += 1) {
      expect(samples[index]).toBeGreaterThan(samples[index - 1]);
    }
    // 1x playback is 0.001 minutes per millisecond.
    expect(samples[5] - samples[4]).toBeCloseTo(16 * 0.001, 4);
  });
});
