/**
 * @file pixelValueUnits.ts
 * @description Says how to show a pixel value in the pixel readout, as camera counts (ADU) when it can be.
 *
 * The rule follows the library (`FULL_SCALE_COUNTS` = 65535). An integer file (BITPIX above 0) already
 * holds counts. A float stack from Siril stores each pixel as a fraction of full scale, so 1.0 is 65535
 * counts in one frame. A picture the app stretched for display holds no counts at all.
 */

/** Counts at full scale: a float stack's value 1.0 is this many counts in one frame. */
export const FULL_SCALE_COUNTS = 65535;

/** The History note the library writes into a picture it stretched for display. */
export const STRETCHED_PICTURE_NOTE = 'Stretched picture for display';

/** What a pixel value means. `plain` is a value with no known unit. */
export type PixelValueKind = 'raw' | 'stack' | 'stretched' | 'plain';

/** How to write a pixel's values. */
export interface PixelValueText {
  /** The values, written for the badge. */
  text: string;
  /** What kind of value it is. */
  kind: PixelValueKind;
  /** The tooltip of the badge. */
  tooltip: string;
}

/** The tooltip of a plain value, whose unit is not known. */
export const PLAIN_VALUE_TOOLTIP =
  'Value of this pixel in the original data, before any brightness scaling. A color image shows its red, green and blue values.';

/** The tooltip of a value in camera counts. */
export const RAW_VALUE_TOOLTIP = 'Counts recorded by the camera.';

/** The tooltip of a float stack's value converted to counts. */
export const STACK_VALUE_TOOLTIP = 'Value × 65,535: counts in one frame (the stack is an average of its frames).';

/** The tooltip of a stretched picture's value. */
export const STRETCHED_VALUE_TOOLTIP = 'This picture is stretched for display, so its values are not camera counts.';

/**
 * Writes a number with up to six significant digits, so a count of 1234 stays whole and a small
 * float stays short.
 *
 * @param value The pixel value.
 * @returns The text.
 */
export function formatPlainValue(value: number): string {
  if (Number.isNaN(value)) return 'NaN';
  if (!Number.isFinite(value)) return value > 0 ? '+Inf' : '-Inf';
  if (Number.isInteger(value)) return String(value);
  return String(Number(value.toPrecision(6)));
}

/**
 * Writes counts as a whole number from 100 up, and with one decimal below 100.
 *
 * @param counts The counts.
 * @returns The text.
 */
export function formatAdu(counts: number): string {
  if (!Number.isFinite(counts)) return formatPlainValue(counts);
  return Math.abs(counts) >= 100 ? String(Math.round(counts)) : counts.toFixed(1);
}

/**
 * Joins the channel texts: one for gray, or R, G and B for color.
 *
 * @param texts One text per channel.
 * @returns The joined text.
 */
function joinChannels(texts: readonly string[]): string {
  return texts.length === 3 ? `R ${texts[0]}  G ${texts[1]}  B ${texts[2]}` : texts.join('  ');
}

/**
 * Chooses how to write the values of one pixel, from the file's header.
 *
 * @param values One value per channel.
 * @param header The header cards of the image (BITPIX and the History text are read); may be missing.
 * @returns The text, its kind and its tooltip.
 */
export function describePixelValues(
  values: readonly number[],
  header: Readonly<Record<string, string | number>> | undefined,
): PixelValueText {
  const bitpix = Number(header?.BITPIX);
  if (header === undefined || !Number.isFinite(bitpix) || bitpix === 0) {
    return { text: joinChannels(values.map(formatPlainValue)), kind: 'plain', tooltip: PLAIN_VALUE_TOOLTIP };
  }
  if (bitpix > 0) {
    return { text: `${joinChannels(values.map(formatAdu))} ADU`, kind: 'raw', tooltip: RAW_VALUE_TOOLTIP };
  }
  if (String(header.HISTORY ?? '').includes(STRETCHED_PICTURE_NOTE)) {
    return { text: `${joinChannels(values.map(formatPlainValue))} stretched`, kind: 'stretched', tooltip: STRETCHED_VALUE_TOOLTIP };
  }
  return {
    text: `${joinChannels(values.map((value) => formatAdu(value * FULL_SCALE_COUNTS)))} ADU`,
    kind: 'stack',
    tooltip: STACK_VALUE_TOOLTIP,
  };
}
