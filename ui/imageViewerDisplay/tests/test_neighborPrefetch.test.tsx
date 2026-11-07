/**
 * @file test_neighborPrefetch.test.tsx
 * @description Tests for the Image Viewer's neighbor prefetch: only the targets
 * directly above and below the selected one are fetched and decoded, once, when
 * the browser is idle; a new selection cancels what is under way; a hidden
 * window prefetches nothing. Also tests that the preview reaches the viewer.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, renderHook, waitFor } from '@testing-library/react';

const fetchProcessedImageMock = vi.fn();
vi.mock('../../common/services/imaging/imageService', () => ({
  fetchProcessedImage: (...args: unknown[]) => fetchProcessedImageMock(...args),
}));
const prefetchDecodeMock = vi.fn();
vi.mock('../../common/fitsViewer/hooks/useFitsLoader', () => ({
  prefetchDecode: (...args: unknown[]) => prefetchDecodeMock(...args),
}));

import { useNeighborPrefetch, type PrefetchCandidate } from '../hooks/useNeighborPrefetch';
import { useTargetImage } from '../hooks/useTargetImage';
import { emit as emitEvent } from '../../common/utils/eventBus';
import { cacheTargetImage, cacheTargetPreview, clearTargetImageCache, getCachedTargetImage, getCachedTargetPreview, setShownTargetImage } from '../hooks/targetImageCache';

const LIST: PrefetchCandidate[] = ['A', 'B', 'C', 'D', 'E'].map((id) => ({ id, hasImage: true }));

describe('useNeighborPrefetch', () => {
  let idleQueue: Array<() => void>;
  let hidden = false;

  /** Runs everything waiting for an idle moment. */
  const flushIdle = async () => {
    await act(async () => {
      const queued = idleQueue.splice(0);
      queued.forEach((callback) => callback());
      await Promise.resolve();
    });
  };

  beforeEach(() => {
    idleQueue = [];
    hidden = false;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    window.requestIdleCallback = ((callback: () => void) => idleQueue.push(callback)) as unknown as typeof window.requestIdleCallback;
    window.cancelIdleCallback = (() => undefined) as unknown as typeof window.cancelIdleCallback;
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    fetchProcessedImageMock.mockReset();
    fetchProcessedImageMock.mockImplementation(async () => new Blob(['x'], { type: 'application/fits' }));
    prefetchDecodeMock.mockReset();
    prefetchDecodeMock.mockResolvedValue(undefined);
  });
  afterEach(() => {
    cleanup();
    clearTargetImageCache();
  });

  /** Only the neighbors of the selected target are fetched and decoded, each once. */
  it('prefetches only the target above and the target below, once', async () => {
    const { rerender } = renderHook(({ selected }) => useNeighborPrefetch(LIST, selected, true), {
      initialProps: { selected: 'C' },
    });
    expect(fetchProcessedImageMock).not.toHaveBeenCalled(); // waits for the browser to be idle
    await flushIdle();
    await waitFor(() => expect(prefetchDecodeMock).toHaveBeenCalledTimes(2));
    const fetched = fetchProcessedImageMock.mock.calls.map((call) => call[0]).sort();
    expect(fetched).toEqual(['B', 'D']);

    // A render with the same selection starts nothing new.
    rerender({ selected: 'C' });
    await flushIdle();
    expect(fetchProcessedImageMock).toHaveBeenCalledTimes(2);
  });

  /** Moving the selection cancels the prefetch under way and prefetches the new neighbors only. */
  it('cancels on a new selection and never goes beyond the neighbors', async () => {
    const signals: AbortSignal[] = [];
    fetchProcessedImageMock.mockImplementation((_id: string, signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<Blob | null>(() => undefined); // never finishes: still "downloading"
    });
    const { rerender } = renderHook(({ selected }) => useNeighborPrefetch(LIST, selected, true), {
      initialProps: { selected: 'C' },
    });
    await flushIdle();
    await waitFor(() => expect(signals).toHaveLength(1));

    rerender({ selected: 'A' });
    expect(signals[0].aborted).toBe(true);
    await flushIdle();
    await waitFor(() => expect(signals).toHaveLength(2));
    const requested = fetchProcessedImageMock.mock.calls.map((call) => call[0]);
    expect(requested).toEqual(['D', 'B']);
    expect(requested).not.toContain('E');
  });

  /** Nothing is prefetched while the selected picture is still loading. */
  it('waits until the selected picture is ready', async () => {
    renderHook(() => useNeighborPrefetch(LIST, 'C', false));
    await flushIdle();
    expect(fetchProcessedImageMock).not.toHaveBeenCalled();
  });

  /** A neighbor with no processed image is skipped, not replaced by the next one. */
  it('skips a neighbor that has no image', async () => {
    const list = LIST.map((item) => (item.id === 'B' ? { ...item, hasImage: false } : item));
    renderHook(() => useNeighborPrefetch(list, 'C', true));
    await flushIdle();
    await waitFor(() => expect(fetchProcessedImageMock).toHaveBeenCalledTimes(1));
    expect(fetchProcessedImageMock.mock.calls[0][0]).toBe('D');
  });

  /** A hidden window prefetches nothing, and starts when it is shown again. */
  it('does nothing while the window is hidden', async () => {
    hidden = true;
    renderHook(() => useNeighborPrefetch(LIST, 'C', true));
    await flushIdle();
    expect(fetchProcessedImageMock).not.toHaveBeenCalled();

    hidden = false;
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await flushIdle();
    await waitFor(() => expect(fetchProcessedImageMock).toHaveBeenCalledTimes(2));
  });
});

describe('useTargetImage preview', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    fetchProcessedImageMock.mockReset();
  });
  afterEach(() => {
    cleanup();
    clearTargetImageCache();
  });

  /** The preview is handed out as soon as the service has it, before the full picture. */
  it('offers the preview before the full picture arrives', async () => {
    const preview = new Blob(['jpeg'], { type: 'image/jpeg' });
    let finish: (blob: Blob) => void = () => undefined;
    fetchProcessedImageMock.mockImplementation((_id: string, _signal: AbortSignal, onPreview: (blob: Blob) => void) => {
      onPreview(preview);
      return new Promise<Blob>((resolve) => { finish = resolve; });
    });
    const Probe: React.FC = () => {
      const { previewBlob, imageBlob, loading } = useTargetImage('M 42');
      return <div data-testid="state">{`${previewBlob ? 'preview' : 'nopreview'}-${imageBlob ? 'full' : 'nofull'}-${loading}`}</div>;
    };
    const { getByTestId } = render(<Probe />);
    await waitFor(() => expect(getByTestId('state').textContent).toBe('preview-nofull-true'));
    await act(async () => finish(new Blob(['fits'], { type: 'application/fits' })));
    await waitFor(() => expect(getByTestId('state').textContent).toBe('preview-full-false'));
  });

  /** Full picture kept but its decoded copy gone: the kept preview is shown first, with no fetch. */
  it('shows the cached preview with a cached full picture', async () => {
    const preview = new Blob(['jpeg'], { type: 'image/jpeg' });
    cacheTargetImage('M 42', new Blob(['fits'], { type: 'application/fits' }));
    cacheTargetPreview('M 42', preview);
    const Probe: React.FC = () => {
      const { previewBlob, imageBlob } = useTargetImage('M 42');
      return <div data-testid="state">{`${previewBlob === preview ? 'preview' : 'nopreview'}-${imageBlob ? 'full' : 'nofull'}`}</div>;
    };
    const { getByTestId } = render(<Probe />);
    await waitFor(() => expect(getByTestId('state').textContent).toBe('preview-full'));
    expect(fetchProcessedImageMock).not.toHaveBeenCalled();
  });

  /** A preview kept from an earlier fetch shows at once when the full picture must be fetched again. */
  it('reuses a cached preview while the full picture loads, and forgets it on targetsUpdated', async () => {
    const preview = new Blob(['jpeg'], { type: 'image/jpeg' });
    cacheTargetPreview('M 42', preview);
    fetchProcessedImageMock.mockImplementation(() => new Promise<Blob>(() => undefined));
    const Probe: React.FC = () => {
      const { previewBlob } = useTargetImage('M 42');
      return <div data-testid="state">{previewBlob ? 'preview' : 'nopreview'}</div>;
    };
    const { getByTestId } = render(<Probe />);
    await waitFor(() => expect(getByTestId('state').textContent).toBe('preview'));
    act(() => emitEvent('targetsUpdated'));
    await waitFor(() => expect(getByTestId('state').textContent).toBe('nopreview'));
  });

  /** The prefetch hands neighbors' previews to the cache. */
  it('stores a prefetched neighbor preview', async () => {
    const preview = new Blob(['jpeg'], { type: 'image/jpeg' });
    fetchProcessedImageMock.mockImplementation(async (_id: string, _s: AbortSignal, onPreview: (b: Blob) => void) => {
      onPreview(preview);
      return new Blob(['x'], { type: 'application/fits' });
    });
    window.requestIdleCallback = ((callback: () => void) => { callback(); return 1; }) as unknown as typeof window.requestIdleCallback;
    renderHook(() => useNeighborPrefetch(LIST, 'C', true));
    await waitFor(() => expect(getCachedTargetPreview('B')).toBe(preview));
  });
});

describe('targetImageCache eviction', () => {
  beforeEach(() => {
    let counter = 0;
    URL.createObjectURL = vi.fn(() => `blob:${counter++}`);
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => {
    setShownTargetImage(null);
    clearTargetImageCache();
  });

  /** Reading an entry makes it the newest, so a busy entry outlives an idle one. */
  it('frees the least recently used picture first', () => {
    for (let index = 0; index < 12; index += 1) cacheTargetImage(`T${index}`, new Blob(['x']));
    getCachedTargetImage('T0');
    cacheTargetImage('T12', new Blob(['x']));
    expect(getCachedTargetImage('T0')).toBeDefined();
    expect(getCachedTargetImage('T1')).toBeUndefined();
  });

  /** The picture on screen is never freed, even when it is the oldest. */
  it('never frees the picture of the shown target', () => {
    setShownTargetImage('T0');
    const shown = cacheTargetImage('T0', new Blob(['x']));
    for (let index = 1; index <= 14; index += 1) cacheTargetImage(`T${index}`, new Blob(['x']));
    expect(getCachedTargetImage('T0')).toBe(shown);
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith(shown.objectUrl);
  });
});
