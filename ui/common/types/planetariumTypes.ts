/**
 * Planetarium Types
 *
 * TypeScript interfaces for the Planetarium view.
 * Separated from backendTypes.ts to prevent overwrites by the Python codegen script.
 */

/**
 * PlanetariumSource Interface
 *
 * Defines a stellar library object positioned for the sky overlay.
 */
export interface PlanetariumSource {
  id: string;
  ra: number;
  dec: number;
  name: string;
  spectralType?: string;
  magnitude?: number;
  /** The library's verdict that `magnitude` is a real catalog magnitude (not missing, 0 or instrumental). */
  hasCatalogMagnitude?: boolean;
  hasSpectra: boolean;
  hasPhotometry: boolean;
  type?: "star" | "target" | "alignment";
  altitude?: number;
  azimuth?: number;
  hourAngle?: number;
  flipRequired?: boolean;
  timeToFlipSeconds?: number;
  riseTime?: string;
  setTime?: string;
  transitTime?: string;
  aboveHorizon?: boolean;
  /** Online catalog driver that produced this source. Undefined for local library sources. */
  catalogSource?: 'deep_stars' | 'hipparcos';
  stackedImage?: string;
  fieldOfView?: string;
  /** Associated alignment attempt metadata when type === 'alignment'. */
  alignmentAttempt?: import('./backendTypes').AlignmentAttempt;
  /** The library's per-target alignment session when type === 'alignment'. */
  alignmentSession?: import('./backendTypes').AlignmentTargetSession;
}

/**
 * What `planetarium:get_catalog_sources` replies with.
 *
 * `starsTruncated` is true when a catalog driver cut its answer short (the Gaia driver stops at
 * 20,000 stars), so fainter stars inside the circle are missing. A cut answer must not be reused
 * for a different view, because a fresh query for that view would return stars this one lacks.
 * An answer with failedCatalogs is missing the stars of those catalogs, so it is shown but not kept.
 */
export interface CatalogSourcesResult {
  sources: PlanetariumSource[];
  starsTruncated: boolean;
  /** Names of the catalog drivers that could not answer; empty when all did. */
  failedCatalogs: string[];
}

/**
 * PlanetariumTarget Interface
 *
 * Defines a target with an associated stacked FITS image for sky projection.
 */
export interface PlanetariumTarget {
  id: string;
  commonName?: string;
  ra: number;
  dec: number;
  stackedImage?: string;
  fieldOfView?: string;
}

/**
 * ObserverLocation Interface
 *
 * Defines geographical observer coordinates needed for dynamic horizon overlays.
 */
export interface ObserverLocation {
  latitude: number;
  longitude: number;
  elevation: number;
}

/**
 * ConstellationLineSegment Interface
 *
 * A single stick-figure line segment between two stars in a constellation,
 * pre-resolved to RA/Dec by the backend's bundled constellation line data.
 */
export interface ConstellationLineSegment {
  constellation: string;
  ra1: number;
  dec1: number;
  ra2: number;
  dec2: number;
}
