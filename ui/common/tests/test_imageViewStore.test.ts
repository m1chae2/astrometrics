/**
 * @file test_imageViewStore.test.ts
 * @description Tests the saved Image Processing view kept in local storage: reading it back, refusing a record of the
 * wrong shape, marking it as put back, and working when storage throws.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  IMAGE_VIEW_CHANGED_EVENT, IMAGE_VIEW_STORAGE_KEY, clearImageView, loadImageView, markImageViewReturned, saveImageView,
} from '../utils/imageViewStore';
import type { SavedImageView } from '../utils/imageViewStore';

const RECORD: SavedImageView = {
  target: 'M 81', file: '/stack/M81.fits', displayMode: 'linear', astrometry: true, detections: false,
  view: { x: 0.4, y: 0.5, zoomOverFit: 3, turn: 1 }, returnPending: true, arrivedFromStar: 'Vega',
};

describe('imageViewStore', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it('reads back what was saved, and tells listeners', () => {
    const heard = vi.fn();
    window.addEventListener(IMAGE_VIEW_CHANGED_EVENT, heard);
    saveImageView(RECORD);
    window.removeEventListener(IMAGE_VIEW_CHANGED_EVENT, heard);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(loadImageView()).toEqual(RECORD);
  });

  it('refuses a record of the wrong shape and text that is not JSON', () => {
    localStorage.setItem(IMAGE_VIEW_STORAGE_KEY, JSON.stringify({ ...RECORD, view: { x: 'left' } }));
    expect(loadImageView()).toBeNull();
    localStorage.setItem(IMAGE_VIEW_STORAGE_KEY, 'not json');
    expect(loadImageView()).toBeNull();
  });

  it('marks the view as put back and forgets the star it came from, keeping the rest', () => {
    saveImageView(RECORD);
    markImageViewReturned();
    expect(loadImageView()).toEqual({ ...RECORD, returnPending: false, arrivedFromStar: null });
  });

  it('forgets the view', () => {
    saveImageView(RECORD);
    clearImageView();
    expect(loadImageView()).toBeNull();
  });

  it('does not throw when storage cannot be used', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(loadImageView()).toBeNull();
    expect(() => saveImageView(RECORD)).not.toThrow();
    expect(() => markImageViewReturned()).not.toThrow();
  });
});
