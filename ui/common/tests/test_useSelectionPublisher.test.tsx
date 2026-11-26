/**
 * @file test_useSelectionPublisher.test.tsx
 * @description Checks the hook that tells the backend what is picked in Image Processing: it waits for the pick
 * to rest, sends nothing twice, sends nothing for an empty first pick, stays quiet when the send fails, and
 * turns an overlay star into the star the backend takes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const callBackendMock = vi.fn();
vi.mock('../services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import {
  publishedStarFromOverlay, SELECTION_PUBLISH_DELAY_MS, useSelectionPublisher,
} from '../hooks/useSelectionPublisher';
import type { PublishedSelection } from '../hooks/useSelectionPublisher';
import type { AstrometryOverlayStar } from '../services/astronomyService';

/** A pick of a stack with no star. */
const STACK_PICK: PublishedSelection = { target: 'M 81', path: '/stack/M81.fits', kind: 'stack', star: null };

/** An empty pick. */
const EMPTY_PICK: PublishedSelection = { target: null, path: null, kind: null, star: null };

describe('useSelectionPublisher', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    callBackendMock.mockReset();
    callBackendMock.mockResolvedValue({});
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('sends the pick once it has rested, and only the last of quick changes', () => {
    const { rerender } = renderHook(({ pick }) => useSelectionPublisher(pick), { initialProps: { pick: STACK_PICK } });
    act(() => { vi.advanceTimersByTime(SELECTION_PUBLISH_DELAY_MS - 50); });
    rerender({ pick: { ...STACK_PICK, path: '/raw/a.fits', kind: 'frame' } });
    act(() => { vi.advanceTimersByTime(SELECTION_PUBLISH_DELAY_MS - 50); });
    expect(callBackendMock).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(60); });
    expect(callBackendMock).toHaveBeenCalledTimes(1);
    expect(callBackendMock).toHaveBeenCalledWith(
      'ui:set_selection',
      { target: 'M 81', path: '/raw/a.fits', kind: 'frame', star: null },
      { silent: true, maxRetries: 0 },
    );
  });

  it('does not send the same pick twice', () => {
    const { rerender } = renderHook(({ pick }) => useSelectionPublisher(pick), { initialProps: { pick: STACK_PICK } });
    act(() => { vi.advanceTimersByTime(SELECTION_PUBLISH_DELAY_MS); });
    rerender({ pick: { ...STACK_PICK } });
    act(() => { vi.advanceTimersByTime(SELECTION_PUBLISH_DELAY_MS * 3); });
    expect(callBackendMock).toHaveBeenCalledTimes(1);
  });

  it('sends nothing for an empty first pick, but sends an empty pick after a real one', () => {
    const { rerender } = renderHook(({ pick }) => useSelectionPublisher(pick), { initialProps: { pick: EMPTY_PICK } });
    act(() => { vi.advanceTimersByTime(SELECTION_PUBLISH_DELAY_MS * 2); });
    expect(callBackendMock).not.toHaveBeenCalled();
    rerender({ pick: STACK_PICK });
    act(() => { vi.advanceTimersByTime(SELECTION_PUBLISH_DELAY_MS); });
    rerender({ pick: EMPTY_PICK });
    act(() => { vi.advanceTimersByTime(SELECTION_PUBLISH_DELAY_MS); });
    expect(callBackendMock).toHaveBeenCalledTimes(2);
    expect(callBackendMock.mock.calls[1][1]).toEqual(EMPTY_PICK);
  });

  it('stays quiet and does not retry when the send fails', async () => {
    callBackendMock.mockRejectedValue(new Error('Method not found'));
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { rerender } = renderHook(({ pick }) => useSelectionPublisher(pick), { initialProps: { pick: STACK_PICK } });
    await act(async () => { await vi.advanceTimersByTimeAsync(SELECTION_PUBLISH_DELAY_MS * 10); });
    rerender({ pick: { ...STACK_PICK } });
    await act(async () => { await vi.advanceTimersByTimeAsync(SELECTION_PUBLISH_DELAY_MS * 10); });
    expect(callBackendMock).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it('sends nothing while it is turned off', () => {
    renderHook(() => useSelectionPublisher(STACK_PICK, false));
    act(() => { vi.advanceTimersByTime(SELECTION_PUBLISH_DELAY_MS * 2); });
    expect(callBackendMock).not.toHaveBeenCalled();
  });
});

describe('publishedStarFromOverlay', () => {
  it('maps the overlay star fields to the names the backend takes, with pixels counted from 0', () => {
    const star = {
      id: 'det-1', name: 'HD 1', x: 250.5, y: 100, raDeg: 148.9, decDeg: 69.1, libraryId: 'HD 1',
    } as AstrometryOverlayStar;
    expect(publishedStarFromOverlay(star)).toEqual({
      id: 'det-1', name: 'HD 1', library_id: 'HD 1', ra: 148.9, dec: 69.1, x: 250.5, y: 100,
    });
  });

  it('uses the name as the id when the star has no id, and leaves unknown values empty', () => {
    const star = { id: '', name: 'Vega', x: 3, y: Number.NaN } as AstrometryOverlayStar;
    expect(publishedStarFromOverlay(star)).toEqual({
      id: 'Vega', name: 'Vega', library_id: null, ra: null, dec: null, x: 3, y: null,
    });
  });

  it('gives null for no star, or for a star with nothing to know it by', () => {
    expect(publishedStarFromOverlay(null)).toBeNull();
    expect(publishedStarFromOverlay({ id: '', name: '', x: 1, y: 1 } as AstrometryOverlayStar)).toBeNull();
  });
});
