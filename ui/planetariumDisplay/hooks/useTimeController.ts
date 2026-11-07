/**
 * @module useTimeController
 * @fileoverview Manages the sky map's simulated-time playback state: the
 * time-offset-from-now slider, play/pause, and playback speed used by the
 * on-screen time controller and the render loop's Local Sidereal Time calculation.
 *
 * Extracted from CelestialSkyMap to separate time-playback bookkeeping from the
 * canvas render loop and camera/input handling.
 */

import { useState, useRef, useEffect, useCallback } from 'react';

/** Return shape of useTimeController. */
export interface TimeController {
  /** Current time offset from the simulation/live clock, in minutes. */
  timeOffsetMinutes: number;
  setTimeOffsetMinutes: (value: number | ((prev: number) => number)) => void;
  /** Ref mirror of timeOffsetMinutes for reads inside the high-frequency render loop. */
  timeOffsetMinutesRef: React.MutableRefObject<number>;
  /** Whether sidereal time is currently auto-advancing. */
  isTimePlaying: boolean;
  setIsTimePlaying: (value: boolean) => void;
  /** Playback speed multiplier (1x-1000x) applied while isTimePlaying is true. */
  timeSpeed: number;
  setTimeSpeed: (value: number) => void;
  /**
   * The offset at a given moment, for the render loop. While time plays it adds the part of the
   * current step that has already passed, so the sky turns smoothly between steps.
   */
  getOffsetMinutesAt: (nowMs: number) => number;
}

/** Minutes of sky time that pass for each millisecond of real time at 1x playback. */
export const MINUTES_PER_MILLISECOND_AT_1X = 0.001;

/** The longest gap (ms) the offset is carried forward past the last step, if steps stop arriving. */
const MAX_CARRY_FORWARD_MS = 250;

/**
 * Manages simulated time-offset/play/speed state, including the interval that
 * advances the offset while playback is active.
 *
 * @func useTimeController
 * @returns {TimeController} Time-playback state, setters, and a ref mirror for the render loop.
 */
export const useTimeController = (): TimeController => {
  const [timeOffsetMinutes, setTimeOffsetMinutes] = useState<number>(0);
  const timeOffsetMinutesRef = useRef<number>(0);
  // The latest offset and the moment (performance.now) it was set. Playback steps write it
  // themselves, so the stamp is exact; any other change (a slider) is stamped when seen here.
  const offsetSampleRef = useRef<{ minutes: number; at: number }>({ minutes: 0, at: 0 });
  useEffect(() => {
    timeOffsetMinutesRef.current = timeOffsetMinutes;
    if (offsetSampleRef.current.minutes !== timeOffsetMinutes) {
      offsetSampleRef.current = { minutes: timeOffsetMinutes, at: performance.now() };
    }
  }, [timeOffsetMinutes]);

  const [isTimePlaying, setIsTimePlaying] = useState<boolean>(false);
  const [timeSpeed, setTimeSpeed] = useState<number>(1);
  const playbackRef = useRef({ playing: false, speed: 1 });
  useEffect(() => {
    playbackRef.current = { playing: isTimePlaying, speed: timeSpeed };
  }, [isTimePlaying, timeSpeed]);

  // Dynamic Sidereal Time play interval loop
  useEffect(() => {
    if (!isTimePlaying) return;
    // Each step adds the real time since the last one, so the render loop can carry the offset
    // forward between steps and land exactly on the next one.
    offsetSampleRef.current = { ...offsetSampleRef.current, at: performance.now() };
    const interval = setInterval(() => {
      const now = performance.now();
      const sample = offsetSampleRef.current;
      const nextMinutes = sample.minutes + (now - sample.at) * timeSpeed * MINUTES_PER_MILLISECOND_AT_1X;
      offsetSampleRef.current = { minutes: nextMinutes, at: now };
      setTimeOffsetMinutes(nextMinutes);
    }, 100);
    return () => clearInterval(interval);
  }, [isTimePlaying, timeSpeed]);

  const getOffsetMinutesAt = useCallback((nowMs: number): number => {
    const sample = offsetSampleRef.current;
    const playback = playbackRef.current;
    if (!playback.playing) return timeOffsetMinutesRef.current;
    const carriedMs = Math.min(Math.max(nowMs - sample.at, 0), MAX_CARRY_FORWARD_MS);
    return sample.minutes + carriedMs * playback.speed * MINUTES_PER_MILLISECOND_AT_1X;
  }, []);

  return {
    timeOffsetMinutes,
    setTimeOffsetMinutes,
    timeOffsetMinutesRef,
    isTimePlaying,
    setIsTimePlaying,
    timeSpeed,
    setTimeSpeed,
    getOffsetMinutesAt,
  };
};
