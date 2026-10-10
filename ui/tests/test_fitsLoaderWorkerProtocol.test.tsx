/**
 * @fileoverview Tests for how the FITS loader and drawer hooks share one worker.
 *
 * A fake worker stands in for `fitsWorker.ts`. The tests check that the loader
 * sends a raw file to be decoded, that the drawer asks the same worker to draw
 * the image it already holds (nothing is copied back and forth), that a cached
 * image is not decoded twice, and that decoded images are freed inside the
 * worker when they are no longer wanted.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useFitsLoader } from '../common/fitsViewer/hooks/useFitsLoader';
import { useCanvasDrawer } from '../common/fitsViewer/hooks/useCanvasDrawer';
import { PARSED_FITS_CACHE_LIMIT } from '../common/fitsViewer/parsedFitsCache';

type WorkerListener = (event: MessageEvent) => void;

/** A stand-in for the FITS worker that records what it is sent and answers on request. */
class FakeFitsWorker {
  static instances: FakeFitsWorker[] = [];

  sent: any[] = [];
  terminated = false;
  onerror: ((event: unknown) => void) | null = null;
  private listeners = new Set<WorkerListener>();

  constructor() {
    FakeFitsWorker.instances.push(this);
  }

  addEventListener(_type: string, listener: WorkerListener): void {
    this.listeners.add(listener);
  }

  removeEventListener(_type: string, listener: WorkerListener): void {
    this.listeners.delete(listener);
  }

  postMessage(message: any): void {
    this.sent.push(message);
    if (message.cmd === 'parse') {
      this.reply({
        cmd: 'parseComplete',
        requestId: message.requestId,
        imageId: message.imageId,
        result: { w: message.pw, h: message.ph, channels: message.channels, min: 0, max: 100 },
      });
    } else if (message.cmd === 'render') {
      const fakeBitmap = { width: message.dstW, height: message.dstH, close: vi.fn() };
      this.reply({ cmd: 'renderComplete', requestId: message.requestId, imageId: message.imageId, bitmap: fakeBitmap });
    }
  }

  terminate(): void {
    this.terminated = true;
  }

  /** Delivers a message to every listener, on a later tick like a real worker. */
  reply(data: unknown): void {
    queueMicrotask(() => this.listeners.forEach((listener) => listener({ data } as MessageEvent)));
  }

  commandsOf(cmd: string): any[] {
    return this.sent.filter((message) => message.cmd === cmd);
  }
}

/** Builds a minimal FITS file: one 2880-byte header block followed by 16-bit pixels. */
function buildFitsBlob(width: number, height: number): Blob {
  const card = (key: string, value: string | number) => `${key.padEnd(8)}= ${String(value).padStart(20)}`.padEnd(80);
  const header = [card('SIMPLE', 'T'), card('BITPIX', 16), card('NAXIS', 2), card('NAXIS1', width), card('NAXIS2', height), 'END'.padEnd(80)]
    .join('')
    .padEnd(2880);
  const bytes = new Uint8Array(2880 + Math.ceil((width * height * 2) / 2880) * 2880);
  bytes.set(new TextEncoder().encode(header), 0);
  const blob = new Blob([bytes]);
  // The test environment's Blob has no arrayBuffer(), which the loader calls.
  Object.defineProperty(blob, 'arrayBuffer', { value: async () => bytes.buffer.slice(0) });
  return blob;
}

describe('useFitsLoader and useCanvasDrawer sharing one worker', () => {
  beforeEach(() => {
    FakeFitsWorker.instances = [];
    vi.stubGlobal('Worker', FakeFitsWorker);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function renderLoader(initialBlob: Blob) {
    const setStatus = vi.fn();
    const hook = renderHook(({ blob }) => useFitsLoader(null, blob, setStatus), { initialProps: { blob: initialBlob } });
    return { hook, setStatus, rerender: (blob: Blob) => hook.rerender({ blob }) };
  }

  it('sends the raw file to the worker and keeps only a record of the decoded image', async () => {
    const { hook } = renderLoader(buildFitsBlob(8, 6));

    await waitFor(() => expect(hook.result.current.parsedData).not.toBeNull());

    const worker = FakeFitsWorker.instances[0];
    const parseCommand = worker.commandsOf('parse')[0];
    expect(parseCommand.pw).toBe(8);
    expect(parseCommand.ph).toBe(6);
    expect(parseCommand.bitpix).toBe(16);
    expect(hook.result.current.parsedData).toMatchObject({ w: 8, h: 6, channels: 1, min: 0, max: 100 });
    expect(hook.result.current.parsedData).not.toHaveProperty('raw');
    expect(hook.result.current.workerRef.current).toBe(worker);
  });

  it('asks the same worker to draw the image it already holds, once per stretch setting', async () => {
    const canvas = document.createElement('canvas');
    const setDrawnSize = vi.fn();
    const blob = buildFitsBlob(8, 6);
    const hook = renderHook(
      ({ stretch }) => {
        const loader = useFitsLoader(null, blob, vi.fn());
        useCanvasDrawer({ current: canvas }, loader.workerRef, loader.parsedData, loader.bitmap, setDrawnSize, stretch);
        return loader;
      },
      { initialProps: { stretch: true } },
    );

    await waitFor(() => expect(FakeFitsWorker.instances[0]?.commandsOf('render')).toHaveLength(1));
    const worker = FakeFitsWorker.instances[0];
    const firstRender = worker.commandsOf('render')[0];
    expect(firstRender.imageId).toBe(hook.result.current.parsedData?.imageId);
    expect(firstRender.stretch).toBe(true);

    hook.rerender({ stretch: false });
    await waitFor(() => expect(worker.commandsOf('render')).toHaveLength(2));

    expect(worker.commandsOf('render')[1]).toMatchObject({ imageId: firstRender.imageId, stretch: false });
    expect(worker.commandsOf('parse')).toHaveLength(1);
    expect(FakeFitsWorker.instances).toHaveLength(1);
  });

  it('passes the display range to the worker so a stretched picture keeps its 0 to 1 scale', async () => {
    const canvas = document.createElement('canvas');
    const blob = buildFitsBlob(8, 6);
    const stretchedPictureRange = [0, 1] as const;
    renderHook(() => {
      const loader = useFitsLoader(null, blob, vi.fn());
      useCanvasDrawer({ current: canvas }, loader.workerRef, loader.parsedData, loader.bitmap, vi.fn(), false, stretchedPictureRange);
      return loader;
    });

    await waitFor(() => expect(FakeFitsWorker.instances[0]?.commandsOf('render')).toHaveLength(1));

    expect(FakeFitsWorker.instances[0].commandsOf('render')[0]).toMatchObject({
      stretch: false,
      displayRange: stretchedPictureRange,
    });
  });

  it('does not decode the same file twice when it is shown again', async () => {
    const first = buildFitsBlob(8, 6);
    const second = buildFitsBlob(10, 4);
    const { hook, rerender } = renderLoader(first);
    await waitFor(() => expect(hook.result.current.parsedData?.w).toBe(8));

    rerender(second);
    await waitFor(() => expect(hook.result.current.parsedData?.w).toBe(10));
    rerender(first);
    await waitFor(() => expect(hook.result.current.parsedData?.w).toBe(8));

    expect(FakeFitsWorker.instances[0].commandsOf('parse')).toHaveLength(2);
  });

  it('frees the oldest decoded image inside the worker once the cache is full', async () => {
    const { hook, rerender } = renderLoader(buildFitsBlob(2, 2));
    await waitFor(() => expect(hook.result.current.parsedData).not.toBeNull());
    const firstImageId = hook.result.current.parsedData!.imageId;

    for (let count = 0; count < PARSED_FITS_CACHE_LIMIT; count++) {
      const blob = buildFitsBlob(3 + count, 2);
      rerender(blob);
      await waitFor(() => expect(hook.result.current.parsedData?.w).toBe(3 + count));
    }

    const releases = FakeFitsWorker.instances[0].commandsOf('release').map((message) => message.imageId);
    expect(releases).toContain(firstImageId);
  });

  it('frees a decoded image whose load was cancelled before it finished', async () => {
    const original = FakeFitsWorker.prototype.postMessage;
    vi.spyOn(FakeFitsWorker.prototype, 'postMessage').mockImplementation(function (this: FakeFitsWorker, message: any) {
      if (message.cmd === 'parse') {
        this.sent.push(message); // recorded, but never answered: the decode is "still running"
        return;
      }
      original.call(this, message);
    });

    const { hook, rerender } = renderLoader(buildFitsBlob(2, 2));
    await waitFor(() => expect(FakeFitsWorker.instances[0]?.commandsOf('parse')).toHaveLength(1));
    const abandonedImageId = FakeFitsWorker.instances[0].commandsOf('parse')[0].imageId;

    rerender(buildFitsBlob(5, 5));
    await waitFor(() => expect(FakeFitsWorker.instances[0].commandsOf('parse')).toHaveLength(2));

    const releases = FakeFitsWorker.instances[0].commandsOf('release').map((message) => message.imageId);
    expect(releases).toContain(abandonedImageId);
    expect(hook.result.current.parsedData).toBeNull();
  });

  it('stops the worker when the viewer goes away', async () => {
    const { hook } = renderLoader(buildFitsBlob(2, 2));
    await waitFor(() => expect(hook.result.current.parsedData).not.toBeNull());
    const worker = FakeFitsWorker.instances[0];

    hook.unmount();

    expect(worker.terminated).toBe(true);
  });

  it('shows an error when the worker reports one', async () => {
    const original = FakeFitsWorker.prototype.postMessage;
    vi.spyOn(FakeFitsWorker.prototype, 'postMessage').mockImplementation(function (this: FakeFitsWorker, message: any) {
      if (message.cmd === 'parse') {
        this.sent.push(message); // recorded, but not answered, so the test can send the error itself
        return;
      }
      original.call(this, message);
    });
    const { hook, setStatus } = renderLoader(buildFitsBlob(2, 2));
    await waitFor(() => expect(FakeFitsWorker.instances[0]?.commandsOf('parse')).toHaveLength(1));
    const requestId = FakeFitsWorker.instances[0].commandsOf('parse')[0].requestId;

    act(() => {
      FakeFitsWorker.instances[0].reply({ cmd: 'error', requestId, error: 'bad pixels' });
    });

    await waitFor(() => expect(setStatus).toHaveBeenCalledWith('Error: bad pixels'));
    hook.unmount();
  });
});
