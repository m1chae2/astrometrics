/**
 * @module mtfStretchGL
 * @fileoverview Single WebGL2 implementation of the FITS midtone transfer
 * function (MTF) stretch, used by fitsWorker.ts.
 *
 * The stretch settings (black point, white point and midtones balance) come
 * from the library: the backend sends them as `stretchParameters` with an
 * image (see `ImageScaler.autostretch_parameters`), so the app and the
 * library draw an image the same way. This module only applies them: the
 * per-pixel remap, a nonlinear function of each pixel alone, runs on the GPU
 * as a single-pass fragment shader. It keeps the numerical-stability guard
 * near midtones of 0.5, planar RGB support and FITS row order handling.
 */

import { compileShader, linkProgram, createBuffer } from '../webgl/glUtils';
import { StretchParameters } from '../types/backendTypes';

const VERTEX_SHADER_SOURCE = `#version 300 es
layout(location = 0) in vec2 aPosition;
out vec2 vTexCoord;

void main() {
  // aPosition spans [-1, 1] (clip space); texcoord (0,0) lands at the
  // bottom-left, matching WebGL's default (non-flipped) texel row order.
  vTexCoord = (aPosition + 1.0) * 0.5;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
`;

const FRAGMENT_SHADER_SOURCE = `#version 300 es
precision highp float;
in vec2 vTexCoord;
uniform sampler2D uSourceTexture;
uniform float uShadows;
uniform float uRange;
uniform float uMidtones;
uniform bool uIsColor;
uniform bool uFlipVertical;
out vec4 fragColor;

float applyMidtoneTransferFunction(float midtones, float x) {
  if (x <= 0.0) return 0.0;
  if (x >= 1.0) return 1.0;
  // Numerical-stability guard: the closed-form MTF is 0/0 exactly at
  // midtones == 0.5, where the curve is mathematically the identity anyway.
  if (abs(midtones - 0.5) < 1e-6) return x;
  return ((midtones - 1.0) * x) / ((2.0 * midtones - 1.0) * x - midtones);
}

float stretchChannel(float rawValue) {
  float normalized = rawValue < uShadows ? 0.0 : (rawValue - uShadows) / uRange;
  normalized = clamp(normalized, 0.0, 1.0);
  return clamp(applyMidtoneTransferFunction(uMidtones, normalized), 0.0, 1.0);
}

void main() {
  // The texture is always uploaded in file order (row 0 at v = 0, the bottom of the canvas).
  // Flipping here instead of during upload lets one upload serve either row order.
  vec2 sourceCoord = vec2(vTexCoord.x, uFlipVertical ? 1.0 - vTexCoord.y : vTexCoord.y);
  vec4 texel = texture(uSourceTexture, sourceCoord);
  if (uIsColor) {
    fragColor = vec4(stretchChannel(texel.r), stretchChannel(texel.g), stretchChannel(texel.b), 1.0);
  } else {
    float value = stretchChannel(texel.r);
    fragColor = vec4(value, value, value, 1.0);
  }
}
`;

/** Full-viewport quad covering clip space, drawn as a triangle strip. */
const FULL_VIEWPORT_QUAD_VERTICES = new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]);

interface CachedGLResources {
  program: WebGLProgram;
  quadBuffer: WebGLBuffer;
  vertexArrayObject: WebGLVertexArrayObject;
  sourceTexture: WebGLTexture;
  uniformLocations: {
    shadows: WebGLUniformLocation | null;
    range: WebGLUniformLocation | null;
    midtones: WebGLUniformLocation | null;
    isColor: WebGLUniformLocation | null;
    flipVertical: WebGLUniformLocation | null;
    sourceTexture: WebGLUniformLocation | null;
  };
  /** Caller-chosen key of the image currently held in `sourceTexture`, or `undefined` if none/unknown. */
  uploadedTextureKey: unknown;
  /** Size and channel count of the upload, so a key match on a different layout never skips the upload. */
  uploadedLayout: string;
}

// One set of GL resources per context: each call site owns a long-lived
// canvas/context (fitsWorker keeps a single OffscreenCanvas for its whole
// life), so compiling the shader once per context and reusing it across
// repeated render calls avoids relinking on every frame.
const resourcesByContext = new WeakMap<WebGL2RenderingContext, CachedGLResources>();

function getOrCreateResources(gl: WebGL2RenderingContext): CachedGLResources {
  const cached = resourcesByContext.get(gl);
  if (cached) return cached;

  const program = linkProgram(gl, VERTEX_SHADER_SOURCE, FRAGMENT_SHADER_SOURCE);
  const quadBuffer = createBuffer(gl, FULL_VIEWPORT_QUAD_VERTICES, gl.STATIC_DRAW);

  const vertexArrayObject = gl.createVertexArray();
  if (!vertexArrayObject) {
    throw new Error('Failed to create WebGL vertex array object.');
  }
  gl.bindVertexArray(vertexArrayObject);
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);

  const sourceTexture = gl.createTexture();
  if (!sourceTexture) {
    throw new Error('Failed to create WebGL texture object.');
  }

  const resources: CachedGLResources = {
    program,
    quadBuffer,
    vertexArrayObject,
    sourceTexture,
    uniformLocations: {
      shadows: gl.getUniformLocation(program, 'uShadows'),
      range: gl.getUniformLocation(program, 'uRange'),
      midtones: gl.getUniformLocation(program, 'uMidtones'),
      isColor: gl.getUniformLocation(program, 'uIsColor'),
      flipVertical: gl.getUniformLocation(program, 'uFlipVertical'),
      sourceTexture: gl.getUniformLocation(program, 'uSourceTexture'),
    },
    uploadedTextureKey: undefined,
    uploadedLayout: '',
  };
  resourcesByContext.set(gl, resources);
  return resources;
}

/** MTF stretch parameters, ready to hand to the fragment shader as uniforms. */
export interface MtfStretchParameters {
  shadows: number;
  range: number;
  midtones: number;
}

/**
 * Turns the library's automatic stretch (black point, white point and
 * midtones balance, worked out by the backend for this image) into shader
 * settings. The stretch itself is never worked out here.
 *
 * @param {StretchParameters} stretch - The library's `stretchParameters` for the image.
 * @returns {MtfStretchParameters} The shadows, range (white point - black point) and midtones.
 */
export function shaderParametersFromLibraryStretch(stretch: StretchParameters): MtfStretchParameters {
  const range = stretch.whitePoint - stretch.blackPoint;
  return { shadows: stretch.blackPoint, range: range > 0 ? range : 1, midtones: stretch.midtones };
}

/**
 * Builds the parameters for a plain linear (unstretched) view: the darkest
 * pixel maps to black, the brightest to white, and the midtones curve is
 * switched off (a midtones balance of 0.5 leaves values unchanged), so the
 * same shader draws both views.
 *
 * @param {number} minimum - The darkest pixel value in the image.
 * @param {number} maximum - The brightest pixel value in the image.
 * @returns {MtfStretchParameters} Parameters for a linear mapping of [minimum, maximum] to [0, 1].
 */
export function computeLinearStretchParameters(minimum: number, maximum: number): MtfStretchParameters {
  return { shadows: minimum, range: maximum > minimum ? maximum - minimum : 1, midtones: 0.5 };
}

/** Input describing one MTF-stretch render pass. */
export interface MtfStretchRenderInput {
  /** Raw physical pixel values: single-channel (grayscale) or 3 concatenated planes (planar RGB, R then G then B). */
  raw: Float32Array;
  sourceWidth: number;
  sourceHeight: number;
  channels: 1 | 3;
  /**
   * True when row 0 of `raw` is already the top row of the image (FITS
   * ROWORDER = 'TOP-DOWN'); false for the FITS default (BOTTOM-UP), where
   * row 0 is the bottom row and must be flipped to display correctly.
   */
  isTopDownRowOrder: boolean;
  /** Destination canvas size in pixels; the source texture is resampled (nearest-neighbor) to fit. */
  destinationWidth: number;
  destinationHeight: number;
  /** Shader settings: the library's stretch (see shaderParametersFromLibraryStretch) or a linear range. */
  parameters: MtfStretchParameters;
  /**
   * Identifies the image in `raw`. When the same key (and size, channels and row order) is
   * passed again on the same context, the pixel upload is skipped and the texture already on
   * the GPU is redrawn. Leave it out to always upload.
   */
  textureKey?: unknown;
}

/**
 * Renders one MTF-stretched frame into the given WebGL2 context's canvas
 * (or OffscreenCanvas), sized to destinationWidth x destinationHeight. The
 * caller owns the canvas/context and reads the result back however suits its
 * use case (drawImage() for an on-screen canvas, transferToImageBitmap() for
 * an OffscreenCanvas).
 *
 * @param {WebGL2RenderingContext} gl - The caller-owned WebGL2 context, bound to its own canvas/OffscreenCanvas.
 * @param {MtfStretchRenderInput} input - Raw pixel data, dimensions, row order, and destination size.
 * @returns {void}
 */
export function renderMtfStretch(gl: WebGL2RenderingContext, input: MtfStretchRenderInput): void {
  const { raw, sourceWidth, sourceHeight, channels, isTopDownRowOrder, destinationWidth, destinationHeight } = input;
  const resources = getOrCreateResources(gl);
  const parameters = input.parameters;

  const canvas = gl.canvas as HTMLCanvasElement | OffscreenCanvas;
  // Assigning a size, even the same one, can reallocate the drawing buffer; only do it on a real change.
  if (canvas.width !== destinationWidth) canvas.width = destinationWidth;
  if (canvas.height !== destinationHeight) canvas.height = destinationHeight;
  gl.viewport(0, 0, destinationWidth, destinationHeight);

  gl.bindTexture(gl.TEXTURE_2D, resources.sourceTexture);
  const layout = `${sourceWidth}x${sourceHeight}x${channels}`;
  const canReuseUpload =
    input.textureKey !== undefined &&
    resources.uploadedTextureKey === input.textureKey &&
    resources.uploadedLayout === layout;

  if (!canReuseUpload) {
    // The upload is never flipped: raw's row 0 lands at the texture's v=0 edge,
    // which the full-viewport quad maps to the bottom of the canvas — exactly
    // the BOTTOM-UP convention. TOP-DOWN data is flipped by the shader's
    // uFlipVertical uniform instead, so the texture does not depend on row order.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    // NEAREST (not LINEAR): float textures aren't linear-filterable without the
    // optional OES_texture_float_linear extension, and NEAREST also faithfully
    // reproduces FitsLoaderItem's original nearest-neighbor downsample when
    // destinationWidth/Height are smaller than sourceWidth/Height.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);

    // Forget the old upload first: if the upload below throws, the texture must not be trusted.
    resources.uploadedTextureKey = undefined;
    resources.uploadedLayout = '';
    if (channels === 3) {
      const planeSize = sourceWidth * sourceHeight;
      const interleaved = new Float32Array(planeSize * 3);
      for (let i = 0; i < planeSize; i++) {
        interleaved[i * 3] = raw[i];
        interleaved[i * 3 + 1] = raw[planeSize + i];
        interleaved[i * 3 + 2] = raw[planeSize * 2 + i];
      }
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB32F, sourceWidth, sourceHeight, 0, gl.RGB, gl.FLOAT, interleaved);
    } else {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, sourceWidth, sourceHeight, 0, gl.RED, gl.FLOAT, raw);
    }
    resources.uploadedTextureKey = input.textureKey;
    resources.uploadedLayout = layout;
  }

  gl.useProgram(resources.program);
  gl.uniform1f(resources.uniformLocations.shadows, parameters.shadows);
  gl.uniform1f(resources.uniformLocations.range, parameters.range);
  gl.uniform1f(resources.uniformLocations.midtones, parameters.midtones);
  gl.uniform1i(resources.uniformLocations.isColor, channels === 3 ? 1 : 0);
  gl.uniform1i(resources.uniformLocations.flipVertical, isTopDownRowOrder ? 1 : 0);
  gl.uniform1i(resources.uniformLocations.sourceTexture, 0);
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, resources.sourceTexture);

  gl.bindVertexArray(resources.vertexArrayObject);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  gl.bindVertexArray(null);
}
