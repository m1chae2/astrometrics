/**
 * @fileoverview Tests that useTelescopeStatus never shows toasts. It is used by
 * displays that can be paused, so it only reads data; the always-mounted status
 * header hook shows the transition toasts. That keeps a stale toast from
 * appearing when a paused display resumes.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const toastMock = vi.fn();
let appReady = true;
let currentTelescope: Record<string, unknown> = {};

vi.mock('../utils/emitToast', () => ({ emitToast: (...args: unknown[]) => toastMock(...args) }));
vi.mock('../utils/appBootReadiness', () => ({ isAppReady: () => appReady }));
vi.mock('../context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ telescope: currentTelescope, connected: true }),
}));

import { useTelescopeStatus } from '../hooks/useTelescopeStatus';

/**
 * Renders a hook with one status, then re-renders with the next.
 *
 * @param useHook - The hook to run.
 * @param statuses - The tracking texts, in the order the mount reports them.
 */
function reportStatuses(useHook: () => unknown, ...statuses: string[]): void {
    currentTelescope = { connectionStatus: 'Connected', trackingStatus: statuses[0] };
    const { rerender } = renderHook(useHook);
    for (const status of statuses.slice(1)) {
        currentTelescope = { connectionStatus: 'Connected', trackingStatus: status };
        rerender();
    }
}

describe('telescope transition toasts', () => {
    beforeEach(() => {
        toastMock.mockClear();
        appReady = true;
    });

    /** The data hook of paused displays never toasts, whatever the mount does. */
    it('useTelescopeStatus shows no toast on any change', () => {
        reportStatuses(useTelescopeStatus, 'Idle', 'Tracking', 'Parked');
        expect(toastMock).not.toHaveBeenCalled();
    });

    /** It still reports the data. */
    it('useTelescopeStatus still reports the tracking status', () => {
        currentTelescope = { connectionStatus: 'Connected', trackingStatus: 'Tracking' };
        const { result } = renderHook(() => useTelescopeStatus());
        expect(result.current.trackingStatus).toBe('Tracking');
        expect(result.current.telescopeConnection).toBe(true);
    });
});
