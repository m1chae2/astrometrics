/**
 * @fileoverview Unit tests for the target details coordinate formatters.
 * Right ascension is shown in hours, minutes and seconds, and declination in
 * degrees, arcminutes and arcseconds.
 */

import { describe, it, expect } from 'vitest';
import { formatRaString, formatDecString } from '../imageViewerDisplay/targetDetailsManager/targetDetails/TargetDetails';

describe('formatRaString', () => {
  /** An RA the backend already writes in hours stays in hours. */
  it('keeps an RA written in hours unchanged', () => {
    expect(formatRaString('10h 6m 41s')).toBe('10h 6m 41s');
  });

  /** RA never gets degree symbols. */
  it('formats colon and space separated RA as hours, minutes and seconds', () => {
    expect(formatRaString('10:6:41')).toBe('10h 6m 41s');
    expect(formatRaString('10 6 41.4')).toBe('10h 6m 41s');
    expect(formatRaString('10 6')).toBe('10h 6m 0s');
  });

  /** A lone number has no known unit, so it is not relabeled. */
  it('does not guess the unit of a lone number', () => {
    expect(formatRaString('151.67')).toBe('151.67');
  });

  /** Missing values show a dash. */
  it('shows a dash for missing values', () => {
    expect(formatRaString(null)).toBe('—');
    expect(formatRaString('')).toBe('—');
  });
});

describe('formatDecString', () => {
  /** A declination with symbols is left alone. */
  it('keeps a declination that already has symbols', () => {
    expect(formatDecString('-5° 3′ 2′′')).toBe('-5° 3′ 2′′');
  });

  /** Separated numbers become degrees, arcminutes and arcseconds. */
  it('formats separated numbers with degree symbols', () => {
    expect(formatDecString('-5:3:2')).toBe('-5° 3′ 2″');
    expect(formatDecString('12 30')).toBe('12° 30′ 0″');
  });

  /** A decimal number of degrees is converted. */
  it('converts decimal degrees', () => {
    expect(formatDecString(-5.5)).toBe('-5° 30′ 0″');
  });

  /** Missing values show a dash. */
  it('shows a dash for missing values', () => {
    expect(formatDecString(undefined)).toBe('—');
  });
});
