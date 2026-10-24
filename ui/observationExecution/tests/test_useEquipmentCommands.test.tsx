/**
 * @fileoverview Tests of the equipment command hook: while commanding is not
 * allowed no command reaches the services, and when it is allowed they do.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const motion = vi.hoisted(() => ({
    parkTelescope: vi.fn(),
    unparkTelescope: vi.fn(),
    slewTelescope: vi.fn(),
    abortTelescopeMotion: vi.fn(),
    setTelescopeTracking: vi.fn(),
    moveTelescope: vi.fn(),
    setTelescopeSlewRate: vi.fn(),
}));
const device = vi.hoisted(() => ({ moveFocuser: vi.fn(), setFilterWheelPosition: vi.fn() }));
const guiding = vi.hoisted(() => ({ startGuiding: vi.fn(), stopGuiding: vi.fn(), captureGuideFrame: vi.fn() }));
const alignment = vi.hoisted(() => ({ startAlignment: vi.fn(), stopAlignment: vi.fn() }));

vi.mock('../../common/services/telescope/motionService', () => motion);
vi.mock('../../common/services/telescope/deviceService', () => device);
vi.mock('../../common/services/telescope/guidingService', () => guiding);
vi.mock('../../common/services/alignmentApi', () => alignment);
vi.mock('../../common/utils/emitToast', () => ({ emitToast: vi.fn() }));

import { useEquipmentCommands } from '../hooks/useEquipmentCommands';

beforeEach(() => {
    for (const group of [motion, device, guiding, alignment]) {
        for (const mock of Object.values(group)) mock.mockReset();
    }
});

describe('useEquipmentCommands', () => {
    /** With commanding off, every command does nothing. */
    it('refuses every command when commanding is not allowed', async () => {
        const { result } = renderHook(() => useEquipmentCommands(false));
        await act(async () => {
            const { handlers } = result.current;
            await handlers.onSlewToTarget('1', '2');
            await handlers.onStop();
            await handlers.onStartMove('N');
            await handlers.onStopMove('N');
            await handlers.onSetSlewRate(1);
            await handlers.onMoveFocuser(10);
            await handlers.onSelectFilter('L');
            await handlers.onSetTracking(true);
            await handlers.onPark();
            await handlers.onUnpark();
            await handlers.onGuideStart();
            await handlers.onGuideStop();
            await handlers.onGuideCapture();
            await handlers.onStartAlignment('1', '2');
            await handlers.onStopAlignment();
        });
        for (const group of [motion, device, guiding, alignment]) {
            for (const mock of Object.values(group)) expect(mock).not.toHaveBeenCalled();
        }
    });

    /** With commanding on, a command reaches its service. */
    it('runs commands when commanding is allowed', async () => {
        guiding.captureGuideFrame.mockResolvedValue(true);
        const { result } = renderHook(() => useEquipmentCommands(true));
        await act(async () => {
            await result.current.handlers.onPark();
            await result.current.handlers.onMoveFocuser(-50);
            await result.current.handlers.onSlewToTarget('10', '20');
            await result.current.handlers.onGuideCapture();
        });
        expect(motion.parkTelescope).toHaveBeenCalledTimes(1);
        expect(device.moveFocuser).toHaveBeenCalledWith(-50);
        expect(motion.slewTelescope).toHaveBeenCalledWith(10, 20);
        expect(guiding.captureGuideFrame).toHaveBeenCalledWith(1.0, 100);
    });

    /** A slew without coordinates is refused even when commanding is allowed. */
    it('refuses a slew with no coordinates', async () => {
        const { result } = renderHook(() => useEquipmentCommands(true));
        await act(async () => {
            await result.current.handlers.onSlewToTarget(null, null);
        });
        expect(motion.slewTelescope).not.toHaveBeenCalled();
    });
});
