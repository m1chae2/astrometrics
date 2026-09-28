/**
 * @module useEquipmentConfiguration
 * @fileoverview Fetches and manages the active telescope + camera equipment configuration.
 *
 * Provides the active EquipmentConfiguration including computed sensor FOV dimensions,
 * a list of all available cameras, and a setter to persist a new active camera selection.
 *
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { callBackend, EquipmentCameraProfile, EquipmentConfigurationResult } from '../../common/services/backendApi';

export type { EquipmentCameraProfile, EquipmentConfigurationResult };
export type CameraProfile = EquipmentCameraProfile;
export type EquipmentConfiguration = EquipmentConfigurationResult;

/** Return value of useEquipmentConfiguration. */
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
  const { data, loading, error } = useBackendFetch<FetchedEquipment>(
    async (signal) => {
      const [equipmentConfig, cameras] = await Promise.all([
        callBackend('observatory:get_equipment_configuration', {}, { signal }),
        callBackend('observatory:list_cameras', {}, { signal }),
      ]);
      return { configuration: equipmentConfig ?? null, availableCameras: cameras ?? [] };
    },
    [],
    { errorMessage: 'Failed to load equipment configuration' }
  );

  // Optimistic override applied by setActiveCamera, computed instantly from
  // already-known local data rather than waiting on a round trip. Cleared
  // whenever a fresh fetch lands so it never outlives the data it overrides.
  const [configOverride, setConfigOverride] = useState<EquipmentConfiguration | null>(null);
  useEffect(() => {
    setConfigOverride(null);
  }, [data]);

  const configuration = configOverride ?? data?.configuration ?? null;
  const availableCameras = useMemo(() => data?.availableCameras ?? [], [data]);

  const setActiveCamera = useCallback(async (cameraName: string) => {
    const camera = availableCameras.find(candidateCamera => candidateCamera.name === cameraName);
    if (camera && configuration) {
      const plateScale = 206.265 * camera.pixelSizeUm / configuration.telescope.focalLengthMm;
      setConfigOverride({
        ...configuration,
        camera,
        plateScaleArcsecPerPx: Math.round(plateScale * 10000) / 10000,
        fovWidthDeg: Math.round(plateScale * camera.sensorWidthPx / 3600 * 1000000) / 1000000,
        fovHeightDeg: Math.round(plateScale * camera.sensorHeightPx / 3600 * 1000000) / 1000000,
      });
    }
    // Persist to config in the background — no need to re-fetch
    callBackend('observatory:set_active_camera', { camera_name: cameraName }).catch(error => {
      console.error('Failed to persist active camera selection:', error);
    });
  }, [availableCameras, configuration]);

  return { configuration, availableCameras, loading, error, setActiveCamera };
};
