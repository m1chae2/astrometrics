/**
 * @file EquipmentSettingsPanel.tsx
 * @description The active telescope and camera, with the plate scale and
 * sensor field of view the library works out, and a list to change the active
 * camera. Moved here from the Planetarium, where it was a floating panel.
 * Nothing about the optics is calculated here.
 */

import React, { useState } from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { QualityPanelState } from '../../common/components/quality/QualityPanelState';
import type { EquipmentConfiguration, CameraProfile } from '../../common/hooks/useEquipmentConfiguration';
import '../../common/components/quality/quality.css';
import '../observatoryManager.css';

/** Properties of {@link EquipmentSettingsPanel}. */
export interface EquipmentSettingsPanelProps {
    /** The active equipment, or null while loading or when none is set. */
    configuration: EquipmentConfiguration | null;
    /** Every camera profile in the settings. */
    availableCameras: CameraProfile[];
    /** Whether the first read is still running. */
    loading: boolean;
    /** The error text of a failed read, or null. */
    error: string | null;
    /** Saves a new active camera. */
    onSelectCamera: (cameraName: string) => Promise<void>;
    /** Reads the equipment again. */
    onRetry: () => void;
}

/**
 * Shows the active equipment and lets the user change the active camera.
 * Choosing a camera saves a setting; it does not command a device.
 * @param props See {@link EquipmentSettingsPanelProps}.
 * @return The panel.
 */
export const EquipmentSettingsPanel: React.FC<EquipmentSettingsPanelProps> = ({
    configuration,
    availableCameras,
    loading,
    error,
    onSelectCamera,
    onRetry,
}) => {
    const [isSaving, setIsSaving] = useState(false);

    const handleChange = async (event: React.ChangeEvent<HTMLSelectElement>) => {
        setIsSaving(true);
        try {
            await onSelectCamera(event.target.value);
        } finally {
            setIsSaving(false);
        }
    };

    let body: React.ReactNode;
    if (loading) {
        body = <QualityPanelState state="loading" />;
    } else if (error) {
        body = <QualityPanelState state="error" subject="equipment settings" onRetry={onRetry} />;
    } else if (!configuration) {
        body = <QualityPanelState state="empty" emptyMessage="No equipment is set. Add a telescope and camera in the settings." />;
    } else {
        body = (
            <div className="equipment-block" data-testid="equipment-settings">
                <div className="equipment-block__row">
                    <span className="equipment-block__label">Telescope</span>
                    <span>{configuration.telescope.name}</span>
                </div>
                <div className="equipment-block__row">
                    <span className="equipment-block__label">Focal length</span>
                    <span>{configuration.telescope.focalLengthMm} mm</span>
                </div>
                <div className="equipment-block__row">
                    <label className="equipment-block__label" htmlFor="active-camera-select">Camera</label>
                    <select
                        id="active-camera-select"
                        className="dropdown"
                        value={configuration.camera.name}
                        onChange={handleChange}
                        disabled={isSaving || availableCameras.length === 0}
                    >
                        {availableCameras.map((camera) => (
                            <option key={camera.name} value={camera.name}>{camera.name}</option>
                        ))}
                    </select>
                </div>
                <div className="equipment-block__row">
                    <span className="equipment-block__label">Pixel size</span>
                    <span>{configuration.camera.pixelSizeUm} μm</span>
                </div>
                <div className="equipment-block__row">
                    <span className="equipment-block__label">Sensor</span>
                    <span>{configuration.camera.sensorWidthPx} × {configuration.camera.sensorHeightPx} px</span>
                </div>
                <div className="equipment-block__row">
                    <span className="equipment-block__label">Plate scale</span>
                    <span>{configuration.plateScaleArcsecPerPx.toFixed(3)} ″/px</span>
                </div>
                <div className="equipment-block__row">
                    <span className="equipment-block__label">Field of view</span>
                    <span>{configuration.fovWidthDeg.toFixed(3)}° × {configuration.fovHeightDeg.toFixed(3)}°</span>
                </div>
            </div>
        );
    }

    return <SectionPanel title="Equipment Settings">{body}</SectionPanel>;
};
