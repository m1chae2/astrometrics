/**
 * @module useDeepCatalogStatus
 * @fileoverview React hook that asks the backend how much of the downloaded
 * deep-star (Gaia DR3) catalog is installed.
 *
 * The Planetarium draws faint stars from a copy of Gaia DR3 saved on this
 * computer. It is downloaded once, by a command-line script, so the app needs
 * to know whether that has happened in order to prompt for it.
 */

import { useState, useEffect } from 'react';
import { callBackend, DeepCatalogStatus } from '../../common/services/backendApi';

/**
 * Fetches the deep-star catalog's status once, when the component mounts.
 *
 * The status is only used to decide whether to show a prompt, so a failed
 * request is not an error worth interrupting the user for: it simply leaves
 * the status null, and no prompt is shown.
 *
 * @func useDeepCatalogStatus
 * @returns {{ status: DeepCatalogStatus | null }} The status, or null until it has loaded (or if it could not be).
 */
export const useDeepCatalogStatus = () => {
  const [status, setStatus] = useState<DeepCatalogStatus | null>(null);

  useEffect(() => {
    let active = true;
    callBackend('planetarium:get_deep_catalog_status', {})
      .then((loadedStatus) => {
        if (active) setStatus(loadedStatus);
      })
      .catch(() => {
        // Leave the status null: without it the prompt just doesn't appear.
      });
    return () => {
      active = false;
    };
  }, []);

  return { status };
};
