/**
 * @module mtfStretchGL
 * @fileoverview Single canonical WebGL2 implementation of the FITS midtone
 * transfer function (MTF) auto-stretch, shared by FitsLoaderItem.tsx and
 * fitsWorker.ts.
 *
 * Those two call sites previously carried independent, genuinely-diverged
 * copies of this stretch: fitsWorker.ts had a numerical-stability guard near
 * midtones ≈ 0.5 and RGB-planar support that FitsLoaderItem.tsx lacked;
 * FitsLoaderItem.tsx corrected FITS row order (TOP-DOWN vs BOTTOM-UP) which
 * fitsWorker.ts's render command silently ignored; the two used different
 * statistics sample caps (50k vs 100k). This module keeps every one of those
 * behaviors (unified to fitsWorker.ts's 100k sample cap), and moves the
 * actual per-pixel remap — a pure, cross-pixel-independent nonlinear
 * function — onto the GPU as a single-pass fragment shader. The bounded
 * subsample statistics pass that derives the stretch parameters stays on the
 * CPU, since it inherently needs a sort and is already cheap relative to a
 * full-resolution per-pixel remap.
 */

import { compileShader, linkProgram, createBuffer } from '../webgl/glUtils';

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
  vec4 texel = texture(uSourceTexture, vTexCoord);
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
  };
}

// One set of GL resources per context: each call site owns a long-lived
// canvas/context (FitsLoaderItem's offscreen canvas, fitsWorker's
// OffscreenCanvas), so compiling the shader once per context and reusing it
// across repeated render calls avoids relinking on every frame.
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
    },
  };
  resourcesByContext.set(gl, resources);
  return resources;
}

/** Statistics derived from a bounded subsample of raw pixel values. */
export interface MtfStretchStatistics {
  median: number;
  standardDeviation: number;
}

/**
 * Estimates the median and standard deviation of a raw FITS pixel buffer via
 * a bounded subsample, so the cost stays flat regardless of image resolution.
 *
 * @param {Float32Array} raw - Raw physical pixel values (single-channel, or multi-plane concatenated).
 * @param {number} [sampleCap] - Maximum number of samples to draw, unified across both call sites to 100k.
 * @returns {MtfStretchStatistics} The estimated median and standard deviation.
 */
export function calculateMtfStretchStatistics(raw: Float32Array, sampleCap: number = 100000): MtfStretchStatistics {
  const totalPixels = raw.length;
  const step = Math.max(1, Math.floor(totalPixels / sampleCap));
  const samples: number[] = [];
  let sum = 0;
  let count = 0;

  for (let i = 0; i < totalPixels; i += step) {
    const value = raw[i];
    if (!isNaN(value) && value !== 0) {
      samples.push(value);
      sum += value;
      count++;
    }
  }

  if (count === 0) return { median: 0, standardDeviation: 0 };

  samples.sort((a, b) => a - b);
  const medianIndex = Math.floor(samples.length / 2);
  const median =
    samples.length % 2 !== 0 ? samples[medianIndex] : (samples[medianIndex - 1] + samples[medianIndex]) / 2;

  const mean = sum / count;
  let varianceSum = 0;
  for (let k = 0; k < count; k++) {
    varianceSum += (samples[k] - mean) ** 2;
  }
  const standardDeviation = Math.sqrt(varianceSum / count);

  return { median, standardDeviation };
}

/** Derived MTF stretch parameters, ready to hand to the fragment shader as uniforms. */
export interface MtfStretchParameters {
  shadows: number;
  range: number;
  midtones: number;
}

/**
 * Derives shadow/highlight/midtone stretch parameters from a raw pixel
 * buffer's statistics, targeting a 5% background level.
 *
 * @param {Float32Array} raw - Raw physical pixel values (single-channel, or multi-plane concatenated).
 * @param {number} [sampleCap] - Maximum number of samples used to estimate statistics.
 * @returns {MtfStretchParameters} The derived shadows, range (highlights - shadows), and midtones.
 */
export function computeMtfStretchParameters(raw: Float32Array, sampleCap: number = 100000): MtfStretchParameters {
  const { median, standardDeviation } = calculateMtfStretchStatistics(raw, sampleCap);

  const shadowSigma = -2.8;
  const shadows = Math.max(0, median + shadowSigma * standardDeviation);
  const globalMax = raw[0] > 1 || median > 1 ? 65535 : 1.0;
  const highlights = globalMax;
  const range = highlights - shadows || 1;

  const targetBackground = 0.05;
  const normalizedMedian = (median - shadows) / range;
  let midtones = 0.5;
  if (normalizedMedian > 0 && normalizedMedian < 1) {
    const x = normalizedMedian;
    const y = targetBackground;
    const numerator = x * y - x;
    const denominator = 2 * x * y - y - x;
    if (denominator !== 0) {
      midtones = numerator / denominator;
    }
  }
  midtones = Math.max(0.0001, Math.min(0.9999, midtones));

  return { shadows, range, midtones };
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
  /** Precomputed stretch parameters; derived from `raw` via computeMtfStretchParameters() when omitted. */
  parameters?: MtfStretchParameters;
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
  const parameters = input.parameters ?? computeMtfStretchParameters(raw);

  const canvas = gl.canvas as HTMLCanvasElement | OffscreenCanvas;
  canvas.width = destinationWidth;
  canvas.height = destinationHeight;
  gl.viewport(0, 0, destinationWidth, destinationHeight);

  gl.bindTexture(gl.TEXTURE_2D, resources.sourceTexture);
  // Row order is handled entirely via this flip flag rather than a shader
  // uniform: WebGL's default (unflipped) upload places raw's row 0 at the
  // texture's v=0 edge, which the full-viewport quad below maps to the
  // bottom of the canvas — exactly the BOTTOM-UP convention. Flipping during
  // upload for TOP-DOWN data places raw's row 0 at the top instead.
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, isTopDownRowOrder);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  // NEAREST (not LINEAR): float textures aren't linear-filterable without the
  // optional OES_texture_float_linear extension, and NEAREST also faithfully
  // reproduces FitsLoaderItem's original nearest-neighbor downsample when
  // destinationWidth/Height are smaller than sourceWidth/Height.
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);

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
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);

  gl.useProgram(resources.program);
  gl.uniform1f(resources.uniformLocations.shadows, parameters.shadows);
  gl.uniform1f(resources.uniformLocations.range, parameters.range);
  gl.uniform1f(resources.uniformLocations.midtones, parameters.midtones);
  gl.uniform1i(resources.uniformLocations.isColor, channels === 3 ? 1 : 0);
  gl.uniform1i(gl.getUniformLocation(resources.program, 'uSourceTexture'), 0);
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, resources.sourceTexture);

  gl.bindVertexArray(resources.vertexArrayObject);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  gl.bindVertexArray(null);
}
