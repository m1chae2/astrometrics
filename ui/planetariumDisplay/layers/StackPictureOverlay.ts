/**
 * @module StackPictureOverlay
 * @fileoverview Draws each target's stack picture on the Planetarium sky (the Images layer).
 *
 * The backend gives, for each picture, where a regular grid of picture points lies on the
 * sky (worked out from the stack's plate solution, distortion included). Each grid cell is
 * split into two triangles. Every triangle's three corners go through the sky map's own
 * projection, and the picture is drawn into the triangle with the one flat (affine) mapping
 * that sends the picture triangle onto the screen triangle. So the picture follows zoom, pan
 * and sky rotation, and stays right near the pole, with no projection math of its own.
 *
 * It is drawn on the 2D canvas after the background and before the grid, constellation lines,
 * markers and labels, and the star field (which is on its own canvas above), so all of those
 * stay on top of it.
 */

import type { StackSkyPicture } from '../../common/types/planetariumTypes';
import type { PlanetariumOverlay, ProjectionContext } from './overlayTypes';
import type { PictureDrawable } from '../hooks/useStackPictures';

/** A projected grid point: its screen position, or null when it is off the visible side of the sky. */
type ScreenPoint = { x: number; y: number } | null;

/** One triangle of the mesh: three picture points and the three screen points they land on. */
export interface MeshTriangle {
  /** Picture points, in pixels of the full picture from its top-left corner. */
  source: [[number, number], [number, number], [number, number]];
  /** Screen points, in canvas pixels. */
  target: [[number, number], [number, number], [number, number]];
}

/**
 * A view this many times wider than the picture, or less, draws the full-size picture; a
 * wider view draws the smaller copy, which looks the same there and uses far less memory.
 */
const FULL_PICTURE_VIEW_RATIO = 3;

/**
 * Pixels each triangle is grown by when clipped, so neighbouring triangles overlap instead of
 * leaving an anti-aliased hairline between them. One pixel hides the seam at every zoom.
 */
const SEAM_OVERLAP_PX = 1.0;

/**
 * How far off the canvas, in canvas sizes, a projected point may lie and still be used. The
 * map's projection marks a point "not visible" once it is 100 px past the edge, but a triangle
 * reaching past the edge still has to be drawn, so its far corners are kept. Points much
 * further out are near the back of the sky, where the projection is no longer usable.
 */
const USABLE_POINT_CANVAS_SIZES = 20;

/**
 * Projects every grid point of a picture.
 *
 * @param {StackSkyPicture} picture The picture and its sky grid.
 * @param {ProjectionContext['projectCoords']} projectCoords The sky map's projection.
 * @param {number} [width] The canvas width, to judge how far out a point may be.
 * @param {number} [height] The canvas height.
 * @returns {ScreenPoint[]} One screen point per grid point, in grid order; null for a point
 *   toward the back of the sky.
 */
export function projectGrid(
  picture: StackSkyPicture,
  projectCoords: ProjectionContext['projectCoords'],
  width: number = Infinity,
  height: number = Infinity,
): ScreenPoint[] {
  const reach = USABLE_POINT_CANVAS_SIZES * Math.max(width, height);
  return picture.grid.map(([ra, dec]) => {
    const point = projectCoords(ra, dec);
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
    // Off-screen points are kept (a triangle reaching past the edge needs them) unless they
    // are so far out that they lie toward the back of the sky.
    if (!point.visible && (Math.abs(point.x) > reach || Math.abs(point.y) > reach)) return null;
    return { x: point.x, y: point.y };
  });
}

/**
 * Whether a triangle lies wholly outside the canvas: all three corners beyond the same edge.
 * A triangle that only reaches past an edge is kept, so the picture is not cut at the edges.
 *
 * @param {MeshTriangle['target']} corners The screen corners.
 * @param {number} width The canvas width.
 * @param {number} height The canvas height.
 * @returns {boolean} True when nothing of the triangle can be on the canvas.
 */
export function isTriangleOffCanvas(corners: MeshTriangle['target'], width: number, height: number): boolean {
  return corners.every(([x]) => x < 0) || corners.every(([x]) => x > width)
    || corners.every(([, y]) => y < 0) || corners.every(([, y]) => y > height);
}

/**
 * Builds the triangle mesh of a picture from its projected grid.
 *
 * Each grid cell gives two triangles, which share their edges exactly (the same grid points),
 * so the mesh has no gaps. A triangle with a corner toward the back of the sky is left out.
 *
 * @param {StackSkyPicture} picture The picture and its grid size.
 * @param {ScreenPoint[]} screen The projected grid points (see projectGrid).
 * @returns {MeshTriangle[]} The triangles to draw.
 */
export function buildMesh(picture: StackSkyPicture, screen: ScreenPoint[]): MeshTriangle[] {
  const size = picture.gridSize;
  const cell = size - 1;
  const pictureX = (column: number) => (column / cell) * picture.width;
  const pictureY = (row: number) => (row / cell) * picture.height;
  const triangles: MeshTriangle[] = [];
  for (let row = 0; row < cell; row += 1) {
    for (let column = 0; column < cell; column += 1) {
      const topLeft = screen[row * size + column];
      const topRight = screen[row * size + column + 1];
      const bottomLeft = screen[(row + 1) * size + column];
      const bottomRight = screen[(row + 1) * size + column + 1];
      const sourceTopLeft: [number, number] = [pictureX(column), pictureY(row)];
      const sourceTopRight: [number, number] = [pictureX(column + 1), pictureY(row)];
      const sourceBottomLeft: [number, number] = [pictureX(column), pictureY(row + 1)];
      const sourceBottomRight: [number, number] = [pictureX(column + 1), pictureY(row + 1)];
      if (topLeft && topRight && bottomLeft) {
        triangles.push({
          source: [sourceTopLeft, sourceTopRight, sourceBottomLeft],
          target: [[topLeft.x, topLeft.y], [topRight.x, topRight.y], [bottomLeft.x, bottomLeft.y]],
        });
      }
      if (topRight && bottomRight && bottomLeft) {
        triangles.push({
          source: [sourceTopRight, sourceBottomRight, sourceBottomLeft],
          target: [[topRight.x, topRight.y], [bottomRight.x, bottomRight.y], [bottomLeft.x, bottomLeft.y]],
        });
      }
    }
  }
  return triangles;
}

/**
 * Whether any part of the projected grid can be on the canvas.
 *
 * @param {ScreenPoint[]} screen The projected grid points.
 * @param {number} width The canvas width.
 * @param {number} height The canvas height.
 * @returns {boolean} True when the grid's screen box overlaps the canvas.
 */
export function isGridInView(screen: ScreenPoint[], width: number, height: number): boolean {
  let left = Infinity;
  let right = -Infinity;
  let top = Infinity;
  let bottom = -Infinity;
  for (const point of screen) {
    if (!point) continue;
    left = Math.min(left, point.x);
    right = Math.max(right, point.x);
    top = Math.min(top, point.y);
    bottom = Math.max(bottom, point.y);
  }
  return right >= 0 && left <= width && bottom >= 0 && top <= height;
}

/**
 * Draws one triangle of a picture: clips to the screen triangle and draws the picture with
 * the affine mapping that sends the picture triangle onto it.
 *
 * @param {CanvasRenderingContext2D} context The canvas.
 * @param {PictureDrawable} drawable The loaded picture (full or smaller copy).
 * @param {number} drawableScale The copy's size divided by the full picture's size.
 * @param {MeshTriangle} triangle The triangle.
 * @returns {void}
 */
function drawTriangle(
  context: CanvasRenderingContext2D,
  drawable: PictureDrawable,
  drawableScale: number,
  triangle: MeshTriangle,
): void {
  const [[u0, v0], [u1, v1], [u2, v2]] = triangle.source.map(([u, v]) => [u * drawableScale, v * drawableScale]);
  const [[x0, y0], [x1, y1], [x2, y2]] = triangle.target;
  const determinant = (u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0);
  if (Math.abs(determinant) < 1e-9) return;
  // The affine map (u, v) -> (x, y) through the three corner pairs.
  const a = ((x1 - x0) * (v2 - v0) - (x2 - x0) * (v1 - v0)) / determinant;
  const b = ((y1 - y0) * (v2 - v0) - (y2 - y0) * (v1 - v0)) / determinant;
  const c = ((x2 - x0) * (u1 - u0) - (x1 - x0) * (u2 - u0)) / determinant;
  const d = ((y2 - y0) * (u1 - u0) - (y1 - y0) * (u2 - u0)) / determinant;
  const e = x0 - a * u0 - c * v0;
  const f = y0 - b * u0 - d * v0;

  // Grow the clip triangle a little from its center, so neighbours overlap instead of leaving a seam.
  const centerX = (x0 + x1 + x2) / 3;
  const centerY = (y0 + y1 + y2) / 3;
  const grow = (x: number, y: number): [number, number] => {
    const length = Math.hypot(x - centerX, y - centerY) || 1;
    return [x + ((x - centerX) / length) * SEAM_OVERLAP_PX, y + ((y - centerY) / length) * SEAM_OVERLAP_PX];
  };

  context.save();
  context.beginPath();
  context.moveTo(...grow(x0, y0));
  context.lineTo(...grow(x1, y1));
  context.lineTo(...grow(x2, y2));
  context.closePath();
  context.clip();
  context.transform(a, b, c, d, e, f);
  context.drawImage(drawable, 0, 0);
  context.restore();
}

/**
 * The width of a drawable in pixels.
 *
 * @param {PictureDrawable} drawable The picture or copy.
 * @returns {number} Its width.
 */
function drawableWidth(drawable: PictureDrawable): number {
  return 'naturalWidth' in drawable ? drawable.naturalWidth : drawable.width;
}

/**
 * The Images layer: draws each target's stack picture where it lies on the sky.
 */
export class StackPictureOverlay implements PlanetariumOverlay {
  id = 'stack-pictures';
  name = 'Stack Pictures';

  /**
   * Draws every picture in view, if the Images layer is on.
   *
   * @param {CanvasRenderingContext2D} context The 2D canvas.
   * @param {ProjectionContext} projectionContext The projection and the layer's pictures.
   * @returns {void}
   */
  draw(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    const layer = projectionContext.stackPictures;
    if (!layer || !layer.show || layer.pictures.length === 0) return;

    // The triangles overlap a little at their edges. Drawn straight onto the sky at part
    // opacity, each overlap strip would be painted twice and show as a light line. So the
    // pictures are drawn at full strength on a separate canvas, which is then faded once.
    const width = context.canvas?.width ?? projectionContext.width;
    const height = context.canvas?.height ?? projectionContext.height;
    const scratch = this.getScratch(width, height);
    if (!scratch) return;
    scratch.setTransform(1, 0, 0, 1, 0, 0);
    scratch.clearRect(0, 0, width, height);
    scratch.setTransform(context.getTransform());
    this.drawPictures(scratch, projectionContext);

    context.save();
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.globalAlpha = layer.opacity;
    context.drawImage(scratch.canvas, 0, 0);
    context.restore();
  }

  /** The separate canvas the pictures are drawn on before they are faded onto the sky. */
  private scratch: CanvasRenderingContext2D | null = null;

  /**
   * Makes the overlay.
   *
   * @param {(width: number, height: number) => CanvasRenderingContext2D | null} [createScratch]
   *   Makes the separate canvas; tests pass a stand-in. By default a new page canvas is made.
   */
  constructor(
    private readonly createScratch: (width: number, height: number) => CanvasRenderingContext2D | null = (width, height) => {
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      return canvas.getContext('2d');
    },
  ) {}

  /**
   * The separate canvas, sized to the sky canvas.
   *
   * @param {number} width The sky canvas width in device pixels.
   * @param {number} height The sky canvas height in device pixels.
   * @returns {CanvasRenderingContext2D | null} Its 2D context, or null when none can be made.
   */
  private getScratch(width: number, height: number): CanvasRenderingContext2D | null {
    const canvas = this.scratch?.canvas;
    if (this.scratch && canvas && (canvas.width !== width || canvas.height !== height)) {
      canvas.width = width;
      canvas.height = height;
    }
    if (!this.scratch) this.scratch = this.createScratch(width, height);
    return this.scratch;
  }

  /**
   * Draws every picture in view at full strength.
   *
   * @param {CanvasRenderingContext2D} context The canvas to draw on.
   * @param {ProjectionContext} projectionContext The projection and the layer's pictures.
   * @returns {void}
   */
  private drawPictures(context: CanvasRenderingContext2D, projectionContext: ProjectionContext): void {
    const layer = projectionContext.stackPictures;
    if (!layer) return;
    context.save();
    context.imageSmoothingEnabled = true;
    for (const picture of layer.pictures) {
      const screen = projectGrid(picture, projectionContext.projectCoords, projectionContext.width, projectionContext.height);
      if (!isGridInView(screen, projectionContext.width, projectionContext.height)) continue;
      const center = picture.grid[Math.floor(picture.grid.length / 2)];
      const corner = picture.grid[0];
      // The picture's size on the sky, roughly, from its center to a corner and back.
      const pictureSpanDeg = 2 * Math.hypot(
        (center[0] - corner[0]) * Math.cos((center[1] * Math.PI) / 180),
        center[1] - corner[1],
      );
      const wantFull = projectionContext.fov <= pictureSpanDeg * FULL_PICTURE_VIEW_RATIO;
      const drawable = layer.getDrawable(picture, wantFull);
      if (!drawable) continue;
      const scale = drawableWidth(drawable) / picture.width;
      for (const triangle of buildMesh(picture, screen)) {
        if (isTriangleOffCanvas(triangle.target, projectionContext.width, projectionContext.height)) continue;
        drawTriangle(context, drawable, scale, triangle);
      }
    }
    context.restore();
  }
}
