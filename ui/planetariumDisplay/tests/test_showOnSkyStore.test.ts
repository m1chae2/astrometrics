/**
 * @fileoverview Tests of the `showOnSky` store: a request that arrives before
 * anything subscribes is kept, bad payloads are ignored, and clearing works.
 */

import { describe, expect, it, vi } from 'vitest';
import {
  clearShowOnSkyRequest,
  getShowOnSkyRequest,
  subscribeToShowOnSky,
} from '../showOnSky/showOnSkyStore';

/**
 * Sends the window event the app turns a remote action into.
 * @param detail The event payload.
 */
function sendShowOnSky(detail: unknown): void {
  window.dispatchEvent(new CustomEvent('astrometrics:showOnSky', { detail }));
}

describe('showOnSkyStore', () => {
  /** A request sent before a subscriber exists is still there afterward. */
  it('keeps a request that arrives before mount', () => {
    clearShowOnSkyRequest();
    sendShowOnSky({ targetId: 'M 81' });
    expect(getShowOnSkyRequest()).toEqual({ targetId: 'M 81' });
  });

  /** Subscribers hear about new requests and clears. */
  it('notifies subscribers and stops after unsubscribe', () => {
    clearShowOnSkyRequest();
    const listener = vi.fn();
    const unsubscribe = subscribeToShowOnSky(listener);
    sendShowOnSky({ targetId: 'M 13' });
    expect(listener).toHaveBeenLastCalledWith({ targetId: 'M 13' });
    clearShowOnSkyRequest();
    expect(listener).toHaveBeenLastCalledWith(null);
    unsubscribe();
    sendShowOnSky({ targetId: 'M 57' });
    expect(listener).toHaveBeenCalledTimes(2);
  });

  /** A payload without a target id is ignored. */
  it('ignores a payload with no target', () => {
    clearShowOnSkyRequest();
    sendShowOnSky({});
    sendShowOnSky(null);
    expect(getShowOnSkyRequest()).toBeNull();
  });
});
