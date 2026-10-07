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
import { AlignmentSessionSummary, AlignmentTargetSession, PolarAlignmentStatus } from '../../common/types/backendTypes';

/**
 * Telemetry fields this hook needs from live telescope status, kept minimal
 * so it doesn't have to import the whole telemetry shape.
 */
export interface AlignmentSessionTelemetry {
  /** Live plate solves grouped by target, as the library groups them. */
  alignmentTargets?: AlignmentTargetSession[];
  polarAlignment?: PolarAlignmentStatus | null;
}

/** Return shape of useAlignmentSessionData. */
export interface AlignmentSessionData {
  /** All historical alignment sessions available for review. */
  availableSessions: AlignmentSessionSummary[];
  /** Currently selected historical session id, or null for live telemetry. */
  selectedSessionId: string | null;
  setSelectedSessionId: (id: string | null) => void;
  /** Targets for the active view: historical session if selected, else live telemetry. */
  activeAlignmentTargets: AlignmentTargetSession[];
  /** Targets over every recorded night; the live targets until those have loaded. */
  activeCumulativeAlignmentTargets: AlignmentTargetSession[];
  /** Polar alignment status for the active view: historical session if selected, else live telemetry. */
  activePolarAlignment: PolarAlignmentStatus | null;
}

/**
 * Manages alignment/tracking session history fetching and the historical-vs-live
 * selection logic shared by the toolbar's session picker and the sky map's
 * alignment/tracking-risk overlays.
 *
 * Each of the three fetches (session list, cumulative tracking, per-session
 * detail) is called from more than one place — mount and a session-selection
 * change — so each keeps its own AbortController, aborting
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
  const [sessionAlignmentTargets, setSessionAlignmentTargets] = useState<AlignmentTargetSession[] | null>(null);
  const [sessionPolarAlignment, setSessionPolarAlignment] = useState<PolarAlignmentStatus | null>(null);
  const [cumulativeAlignmentTargets, setCumulativeAlignmentTargets] = useState<AlignmentTargetSession[] | null>(null);

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
      if (res && res.alignmentTargets) {
        setCumulativeAlignmentTargets(res.alignmentTargets);
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

  useEffect(() => {
    if (!selectedSessionId) {
      setSessionAlignmentTargets(null);
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
          setSessionAlignmentTargets(res.alignmentTargets || []);
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

  const activeAlignmentTargets = useMemo(() => {
    if (selectedSessionId && sessionAlignmentTargets !== null) {
      return sessionAlignmentTargets;
    }
    return telemetry?.alignmentTargets ?? [];
  }, [selectedSessionId, sessionAlignmentTargets, telemetry?.alignmentTargets]);

  const activeCumulativeAlignmentTargets = useMemo(() => {
    const historical = cumulativeAlignmentTargets ?? [];
    return historical.length > 0 ? historical : telemetry?.alignmentTargets ?? [];
  }, [cumulativeAlignmentTargets, telemetry?.alignmentTargets]);

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
    activeAlignmentTargets,
    activeCumulativeAlignmentTargets,
    activePolarAlignment,
  };
};
