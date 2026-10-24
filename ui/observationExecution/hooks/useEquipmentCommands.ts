/**
 * @fileoverview The commands that Observation Execution can send to the
 * equipment: slew, move, focus, filter, park, tracking, guiding and alignment.
 *
 * This is the command half of the old Observatory Manager hook. Every command
 * is guarded: when `canCommand` is false (Ekos is in charge, or the policy is
 * still loading) the command does nothing. The buttons are disabled as well,
 * so this guard is the second line of defense.
 */
import { useState } from 'react';
import {
    parkTelescope,
    unparkTelescope,
    slewTelescope,
    abortTelescopeMotion,
    setTelescopeTracking,
    moveTelescope,
    setTelescopeSlewRate,
} from '../../common/services/telescope/motionService';
import { moveFocuser, setFilterWheelPosition } from '../../common/services/telescope/deviceService';
import { startGuiding, stopGuiding, captureGuideFrame } from '../../common/services/telescope/guidingService';
import { startAlignment, stopAlignment } from '../../common/services/alignmentApi';
import { emitToast } from '../../common/utils/emitToast';

/**
 * Builds the equipment command handlers.
 * @param canCommand Whether this app may command the equipment now. Every
 *     handler does nothing when this is false.
 * @return The guiding settings and the command handlers.
 */
export const useEquipmentCommands = (canCommand: boolean) => {
    const [guidingExposure, setGuidingExposure] = useState<number>(1.0);
    const [guidingGain, setGuidingGain] = useState<number>(100);

    /**
     * Wraps a command so it runs only when commanding is allowed.
     * @param command The command to run.
     * @return The guarded command.
     */
    const guarded =
        <Arguments extends unknown[]>(command: (...commandArguments: Arguments) => Promise<void> | void) =>
        async (...commandArguments: Arguments): Promise<void> => {
            if (!canCommand) return;
            await command(...commandArguments);
        };

    const handleSlewToTarget = guarded(async (rightAscension: string | null, declination: string | null) => {
        if (!rightAscension || !declination) {
            emitToast('Target has no coordinates', 'error');
            return;
        }
        emitToast(`Slewing to RA:${rightAscension} Dec:${declination}`, 'info');
        await slewTelescope(parseFloat(rightAscension), parseFloat(declination));
    });

    const handleStop = guarded(async () => {
        emitToast('Stopping Telescope', 'info');
        await abortTelescopeMotion();
    });

    const handleStartMove = guarded(async (direction: string) => {
        emitToast(`Manual Slew Start: ${direction}`, 'info');
        await moveTelescope(direction, true);
    });

    const handleStopMove = guarded(async (direction: string) => {
        await moveTelescope(direction, false);
    });

    const handleSetSlewRate = guarded(async (rate: number) => {
        emitToast(`Setting Slew Rate: ${rate}`, 'info');
        await setTelescopeSlewRate(rate);
    });

    const handleMoveFocuser = guarded(async (steps: number) => {
        await moveFocuser(steps);
    });

    const handleSelectFilter = guarded(async (filter: string) => {
        await setFilterWheelPosition(filter);
    });

    const handleSetTracking = guarded(async (enabled: boolean) => {
        emitToast(`${enabled ? 'Enabling' : 'Disabling'} Tracking`, 'info');
        await setTelescopeTracking(enabled);
    });

    const handlePark = guarded(async () => {
        emitToast('Parking Telescope', 'info');
        await parkTelescope();
    });

    const handleUnpark = guarded(async () => {
        emitToast('Unparking Telescope', 'info');
        await unparkTelescope();
    });

    const handleGuideStart = guarded(async () => {
        emitToast(`Starting Guiding (Exp: ${guidingExposure}s, Gain: ${guidingGain})`, 'info');
        await startGuiding(guidingExposure, guidingGain);
    });

    const handleGuideStop = guarded(async () => {
        emitToast('Stopping Guiding', 'info');
        await stopGuiding();
    });

    const handleGuideCapture = guarded(async () => {
        emitToast(`Capturing single guide frame (${guidingExposure}s)`, 'info');
        const captured = await captureGuideFrame(guidingExposure, guidingGain);
        emitToast(captured ? 'Guide frame captured' : 'Guide frame capture failed', captured ? 'success' : 'error');
    });

    const handleStartAlignment = guarded(async (rightAscension: string | null, declination: string | null) => {
        if (!rightAscension || !declination) {
            emitToast('Target has no coordinates', 'error');
            return;
        }
        emitToast(`Starting alignment for RA:${rightAscension} Dec:${declination}`, 'info');
        await startAlignment(rightAscension, declination);
    });

    const handleStopAlignment = guarded(async () => {
        emitToast('Stopping Alignment', 'info');
        await stopAlignment();
    });

    return {
        guidingExposure,
        setGuidingExposure,
        guidingGain,
        setGuidingGain,
        handlers: {
            onSlewToTarget: handleSlewToTarget,
            onStop: handleStop,
            onStartMove: handleStartMove,
            onStopMove: handleStopMove,
            onSetSlewRate: handleSetSlewRate,
            onMoveFocuser: handleMoveFocuser,
            onSelectFilter: handleSelectFilter,
            onSetTracking: handleSetTracking,
            onPark: handlePark,
            onUnpark: handleUnpark,
            onGuideStart: handleGuideStart,
            onGuideStop: handleGuideStop,
            onGuideCapture: handleGuideCapture,
            onStartAlignment: handleStartAlignment,
            onStopAlignment: handleStopAlignment,
        },
    };
};
