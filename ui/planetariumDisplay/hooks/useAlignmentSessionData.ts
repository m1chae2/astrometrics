/**
 * @module useAlignmentSessionData
 * @fileoverview Fetches and tracks plate-solve alignment session history for the
 * Planetarium display: the list of past sessions, the currently-selected
 * historical session's attempts/polar-alignment, cumulative tracking data across
 * all sessions, and the "active" (historical-or-live) view of each.
 *
 * Extracted from PlanetariumDisplay to keep the root component focused on
 * layout/composition rather than backend session-data bookkeeping.
 */

import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { AlignmentSessionSummary, AlignmentAttempt, PolarAlignmentStatus } from '../../common/types/backendTypes';

/**
 * Telemetry fields this hook needs from live telescope status, kept minimal
 * so it doesn't have to import the whole telemetry shape.
 */
export interface AlignmentSessionTelemetry {
  alignmentAttempts?: AlignmentAttempt[];
  polarAlignment?: PolarAlignmentStatus | null;
}

/** Return shape of useAlignmentSessionData. */
export interface AlignmentSessionData {
  /** All historical alignment sessions available for review. */
  availableSessions: AlignmentSessionSummary[];
  /** Currently selected historical session id, or null for live telemetry. */
  selectedSessionId: string | null;
  setSelectedSessionId: (id: string | null) => void;
  /** Whether a manual telescope-log sync is in progress. */
  isSyncingLogs: boolean;
  /** Triggers a telescope log sync, then refreshes session lists. */
  handleSyncLogs: () => Promise<void>;
  /** Alignment attempts for the active view: historical session if selected, else live telemetry. */
  activeAlignmentAttempts: AlignmentAttempt[];
  /** All recorded historical sessions' attempts merged with live telemetry. */
  activeCumulativeTrackingAttempts: AlignmentAttempt[];
  /** Polar alignment status for the active view: historical session if selected, else live telemetry. */
  activePolarAlignment: PolarAlignmentStatus | null;
}

/**
 * Manages alignment/tracking session history fetching and the historical-vs-live
 * selection logic shared by the toolbar's session picker and the sky map's
 * alignment/tracking-risk overlays.
 *
 * Each of the three fetches (session list, cumulative tracking, per-session
 * detail) is called from more than one place — mount, a manual sync, and a
 * session-selection change — so each keeps its own AbortController, aborting
 * its own previous in-flight call before starting a new one. That way a rapid
 * session-picker change or an unmount never leaves a superseded request
 * running in the background.
 *
 * @func useAlignmentSessionData
 * @param {AlignmentSessionTelemetry | undefined} telemetry - Live telescope telemetry.
 * @returns {AlignmentSessionData} Session list, selection state, and active attempt/polar-alignment views.
 */
export const useAlignmentSessionData = (
  telemetry: AlignmentSessionTelemetry | undefined,
): AlignmentSessionData => {
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [availableSessions, setAvailableSessions] = useState<AlignmentSessionSummary[]>([]);
  const [sessionAlignmentAttempts, setSessionAlignmentAttempts] = useState<AlignmentAttempt[] | null>(null);
  const [sessionPolarAlignment, setSessionPolarAlignment] = useState<PolarAlignmentStatus | null>(null);
  const [cumulativeTrackingAttempts, setCumulativeTrackingAttempts] = useState<AlignmentAttempt[] | null>(null);
  const [isSyncingLogs, setIsSyncingLogs] = useState<boolean>(false);

  const sessionsControllerRef = useRef<AbortController | null>(null);
  const trackingControllerRef = useRef<AbortController | null>(null);

  const refreshSessions = useCallback(async () => {
    sessionsControllerRef.current?.abort();
    const controller = new AbortController();
    sessionsControllerRef.current = controller;
    try {
      const sessions = await callBackend('telescope:list_alignment_sessions', {}, { signal: controller.signal });
      if (sessions) {
        setAvailableSessions(sessions);
      }
    } catch (err) {
      if (!(err instanceof Error && err.name === 'AbortError')) {
        console.error('Failed to list alignment sessions:', err);
      }
    }
  }, []);

  const refreshCumulativeTracking = useCallback(async () => {
    trackingControllerRef.current?.abort();
    const controller = new AbortController();
    trackingControllerRef.current = controller;
    try {
      const res = await callBackend(
        'telescope:get_session_alignment',
        { session_id: 'all' },
        { silent: true, signal: controller.signal }
      );
      if (res && res.alignmentAttempts) {
        setCumulativeTrackingAttempts(res.alignmentAttempts);
      }
    } catch (err) {
      if (!(err instanceof Error && err.name === 'AbortError')) {
        console.error('Failed to load cumulative tracking data:', err);
      }
    }
  }, []);

  useEffect(() => {
    refreshSessions();
    refreshCumulativeTracking();
    return () => {
      sessionsControllerRef.current?.abort();
      trackingControllerRef.current?.abort();
    };
  }, [refreshSessions, refreshCumulativeTracking]);

  const handleSyncLogs = useCallback(async () => {
    setIsSyncingLogs(true);
    try {
      await callBackend('telescope:sync_logs', {});
      await refreshSessions();
      await refreshCumulativeTracking();
    } catch (err) {
      console.error('Failed to sync telescope logs:', err);
    } finally {
      setIsSyncingLogs(false);
    }
  }, [refreshSessions, refreshCumulativeTracking]);

  useEffect(() => {
    if (!selectedSessionId) {
      setSessionAlignmentAttempts(null);
      setSessionPolarAlignment(null);
      return;
    }
    const controller = new AbortController();
    let active = true;
    const fetchSessionData = async () => {
      try {
        const res = await callBackend(
          'telescope:get_session_alignment',
          { session_id: selectedSessionId },
          { signal: controller.signal }
        );
        if (active && res) {
          setSessionAlignmentAttempts(res.alignmentAttempts || []);
          setSessionPolarAlignment(res.polarAlignment || null);
        }
      } catch (err) {
        if (!(err instanceof Error && err.name === 'AbortError')) {
          console.error(`Failed to fetch alignment for session ${selectedSessionId}:`, err);
        }
      }
    };
    fetchSessionData();
    return () => {
      active = false;
      controller.abort();
    };
  }, [selectedSessionId]);

  const activeAlignmentAttempts = useMemo(() => {
    if (selectedSessionId && sessionAlignmentAttempts !== null) {
      return sessionAlignmentAttempts;
    }
    return telemetry?.alignmentAttempts ?? [];
  }, [selectedSessionId, sessionAlignmentAttempts, telemetry?.alignmentAttempts]);

  const activeCumulativeTrackingAttempts = useMemo(() => {
    const historical = cumulativeTrackingAttempts ?? [];
    const live = telemetry?.alignmentAttempts ?? [];
    if (live.length === 0) return historical;
    if (historical.length === 0) return live;
    return [...historical, ...live];
  }, [cumulativeTrackingAttempts, telemetry?.alignmentAttempts]);

  const activePolarAlignment = useMemo(() => {
    if (selectedSessionId && sessionPolarAlignment !== null) {
      return sessionPolarAlignment;
    }
    return telemetry?.polarAlignment ?? null;
  }, [selectedSessionId, sessionPolarAlignment, telemetry?.polarAlignment]);

  return {
    availableSessions,
    selectedSessionId,
    setSelectedSessionId,
    isSyncingLogs,
    handleSyncLogs,
    activeAlignmentAttempts,
    activeCumulativeTrackingAttempts,
    activePolarAlignment,
  };
};
