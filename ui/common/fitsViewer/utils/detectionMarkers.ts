/**
 * @file detectionMarkers.ts
 * @description Turns the stars the star finder detected into the shapes the Detections overlay draws. A
 * detection sits at a 1-based file pixel; the picture is shown with a zoom, a pan and a row order, so each
 * position is turned into a screen position the same way the star profile marker is. Thousands of markers
 * are drawn as two SVG paths (one for single stars, one for merged ones), and markers outside the view
 * are left out. When the measurement gives the photometry circle radius, each circle is drawn at that size
 * in image pixels, so it grows and shrinks with the zoom like the stars do and shows the light photometry
 * would count.
 */
import type { StarDetection } from '../../types/backendTypes';

/** Radius, in screen pixels, of the circle drawn for a single detection when no photometry radius is known. */
export const DETECTION_MARKER_RADIUS_PX = 5;

/** Half the width, in screen pixels, of the diamond drawn for a merged detection when no photometry radius is known. */
export const MERGED_MARKER_RADIUS_PX = 6;

/** The smallest circle, in screen pixels, so markers stay visible when the image is zoomed far out. */
export const MINIMUM_MARKER_RADIUS_PX = 3;

/** How much wider a merged star's diamond is than a single star's circle, so the two shapes read apart. */
const MERGED_TO_SINGLE_SIZE = MERGED_MARKER_RADIUS_PX / DETECTION_MARKER_RADIUS_PX;

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
  /**
   * Radius, in image pixels, of the circle photometry measures each star in. When it is set, markers are
   * drawn at this size (never below {@link MINIMUM_MARKER_RADIUS_PX}); otherwise at the fixed screen size.
   */
  apertureRadiusPx?: number | null;
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
  const hasAperture = geometry.apertureRadiusPx != null && geometry.apertureRadiusPx > 0;
  // The circle's radius on screen along each axis: the photometry circle scaled by the zoom, or the fixed size.
  const rx = hasAperture
    ? Math.max(geometry.apertureRadiusPx! * geometry.stepX, MINIMUM_MARKER_RADIUS_PX)
    : DETECTION_MARKER_RADIUS_PX;
  const ry = hasAperture
    ? Math.max(geometry.apertureRadiusPx! * geometry.stepY, MINIMUM_MARKER_RADIUS_PX)
    : DETECTION_MARKER_RADIUS_PX;
  const diamondX = rx * MERGED_TO_SINGLE_SIZE;
  const diamondY = ry * MERGED_TO_SINGLE_SIZE;
  const margin = Math.max(diamondX, diamondY);
  for (const detection of detections) {
    const { x, y } = detectionScreenPosition(detection, geometry);
    if (x < -margin || y < -margin || x > geometry.viewWidth + margin || y > geometry.viewHeight + margin) continue;
    const px = x.toFixed(1);
    const py = y.toFixed(1);
    if (detection.merged) {
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
  return { circles: circles.join(''), diamonds: diamonds.join(''), visibleCount: circles.length + diamonds.length };
}
