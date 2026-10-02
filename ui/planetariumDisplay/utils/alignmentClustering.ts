/**
 * @module alignmentClustering
 * @fileoverview Groups raw plate-solve alignment attempts into discrete observing sessions
 * and computes statistical tracking, drift, and dispersion telemetry.
 */

import { AlignmentAttempt } from '../../common/types/backendTypes';

export interface ClusteredAlignmentSession {
  id: string;
  targetName: string;
  centroidRa: number;
  centroidDec: number;
  totalFrames: number;
  initialErrorArcsec: number;
  rmsTotal: number;
  rmsRa: number;
  rmsDec: number;
  driftSlopeRaArcsecPerMin: number;
  driftSlopeDecArcsecPerMin: number;
  startTime: number | null;
  endTime: number | null;
  elapsedSeconds: number;
  timeSeries: {
    elapsedSec: number;
    deltaRa: number;
    deltaDec: number;
    totalErr: number;
    timestamp: number;
  }[];
  rawAttempts: AlignmentAttempt[];
}

/**
 * Normalizes RA into degrees (0..360).
 *
 * @param {number | null | undefined} ra - RA in hours or degrees.
 * @returns {number} RA in degrees.
 */
function normalizeRa(ra: number | null | undefined): number {
  if (ra == null || isNaN(ra)) return 0;
  return ((ra % 360.0) + 360.0) % 360.0;
}

/**
 * Formats a duration in seconds into a compact string like "13m 20s" or "1h 05m".
 *
 * @param {number} seconds - Duration in seconds.
 * @returns {string} Formatted duration.
 */
export function formatDuration(seconds: number): string {
  if (seconds <= 0) return '0s';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins < 60) {
    return `${mins}m ${secs}s`;
  }
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}

/**
 * Clusters raw alignment attempts into discrete target sessions.
 * Consecutive attempts with the same targetName (or within 0.25 deg) are grouped.
 *
 * @param {AlignmentAttempt[]} rawAttempts - Raw alignment attempts from INDI/database.
 * @returns {ClusteredAlignmentSession[]} Clustered session records.
 */
export function clusterAlignmentAttempts(
  rawAttempts: AlignmentAttempt[]
): ClusteredAlignmentSession[] {
  const valid = rawAttempts.filter(
    (a) => a.ra != null && a.dec != null && !isNaN(a.ra) && !isNaN(a.dec)
  );

  if (valid.length === 0) return [];

  // Deduplicate attempts with identical timestamps (e.g. from repeated INDI sync log ingestion)
  const seenTimestamps = new Set<string>();
  const deduped: AlignmentAttempt[] = [];
  for (const a of valid) {
    const key = a.timestamp != null ? `${a.timestamp}_${a.targetName || ''}` : `${a.ra}_${a.dec}`;
    if (!seenTimestamps.has(key)) {
      seenTimestamps.add(key);
      deduped.push(a);
    }
  }

  // Sort chronologically by timestamp if available
  deduped.sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));

  // Cluster targets by targetName or tight spatial proximity (< 0.5 deg)
  const targetMap = new Map<string, AlignmentAttempt[]>();

  for (const attempt of deduped) {
    let key = attempt.targetName?.trim();
    if (!key) {
      // Find closest existing cluster within 0.5 deg
      const ra = normalizeRa(attempt.ra);
      const dec = attempt.dec ?? 0;
      let closestKey: string | null = null;
      let minDist = 0.5;

      for (const [existingKey, group] of targetMap.entries()) {
        const ref = group[0];
        const refRa = normalizeRa(ref.ra);
        const refDec = ref.dec ?? 0;
        const d = Math.hypot((ra - refRa) * Math.cos((refDec * Math.PI) / 180), dec - refDec);
        if (d < minDist) {
          minDist = d;
          closestKey = existingKey;
        }
      }
      key = closestKey ?? `Target_${attempt.ra?.toFixed(2)}_${attempt.dec?.toFixed(2)}`;
    }

    if (!targetMap.has(key)) {
      targetMap.set(key, []);
    }
    targetMap.get(key)!.push(attempt);
  }

  const clusters = Array.from(targetMap.values());

  return clusters.map((group, clusterIdx) => {
    const first = group[0];
    const last = group[group.length - 1];

    const targetName = first.targetName || (group.length > 1 ? `Target Session #${clusterIdx + 1}` : `Sync #${clusterIdx + 1}`);

    // Compute coordinate centroids
    let sumRa = 0;
    let sumDec = 0;
    group.forEach((att) => {
      sumRa += normalizeRa(att.ra);
      sumDec += att.dec ?? 0;
    });
    const centroidRa = sumRa / group.length;
    const centroidDec = sumDec / group.length;

    // Distinguish tracking sub-frames from coarse initial mount syncs (e.g. status='warning' or error > 120")
    const isTrackingAttempt = (a: AlignmentAttempt) => {
      const err = a.pointingErrorArcsec ?? Math.hypot(a.deltaRaArcsec ?? 0, a.deltaDecArcsec ?? 0);
      return a.status !== 'warning' && err <= 120.0;
    };

    const trackingAttempts = group.filter(isTrackingAttempt);
    // If we have actual tracking frames, use them for segmenting tracking runs and measuring jitter;
    // otherwise fall back to group (e.g. for standalone sync reticles).
    const activeTrackingGroup = trackingAttempts.length > 0 ? trackingAttempts : group;

    // Segment into contiguous tracking runs (break on > 2 hour / 7200s gap)
    const runs: AlignmentAttempt[][] = [];
    let currentRun: AlignmentAttempt[] = [activeTrackingGroup[0]];

    for (let i = 1; i < activeTrackingGroup.length; i++) {
      const prev = activeTrackingGroup[i - 1];
      const curr = activeTrackingGroup[i];
      const prevTs = prev.timestamp ?? 0;
      const currTs = curr.timestamp ?? 0;
      const dt = currTs - prevTs;

      // If gap exceeds 2 hours (7200 seconds), start a new tracking run
      if (prev.timestamp != null && curr.timestamp != null && dt > 7200) {
        runs.push(currentRun);
        currentRun = [curr];
      } else {
        currentRun.push(curr);
      }
    }
    runs.push(currentRun);

    // Initial slew error from the very first frame
    const initialDra = first.deltaRaArcsec ?? 0;
    const initialDdec = first.deltaDecArcsec ?? 0;
    const initialErrorArcsec = first.pointingErrorArcsec ?? Math.hypot(initialDra, initialDdec);

    // Compute cumulative exposure elapsed time and true tracking jitter variance across runs
    let totalElapsedSeconds = 0;
    let totalWeightedVarRa = 0;
    let totalWeightedVarDec = 0;
    let totalTrackingFrames = 0;

    for (const run of runs) {
      if (run.length > 1) {
        const rStart = run[0].timestamp ?? 0;
        const rEnd = run[run.length - 1].timestamp ?? 0;
        const rElapsed = rEnd >= rStart ? rEnd - rStart : (run.length - 1) * 5;
        totalElapsedSeconds += rElapsed;

        // Skip initial slew frame when run has >= 3 frames to exclude GoTo settling
        const trackingFrames = run.length >= 3 ? run.slice(1) : run;
        const n = trackingFrames.length;

        // Centroid of settled position for this run
        let meanRa = 0;
        let meanDec = 0;
        trackingFrames.forEach((a) => {
          meanRa += a.deltaRaArcsec ?? 0;
          meanDec += a.deltaDecArcsec ?? 0;
        });
        meanRa /= n;
        meanDec /= n;

        // Variance (jitter) around the settled mean position
        let runVarRa = 0;
        let runVarDec = 0;
        trackingFrames.forEach((a) => {
          const dRa = (a.deltaRaArcsec ?? 0) - meanRa;
          const dDec = (a.deltaDecArcsec ?? 0) - meanDec;
          runVarRa += dRa * dRa;
          runVarDec += dDec * dDec;
        });
        runVarRa /= n;
        runVarDec /= n;

        totalWeightedVarRa += runVarRa * n;
        totalWeightedVarDec += runVarDec * n;
        totalTrackingFrames += n;
      }
    }

    const rmsRa = totalTrackingFrames > 0 ? Math.sqrt(totalWeightedVarRa / totalTrackingFrames) : 0;
    const rmsDec = totalTrackingFrames > 0 ? Math.sqrt(totalWeightedVarDec / totalTrackingFrames) : 0;
    const rmsTotal = Math.sqrt(rmsRa * rmsRa + rmsDec * rmsDec);

    const startTime = first.timestamp ?? null;
    const endTime = last.timestamp ?? null;
    const elapsedSeconds = totalElapsedSeconds > 0 ? totalElapsedSeconds : (group.length - 1) * 5;

    // Time series for telemetry charts
    const t0 = startTime ?? 0;
    const timeSeries = group.map((att, idx) => {
      const dRa = att.deltaRaArcsec ?? 0;
      const dDec = att.deltaDecArcsec ?? 0;
      const err = att.pointingErrorArcsec ?? Math.hypot(dRa, dDec);
      const ts = att.timestamp ?? t0 + idx * 5;
      const elapsedSec = ts - t0;
      return {
        elapsedSec,
        deltaRa: dRa,
        deltaDec: dDec,
        totalErr: err,
        timestamp: ts,
      };
    });

    // Linear regression for drift slopes (arcsec / min)
    let driftSlopeRaArcsecPerMin = 0;
    let driftSlopeDecArcsecPerMin = 0;
    if (timeSeries.length >= 2 && elapsedSeconds > 10) {
      const n = timeSeries.length;
      let sumT = 0;
      let sumRaPts = 0;
      let sumDecPts = 0;
      let sumTRa = 0;
      let sumTDec = 0;
      let sumTSq = 0;

      timeSeries.forEach((pt) => {
        const tMins = pt.elapsedSec / 60.0;
        sumT += tMins;
        sumRaPts += pt.deltaRa;
        sumDecPts += pt.deltaDec;
        sumTRa += tMins * pt.deltaRa;
        sumTDec += tMins * pt.deltaDec;
        sumTSq += tMins * tMins;
      });

      const denom = n * sumTSq - sumT * sumT;
      if (Math.abs(denom) > 1e-6) {
        driftSlopeRaArcsecPerMin = (n * sumTRa - sumT * sumRaPts) / denom;
        driftSlopeDecArcsecPerMin = (n * sumTDec - sumT * sumDecPts) / denom;
      }
    }

    return {
      id: `session_cluster_${clusterIdx}`,
      targetName,
      centroidRa,
      centroidDec,
      totalFrames: group.length,
      initialErrorArcsec,
      rmsTotal,
      rmsRa,
      rmsDec,
      driftSlopeRaArcsecPerMin,
      driftSlopeDecArcsecPerMin,
      startTime,
      endTime,
      elapsedSeconds,
      timeSeries,
      rawAttempts: group,
    };
  });
}
