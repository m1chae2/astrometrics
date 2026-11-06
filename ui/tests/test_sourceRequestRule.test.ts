/**
 * @fileoverview Tests for the rule that skips a source request already answered by
 * an earlier one, and for the equality check that keeps source arrays stable.
 */

import { describe, it, expect } from 'vitest';
import { isCoveredByFetched, sameSources } from '../planetariumDisplay/utils/sourceRequestRule';

const fetched = { ra: 100, dec: 20, radius: 30, limitingMagnitude: 9, includeUncataloged: false };

describe('isCoveredByFetched', () => {
  it('skips a zoom-in at the same center and magnitude', () => {
    expect(isCoveredByFetched(fetched, { ...fetched, radius: 15 })).toBe(true);
  });

  it('skips a smaller query offset inside the fetched circle', () => {
    expect(isCoveredByFetched(fetched, { ra: 100, dec: 30, radius: 10, limitingMagnitude: 9 })).toBe(true);
  });

  it('requests a zoom-out', () => {
    expect(isCoveredByFetched(fetched, { ...fetched, radius: 45 })).toBe(false);
  });

  it('requests when the magnitude limit is deeper', () => {
    expect(isCoveredByFetched(fetched, { ...fetched, radius: 15, limitingMagnitude: 10 })).toBe(false);
  });

  it('requests when stars without a catalog magnitude are newly wanted', () => {
    expect(isCoveredByFetched(fetched, { ...fetched, radius: 15, includeUncataloged: true })).toBe(false);
  });

  it('requests when nothing was fetched yet', () => {
    expect(isCoveredByFetched(null, fetched)).toBe(false);
  });
});

describe('sameSources', () => {
  const star = (id: string, magnitude = 5) => ({
    id, ra: 1, dec: 2, name: id, magnitude, hasSpectra: false, hasPhotometry: false, type: 'star' as const,
  });

  it('treats equal content in new objects as the same', () => {
    expect(sameSources([star('a'), star('b')], [star('a'), star('b')])).toBe(true);
  });

  it('notices a changed field or length', () => {
    expect(sameSources([star('a')], [star('a', 6)])).toBe(false);
    expect(sameSources([star('a')], [star('a'), star('b')])).toBe(false);
  });
});
