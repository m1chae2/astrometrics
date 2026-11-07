import { useState } from 'react';
import { parkTelescope, unparkTelescope, slewTelescope, abortTelescopeMotion, setTelescopeTracking, moveTelescope, setTelescopeSlewRate } from '../../common/services/telescope/motionService';
import { captureGuideFrame } from '../../common/services/telescope/guidingService';
import { startAlignment, stopAlignment } from '../../common/services/alignmentApi';
import { createTarget } from '../../common/services/targetService';
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

    const handleSlewToTarget = async (raShared: string | null, decShared: string | null) => {
        if (!raShared || !decShared) {
            emitToast('Target has no coordinates', 'error');
            return;
        }

        await slewTelescope(parseFloat(raShared), parseFloat(decShared));
    };

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

    const handleSetTracking = async (enabled: boolean) => {
        await setTelescopeTracking(enabled);
    };

    const handlePark = async () => {
        await parkTelescope();
    };

    const handleUnpark = async () => {
        await unparkTelescope();
    };

    const handleAddTarget = () => {
        setIsAddModalOpen(true);
    };

    const handleGuideCapture = async () => {
        const captured = await captureGuideFrame(guidingExposure, guidingGain);
        if (!captured) emitToast('Guide frame capture failed', 'error');
    };

    const handleStartAlignment = async (raShared: string | null, decShared: string | null) => {
        if (!raShared || !decShared) {
            emitToast('Target has no coordinates', 'error');
            return;
        }

        await startAlignment(raShared, decShared);
    };

    const handleStopAlignment = async () => {
        await stopAlignment();
    };

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
            onStopAlignment: handleStopAlignment
        }
    };
};
