/**
 * @module StarFieldRenderer
 * @fileoverview WebGL2 point-sprite renderer for the Planetarium star field.
 *
 * Replaces StarOverlay's per-star context.arc()+fill() calls (up to
 * thousands per frame) with a single instanced-style gl.POINTS draw call.
 * Star screen positions are computed on the CPU each redraw using the exact
 * same projectionContext.projectCoords()/getRaDec() functions the 2D overlay
 * pipeline already uses, so the two rendering paths stay numerically
 * identical rather than reimplementing the stereographic projection trig in
 * GLSL. Horizon occlusion (stars below the horizon hidden when "show
 * environment" is on) is replicated exactly by omitting those stars from the
 * uploaded buffer, matching EnvironmentOverlay's own early-return edge case
 * (see shouldDiscardBelowHorizon below) rather than approximating it.
 *
 * Not a PlanetariumOverlay: that interface is hard-typed to
 * CanvasRenderingContext2D, which doesn't fit a renderer owning its own
 * WebGL2RenderingContext on a second canvas.
 */

import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { ProjectionContext } from '../layers/overlayTypes';
import {
  isDisplayableStar,
  computeLimitingMagnitude,
  computeStarBrightness,
  STAR_MARKER_RADIUS_PX,
} from '../layers/StarOverlay';
import { viewportDipsBelowHorizon } from '../layers/overlayShared';
import { compileShader, linkProgram, createBuffer, resizeCanvasToDisplaySize } from '../../common/webgl/glUtils';

const VERTEX_SHADER_SOURCE = `#version 300 es
layout(location = 0) in vec2 aScreenPosition;
layout(location = 1) in float aRadiusPx;
layout(location = 2) in float aBrightness;
uniform vec2 uCanvasSize;
out float vRadiusPx;
out float vBrightness;

void main() {
  vec2 normalized = aScreenPosition / uCanvasSize;
  vec2 clipSpace = normalized * 2.0 - 1.0;
  // Screen-space Y grows downward; clip-space Y grows upward.
  gl_Position = vec4(clipSpace.x, -clipSpace.y, 0.0, 1.0);
  gl_PointSize = aRadiusPx * 2.0;
  vRadiusPx = aRadiusPx;
  vBrightness = aBrightness;
}
`;

const FRAGMENT_SHADER_SOURCE = `#version 300 es
precision mediump float;
in float vRadiusPx;
in float vBrightness;
out vec4 fragColor;

void main() {
  // gl_PointCoord spans [0, 1] across the point sprite; recenter to [-1, 1].
  vec2 centered = (gl_PointCoord - vec2(0.5)) * 2.0;
  float distanceFromCenter = length(centered);

  // Anti-alias the circle edge over roughly one device pixel, matching the
  // soft edge context.arc()+fill() already produces on the 2D canvas.
  float edgeSoftness = 1.0 / max(vRadiusPx, 1.0);
  float alpha = (1.0 - smoothstep(1.0 - edgeSoftness, 1.0, distanceFromCenter)) * vBrightness;
  if (alpha <= 0.0) {
    discard;
  }
  fragColor = vec4(1.0, 1.0, 1.0, alpha);
}
`;

/** Floats uploaded per star instance: screenX, screenY, radiusPx, brightness. */
const FLOATS_PER_STAR = 4;

/**
 * WebGL2 renderer drawing the Planetarium star field as anti-aliased point sprites.
 */
export class StarFieldRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly program: WebGLProgram;
  private readonly instanceBuffer: WebGLBuffer;
  private readonly vertexArrayObject: WebGLVertexArrayObject;
  private readonly canvasSizeUniformLocation: WebGLUniformLocation | null;
  private starInstanceData: Float32Array = new Float32Array(0);
  private starInstanceCount = 0;

  /**
   * Constructs the renderer, acquiring a WebGL2 context from the given canvas.
   *
   * @param {HTMLCanvasElement} canvas - The (transparent, pointer-events-none) canvas dedicated to star rendering.
   * @throws {Error} If WebGL2 is unavailable on this canvas.
   */
  constructor(canvas: HTMLCanvasElement) {
    const gl = canvas.getContext('webgl2', { alpha: true, premultipliedAlpha: true, antialias: true });
    if (!gl) {
      throw new Error('WebGL2 is not available on this canvas.');
    }
    this.gl = gl;
    this.program = linkProgram(gl, VERTEX_SHADER_SOURCE, FRAGMENT_SHADER_SOURCE);
    this.canvasSizeUniformLocation = gl.getUniformLocation(this.program, 'uCanvasSize');

    this.instanceBuffer = createBuffer(gl);

    const vertexArrayObject = gl.createVertexArray();
    if (!vertexArrayObject) {
      throw new Error('Failed to create WebGL vertex array object.');
    }
    this.vertexArrayObject = vertexArrayObject;

    gl.bindVertexArray(this.vertexArrayObject);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instanceBuffer);
    const stride = FLOATS_PER_STAR * Float32Array.BYTES_PER_ELEMENT;
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, stride, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 1, gl.FLOAT, false, stride, 2 * Float32Array.BYTES_PER_ELEMENT);
    gl.enableVertexAttribArray(2);
    gl.vertexAttribPointer(2, 1, gl.FLOAT, false, stride, 3 * Float32Array.BYTES_PER_ELEMENT);
    gl.bindVertexArray(null);

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  }

  /**
   * Renders the current frame's visible star field, projecting each source
   * to screen space via the shared projectionContext helpers.
   *
   * @param {ProjectionContext} projectionContext - The current frame's projection and visibility state.
   * @returns {void}
   */
  render(projectionContext: ProjectionContext): void {
    const { gl } = this;
    const canvas = gl.canvas as HTMLCanvasElement;
    resizeCanvasToDisplaySize(canvas, projectionContext.width, projectionContext.height);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    // showStars gates only the generic Hipparcos background field; the
    // user's own cataloged stars stay independently gated by showCatalog
    // inside isDisplayableStar, so this early-out only fires when neither
    // could possibly show anything.
    if (!projectionContext.showStars && !projectionContext.showCatalog) return;

    this.rebuildStarInstanceData(projectionContext);
    if (this.starInstanceCount === 0) return;

    gl.useProgram(this.program);
    gl.uniform2f(this.canvasSizeUniformLocation, canvas.width, canvas.height);

    gl.bindVertexArray(this.vertexArrayObject);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instanceBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, this.starInstanceData, gl.DYNAMIC_DRAW);
    gl.drawArrays(gl.POINTS, 0, this.starInstanceCount);
    gl.bindVertexArray(null);
  }

  /**
   * Recomputes screen positions and radii for every currently-displayable,
   * on-screen, non-occluded star, replacing starInstanceData/starInstanceCount.
   *
   * @param {ProjectionContext} projectionContext - The current frame's projection and visibility state.
   * @returns {void}
   */
  private rebuildStarInstanceData(projectionContext: ProjectionContext): void {
    const sources: PlanetariumSource[] = projectionContext.sources;

    // Mirrors EnvironmentOverlay's own early-return: when the whole viewport
    // is above the horizon, nothing is painted over below-horizon stars, so
    // none should be discarded even if a marginal one projects to alt < 0.
    const shouldDiscardBelowHorizon =
      projectionContext.showEnvironment &&
      viewportDipsBelowHorizon(projectionContext.centerAlt, projectionContext.fov);

    const requiredFloats = sources.length * FLOATS_PER_STAR;
    if (this.starInstanceData.length < requiredFloats) {
      this.starInstanceData = new Float32Array(requiredFloats);
    }

    const limitingMagnitude = computeLimitingMagnitude(projectionContext.fov);

    let writeIndex = 0;
    for (const source of sources) {
      if (!isDisplayableStar(source, projectionContext.showStars, projectionContext.showCatalog, limitingMagnitude))
        continue;

      const point = projectionContext.projectCoords(source.ra, source.dec);
      if (!point.visible) continue;
      if (shouldDiscardBelowHorizon && point.alt < 0) continue;

      const magnitude = typeof source.magnitude === 'number' ? source.magnitude : 5.0;
      const offset = writeIndex * FLOATS_PER_STAR;
      this.starInstanceData[offset] = point.x;
      this.starInstanceData[offset + 1] = point.y;
      this.starInstanceData[offset + 2] = STAR_MARKER_RADIUS_PX;
      this.starInstanceData[offset + 3] = computeStarBrightness(magnitude);
      writeIndex += 1;
    }

    this.starInstanceCount = writeIndex;
  }

  /**
   * Releases all WebGL resources owned by this renderer.
   *
   * Deliberately does not call the WEBGL_lose_context extension's
   * loseContext(): that loss is permanent for the canvas element unless
   * something later calls restoreContext(), which nothing here does. React
   * StrictMode's dev-mode mount->cleanup->mount cycle reuses this same
   * canvas element across that cleanup, so an earlier version of this method
   * that called loseContext() left the canvas's WebGL2 context permanently
   * unusable after the very first mount, throwing on the second and
   * silently downgrading to the 2D fallback star renderer forever after.
   *
   * @returns {void}
   */
  dispose(): void {
    const { gl } = this;
    gl.deleteBuffer(this.instanceBuffer);
    gl.deleteVertexArray(this.vertexArrayObject);
    gl.deleteProgram(this.program);
  }
}
