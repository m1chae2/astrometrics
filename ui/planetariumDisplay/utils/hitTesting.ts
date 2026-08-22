/**
 * @module hitTesting
 * @fileoverview Spatial hit-testing utilities for the Planetarium canvas viewport.
 *
 * Provides findNearestSource(), which finds the closest visible celestial object
 * to a pixel click position by projecting each source into screen space and
 * computing Euclidean distance. Extracted from CelestialSkyMap to keep the
 * engine component free of overlay-specific sizing knowledge (reticle radii, etc.).
 *
 * REQ: PLN-2.1, REQ: PLN-2.5
 */

import { PlanetariumSource, PlanetariumTarget } from '../../common/types/planetariumTypes';
import { getAltAz, projectAltAz, pixelsPerDegree } from './projectionMath';
import { shouldOccludeBelowHorizon, parseTargetFovDegrees } from '../layers/overlayShared';
import { isDisplayableStar, computeLimitingMagnitude } from '../layers/StarOverlay';
import { TARGET_RETICLE_MIN_PX } from '../layers/TargetOverlay';

/**
 * Parameters required to perform a spatial hit-test against the visible source list.
 */
export interface HitTestParams {
  /** Click position in canvas-local pixels. */
  clickX: number;
  clickY: number;
  /** All visible star sources to test against. */
  sources: PlanetariumSource[];
  /** All visible target sources to test against. */
  targets: PlanetariumTarget[];
  /** Overlay visibility toggles (used to filter which sources are active). */
  showStars: boolean;
  showCatalog: boolean;
  /** Whether the ground/horizon overlay is showing; below-horizon sources are hidden beneath it. */
  showEnvironment: boolean;
  /** Viewport dimensions in pixels. */
  canvasWidth: number;
  canvasHeight: number;
  /** Current field of view in degrees. */
  fov: number;
  /** Current viewport centre in Alt/Az degrees. */
  centerAlt: number;
  centerAz: number;
  /** Local Sidereal Time in degrees for coordinate conversion. */
  lst: number;
  /** Observer latitude in degrees. */
  observerLat: number;
}

/**
 * Finds the nearest visible celestial source to a pixel click position.
 *
 * Stars are matched within a fixed 18px radius. Targets use a dynamic reticle
 * radius derived from their fieldOfView metadata, with a minimum of 16px, plus
 * an 8px hit-box margin. When both a star and a target are within range, the
 * star is preferred. Sources below the horizon are excluded whenever the ground
 * overlay is showing, since they are visually hidden beneath it.
 *
 * @param {HitTestParams} params - Hit-test configuration.
 * @returns {PlanetariumSource | null} The nearest matching source, or null if nothing is in range.
 *
 */
export function findNearestSource(params: HitTestParams): PlanetariumSource | null {
  const {
    clickX, clickY,
    sources, targets,
    showStars, showCatalog, showEnvironment,
    canvasWidth, canvasHeight, fov, centerAlt, centerAz, lst, observerLat,
  } = params;

  // Merge and filter to currently visible sources, routing by catalogSource.
  // Stars route through the shared isDisplayableStar rule (same as StarOverlay/
  // StarFieldRenderer); targets have no catalogSource routing and are gated
  // purely by showCatalog (matches TargetOverlay).
  const limitingMagnitude = computeLimitingMagnitude(fov);
  const combined = [...sources, ...(targets as unknown as PlanetariumSource[])];
  const activeSources = combined.filter(source => {
    if (source.ra === 0 && source.dec === 0) return false;
    if (source.type === 'star') return isDisplayableStar(source, showStars, showCatalog, limitingMagnitude);
    return showCatalog;
  });

  let nearestStar: PlanetariumSource | null = null;
  let minStarDist = 18; // px threshold for stars

  let nearestTarget: PlanetariumSource | null = null;
  let minTargetDist = Infinity;

  activeSources.forEach(source => {
    const { alt, az } = getAltAz(source.ra, source.dec, lst, observerLat);
    if (shouldOccludeBelowHorizon(showEnvironment, centerAlt, fov, alt)) return;
    const point = projectAltAz(alt, az, centerAlt, centerAz, fov, canvasWidth, canvasHeight);
    if (!point.visible) return;

    const distance = Math.sqrt(Math.pow(point.x - clickX, 2) + Math.pow(point.y - clickY, 2));

    if (source.type === 'target') {
      // Compute dynamic reticle radius from target fieldOfView metadata
      const fovDeg = parseTargetFovDegrees(source as unknown as PlanetariumTarget);
      const scale = pixelsPerDegree(canvasWidth, canvasHeight, fov);
      const sizePx = fovDeg * scale;
      const targetRadius = Math.max(TARGET_RETICLE_MIN_PX, sizePx) / 2;

      if (distance <= targetRadius + 8 && distance < minTargetDist) {
        minTargetDist = distance;
        nearestTarget = source;
      }
    } else {
      if (distance < minStarDist) {
        minStarDist = distance;
        nearestStar = source;
      }
    }
  });

  // Stars take priority over targets when both are in range
  return nearestStar || nearestTarget;
}
