/**
 * @module useEquipmentConfiguration
 * @fileoverview Fetches and manages the active telescope + camera equipment configuration.
 *
 * Provides the active EquipmentConfiguration, with the plate scale and sensor field of
 * view the library works out (`control.equipment.status`), a list of all available
 * cameras, and a setter that saves a new active camera and reloads the configuration.
 * Nothing about the optics is calculated here.
 */

import { useCallback, useMemo, useState } from 'react';
import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { callBackend, EquipmentCameraProfile, EquipmentConfigurationResult } from '../../common/services/backendApi';

export type { EquipmentCameraProfile, EquipmentConfigurationResult };
export type CameraProfile = EquipmentCameraProfile;
export type EquipmentConfiguration = EquipmentConfigurationResult;

/** Return value of useEquipmentConfiguration. */
export interface UseEquipmentConfigurationResult {
  /** Active equipment configuration with the library's plate scale and FOV, or null while loading. */
  configuration: EquipmentConfiguration | null;
  /** All available camera profiles from the observatory config. */
  availableCameras: CameraProfile[];
  /** Whether the initial configuration fetch is in progress. */
  loading: boolean;
  /** Error message if the fetch failed, or null. */
  error: string | null;
  /**
   * Saves a new active camera selection, then reloads the configuration from the backend.
   *
   * @param {string} cameraName - Must match a name in availableCameras.
   * @returns {Promise<void>}
   */
  setActiveCamera: (cameraName: string) => Promise<void>;
}

interface FetchedEquipment {
  configuration: EquipmentConfiguration | null;
  availableCameras: CameraProfile[];
}

/**
 * Fetches the active equipment configuration and available cameras from the backend.
 *
 * @func useEquipmentConfiguration
 * @returns {UseEquipmentConfigurationResult}
 */
export const useEquipmentConfiguration = (): UseEquipmentConfigurationResult => {
  // Bumped after a camera change, so the configuration (with the plate scale
  // and field of view the library works out for the new camera) is fetched again.
  const [reloadKey, setReloadKey] = useState(0);
  const { data, loading, error } = useBackendFetch<FetchedEquipment>(
    async (signal) => {
      const [equipmentConfig, cameras] = await Promise.all([
        callBackend('observatory:get_equipment_configuration', {}, { signal }),
        callBackend('observatory:list_cameras', {}, { signal }),
      ]);
      return { configuration: equipmentConfig ?? null, availableCameras: cameras ?? [] };
    },
    [reloadKey],
    { errorMessage: 'Failed to load equipment configuration' }
  );

  const configuration = data?.configuration ?? null;
  const availableCameras = useMemo(() => data?.availableCameras ?? [], [data]);

  const setActiveCamera = useCallback(async (cameraName: string) => {
    try {
      await callBackend('observatory:set_active_camera', { camera_name: cameraName });
      setReloadKey((key) => key + 1);
    } catch (error) {
      console.error('Failed to persist active camera selection:', error);
    }
  }, []);

  return { configuration, availableCameras, loading, error, setActiveCamera };
};
