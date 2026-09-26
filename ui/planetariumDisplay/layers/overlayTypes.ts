/**
 * @module overlayTypes
 * @fileoverview Shared types for the Planetarium overlay rendering pipeline.
 */

import { PlanetariumSource, PlanetariumTarget, ConstellationLineSegment } from '../../common/types/planetariumTypes';
import { LoadedFitsEntry } from '../components/FitsLoaderItem';
import { AlignmentAttempt, PolarAlignmentStatus } from '../../common/types/backendTypes';

/**
 * Interface representing the read-only projection, coordinates, and view configuration
 * context passed from the core renderer to each overlay.
 */
export interface ProjectionContext {
  width: number;
  height: number;
  fov: number;
  centerAz: number;
  centerAlt: number;
  /** Local Sidereal Time in degrees. */
  lst: number;
  observerLat: number;
  observerLon: number;
  selectedTargetId: string;
  /** Pre-loaded and pixel-stretched FITS image data keyed by target ID. */
  loadedFits: Record<string, LoadedFitsEntry>;
  sources: PlanetariumSource[];
  targets: PlanetariumTarget[];
  showStars: boolean;
  showFOV: boolean;
  showFITS: boolean;
  showEnvironment: boolean;
  showGrid: boolean;
  showCatalog: boolean;
  /** Show bundled constellation stick-figure lines. */
  showConstellations: boolean;
  /** Full bundled set of constellation stick-figure line segments, pre-resolved to RA/Dec. */
  constellationLines: ConstellationLineSegment[];
  /** When true, the viewport is locked to follow a fixed RA/Dec coordinate. */
  trackingMode: boolean;
  projectCoords: (ra: number, dec: number) => { x: number; y: number; visible: boolean; alt: number };
  getAltAz: (ra: number, dec: number) => { alt: number; az: number };
  getRaDec: (alt: number, az: number) => { ra: number; dec: number };
  telescopeConnected?: boolean;
  telescopeRa?: number | null;
  telescopeDec?: number | null;
  showTelescope?: boolean;
  /** Show alignment pointing vectors and polar alignment overlay. */
  showAlignment?: boolean;
  /** Plate-solve alignment attempts to project onto the celestial sphere. */
  alignmentAttempts?: AlignmentAttempt[];
  /** Polar Alignment Assistant (PAA) status and coordinates. */
  polarAlignment?: PolarAlignmentStatus | null;
  /** Selected historical session identifier being reviewed. */
  selectedSessionId?: string | null;
  /** Sensor FOV width in degrees from active equipment configuration. */
  sensorFovWidthDeg?: number;
  /** Sensor FOV height in degrees from active equipment configuration. */
  sensorFovHeightDeg?: number;
  /** Show mount tracking mechanical risk heatmap. */
  showTrackingRisk?: boolean;
  /** Cumulative tracking and alignment attempts across all recorded observing sessions. */
  cumulativeTrackingAttempts?: AlignmentAttempt[];
}

/**
 * Common interface implemented by all drawing layers.
 */
export interface PlanetariumOverlay {
  id: string;
  name: string;
  /**
   * Renders this overlay layer onto the canvas.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection and visibility configuration.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void;
}
