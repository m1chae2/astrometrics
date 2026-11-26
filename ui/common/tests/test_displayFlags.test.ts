/**
 * @file test_displayFlags.test.ts
 * @description Tests which display is shown when Settings turns a display off:
 * a display the person opened on purpose stays, any other turned-off display is replaced.
 */
import { describe, expect, it } from 'vitest';
import { fallbackDisplayMode } from '../constants/displayFlags';

const configWithAstronomyOff = { Frontend: { enable_astronomy: 'false' } };

describe('fallbackDisplayMode', () => {
  /**
   * A click that opens the Astronomy Manager works even when Settings turns it off.
   */
  it('keeps a turned-off display the person asked to open', () => {
    expect(fallbackDisplayMode(configWithAstronomyOff, 'Astronomy Manager', 'Astronomy Manager')).toBeNull();
  });

  /**
   * A turned-off display nobody asked for is replaced by an enabled one.
   */
  it('replaces a turned-off display that was not requested', () => {
    expect(fallbackDisplayMode(configWithAstronomyOff, 'Astronomy Manager', null)).toBe('Image Viewer');
    expect(fallbackDisplayMode(configWithAstronomyOff, 'Astronomy Manager', 'Image Viewer')).toBe('Image Viewer');
  });

  /**
   * An enabled display always stays.
   */
  it('keeps an enabled display', () => {
    expect(fallbackDisplayMode({ Frontend: {} }, 'Image Viewer', null)).toBeNull();
  });
});
