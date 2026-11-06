/**
 * @fileoverview Tests for the telescope transition toasts of the always-mounted
 * status header hook (useStatusData): a stop is announced, a start and a
 * reconnect are only status, and nothing is shown before the app is ready.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const toastMock = vi.fn();
let appReady = true;
let currentTelescope: Record<string, unknown> = {};

vi.mock('../../common/utils/emitToast', () => ({ emitToast: (...args: unknown[]) => toastMock(...args) }));
vi.mock('../../common/utils/appBootReadiness', () => ({ isAppReady: () => appReady }));
vi.mock('../../common/context/AstrometricsContext', () => ({
    useAstrometrics: () => ({ telescope: currentTelescope, connected: true }),
}));

import { useStatusData } from '../hooks/useStatusData';

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

describe('status header transition toasts', () => {
    beforeEach(() => {
        toastMock.mockClear();
        appReady = true;
    });

    /** Tracking starting or stopping is a status for the chip, so no toast. */
    it('the header hook shows no toast when tracking starts or stops', () => {
        reportStatuses(useStatusData, 'Idle', 'Tracking', 'Parked');
        expect(toastMock).not.toHaveBeenCalled();
    });

    /** Not Tracking is not tracking. */
    it('does not treat Not Tracking as tracking', () => {
        reportStatuses(useStatusData, 'Not Tracking', 'Idle');
        expect(toastMock).not.toHaveBeenCalled();
    });

    /** Nothing before the app is ready. */
    it('stays quiet before the app is ready', () => {
        appReady = false;
        reportStatuses(useStatusData, 'Tracking', 'Idle');
        expect(toastMock).not.toHaveBeenCalled();
    });

    /** Coming back online is a status, so there is no "connected" toast. */
    it('shows no toast when the telescope connects', () => {
        currentTelescope = { connectionStatus: 'Disconnected', trackingStatus: 'Not Tracking' };
        const { rerender } = renderHook(() => useStatusData());
        currentTelescope = { connectionStatus: 'Connected', trackingStatus: 'Not Tracking' };
        rerender();
        expect(toastMock).not.toHaveBeenCalled();
    });
});
