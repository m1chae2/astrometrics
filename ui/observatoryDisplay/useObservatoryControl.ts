import { useState } from 'react';
import {
    parkTelescope,
    unparkTelescope,
    slewTelescope,
    abortTelescopeMotion,
    setTelescopeTracking,
    moveTelescope,
    setTelescopeSlewRate,
    moveFocuser,
    setFilterWheelPosition,
    startGuiding,
    stopGuiding,
    captureGuideFrame
} from '../common/services/telescopeService';
import { startAlignment, stopAlignment } from '../common/services/alignmentApi';
import { createTarget } from '../common/services/targetService';
import { emitToast } from '../common/utils/emitToast';

export const useObservatoryControl = (forceReload: () => void) => {
    // State for LimitSettings
    const [limitsConfig, setLimitsConfig] = useState({
        altitudeEnabled: false,
        minimumAltitude: 0,
        maximumAltitude: 90,
        hourAngleEnabled: false,
        maximumHourAngle: 2,
        flipHourAngle: 1,
    });

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

    const handleMoveFocuser = async (steps: number) => {
        await moveFocuser(steps);
    };

    const handleSelectFilter = async (filter: string) => {
        await setFilterWheelPosition(filter);
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

    const handleApplyLimits = (
        enabled: boolean,
        minimumAltitude: number,
        maximumAltitude: number,
        hourAngleEnabled: boolean,
        maximumHourAngle: number,
        flipHourAngle: number
    ) => {
        setLimitsConfig({
            altitudeEnabled: enabled,
            minimumAltitude,
            maximumAltitude,
            hourAngleEnabled,
            maximumHourAngle,
            flipHourAngle,
        });
        emitToast(`Limits updated: Alt ${enabled ? 'ON' : 'OFF'} (${minimumAltitude}°-${maximumAltitude}°), HA ${hourAngleEnabled ? 'ON' : 'OFF'} (max ${maximumHourAngle}h), Flip HA: ${flipHourAngle}°`, 'success');
    };

    const handleAddTarget = () => {
        setIsAddModalOpen(true);
    };

    const handleGuideStart = async () => {
        emitToast(`Starting Guiding (Exp: ${guidingExposure}s, Gain: ${guidingGain})`, 'info');
        await startGuiding(guidingExposure, guidingGain);
    };

    const handleGuideStop = async () => {
        emitToast('Stopping Guiding', 'info');
        await stopGuiding();
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
        limitsConfig,
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
            onMoveFocuser: handleMoveFocuser,
            onSelectFilter: handleSelectFilter,
            onSetTracking: handleSetTracking,
            onPark: handlePark,
            onUnpark: handleUnpark,
            onApplyLimits: handleApplyLimits,
            onAddTarget: handleAddTarget,
            onGuideStart: handleGuideStart,
            onGuideStop: handleGuideStop,
            onGuideCapture: handleGuideCapture,
            onStartAlignment: handleStartAlignment,
            onStopAlignment: handleStopAlignment
        }
    };
};
