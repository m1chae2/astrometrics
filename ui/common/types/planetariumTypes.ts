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
  timeToFlipS?: number;
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
  /**
   * The star color class to draw this star in ('O' to 'M', 'C', 'W', or 'none' for gray).
   * Set only on the stars shown in the "By spectral class" mode; other
   * stars have none and are drawn white.
   */
  colorClass?: string;
  /** Draw this star whatever the zoom (a star of a small chosen spectral class). */
  alwaysShow?: boolean;
}

/**
 * One library star of the spectral class lists (or with a spectrum), as
 * `planetarium:get_class_stars` sends it.
 *
 * Carries what decides the star's drawn color: our own spectrum's type, the
 * catalog type and the catalog B-V color.
 */
export interface ClassStarColorRow {
  id: string;
  name: string;
  ra: number | null;
  dec: number | null;
  magnitude: number | null;
  /** The library's verdict that `magnitude` is a real catalog magnitude. */
  hasCatalogMagnitude?: boolean;
  /** The catalog spectral type, empty when unknown. */
  spectralType: string;
  /** The spectral type our own spectrum matched, empty when none. */
  ownSpectralType: string;
  /** The catalog B-V color, null when unknown. */
  bMinusV: number | null;
  hasSpectra: boolean;
  hasPhotometry: boolean;
  targetIds?: string[];
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
  /** The object's catalog size (SIMBAD's major axis) in arcminutes; null or absent when unknown. */
  majorAxisArcmin?: number | null;
  /**
   * The object's catalog minor axis in arcminutes. The library does not record it yet, so the
   * backend does not send it; with it and `positionAngleDeg` the map draws the object's ellipse.
   */
  minorAxisArcmin?: number | null;
  /** The direction of the major axis, in degrees east of north. Not sent yet (see `minorAxisArcmin`). */
  positionAngleDeg?: number | null;
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

/**
 * One target's stack picture and where it lies on the sky, as
 * `planetarium:get_stack_pictures` sends it.
 *
 * `grid` holds `gridSize` x `gridSize` `[ra, dec]` pairs in degrees, row by
 * row from the picture's top-left corner to its bottom-right corner (pixel
 * edges), so grid point (row i, column j) is picture point
 * (j / (gridSize - 1) * width, i / (gridSize - 1) * height).
 */
export interface StackSkyPicture {
  targetId: string;
  /** The picture file on the backend's computer; load it with `resolveImageSrc`. */
  picturePath: string;
  /** The picture's size in pixels. */
  width: number;
  height: number;
  /** Points along each side of the grid, edges included. */
  gridSize: number;
  grid: [number, number][];
}
