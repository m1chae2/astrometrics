/**
 * @file detectionMarkers.ts
 * @description Turns the stars the star finder detected into the shapes the Detections overlay draws. A
 * detection sits at a 1-based file pixel; the picture is shown with a zoom, a pan and a row order, so each
 * position is turned into a screen position the same way the star profile marker is. Thousands of markers
 * are drawn as two SVG paths (one for single stars, one for merged ones), and markers outside the view
 * are left out. When the measurement gives the photometry circle radius, each circle is drawn at that size
 * in image pixels, so it grows and shrinks with the zoom like the stars do and shows the light photometry
 * would count. When that circle would be smaller than {@link MINIMUM_MARKER_RADIUS_PX} on screen, every
 * detection is drawn as a small dot instead, so a missing mark still means "not detected".
 */
import type { StarDetection } from '../../types/backendTypes';
import { fitsPixelToTurnedPicture } from '../displayTurn';

/** Radius, in screen pixels, of the circle drawn for a single detection when no photometry radius is known. */
export const DETECTION_MARKER_RADIUS_PX = 5;

/** Half the width, in screen pixels, of the diamond drawn for a merged detection when no photometry radius is known. */
export const MERGED_MARKER_RADIUS_PX = 6;

/**
 * The smallest photometry circle, in screen pixels, that is drawn as a circle. Below this the circles are
 * too small to read and, with thousands of stars, run together into a mesh, so each star is a dot instead.
 */
export const MINIMUM_MARKER_RADIUS_PX = 3;

/** Radius, in screen pixels, of the dot drawn for a single detection when the circles are too small. */
export const DOT_RADIUS_PX = 1.25;

/** Radius, in screen pixels, of the dot drawn for a merged detection, a little larger than a single star's dot. */
export const MERGED_DOT_RADIUS_PX = 2;

/** How much wider a merged star's diamond is than a single star's circle, so the two shapes read apart. */
const MERGED_TO_SINGLE_SIZE = MERGED_MARKER_RADIUS_PX / DETECTION_MARKER_RADIUS_PX;

/** How the picture sits in the viewer. */
export interface DetectionGeometry {
  /** Width of the decoded image, in file pixels. */
  imageWidth: number;
  /** Height of the decoded image, in file pixels. */
  imageHeight: number;
  /** Screen pixels for one file pixel along the screen's horizontal axis. */
  stepX: number;
  /** Screen pixels for one file pixel along the screen's vertical axis. */
  stepY: number;
  /** Offset of the image's left edge from the viewer's left edge, in screen pixels. */
  panX: number;
  /** Offset of the image's top edge from the viewer's top edge, in screen pixels. */
  panY: number;
  /** True when the picture shows the file's first row at the top. */
  firstRowAtTop: boolean;
  /** How far the picture is shown turned, clockwise in degrees (default 0). */
  turn?: number;
  /** Width of the viewer, in screen pixels. */
  viewWidth: number;
  /** Height of the viewer, in screen pixels. */
  viewHeight: number;
  /**
   * Radius, in image pixels, of the circle photometry measures each star in. When it is set, markers are
   * drawn at this size while it is at least {@link MINIMUM_MARKER_RADIUS_PX} on screen, and as dots below
   * that; with no radius, markers are circles at the fixed screen size.
   */
  apertureRadiusPx?: number | null;
}

/** The two paths to draw, and how many markers are inside the view. */
export interface DetectionPaths {
  /** "circles" when the shapes are outlines (circles and diamonds), "dots" when they are small filled dots. */
  mode: 'circles' | 'dots';
  /** SVG path data of one shape for each single detection in view: a circle, or a dot in dot mode. */
  circles: string;
  /** SVG path data of one shape for each merged detection in view: a diamond, or a larger dot in dot mode. */
  diamonds: string;
  /** How many markers are in view. */
  visibleCount: number;
}

/**
 * Finds where a detection is on the screen.
 *
 * @param detection The detection, in 1-based file pixels.
 * @param geometry How the picture sits in the viewer.
 * @returns The screen position of the center of the star's pixel.
 */
export function detectionScreenPosition(
  detection: Pick<StarDetection, 'x' | 'y'>,
  geometry: DetectionGeometry,
): { x: number; y: number } {
  const place = fitsPixelToTurnedPicture(
    detection.x, detection.y, geometry.imageWidth, geometry.imageHeight, geometry.turn ?? 0, geometry.firstRowAtTop,
  );
  return {
    x: geometry.panX + place.x * geometry.stepX,
    y: geometry.panY + place.y * geometry.stepY,
  };
}

/**
 * Builds the path data of every detection inside the view.
 *
 * @param detections The detections of the image.
 * @param geometry How the picture sits in the viewer.
 * @returns The circle path, the diamond path and the number of markers in view.
 */
export function buildDetectionPaths(detections: StarDetection[], geometry: DetectionGeometry): DetectionPaths {
  const circles: string[] = [];
  const diamonds: string[] = [];
  const hasAperture = geometry.apertureRadiusPx != null && geometry.apertureRadiusPx > 0;
  // The circle's radius on screen along each axis: the photometry circle scaled by the zoom, or the fixed size.
  const rx = hasAperture ? geometry.apertureRadiusPx! * Math.abs(geometry.stepX) : DETECTION_MARKER_RADIUS_PX;
  const ry = hasAperture ? geometry.apertureRadiusPx! * Math.abs(geometry.stepY) : DETECTION_MARKER_RADIUS_PX;
  // Below the minimum, every detection is still drawn, but as a dot. Special marks (saturated stars, close
  // pairs) would be added here as their own paths that are drawn in both modes, never hidden by the dots.
  const dotMode = hasAperture && Math.min(rx, ry) < MINIMUM_MARKER_RADIUS_PX;
  const diamondX = rx * MERGED_TO_SINGLE_SIZE;
  const diamondY = ry * MERGED_TO_SINGLE_SIZE;
  const margin = dotMode ? MERGED_DOT_RADIUS_PX : Math.max(diamondX, diamondY);
  for (const detection of detections) {
    const { x, y } = detectionScreenPosition(detection, geometry);
    if (x < -margin || y < -margin || x > geometry.viewWidth + margin || y > geometry.viewHeight + margin) continue;
    const px = x.toFixed(1);
    const py = y.toFixed(1);
    if (dotMode) {
      const radius = detection.merged ? MERGED_DOT_RADIUS_PX : DOT_RADIUS_PX;
      const width = (2 * radius).toFixed(2);
      const dot = `M${(x - radius).toFixed(1)} ${py}a${radius} ${radius} 0 1 0 ${width} 0a${radius} ${radius} 0 1 0 -${width} 0`;
      (detection.merged ? diamonds : circles).push(dot);
    } else if (detection.merged) {
      diamonds.push(
        `M${px} ${(y - diamondY).toFixed(1)}L${(x + diamondX).toFixed(1)} ${py}`
        + `L${px} ${(y + diamondY).toFixed(1)}L${(x - diamondX).toFixed(1)} ${py}Z`,
      );
    } else {
      const sx = rx.toFixed(1);
      const sy = ry.toFixed(1);
      const width = (2 * rx).toFixed(1);
      circles.push(`M${(x - rx).toFixed(1)} ${py}a${sx} ${sy} 0 1 0 ${width} 0a${sx} ${sy} 0 1 0 -${width} 0`);
    }
  }
  return { mode: dotMode ? 'dots' : 'circles', circles: circles.join(''), diamonds: diamonds.join(''), visibleCount: circles.length + diamonds.length };
}
