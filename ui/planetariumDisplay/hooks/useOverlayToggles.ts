/**
 * @module useOverlayToggles
 * @fileoverview Manages all boolean overlay-visibility toggles for the Planetarium map.
 *
 * Extracted from PlanetariumDisplay to keep the root component focused on data
 * orchestration rather than toggle bookkeeping.
 *
 */

import { useState } from 'react';

/**
 * Default visibility for each overlay/plot toggle, per REQ PLN-2.1. Shared by the
 * initial useState calls below and by callers (e.g. the splash-screen preload in
 * PlanetariumDisplay) that need to revert a toggle back to its default.
 */
export const DEFAULT_OVERLAY_TOGGLES = {
  showStars: true,
  showFOVOutline: false,
  showFITSOverlays: false,
  showEnvironment: true,
  showGrid: true,
  showCatalog: true,
  showConstellations: true,
  showTelescope: true,
  showSpectraPlot: false,
  showPhotometryPlot: false,
} as const;

/**
 * All overlay and plot visibility toggle state/setter pairs returned by useOverlayToggles.
 */
export interface OverlayToggles {
  /** Show star catalog overlay. */
  showStars: boolean;
  setShowStars: (value: boolean) => void;
  /** Show sensor FOV outline overlay. */
  showFOVOutline: boolean;
  setShowFOVOutline: (value: boolean) => void;
  /** Show FITS image overlays. */
  showFITSOverlays: boolean;
  setShowFITSOverlays: (value: boolean) => void;
  /** Show local horizon and ground environment overlay. */
  showEnvironment: boolean;
  setShowEnvironment: (value: boolean) => void;
  /** Show RA/Dec coordinate grid overlay. */
  showGrid: boolean;
  setShowGrid: (value: boolean) => void;
  /** Show library (cataloged) objects. */
  showCatalog: boolean;
  setShowCatalog: (value: boolean) => void;
  /** Show bundled constellation stick-figure lines. */
  showConstellations: boolean;
  setShowConstellations: (value: boolean) => void;
  /** Show telescope pointing crosshair overlay. */
  showTelescope: boolean;
  setShowTelescope: (value: boolean) => void;
  /** Whether the full spectroscopy plot panel is visible. */
  showSpectraPlot: boolean;
  setShowSpectraPlot: (value: boolean) => void;
  /** Whether the full photometry plot panel is visible. */
  showPhotometryPlot: boolean;
  setShowPhotometryPlot: (value: boolean) => void;
}

/**
 * Aggregates all Planetarium overlay boolean toggle states into a single hook.
 * Default values match the initial display configuration per REQ PLN-2.1.
 *
 * @func useOverlayToggles
 * @returns {OverlayToggles} All overlay toggle state/setter pairs.
 */
export const useOverlayToggles = (): OverlayToggles => {
  const [showStars, setShowStars] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showStars);
  const [showFOVOutline, setShowFOVOutline] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showFOVOutline);
  const [showFITSOverlays, setShowFITSOverlays] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showFITSOverlays);
  const [showEnvironment, setShowEnvironment] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showEnvironment);
  const [showGrid, setShowGrid] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showGrid);
  const [showCatalog, setShowCatalog] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showCatalog);
  const [showConstellations, setShowConstellations] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showConstellations);
  const [showTelescope, setShowTelescope] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showTelescope);
  const [showSpectraPlot, setShowSpectraPlot] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showSpectraPlot);
  const [showPhotometryPlot, setShowPhotometryPlot] = useState<boolean>(DEFAULT_OVERLAY_TOGGLES.showPhotometryPlot);

  return {
    showStars, setShowStars,
    showFOVOutline, setShowFOVOutline,
    showFITSOverlays, setShowFITSOverlays,
    showEnvironment, setShowEnvironment,
    showGrid, setShowGrid,
    showCatalog, setShowCatalog,
    showConstellations, setShowConstellations,
    showTelescope, setShowTelescope,
    showSpectraPlot, setShowSpectraPlot,
    showPhotometryPlot, setShowPhotometryPlot,
  };
};
