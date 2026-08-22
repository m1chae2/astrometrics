/**
 * Planetarium Types
 *
 * TypeScript interfaces for the Planetarium view.
 * Separated from backendTypes.ts to prevent overwrites by the Python codegen script.
 *
 * REQ: PLN-1.1, REQ: PLN-2.1, REQ: PLN-2.2, REQ: PLN-2.3
 */

/**
 * PlanetariumSource Interface
 *
 * Defines a stellar library object positioned for the sky overlay.
 * REQ: PLN-1.1, REQ: PLN-2.1
 */
export interface PlanetariumSource {
  id: string;
  ra: number;
  dec: number;
  name: string;
  spectralType?: string;
  magnitude?: number;
  hasSpectra: boolean;
  hasPhotometry: boolean;
  type?: "star" | "target";
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
  catalogSource?: 'gaia' | 'hipparcos';
  stackedImage?: string;
  fieldOfView?: string;
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
 * PlanetariumVisibilityItem Interface
 *
 * Defines the visibility results for a specific astronomical source/target.
 */
export interface PlanetariumVisibilityItem {
  id: string;
  name: string;
  ra: string;
  dec: string;
  altitude: number;
  azimuth: number;
  hour_angle: number;
  flip_required: boolean;
  time_to_flip_seconds: number;
  rise_time: string;
  set_time: string;
  transit_time: string;
  above_horizon: boolean;
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
