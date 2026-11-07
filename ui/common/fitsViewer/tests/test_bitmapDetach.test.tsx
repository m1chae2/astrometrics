/**
 * @file test_bitmapDetach.test.tsx
 * @description Tests that a closed (detached) ImageBitmap is never drawn: the
 * guard skips it, a preview that changes and changes back is not handed out
 * closed, a cache eviction does not close the picture on screen, and a crash
 * inside one display leaves the other displays running.
 */
// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { drawBitmapSafely, isBitmapUsable, resetBitmapGuardWarning } from '../utils/bitmapGuard';
import { usePreviewBitmap } from '../hooks/usePreviewBitmap';
import { useFitsLoader } from '../hooks/useFitsLoader';
import { PanelErrorBoundary } from '../../components/PanelErrorBoundary';

/** A stand-in bitmap whose size drops to zero once closed. */
function makeBitmap(): ImageBitmap & { closed: boolean } {
  const fake = {
    closed: false,
    width: 10,
    height: 10,
    close() {
      fake.closed = true;
      fake.width = 0;
      fake.height = 0;
    },
  };
  return fake as unknown as ImageBitmap & { closed: boolean };
}

/** A canvas context whose drawImage throws for a closed bitmap, like the browser. */
function makeContext() {
  return {
    drawImage: vi.fn((bitmap: { closed: boolean }) => {
      if (bitmap.closed) throw new DOMException('The image source is detached', 'InvalidStateError');
    }),
  } as unknown as CanvasRenderingContext2D & { drawImage: ReturnType<typeof vi.fn> };
}

describe('drawBitmapSafely', () => {
  beforeEach(() => resetBitmapGuardWarning());

  it('draws an open bitmap', () => {
    const ctx = makeContext();
    expect(drawBitmapSafely(ctx, makeBitmap())).toBe(true);
    expect(ctx.drawImage).toHaveBeenCalledTimes(1);
  });

  it('skips a closed bitmap, warns once and does not throw', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const ctx = makeContext();
    const bitmap = makeBitmap();
    bitmap.close();
    expect(isBitmapUsable(bitmap)).toBe(false);
    expect(drawBitmapSafely(ctx, bitmap)).toBe(false);
    expect(drawBitmapSafely(ctx, bitmap)).toBe(false);
    expect(ctx.drawImage).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });

  it('catches a detach error thrown by the browser itself', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const ctx = makeContext();
    const bitmap = makeBitmap();
    bitmap.closed = true; // closed, yet still reporting a size
    expect(drawBitmapSafely(ctx, bitmap)).toBe(false);
    warn.mockRestore();
  });
});

describe('usePreviewBitmap', () => {
  let created: Array<ImageBitmap & { closed: boolean }>;
  beforeEach(() => {
    created = [];
    vi.stubGlobal('createImageBitmap', vi.fn(async () => {
      const bitmap = makeBitmap();
      created.push(bitmap);
      return bitmap;
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('never returns a closed bitmap when the preview changes and changes back', async () => {
    const blobA = new Blob(['a'], { type: 'image/jpeg' });
    const blobB = new Blob(['b'], { type: 'image/jpeg' });
    const { result, rerender } = renderHook(({ blob }) => usePreviewBitmap(blob), { initialProps: { blob: blobA as Blob | null } });
    await waitFor(() => expect(result.current).not.toBeNull());
    const first = result.current as unknown as { closed: boolean };

    rerender({ blob: blobB });
    expect(result.current).toBeNull();
    rerender({ blob: blobA });
    // Whatever is returned at any moment must be open.
    expect(result.current === null || !(result.current as unknown as { closed: boolean }).closed).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(first.closed).toBe(true);
    await waitFor(() => expect(result.current).not.toBeNull());
    expect((result.current as unknown as { closed: boolean }).closed).toBe(false);
  });
});

describe('useFitsLoader bitmap cache', () => {
  let created: Array<ImageBitmap & { closed: boolean }>;
  beforeEach(() => {
    created = [];
    vi.stubGlobal('createImageBitmap', vi.fn(async () => {
      const bitmap = makeBitmap();
      created.push(bitmap);
      return bitmap;
    }));
    vi.stubGlobal('Worker', class { postMessage() { /* none */ } terminate() { /* none */ } addEventListener() { /* none */ } removeEventListener() { /* none */ } });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('does not close the displayed bitmap when the URL cache evicts it, and closes it once replaced', async () => {
    const urls = ['a', 'b', 'c', 'd', 'e', 'f'].map((name) => `data:image/png;base64,${btoa(name)}`);
    const { result, rerender } = renderHook(({ url }) => useFitsLoader(url, null), { initialProps: { url: urls[0] as string | null } });
    await waitFor(() => expect(result.current.phase).toBe('ready'));
    const firstShown = result.current.bitmap as unknown as { closed: boolean };

    // Load four more (cache now holds five), then a sixth that evicts the first.
    for (let index = 1; index <= 4; index++) {
      rerender({ url: urls[index] });
      await waitFor(() => expect(result.current.phase).toBe('ready'));
    }
    // Return to the first (cached) so it is on screen when the sixth is added... it is shown now.
    rerender({ url: urls[0] });
    await waitFor(() => expect(result.current.bitmap).toBe(firstShown));
    // Prefetch-style insertion of a new URL while it is displayed is not possible, so load a new URL then go back.
    rerender({ url: urls[5] });
    await waitFor(() => expect(result.current.phase).toBe('ready'));
    // The oldest cached picture was not on screen when evicted, so it is freed; the one on screen is not.
    expect(created[0].closed).toBe(true);
    expect((result.current.bitmap as unknown as { closed: boolean }).closed).toBe(false);
  });
});

describe('PanelErrorBoundary', () => {
  afterEach(() => cleanup());

  /** Throws while rendering. */
  const Broken: React.FC = () => {
    throw new Error('The image source is detached');
  };

  it('shows a restart state for the broken display and keeps the others mounted', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(
      <div>
        <PanelErrorBoundary panelName="Image Viewer"><Broken /></PanelErrorBoundary>
        <PanelErrorBoundary panelName="Sky"><p>Sky is fine</p></PanelErrorBoundary>
      </div>
    );
    expect(screen.getByText('This display stopped working. The rest of the app still works.')).toBeTruthy();
    expect(screen.getByText('Sky is fine')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Restart display' })).toBeTruthy();
    error.mockRestore();
  });

  it('builds the display again when Restart display is pressed', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    let shouldThrow = true;
    const Flaky: React.FC = () => {
      if (shouldThrow) throw new Error('boom');
      return <p>Recovered</p>;
    };
    render(<div data-testid="display-root"><PanelErrorBoundary><Flaky /></PanelErrorBoundary></div>);
    // Exactly one alert: the message box; the wrapper adds none.
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    shouldThrow = false;
    act(() => { fireEvent.click(screen.getByRole('button', { name: 'Restart display' })); });
    expect(screen.getByText('Recovered')).toBeTruthy();
    // Focus moves to the display root, so it is not lost with the button.
    expect(document.activeElement).toBe(screen.getByTestId('display-root'));
    error.mockRestore();
  });
});
