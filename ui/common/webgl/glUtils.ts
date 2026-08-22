/**
 * @module glUtils
 * @fileoverview Generic, feature-agnostic WebGL2 helper functions.
 *
 * Shared low-level plumbing (shader compilation, program linking, buffer/texture
 * creation, canvas sizing) with no knowledge of what any particular caller
 * renders. Reused by both the Planetarium star-field renderer and the FITS
 * pixel-stretch shader, so it lives under ui/common/ rather than under a
 * feature-specific directory.
 */

/**
 * Compiles a single WebGL2 shader from source, throwing with the driver's
 * info log on failure.
 *
 * @param {WebGL2RenderingContext} gl - The WebGL2 rendering context.
 * @param {number} type - gl.VERTEX_SHADER or gl.FRAGMENT_SHADER.
 * @param {string} source - GLSL ES 3.00 shader source.
 * @returns {WebGLShader} The compiled shader.
 */
export function compileShader(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) {
    throw new Error('Failed to create WebGL shader object.');
  }
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const infoLog = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(`Failed to compile shader: ${infoLog}`);
  }
  return shader;
}

/**
 * Compiles a vertex/fragment shader pair and links them into a WebGL2 program.
 *
 * @param {WebGL2RenderingContext} gl - The WebGL2 rendering context.
 * @param {string} vertexSource - GLSL ES 3.00 vertex shader source.
 * @param {string} fragmentSource - GLSL ES 3.00 fragment shader source.
 * @returns {WebGLProgram} The linked program.
 */
export function linkProgram(
  gl: WebGL2RenderingContext,
  vertexSource: string,
  fragmentSource: string,
): WebGLProgram {
  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
  const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);

  const program = gl.createProgram();
  if (!program) {
    throw new Error('Failed to create WebGL program object.');
  }
  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);

  // Shaders are retained by the linked program; safe to delete our references.
  gl.deleteShader(vertexShader);
  gl.deleteShader(fragmentShader);

  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const infoLog = gl.getProgramInfoLog(program);
    gl.deleteProgram(program);
    throw new Error(`Failed to link WebGL program: ${infoLog}`);
  }
  return program;
}

/**
 * Creates a WebGL buffer, optionally uploading initial data with the given usage hint.
 *
 * @param {WebGL2RenderingContext} gl - The WebGL2 rendering context.
 * @param {BufferSource} [data] - Initial buffer contents to upload.
 * @param {number} [usage] - Usage hint (defaults to gl.DYNAMIC_DRAW, matching per-frame updates).
 * @returns {WebGLBuffer} The created buffer.
 */
export function createBuffer(
  gl: WebGL2RenderingContext,
  data?: BufferSource,
  usage: number = gl.DYNAMIC_DRAW,
): WebGLBuffer {
  const buffer = gl.createBuffer();
  if (!buffer) {
    throw new Error('Failed to create WebGL buffer object.');
  }
  if (data) {
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, usage);
  }
  return buffer;
}

/**
 * Creates a WebGL2 texture configured for non-mipmapped, clamped 2D image data
 * (the common case for uploading a single raster frame such as a FITS image).
 *
 * @param {WebGL2RenderingContext} gl - The WebGL2 rendering context.
 * @returns {WebGLTexture} The created, bound-and-configured texture.
 */
export function createTexture(gl: WebGL2RenderingContext): WebGLTexture {
  const texture = gl.createTexture();
  if (!texture) {
    throw new Error('Failed to create WebGL texture object.');
  }
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  return texture;
}

/**
 * Resizes a canvas's backing store to match the given display size, if it
 * has changed. Mirrors the plain-2D-canvas resize check already used
 * elsewhere in the Planetarium render loop, so a WebGL canvas stacked
 * alongside a 2D canvas stays pixel-aligned with it.
 *
 * @param {HTMLCanvasElement} canvas - The canvas element to resize.
 * @param {number} displayWidth - Target width in the same units the caller already uses (typically CSS pixels, matching the sibling 2D canvas).
 * @param {number} displayHeight - Target height, matching displayWidth's units.
 * @returns {boolean} True if the canvas's width/height attributes were changed.
 */
export function resizeCanvasToDisplaySize(
  canvas: HTMLCanvasElement,
  displayWidth: number,
  displayHeight: number,
): boolean {
  if (canvas.width !== displayWidth || canvas.height !== displayHeight) {
    canvas.width = displayWidth;
    canvas.height = displayHeight;
    return true;
  }
  return false;
}
