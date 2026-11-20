/**
 * @file detectionMarkers.ts
 * @description Turns the stars the star finder detected into the shapes the Detections overlay draws. A
 * detection sits at a 1-based file pixel; the picture is shown with a zoom, a pan and a row order, so each
 * position is turned into a screen position the same way the star profile marker is. Thousands of markers
 * are drawn as two SVG paths (one for single stars, one for merged ones), and markers outside the view
 * are left out.
 */
import type { StarDetection } from '../../types/backendTypes';

/** Radius, in screen pixels, of the circle drawn for a single detection. */
export const DETECTION_MARKER_RADIUS_PX = 5;

/** Half the width, in screen pixels, of the diamond drawn for a merged detection. */
export const MERGED_MARKER_RADIUS_PX = 6;

/** How the picture sits in the viewer. */
export interface DetectionGeometry {
  /** Width of the decoded image, in file pixels. */
  imageWidth: number;
  /** Height of the decoded image, in file pixels. */
  imageHeight: number;
  /** Screen pixels for one file pixel along the columns. */
  stepX: number;
  /** Screen pixels for one file pixel along the rows. */
  stepY: number;
  /** Offset of the image's left edge from the viewer's left edge, in screen pixels. */
  panX: number;
  /** Offset of the image's top edge from the viewer's top edge, in screen pixels. */
  panY: number;
  /** True when the picture shows the file's first row at the top. */
  firstRowAtTop: boolean;
  /** Width of the viewer, in screen pixels. */
  viewWidth: number;
  /** Height of the viewer, in screen pixels. */
  viewHeight: number;
}

/** The two paths to draw, and how many markers are inside the view. */
export interface DetectionPaths {
  /** SVG path data of the circles, one for each single detection in view. */
  circles: string;
  /** SVG path data of the diamonds, one for each merged detection in view. */
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
  const rowFromTop = geometry.firstRowAtTop ? detection.y - 0.5 : geometry.imageHeight - detection.y + 0.5;
  return {
    x: geometry.panX + (detection.x - 0.5) * geometry.stepX,
    y: geometry.panY + rowFromTop * geometry.stepY,
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
  const margin = MERGED_MARKER_RADIUS_PX;
  for (const detection of detections) {
    const { x, y } = detectionScreenPosition(detection, geometry);
    if (x < -margin || y < -margin || x > geometry.viewWidth + margin || y > geometry.viewHeight + margin) continue;
    const px = x.toFixed(1);
    const py = y.toFixed(1);
    if (detection.merged) {
      const r = MERGED_MARKER_RADIUS_PX;
      diamonds.push(`M${px} ${(y - r).toFixed(1)}L${(x + r).toFixed(1)} ${py}L${px} ${(y + r).toFixed(1)}L${(x - r).toFixed(1)} ${py}Z`);
    } else {
      const r = DETECTION_MARKER_RADIUS_PX;
      circles.push(`M${(x - r).toFixed(1)} ${py}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`);
    }
  }
  return { circles: circles.join(''), diamonds: diamonds.join(''), visibleCount: circles.length + diamonds.length };
}
