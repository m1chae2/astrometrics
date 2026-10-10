/**
 * @fileoverview Unit tests for the decoded-FITS cache.
 * Checks that the oldest entry is dropped when the cache is full, that a
 * lookup counts as use, and that every dropped entry is reported so the
 * worker can free its pixels.
 */

import { describe, it, expect } from 'vitest';
import { ParsedFitsCache } from '../common/fitsViewer/parsedFitsCache';

describe('ParsedFitsCache', () => {
  it('drops the oldest entry and reports it when the limit is exceeded', () => {
    const dropped: number[] = [];
    const cache = new ParsedFitsCache<number>(2, (entry) => dropped.push(entry));

    cache.set('a', 1);
    cache.set('b', 2);
    cache.set('c', 3);

    expect(cache.get('a')).toBeUndefined();
    expect(cache.get('b')).toBe(2);
    expect(cache.get('c')).toBe(3);
    expect(dropped).toEqual([1]);
  });

  it('treats a lookup as use, so the looked-up entry survives', () => {
    const dropped: number[] = [];
    const cache = new ParsedFitsCache<number>(2, (entry) => dropped.push(entry));
    cache.set('a', 1);
    cache.set('b', 2);

    cache.get('a');
    cache.set('c', 3);

    expect(cache.get('a')).toBe(1);
    expect(cache.get('b')).toBeUndefined();
    expect(dropped).toEqual([2]);
  });

  it('keeps Blob keys apart by identity and reports a replaced entry', () => {
    const dropped: number[] = [];
    const cache = new ParsedFitsCache<number>(5, (entry) => dropped.push(entry));
    const firstBlob = new Blob(['x']);
    const secondBlob = new Blob(['x']);

    cache.set(firstBlob, 1);
    cache.set(secondBlob, 2);
    cache.set(firstBlob, 3);

    expect(cache.get(firstBlob)).toBe(3);
    expect(cache.get(secondBlob)).toBe(2);
    expect(dropped).toEqual([1]);
  });

  it('reports every entry when cleared', () => {
    const dropped: number[] = [];
    const cache = new ParsedFitsCache<number>(5, (entry) => dropped.push(entry));
    cache.set('a', 1);
    cache.set('b', 2);

    cache.clear();

    expect(cache.get('a')).toBeUndefined();
    expect(dropped.sort()).toEqual([1, 2]);
  });
});
