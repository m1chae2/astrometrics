/**
 * @file test_handedOffStar.test.ts
 * @description Tests that the Astronomy Manager shows a star it was asked to open under the right target.
 */

import { describe, expect, it } from 'vitest';
import { chooseTargetForStar, readHandedOffStar } from '../utils/handedOffStar';

describe('readHandedOffStar', () => {
  /** A plain star id, as the Planetarium sends it, has no target. */
  it('reads a plain star id', () => {
    expect(readHandedOffStar('HD 151023')).toEqual({ starId: 'HD 151023' });
  });

  /** The image hand-off names the target the person came from. */
  it('reads the star id and the target the person came from', () => {
    expect(readHandedOffStar({ starId: 'Gaia DR3 1', targetId: 'NGC 7331' })).toEqual({
      starId: 'Gaia DR3 1',
      targetId: 'NGC 7331',
    });
  });
});

describe('chooseTargetForStar', () => {
  /** The target the person came from wins when the star belongs to it. */
  it('prefers the target the person came from', () => {
    expect(chooseTargetForStar(['Albireo', 'NGC 7331'], 'Albireo', 'NGC 7331')).toBe('NGC 7331');
  });

  /** Moves off the first target of the list when the star is not in it. */
  it('uses the first target of the star when the selected one does not hold it', () => {
    expect(chooseTargetForStar(['NGC 7331', 'M 31'], 'Albireo')).toBe('NGC 7331');
  });

  /** Keeps the selection when the star already belongs to it and no origin is given. */
  it('keeps the selected target when the star belongs to it', () => {
    expect(chooseTargetForStar(['NGC 7331', 'M 31'], 'M 31')).toBe('M 31');
  });

  /** Ignores a target the star does not belong to. */
  it('ignores a came-from target that is not in the star record', () => {
    expect(chooseTargetForStar(['NGC 7331'], 'Albireo', 'M 81')).toBe('NGC 7331');
  });

  /** Leaves the list alone for a star with no targets. */
  it('gives nothing for a star with no targets', () => {
    expect(chooseTargetForStar([], 'Albireo')).toBeNull();
  });
});
