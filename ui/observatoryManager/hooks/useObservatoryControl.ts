import { useState } from 'react';
import { parkTelescope, unparkTelescope, slewTelescope, abortTelescopeMotion, setTelescopeTracking, moveTelescope, setTelescopeSlewRate } from '../../common/services/telescope/motionService';
import { startGuiding, stopGuiding, captureGuideFrame } from '../../common/services/telescope/guidingService';
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

        emitToast(`Slewing to RA:${raShared} Dec:${decShared}`, 'info');
        await slewTelescope(parseFloat(raShared), parseFloat(decShared));
    };

    const handleStop = async () => {
        emitToast('Stopping Telescope', 'info');
        await abortTelescopeMotion();
    };


    const handleStartMove = async (direction: string) => {
        emitToast(`Manual Slew Start: ${direction}`, 'info');
        await moveTelescope(direction, true);
    };

    const handleStopMove = async (direction: string) => {
        // emitToast(`Manual Slew Stop: ${direction}`, 'info');
        await moveTelescope(direction, false);
    };

    const handleSetSlewRate = async (rate: number) => {
        emitToast(`Setting Slew Rate: ${rate}`, 'info');
        await setTelescopeSlewRate(rate);
    };

    const handleSetTracking = async (enabled: boolean) => {
        emitToast(`${enabled ? 'Enabling' : 'Disabling'} Tracking`, 'info');
        await setTelescopeTracking(enabled);
    };

    const handlePark = async () => {
        emitToast('Parking Telescope', 'info');
        await parkTelescope();
    };

    const handleUnpark = async () => {
        emitToast('Unparking Telescope', 'info');
        await unparkTelescope();
    };

    const handleAddTarget = () => {
        setIsAddModalOpen(true);
    };

    /**
     * Starts guiding with the exposure and gain the user entered. A failure is shown as a toast.
     * @param exposure Guide exposure in seconds.
     * @param gain Guide camera gain.
     */
    const handleGuideStart = async (exposure: number, gain: number) => {
        emitToast(`Starting Guiding (Exp: ${exposure}s, Gain: ${gain})`, 'info');
        const started = await startGuiding(exposure, gain);
        if (!started) {
            emitToast('Guiding did not start. Check that the guide camera and mount are connected.', 'error');
        }
    };

    /** Stops guiding. A failure is shown as a toast. */
    const handleGuideStop = async () => {
        emitToast('Stopping Guiding', 'info');
        const stopped = await stopGuiding();
        if (!stopped) {
            emitToast('Guiding did not stop. Check the guiding status and try again.', 'error');
        }
    };

    const handleGuideCapture = async () => {
        emitToast(`Capturing single guide frame (${guidingExposure}s)`, 'info');
        const captured = await captureGuideFrame(guidingExposure, guidingGain);
        emitToast(
            captured ? 'Guide frame captured' : 'Guide frame capture failed',
            captured ? 'success' : 'error'
        );
    };

    const handleStartAlignment = async (raShared: string | null, decShared: string | null) => {
        if (!raShared || !decShared) {
            emitToast('Target has no coordinates', 'error');
            return;
        }

        emitToast(`Starting alignment for RA:${raShared} Dec:${decShared}`, 'info');
        await startAlignment(raShared, decShared);
    };

    const handleStopAlignment = async () => {
        emitToast('Stopping Alignment', 'info');
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
            onGuideStart: handleGuideStart,
            onGuideStop: handleGuideStop,
            onGuideCapture: handleGuideCapture,
            onStartAlignment: handleStartAlignment,
            onStopAlignment: handleStopAlignment
        }
    };
};
