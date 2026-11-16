/**
 * @file test_pixelValueUnits.test.ts
 * @description Tests how the pixel readout writes a value: counts for a raw frame, counts (value x 65,535) for a
 * float stack, "stretched" for the app's display pictures, and each channel of a color image the same way.
 */
import { describe, expect, it } from 'vitest';
import {
  RAW_VALUE_TOOLTIP, STACK_VALUE_TOOLTIP, STRETCHED_VALUE_TOOLTIP, PLAIN_VALUE_TOOLTIP, describePixelValues, formatAdu,
} from '../utils/pixelValueUnits';

describe('formatAdu', () => {
  it('writes a whole number from 100 up and one decimal below', () => {
    expect(formatAdu(468.6)).toBe('469');
    expect(formatAdu(99.94)).toBe('99.9');
    expect(formatAdu(0)).toBe('0.0');
  });
});

describe('describePixelValues', () => {
  it('shows an integer raw frame as counts', () => {
    expect(describePixelValues([412], { BITPIX: 16 })).toEqual({ text: '412 ADU', kind: 'raw', tooltip: RAW_VALUE_TOOLTIP });
  });

  it('converts a float stack by 65,535', () => {
    const result = describePixelValues([469 / 65535], { BITPIX: -32 });
    expect(result).toEqual({ text: '469 ADU', kind: 'stack', tooltip: STACK_VALUE_TOOLTIP });
    expect(describePixelValues([0.0005], { BITPIX: -32 }).text).toBe('32.8 ADU');
  });

  it('shows a stretched picture as the plain value followed by "stretched"', () => {
    const header = { BITPIX: -32, HISTORY: 'Siril 1.2 Stretched picture for display; not linear data.' };
    expect(describePixelValues([0.177], header)).toEqual({ text: '0.177 stretched', kind: 'stretched', tooltip: STRETCHED_VALUE_TOOLTIP });
  });

  it('converts each channel of a color image the same way', () => {
    expect(describePixelValues([10, 20, 30], { BITPIX: 16 }).text).toBe('R 10.0  G 20.0  B 30.0 ADU');
    expect(describePixelValues([0.01, 0.02, 0.5], { BITPIX: -32 }).text).toBe('R 655  G 1311  B 32768 ADU');
    expect(describePixelValues([0.1, 0.2, 0.3], { BITPIX: -32, HISTORY: 'Stretched picture for display' }).text)
      .toBe('R 0.1  G 0.2  B 0.3 stretched');
  });

  it('keeps the plain value when the header says nothing about the type', () => {
    expect(describePixelValues([1234], undefined)).toEqual({ text: '1234', kind: 'plain', tooltip: PLAIN_VALUE_TOOLTIP });
    expect(describePixelValues([1.5], {})).toMatchObject({ kind: 'plain' });
  });
});
