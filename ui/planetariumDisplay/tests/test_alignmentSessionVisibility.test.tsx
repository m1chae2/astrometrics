/**
 * @file test_alignmentSessionVisibility.test.tsx
 * @description Tests that the Planetarium draws no session overlays (pointing path, session
 * boxes, sync markers, error arrows, polar alignment) with "No Session Selected", even when
 * live telemetry has pointings, and draws the selected session's as before.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import { useAlignmentSessionData } from '../hooks/useAlignmentSessionData';
import type { AlignmentTargetSession, PolarAlignmentStatus } from '../../common/types/backendTypes';

const liveTarget = { targetId: 'M 13' } as unknown as AlignmentTargetSession;
const sessionTarget = { targetId: 'IC 1396' } as unknown as AlignmentTargetSession;
const livePolar = { totalErrorArcsec: 120 } as unknown as PolarAlignmentStatus;
const sessionPolar = { totalErrorArcsec: 60 } as unknown as PolarAlignmentStatus;

beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockImplementation((method: string) => {
        if (method === 'telescope:list_alignment_sessions') return Promise.resolve([]);
        if (method === 'telescope:get_session_alignment') {
            return Promise.resolve({ alignmentTargets: [sessionTarget], polarAlignment: sessionPolar });
        }
        return Promise.resolve(null);
    });
});

describe('useAlignmentSessionData session overlays', () => {
    /** No session picked: nothing is handed to the sky map, though live telemetry has pointings. */
    it('gives no session overlays with No Session Selected', async () => {
        const { result } = renderHook(() =>
            useAlignmentSessionData({ alignmentTargets: [liveTarget], polarAlignment: livePolar }),
        );
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('telescope:list_alignment_sessions', {}, expect.anything()));
        expect(result.current.selectedSessionId).toBeNull();
        expect(result.current.activeAlignmentTargets).toEqual([]);
        expect(result.current.activePolarAlignment).toBeNull();
    });

    /** A picked session shows its own pointings and polar alignment, as before. */
    it('gives the selected session its overlays', async () => {
        const { result } = renderHook(() =>
            useAlignmentSessionData({ alignmentTargets: [liveTarget], polarAlignment: livePolar }),
        );
        act(() => result.current.setSelectedSessionId('2026-09-24'));
        await waitFor(() => expect(result.current.activeAlignmentTargets).toEqual([sessionTarget]));
        expect(result.current.activePolarAlignment).toBe(sessionPolar);
    });

    /** Going back to No Session Selected clears the overlays again. */
    it('clears the overlays when the session is unselected', async () => {
        const { result } = renderHook(() => useAlignmentSessionData(undefined));
        act(() => result.current.setSelectedSessionId('all'));
        await waitFor(() => expect(result.current.activeAlignmentTargets).toEqual([sessionTarget]));
        act(() => result.current.setSelectedSessionId(null));
        expect(result.current.activeAlignmentTargets).toEqual([]);
        expect(result.current.activePolarAlignment).toBeNull();
    });
});
