import { useState } from 'react';
import { parkTelescope, unparkTelescope, slewToTarget, abortTelescopeMotion, setTelescopeTracking, moveTelescope, setTelescopeSlewRate } from '../../common/services/telescope/motionService';
import { captureGuideFrame } from '../../common/services/telescope/guidingService';
import { startAlignment, stopAlignment } from '../../common/services/alignmentApi';
import { createTarget, fetchTargetObject } from '../../common/services/targetService';
import { startOpenEndedCapture, stopCapture } from '../../common/services/imaging/captureService';
import { emitToast } from '../../common/utils/emitToast';

export const useObservatoryControl = (forceReload: () => void) => {
    const [isAddModalOpen, setIsAddModalOpen] = useState(false);

    // State for Guiding
    const [guidingExposure, setGuidingExposure] = useState<number>(1.0);
    const [guidingGain, setGuidingGain] = useState<number>(100);

    const handleAddTargetSubmit = async (name: string, rightAscension: string, declination: string) => {
        await createTarget(name, rightAscension, declination);
        forceReload();
    };

    /**
     * Slews the mount to a library target. The backend looks up the target's coordinates.
     * @param targetId The id of the selected target.
     * @return True if the mount accepted the slew.
     */
    const handleSlewToTarget = async (targetId: string): Promise<boolean> => slewToTarget(targetId);

    const handleStop = async () => {
        await abortTelescopeMotion();
    };


    const handleStartMove = async (direction: string) => {
        await moveTelescope(direction, true);
    };

    const handleStopMove = async (direction: string) => {
        await moveTelescope(direction, false);
    };

    const handleSetSlewRate = async (rate: number) => {
        await setTelescopeSlewRate(rate);
    };

    /**
     * Turns sidereal tracking on or off.
     * @param enabled True to track.
     * @return True if the mount accepted the command.
     */
    const handleSetTracking = async (enabled: boolean): Promise<boolean> => setTelescopeTracking(enabled);

    /**
     * Parks the mount.
     * @return True if the mount accepted the command.
     */
    const handlePark = async (): Promise<boolean> => parkTelescope();

    /**
     * Unparks the mount.
     * @return True if the mount accepted the command.
     */
    const handleUnpark = async (): Promise<boolean> => unparkTelescope();

    const handleAddTarget = () => {
        setIsAddModalOpen(true);
    };

    const handleGuideCapture = async () => {
        const captured = await captureGuideFrame(guidingExposure, guidingGain);
        if (!captured) emitToast('Guide frame capture failed', 'error');
    };

    /**
     * Starts alignment on a library target, using the target's own coordinates.
     * @param targetId The id of the selected target.
     * @return True if alignment started.
     */
    const handleStartAlignment = async (targetId: string): Promise<boolean> => {
        const target = await fetchTargetObject(targetId);
        if (!target?.ra || !target?.dec) {
            emitToast(`${targetId} has no coordinates in the library, so alignment cannot start.`, 'error');
            return false;
        }
        return startAlignment(target.ra, target.dec);
    };

    /**
     * Stops alignment.
     * @return True if alignment was stopped.
     */
    const handleStopAlignment = async (): Promise<boolean> => stopAlignment();

    /**
     * Starts the open-ended capture on the target the telescope points at.
     * @param targetName The target the telescope points at, or undefined when it points at none.
     * @return True if the capture started.
     */
    const handleStartCapture = async (targetName: string | undefined): Promise<boolean> => {
        if (!targetName) {
            emitToast('The telescope is not pointing at a target', 'error');
            return false;
        }
        return (await startOpenEndedCapture(targetName)) !== null;
    };

    /**
     * Stops the capture the app started.
     * @return True if a capture was stopped.
     */
    const handleStopCapture = async (): Promise<boolean> => stopCapture();

    return {
        isAddModalOpen,
        setIsAddModalOpen,
        guidingExposure,
        setGuidingExposure,
        guidingGain,
        setGuidingGain,
        handlers: {
            onAddTargetSubmit: handleAddTargetSubmit,
            onSlewToTarget: handleSlewToTarget,
            onStop: handleStop,
            onStartMove: handleStartMove,
            onStopMove: handleStopMove,
            onSetSlewRate: handleSetSlewRate,
            onSetTracking: handleSetTracking,
            onPark: handlePark,
            onUnpark: handleUnpark,
            onAddTarget: handleAddTarget,
            onGuideCapture: handleGuideCapture,
            onStartAlignment: handleStartAlignment,
            onStopAlignment: handleStopAlignment,
            onStartCapture: handleStartCapture,
            onStopCapture: handleStopCapture
        }
    };
};
