/**
 * @module EquipmentConfigPanel
 * @fileoverview Floating panel for viewing and switching the active imaging camera.
 *
 * Displays the telescope (read-only, single observatory), active camera, computed
 * plate scale, and sensor FOV. A dropdown lets the user switch between all cameras
 * defined in astrometrics.config, persisting the selection via the backend.
 *
 */

import React, { useState } from 'react';
import type { EquipmentConfiguration, CameraProfile } from '../hooks/useEquipmentConfiguration';

/**
 * Props for EquipmentConfigPanel.
 */
interface Props {
  /** Active equipment configuration with computed FOV, or null while loading. */
  configuration: EquipmentConfiguration | null;
  /** All available camera profiles from the observatory config. */
  availableCameras: CameraProfile[];
  /** Callback invoked when the user selects a different camera. */
  onSelectCamera: (cameraName: string) => Promise<void>;
}

/**
 * Floating bottom-left panel for managing the active telescope + camera configuration.
 *
 * The telescope is always the single configured observatory scope (read-only).
 * The camera can be switched via dropdown. Computed imaging geometry (plate scale
 * and sensor FOV) updates immediately after a selection change.
 *
 * @func EquipmentConfigPanel
 * @param {Props} props - Component props.
 * @returns {React.ReactElement} The rendered configuration panel.
 */
export const EquipmentConfigPanel: React.FC<Props> = ({
  configuration,
  availableCameras,
  onSelectCamera,
}) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);

  const handleCameraChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    setIsSaving(true);
    try {
      await onSelectCamera(e.target.value);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="equipment-config-panel">
      {isExpanded && (
        <div className="equipment-config-panel__body">
          {/* Telescope — read-only */}
          <div className="equipment-config-panel__row">
            <span className="equipment-config-panel__label">Telescope</span>
            <span className="equipment-config-panel__value">
              {configuration?.telescope.name ?? '—'}
            </span>
          </div>
          <div className="equipment-config-panel__row">
            <span className="equipment-config-panel__label">Focal Length</span>
            <span className="equipment-config-panel__value">
              {configuration ? `${configuration.telescope.focal_length_mm} mm` : '—'}
            </span>
          </div>

          <div className="equipment-config-panel__divider" />

          {/* Camera — selectable */}
          <div className="equipment-config-panel__row">
            <span className="equipment-config-panel__label">Camera</span>
            <select
              className="equipment-config-panel__select"
              value={configuration?.camera.name ?? ''}
              onChange={handleCameraChange}
              disabled={isSaving || availableCameras.length === 0}
            >
              {availableCameras.map(camera => (
                <option key={camera.name} value={camera.name}>{camera.name}</option>
              ))}
            </select>
          </div>
          <div className="equipment-config-panel__row">
            <span className="equipment-config-panel__label">Pixel Size</span>
            <span className="equipment-config-panel__value">
              {configuration ? `${configuration.camera.pixel_size_um} μm` : '—'}
            </span>
          </div>
          <div className="equipment-config-panel__row">
            <span className="equipment-config-panel__label">Sensor</span>
            <span className="equipment-config-panel__value">
              {configuration
                ? `${configuration.camera.sensor_width_px} × ${configuration.camera.sensor_height_px} px`
                : '—'}
            </span>
          </div>

          <div className="equipment-config-panel__divider" />

          {/* Computed geometry */}
          <div className="equipment-config-panel__row">
            <span className="equipment-config-panel__label">Plate Scale</span>
            <span className="equipment-config-panel__value equipment-config-panel__value--mono">
              {configuration ? `${configuration.plate_scale_arcsec_per_px.toFixed(3)} ″/px` : '—'}
            </span>
          </div>
          <div className="equipment-config-panel__row">
            <span className="equipment-config-panel__label">FOV</span>
            <span className="equipment-config-panel__value equipment-config-panel__value--mono">
              {configuration
                ? `${configuration.fov_width_deg.toFixed(3)}° × ${configuration.fov_height_deg.toFixed(3)}°`
                : '—'}
            </span>
          </div>
        </div>
      )}

      <button
        className="equipment-config-panel__toggle"
        onClick={() => setIsExpanded(prev => !prev)}
        title="Equipment Configuration"
      >
        ⌖ {configuration ? configuration.camera.name : 'Equipment'}
      </button>
    </div>
  );
};
