/**
 * @module useTimeController
 * @fileoverview Manages the sky map's simulated-time playback state: the
 * time-offset-from-now slider, play/pause, and playback speed used by the
 * on-screen time controller and the render loop's Local Sidereal Time calculation.
 *
 * Extracted from CelestialSkyMap to separate time-playback bookkeeping from the
 * canvas render loop and camera/input handling.
 */

import { useState, useRef, useEffect } from 'react';

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
}

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
  useEffect(() => {
    timeOffsetMinutesRef.current = timeOffsetMinutes;
  }, [timeOffsetMinutes]);

  const [isTimePlaying, setIsTimePlaying] = useState<boolean>(false);
  const [timeSpeed, setTimeSpeed] = useState<number>(1);

  // Dynamic Sidereal Time play interval loop
  useEffect(() => {
    if (!isTimePlaying) return;
    const interval = setInterval(() => {
      setTimeOffsetMinutes(prev => prev + (timeSpeed * 0.1));
    }, 100);
    return () => clearInterval(interval);
  }, [isTimePlaying, timeSpeed]);

  return {
    timeOffsetMinutes,
    setTimeOffsetMinutes,
    timeOffsetMinutesRef,
    isTimePlaying,
    setIsTimePlaying,
    timeSpeed,
    setTimeSpeed,
  };
};
