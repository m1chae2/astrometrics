/**
 * @fileoverview Unit tests for the duration text shown on alignment sessions.
 * The grouping and statistics behind those sessions now come from the
 * library (`AlignmentTargetSession`) and are tested there.
 */

import { describe, it, expect } from 'vitest';
import { formatDuration } from '../planetariumDisplay/utils/durationFormat';

describe('formatDuration', () => {
  /** Zero and negative durations read as zero seconds. */
  it('shows 0s for no time', () => {
    expect(formatDuration(0)).toBe('0s');
    expect(formatDuration(-5)).toBe('0s');
  });

  /** Durations under an hour show minutes and seconds. */
  it('shows minutes and seconds under an hour', () => {
    expect(formatDuration(120)).toBe('2m 0s');
    expect(formatDuration(800)).toBe('13m 20s');
  });

  /** Durations of an hour or more show hours and minutes. */
  it('shows hours and minutes from one hour', () => {
    expect(formatDuration(3900)).toBe('1h 5m');
  });
});
