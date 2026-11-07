/**
 * @fileoverview Works out the guiding RMS (root mean square error) shown in the
 * status header. The value comes from the newest guiding sample that the
 * backend already pushes in `telescope.guidingHistory`. A stale sample is never
 * shown, so a stopped guider cannot leave an old good-looking number on screen.
 */

import { useEffect, useState } from 'react';

/**
 * Oldest age, in seconds, that the newest guiding sample may have and still be
 * shown. The guider normally adds a sample every second or two, so 60 s means
 * the guider has clearly stopped (or lost its star). Past that we show a dash.
 */
export const GUIDING_SAMPLE_MAX_AGE_SECONDS = 60;

/** How often, in milliseconds, the hook re-checks whether the sample went stale. */
const STALENESS_CHECK_INTERVAL_MS = 5000;

/** Current guiding error in arcseconds, or null when nothing fresh is available. */
export interface GuidingRms {
  /** Total RMS: sqrt(rmsRa^2 + rmsDec^2). */
  total: number;
  /** RMS along right ascension. */
  ra: number;
  /** RMS along declination. */
  dec: number;
}

/**
 * Computes the guiding RMS from the newest sample.
 *
 * @param history - Guiding samples, oldest first, as sent by the backend.
 * @param nowMilliseconds - Current time as a Unix timestamp in milliseconds.
 * @returns The RMS values, or null if there is no sample, it is older than
 *   `GUIDING_SAMPLE_MAX_AGE_SECONDS`, or its RMS fields are not numbers.
 */
export function computeGuidingRms(
  history: Record<string, any>[] | undefined,
  nowMilliseconds: number,
): GuidingRms | null {
  if (!history || history.length === 0) return null;
  const newest = history[history.length - 1];
  const ra = Number(newest?.rmsRa);
  const dec = Number(newest?.rmsDec);
  const sampleTimeSeconds = Number(newest?.time);
  if (newest?.rmsRa == null || newest?.rmsDec == null) return null;
  if (!Number.isFinite(ra) || !Number.isFinite(dec) || !Number.isFinite(sampleTimeSeconds)) return null;
  const ageSeconds = nowMilliseconds / 1000 - sampleTimeSeconds;
  if (ageSeconds > GUIDING_SAMPLE_MAX_AGE_SECONDS) return null;
  return { total: Math.hypot(ra, dec), ra, dec };
}

/**
 * React hook that returns the current guiding RMS and re-checks the sample's
 * age on a timer, so the value turns into a dash even if no new data arrives.
 *
 * @param history - Guiding samples from `telescope.guidingHistory`.
 * @returns The RMS values, or null when there is nothing fresh to show.
 */
export function useGuidingRms(history: Record<string, any>[] | undefined): GuidingRms | null {
  const [nowMilliseconds, setNowMilliseconds] = useState<number>(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNowMilliseconds(Date.now()), STALENESS_CHECK_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);

  return computeGuidingRms(history, nowMilliseconds);
}
