/**
 * @file useStackIdentification.ts
 * @description The star names of the stack on screen, for the Astrometry button. It reads the saved
 * names; when there are none, `start` asks the backend to make them as a job of the analysis type (so
 * the Analyze Target button shows it as running, and its Cancel button stops it). While the job runs,
 * the state is read again every two seconds for its progress. `useJobEndWatcher` follows the job,
 * and the names are read once more when it ends.
 */
import { useCallback, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchStackIdentification,
  startStackIdentification,
  type AstrometryOverlayStar,
  type StackIdentificationState,
} from '../../common/services/astronomyService';
import { reportError } from '../../common/utils/reportError';
import { useJobEndWatcher } from './useJobEndWatcher';

/** How often the state is read while a job is naming the stars, in milliseconds. */
const RUNNING_POLL_MS = 2000;

/** What the Astrometry button shows for a stack. */
export interface StackIdentificationView {
  /** The stars to draw: matched, unmatched and missed catalog stars. */
  stars: AstrometryOverlayStar[];
  /** True while the names are being made. */
  running: boolean;
  /** One line for the viewer header: progress, the counts and notes, or why it failed. */
  statusText: string | null;
  /** True when saved names exist for the stack as it is. */
  hasResult: boolean;
  /** True once the first read has answered. */
  isLoaded: boolean;
  /** Starts the job that makes the names. */
  start: () => Promise<void>;
}

/**
 * Writes the header line for a stack's star names.
 *
 * @param state The backend's answer.
 * @returns The line, or null.
 */
export function describeStackIdentification(state: StackIdentificationState | undefined): string | null {
  if (!state) return null;
  if (state.running) {
    const share = state.progress != null ? ` ${Math.round(state.progress * 100)}%` : '';
    return `Naming the stack's stars${share}${state.message ? ` - ${state.message}` : ''}`;
  }
  if (state.identification) {
    const result = state.identification;
    const counts = `${result.matchedCount.toLocaleString('en-US')} named, `
      + `${result.unmatchedCount.toLocaleString('en-US')} with no catalog star, `
      + `${result.catalogOnlyCount.toLocaleString('en-US')} catalog stars not detected`;
    return [counts, ...result.notes].join('. ');
  }
  if (state.error) return `Naming the stars failed: ${state.error}`;
  return null;
}

/**
 * Reads and makes the star names of the stack on screen.
 *
 * @param targetId The selected target.
 * @param stackPath The stack shown, or null when a raw frame is shown.
 * @param enabled True while the Astrometry overlay is on.
 * @returns What the button and the viewer show.
 */
export function useStackIdentification(
  targetId: string | null,
  stackPath: string | null,
  enabled: boolean,
): StackIdentificationView {
  const queryClient = useQueryClient();
  // The job started by `start`, followed until it ends.
  const [jobId, setJobId] = useState<string | null>(null);
  const queryKey = ['stackIdentification', targetId, stackPath];
  const query = useQuery({
    queryKey,
    queryFn: () => fetchStackIdentification(targetId as string, stackPath as string),
    enabled: Boolean(targetId && stackPath) && enabled,
    refetchInterval: (current) => (current.state.data?.running || jobId ? RUNNING_POLL_MS : false),
    staleTime: 10_000,
  });
  const state = query.data;

  // When the job ends, read the names once more and stop following it.
  useJobEndWatcher(jobId, () => {
    setJobId(null);
    void queryClient.invalidateQueries({ queryKey: ['stackIdentification', targetId, stackPath] });
  });
  useEffect(() => {
    setJobId(null);
  }, [targetId, stackPath]);

  const start = useCallback(async () => {
    if (!targetId || !stackPath) return;
    try {
      const reply = await startStackIdentification(targetId, stackPath);
      setJobId(reply.jobId);
      await queryClient.invalidateQueries({ queryKey: ['stackIdentification', targetId, stackPath] });
    } catch (failure) {
      reportError(failure instanceof Error ? failure : new Error(String(failure)), 'backend');
    }
  }, [targetId, stackPath, queryClient]);

  useEffect(() => {
    if (query.error) {
      reportError(query.error instanceof Error ? query.error : new Error(String(query.error)), 'backend');
    }
  }, [query.error]);

  return {
    stars: enabled && state?.identification ? state.identification.stars : [],
    running: Boolean(state?.running) || jobId !== null,
    statusText: enabled ? describeStackIdentification(state) : null,
    hasResult: Boolean(state?.identification),
    isLoaded: query.isSuccess,
    start,
  };
}
