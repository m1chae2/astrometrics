/**
 * @fileoverview Tests for the header line of a stack's own star names: the job's progress while it
 * runs, the counts and notes when it is done, and why it failed.
 */
import { describe, expect, it } from 'vitest';
import { describeStackIdentification } from '../hooks/useStackIdentification';

const base = { running: false, jobId: 'j1', progress: null, message: null, error: null, identification: null };

describe('describeStackIdentification', () => {
  it('shows the progress while the job runs', () => {
    expect(describeStackIdentification({ ...base, running: true, progress: 0.42, message: 'Downloading Gaia sky tiles' }))
      .toBe("Naming the stack's stars 42% - Downloading Gaia sky tiles");
  });

  it('gives the counts and the notes when done', () => {
    const identification = {
      stars: [], matchedCount: 1200, unmatchedCount: 30, catalogOnlyCount: 4, solutionReused: true,
      notes: ['Offline: the names come from Gaia only, because SIMBAD cannot be asked.'],
    };
    expect(describeStackIdentification({ ...base, identification })).toBe(
      '1,200 named, 30 with no catalog star, 4 catalog stars not detected. '
      + 'Offline: the names come from Gaia only, because SIMBAD cannot be asked.'
    );
  });

  it('says why the job failed, and nothing before the first answer', () => {
    expect(describeStackIdentification({ ...base, error: 'no solution' })).toBe('Naming the stars failed: no solution');
    expect(describeStackIdentification(undefined)).toBeNull();
  });
});
