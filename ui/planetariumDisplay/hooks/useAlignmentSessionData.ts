/**
 * @module useAlignmentSessionData
 * @fileoverview Fetches and tracks plate-solve alignment session history for the
 * Planetarium display: the list of past sessions, the currently-selected
 * historical session's targets and polar alignment, and the view the sky map
 * draws: the selected session, or nothing at all with "No Session Selected".
 * The targets come grouped, with their tracking statistics, from the library.
 *
 * Extracted from PlanetariumDisplay to keep the root component focused on
 * layout/composition rather than backend session-data bookkeeping.
 */

import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { AlignmentSessionSummary, AlignmentTargetSession, PolarAlignmentStatus } from '../../common/types/backendTypes';

/** The empty target list, kept as one array so the sky map does not redraw for a new empty one. */
const NO_ALIGNMENT_TARGETS: AlignmentTargetSession[] = [];

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
  /** Targets of the selected session (live telemetry while it loads); empty when no session is selected. */
  activeAlignmentTargets: AlignmentTargetSession[];
  /** Polar alignment of the selected session (live telemetry while it loads); null when no session is selected. */
  activePolarAlignment: PolarAlignmentStatus | null;
}

/**
 * Manages alignment/tracking session history fetching and the historical-vs-live
 * selection logic shared by the toolbar's session picker and the sky map's
 * alignment overlay.
 *
 * Each fetch (session list, per-session detail) keeps its own AbortController,
 * aborting its own previous in-flight call before starting a new one. That way
 * a rapid session-picker change or an unmount never leaves a superseded
 * request running in the background.
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

  const sessionsControllerRef = useRef<AbortController | null>(null);

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

  useEffect(() => {
    refreshSessions();
    return () => {
      sessionsControllerRef.current?.abort();
    };
  }, [refreshSessions]);

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

  // With "No Session Selected" the sky shows no session at all: no pointings,
  // sync markers, error arrows or polar alignment. The user picks a session
  // in the toolbar to see one.
  const activeAlignmentTargets = useMemo(() => {
    if (!selectedSessionId) return NO_ALIGNMENT_TARGETS;
    if (sessionAlignmentTargets !== null) {
      return sessionAlignmentTargets;
    }
    return telemetry?.alignmentTargets ?? NO_ALIGNMENT_TARGETS;
  }, [selectedSessionId, sessionAlignmentTargets, telemetry?.alignmentTargets]);

  const activePolarAlignment = useMemo(() => {
    if (!selectedSessionId) return null;
    if (sessionPolarAlignment !== null) {
      return sessionPolarAlignment;
    }
    return telemetry?.polarAlignment ?? null;
  }, [selectedSessionId, sessionPolarAlignment, telemetry?.polarAlignment]);

  return {
    availableSessions,
    selectedSessionId,
    setSelectedSessionId,
    activeAlignmentTargets,
    activePolarAlignment,
  };
};
