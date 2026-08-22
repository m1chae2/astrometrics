/**
 * @module useEquipmentConfiguration
 * @fileoverview Fetches and manages the active telescope + camera equipment configuration.
 *
 * Provides the active EquipmentConfiguration including computed sensor FOV dimensions,
 * a list of all available cameras, and a setter to persist a new active camera selection.
 *
 */

import { useState, useEffect, useCallback } from 'react';
import { callBackend, EquipmentCameraProfile, EquipmentConfigurationResult } from '../../common/services/backendApi';

export type { EquipmentCameraProfile, EquipmentConfigurationResult };
export type CameraProfile = EquipmentCameraProfile;
export type EquipmentConfiguration = EquipmentConfigurationResult;

/**
 * Return value of useEquipmentConfiguration.
 */
export interface UseEquipmentConfigurationResult {
  /** Active equipment configuration with computed FOV, or null while loading. */
  configuration: EquipmentConfiguration | null;
  /** All available camera profiles from the observatory config. */
  availableCameras: CameraProfile[];
  /** Whether the initial configuration fetch is in progress. */
  loading: boolean;
  /** Error message if the fetch failed, or null. */
  error: string | null;
  /**
   * Persists a new active camera selection and refreshes the configuration.
   *
   * @param {string} cameraName - Must match a name in availableCameras.
   * @returns {Promise<void>}
   */
  setActiveCamera: (cameraName: string) => Promise<void>;
}

/**
 * Fetches the active equipment configuration and available cameras from the backend.
 *
 * Re-fetches whenever the active camera changes via setActiveCamera.
 *
 * @func useEquipmentConfiguration
 * @returns {UseEquipmentConfigurationResult}
 */
export const useEquipmentConfiguration = (): UseEquipmentConfigurationResult => {
  const [configuration, setConfiguration] = useState<EquipmentConfiguration | null>(null);
  const [availableCameras, setAvailableCameras] = useState<CameraProfile[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchConfiguration = useCallback(async () => {
    try {
      const [equipmentConfig, cameras] = await Promise.all([
        callBackend('observatory:get_equipment_configuration', {}),
        callBackend('observatory:list_cameras', {}),
      ]);
      setConfiguration(equipmentConfig ?? null);
      setAvailableCameras(cameras ?? []);
      setError(null);
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Failed to load equipment configuration');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfiguration();
  }, [fetchConfiguration]);

  const setActiveCamera = useCallback(async (cameraName: string) => {
    // Compute and apply the new FOV immediately from already-known local data
    const camera = availableCameras.find(candidateCamera => candidateCamera.name === cameraName);
    if (camera && configuration) {
      const plateScale = 206.265 * camera.pixel_size_um / configuration.telescope.focal_length_mm;
      setConfiguration({
        ...configuration,
        camera,
        plate_scale_arcsec_per_px: Math.round(plateScale * 10000) / 10000,
        fov_width_deg: Math.round(plateScale * camera.sensor_width_px / 3600 * 1000000) / 1000000,
        fov_height_deg: Math.round(plateScale * camera.sensor_height_px / 3600 * 1000000) / 1000000,
      });
    }
    // Persist to config in the background — no need to re-fetch
    callBackend('observatory:set_active_camera', { camera_name: cameraName }).catch(error => {
      console.error('Failed to persist active camera selection:', error);
    });
  }, [availableCameras, configuration]);

  return { configuration, availableCameras, loading, error, setActiveCamera };
};
