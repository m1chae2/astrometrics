/**
 * @module TargetOverlay
 * @fileoverview Draws the library target markers on the Planetarium sky.
 *
 * Which marker each target gets (none, a picture outline, a small ring, corner brackets or
 * an ellipse) is decided in `targetMarkers.ts`. The colors are the `--sky-target-marker`
 * (faint) and `--sky-target-marker-selected` (bright) theme tokens. No name label is drawn
 * for the selected target, because its panel is open beside the map.
 */

import type { PlanetariumTarget, StackSkyPicture } from '../../common/types/planetariumTypes';
import { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import { pixelsPerDegree } from '../utils/projectionMath';
import { readThemeToken } from '../../common/utils/themeToken';
import { chooseTargetMarker, TargetMarker } from './targetMarkers';
import { isGridInView, projectGrid } from './StackPictureOverlay';

/** Line width of a marker on a target that is not selected, in pixels. */
const MARKER_LINE_PX = 1;

/** The marker color used when a theme token has no value, as in tests, which load no stylesheet. */
export const MISSING_MARKER_COLOR = '#ffffff';

/** Line width of the selected target's marker, in pixels. */
const SELECTED_MARKER_LINE_PX = 2;

/** Line width of the selected target's picture outline, in pixels (thin, so the picture shows). */
const FOOTPRINT_LINE_PX = 1;

/**
 * Lists the Images layer's pictures by target id, or none when the layer is off.
 *
 * @param {ProjectionContext} projectionContext The projection and the Images layer.
 * @returns {Map<string, StackSkyPicture>} The pictures, by target id.
 */
export function picturesByTarget(projectionContext: ProjectionContext): Map<string, StackSkyPicture> {
  const byTarget = new Map<string, StackSkyPicture>();
  const layer = projectionContext.stackPictures;
  if (!layer || !layer.show) return byTarget;
  for (const picture of layer.pictures) byTarget.set(picture.targetId, picture);
  return byTarget;
}

/**
 * Finds the target's picture when the Images layer draws it right now. A picture counts only
 * once it has loaded; until then (or when it fails) its target keeps its marker.
 *
 * Only a picture in view is asked about, since asking starts its load: one whose target is
 * on screen, or the selected target's when any of its edge is on screen.
 *
 * @param {PlanetariumTarget} target The target.
 * @param {boolean} isSelected Whether it is the selected target.
 * @param {ProjectionContext} projectionContext The projection and the Images layer.
 * @param {Map<string, StackSkyPicture>} pictures The layer's pictures (see picturesByTarget).
 * @returns {StackSkyPicture | null} The drawn picture, or null.
 */
export function shownPictureFor(
  target: PlanetariumTarget,
  isSelected: boolean,
  projectionContext: ProjectionContext,
  pictures: Map<string, StackSkyPicture>,
): StackSkyPicture | null {
  const picture = pictures.get(target.id);
  const layer = projectionContext.stackPictures;
  if (!picture || !layer) return null;
  const { width, height, projectCoords } = projectionContext;
  const inView = projectCoords(target.ra, target.dec).visible
    || (isSelected && isGridInView(projectGrid(picture, projectCoords, width, height), width, height));
  if (!inView) return null;
  return layer.getDrawable(picture, false) ? picture : null;
}

/**
 * Strokes one marker.
 *
 * @param {CanvasRenderingContext2D} context The canvas.
 * @param {TargetMarker} marker The marker.
 * @returns {void}
 */
function strokeMarker(context: CanvasRenderingContext2D, marker: TargetMarker): void {
  context.beginPath();
  switch (marker.kind) {
    case 'none':
      return;
    case 'footprint': {
      let drawing = false;
      for (const point of marker.outline) {
        if (!point) {
          drawing = false;
          continue;
        }
        if (drawing) context.lineTo(point.x, point.y);
        else context.moveTo(point.x, point.y);
        drawing = true;
      }
      break;
    }
    case 'ring':
      context.arc(marker.x, marker.y, marker.radiusPx, 0, 2 * Math.PI);
      break;
    case 'ellipse':
      context.ellipse(marker.x, marker.y, marker.semiMajorPx, marker.semiMinorPx, marker.rotationRad, 0, 2 * Math.PI);
      break;
    case 'brackets': {
      const { x, y, halfSizePx: half, armPx: arm } = marker;
      for (const [signX, signY] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
        const cornerX = x + signX * half;
        const cornerY = y + signY * half;
        context.moveTo(cornerX - signX * arm, cornerY);
        context.lineTo(cornerX, cornerY);
        context.lineTo(cornerX, cornerY - signY * arm);
      }
      break;
    }
  }
  context.stroke();
}

/**
 * The library targets layer.
 */
export class TargetOverlay implements PlanetariumOverlay {
  id = 'targets';
  name = 'Library Targets';

  /**
   * Draws a marker for every library target in view, if the catalog layer is on.
   *
   * @param {CanvasRenderingContext2D} context - The 2D rendering context.
   * @param {ProjectionContext} projectionContext - Projection context.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    if (!projectionContext.showCatalog) return;
    const targets = projectionContext.targets.filter(target => !(target.ra === 0 && target.dec === 0));
    if (targets.length === 0) return;

    const pictures = picturesByTarget(projectionContext);
    const scale = pixelsPerDegree(projectionContext.width, projectionContext.height, projectionContext.fov);
    const faintColor = readThemeToken('--sky-target-marker', MISSING_MARKER_COLOR);
    const selectedColor = readThemeToken('--sky-target-marker-selected', MISSING_MARKER_COLOR);

    context.save();
    context.setLineDash([]);
    for (const target of targets) {
      const isSelected = !!projectionContext.selectedTargetId && target.id === projectionContext.selectedTargetId;
      const marker = chooseTargetMarker({
        target,
        isSelected,
        shownPicture: shownPictureFor(target, isSelected, projectionContext, pictures),
        projectCoords: projectionContext.projectCoords,
        pixelsPerDeg: scale,
        width: projectionContext.width,
        height: projectionContext.height,
      });
      if (marker.kind === 'none') continue;
      context.strokeStyle = isSelected ? selectedColor : faintColor;
      context.lineWidth = marker.kind === 'footprint'
        ? FOOTPRINT_LINE_PX
        : isSelected ? SELECTED_MARKER_LINE_PX : MARKER_LINE_PX;
      strokeMarker(context, marker);
    }
    context.restore();
  }
}
