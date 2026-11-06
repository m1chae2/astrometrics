import { useState, useEffect, useRef, useCallback } from 'react';
import { ParsedFitsCache, PARSED_FITS_CACHE_LIMIT } from '../parsedFitsCache';
import { fetchImageFile } from '../../services/backendApi';

/**
 * What the main thread knows about a decoded FITS image.
 *
 * The pixels themselves are not here: they stay inside the FITS worker (see
 * `fitsWorker.ts`), filed under `imageId`, so they are never copied between threads.
 */
export interface ParsedFitsData {
    /** Id the worker filed the decoded pixels under; pass it back to the worker to draw or release them. */
    imageId: number;
    w: number;
    h: number;
    channels: number;
    min: number;
    max: number;
    crval1?: number;
    crval2?: number;
    cdelt1?: number;
    cdelt2?: number;
    roworder?: string;
}

/** Source of fresh ids for decoded images, unique across every viewer on the page. */
let nextImageId = 1;

/**
 * Where a viewer is in loading its picture. Every phase except `ready` and
 * `error` means "still working", so the viewer shows its spinner.
 */
export type FitsLoadPhase =
    | 'idle' // nothing to load
    | 'fetching' // reading the file from the backend, or finding it in a cache
    | 'parsing' // the FITS worker is decoding the file
    | 'decoding' // a plain picture (JPEG or PNG) is being decoded
    | 'ready' // the picture is decoded and can be drawn
    | 'error'; // loading failed; see `errorMessage`

/** A decode that has started in the FITS worker. */
export interface FitsDecode {
    /** Settles with the decoded image's record, or rejects if the decode fails or is cancelled. */
    promise: Promise<ParsedFitsData>;
    /** Stops the decode and frees the worker's copy of the pixels. Safe to call after it finished. */
    cancel: () => void;
}

/**
 * Starts decoding a FITS file in the worker.
 *
 * Reads the header on the main thread (it is only a few blocks of 2880 bytes),
 * then hands the whole file to the worker, which keeps the pixels.
 *
 * @param blob The FITS file.
 * @param getWorker Returns the worker to use, creating it if needed; null if it cannot be made.
 * @returns The pending decode and a way to cancel it.
 */
export function decodeFitsBlob(blob: Blob, getWorker: () => Worker | null): FitsDecode {
    let cancelled = false;
    let finished = false;
    // Id of a parse still in flight; if the decode is cancelled first, the worker must drop the result.
    let pendingImageId: number | null = null;
    let activeWorker: Worker | null = null;
    let removeMessageListener: (() => void) | null = null;

    let rejectOuter: ((reason: unknown) => void) | null = null;
    const work = (async (): Promise<ParsedFitsData> => {
        const arrayBuffer = await blob.arrayBuffer();
        if (cancelled) throw new DOMException('Decode cancelled', 'AbortError');
        const uint8data = new Uint8Array(arrayBuffer);

        // Quick header parse (main thread; fast enough for 2880 bytes).
        const textDecoder = new TextDecoder('ascii');
        const headerInfo: Record<string, string | number> = {};
        let headerEnd = false;
        let offset = 0;

        while (!headerEnd && (offset + 2880) <= uint8data.length) {
            const block = uint8data.slice(offset, offset + 2880);
            const blockText = textDecoder.decode(block);

            for (let i = 0; i < 2880; i += 80) {
                const line = blockText.slice(i, i + 80);
                if (line.length < 80) break;
                const key = line.slice(0, 8).trim().toUpperCase();
                if (key === 'END') {
                    headerEnd = true;
                    break;
                }
                if (line[8] === '=') {
                    const valPart = line.slice(9).split('/')[0].trim();
                    if (valPart.startsWith("'")) {
                        const match = valPart.match(/'(.*?)'/);
                        headerInfo[key] = match ? match[1].trim() : valPart.replace(/'/g, '').trim();
                    } else {
                        const num = parseFloat(valPart);
                        headerInfo[key] = isNaN(num) ? valPart : num;
                    }
                }
            }
            offset += 2880;
        }

        if (!headerEnd && headerInfo['BITPIX'] === undefined) {
            throw new Error("FITS END marker not found");
        }

        const bitpix = Number(headerInfo['BITPIX']);
        const naxis = Number(headerInfo['NAXIS'] || 0);
        const naxis1 = Number(headerInfo['NAXIS1'] || 0);
        const naxis2 = Number(headerInfo['NAXIS2'] || 0);
        const naxis3 = naxis >= 3 ? Number(headerInfo['NAXIS3'] || 1) : 1;
        const bzero = Number(headerInfo['BZERO'] || 0);
        const bscale = Number(headerInfo['BSCALE'] || 1);

        const worker = getWorker();
        if (!worker) throw new Error("Worker Error");
        const imageId = nextImageId++;
        pendingImageId = imageId;
        activeWorker = worker;

        return new Promise<ParsedFitsData>((resolve, reject) => {
            worker.onerror = (e) => {
                console.error("Worker error:", e);
                reject(new Error("Worker Error"));
            };

            const handleMessage = (ev: MessageEvent) => {
                if (ev.data.requestId !== imageId) return;
                worker.removeEventListener('message', handleMessage);
                if (cancelled) return;
                const { cmd, result, error } = ev.data;
                if (error) {
                    console.error("Worker parse error:", error);
                    reject(new Error(`Error: ${error}`));
                    return;
                }
                if (cmd === 'parseComplete' && result) {
                    const parsed: ParsedFitsData = {
                        imageId,
                        w: result.w,
                        h: result.h,
                        channels: result.channels,
                        min: result.min,
                        max: result.max,
                        crval1: headerInfo['CRVAL1'] !== undefined ? Number(headerInfo['CRVAL1']) : undefined,
                        crval2: headerInfo['CRVAL2'] !== undefined ? Number(headerInfo['CRVAL2']) : undefined,
                        cdelt1: headerInfo['CDELT1'] !== undefined ? Number(headerInfo['CDELT1']) : undefined,
                        cdelt2: headerInfo['CDELT2'] !== undefined ? Number(headerInfo['CDELT2']) : undefined,
                        roworder: headerInfo['ROWORDER'] !== undefined ? String(headerInfo['ROWORDER']).trim() : undefined,
                    };
                    if (parsed.cdelt1 === undefined && headerInfo['SCALE'] !== undefined) {
                        parsed.cdelt1 = -Number(headerInfo['SCALE']) / 3600.0;
                    }
                    if (parsed.cdelt2 === undefined && headerInfo['SCALE'] !== undefined) {
                        parsed.cdelt2 = Number(headerInfo['SCALE']) / 3600.0;
                    }
                    pendingImageId = null;
                    finished = true;
                    resolve(parsed);
                }
            };
            worker.addEventListener('message', handleMessage);
            removeMessageListener = () => worker.removeEventListener('message', handleMessage);

            worker.postMessage({
                cmd: 'parse',
                requestId: imageId,
                imageId,
                pw: naxis1,
                ph: naxis2,
                channels: naxis3,
                roworder: headerInfo['ROWORDER'] !== undefined ? String(headerInfo['ROWORDER']).trim() : undefined,
                rawBuffer: arrayBuffer,
                bitpix,
                bzero,
                bscale,
                dataOffset: offset
            }, [arrayBuffer]);
        });
    })();
    const promise = new Promise<ParsedFitsData>((resolve, reject) => {
        rejectOuter = reject;
        work.then(resolve, reject);
    });

    const cancel = () => {
        if (cancelled || finished) return;
        cancelled = true;
        removeMessageListener?.();
        if (activeWorker && pendingImageId !== null) {
            activeWorker.postMessage({ cmd: 'release', imageId: pendingImageId });
        }
        rejectOuter?.(new DOMException('Decode cancelled', 'AbortError'));
    };
    return { promise, cancel };
}

/** Default status listener: the viewer reads `phase` instead. A constant so effects do not rerun. */
const IGNORE_STATUS = (): void => undefined;

/** What the viewer showing a target's picture offers to the neighbour prefetch. */
type PrefetchHandler = (blob: Blob, signal: AbortSignal) => Promise<void>;

/** The one viewer that has said it will keep prefetched pictures, if any. */
let prefetchHandler: PrefetchHandler | null = null;

/**
 * Decodes a picture into the viewer that accepts prefetching, so it is ready
 * before the user selects it. Does nothing when no viewer accepts, the picture
 * is already decoded, or the signal is aborted first.
 *
 * @param blob The picture (a FITS file or a JPEG or PNG).
 * @param signal Aborted when the prefetch is no longer wanted.
 */
export async function prefetchDecode(blob: Blob, signal: AbortSignal): Promise<void> {
    if (!prefetchHandler || signal.aborted) return;
    await prefetchHandler(blob, signal);
}

/**
 * Tells whether a blob is a plain picture rather than a FITS file.
 *
 * @param blob The data.
 * @returns True for image/* types such as JPEG and PNG.
 */
function isPictureBlob(blob: Blob): boolean {
    return Boolean(blob.type?.startsWith('image/'));
}

/**
 * Closes a bitmap, ignoring the error when it is already closed.
 *
 * @param bitmap The bitmap to free.
 */
const closeBitmapQuietly = (bitmap: ImageBitmap): void => {
    try {
        bitmap.close();
    } catch {
        // Already closed.
    }
};

/**
 * Hook to fetch and parse FITS data from a URL or Blob.
 *
 * Decoding happens in one long-lived Web Worker per viewer, which also keeps the
 * decoded pixels and draws them (see `useCanvasDrawer`). The worker is returned
 * so the drawer can talk to the same one.
 *
 * Besides the decoded data, it reports an explicit `phase` and the `loadedKey`
 * (the blob or URL that `parsedData` / `bitmap` belong to), so the viewer never
 * has to read the words of a status message.
 *
 * With `acceptPrefetch`, this viewer also takes pictures decoded ahead of time
 * through `prefetchDecode`.
 */
export const useFitsLoader = (
    imageUrl: string | null,
    imageBlob: Blob | null,
    setStatus: (s: string | null) => void = IGNORE_STATUS,
    acceptPrefetch: boolean = false
) => {
    const [parsedData, setParsedData] = useState<ParsedFitsData | null>(null);
    const [bitmap, setBitmap] = useState<ImageBitmap | null>(null);
    const [phase, setPhase] = useState<FitsLoadPhase>('idle');
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    // The blob or URL that parsedData / bitmap were made from.
    const [loadedKey, setLoadedKey] = useState<string | Blob | null>(null);
    const parsedRef = useRef<ParsedFitsData | null>(null);
    const workerRef = useRef<Worker | null>(null);

    // Caches
    const parsedCacheRef = useRef<ParsedFitsCache<ParsedFitsData> | null>(null);
    const bitmapBlobCacheRef = useRef<WeakMap<Blob, ImageBitmap>>(new WeakMap());
    const bitmapUrlCacheRef = useRef<Map<string, ImageBitmap>>(new Map());
    // The bitmap on screen is never closed; one evicted while shown waits here until it is replaced.
    const displayedBitmapRef = useRef<ImageBitmap | null>(null);
    const evictedBitmapsRef = useRef<Set<ImageBitmap>>(new Set());

    // Close evicted bitmaps as soon as they stop being the one on screen.
    useEffect(() => {
        displayedBitmapRef.current = bitmap;
        for (const evicted of evictedBitmapsRef.current) {
            if (evicted !== bitmap) {
                closeBitmapQuietly(evicted);
                evictedBitmapsRef.current.delete(evicted);
            }
        }
    }, [bitmap]);

    /** Returns the worker, starting it the first time. */
    const getWorker = useCallback((): Worker | null => {
        if (!workerRef.current) {
            try {
                workerRef.current = new Worker(new URL('../fitsWorker.ts', import.meta.url), { type: 'module' });
            } catch (e) {
                console.error("Failed to spawn the FITS Web Worker:", e);
                return null;
            }
        }
        return workerRef.current;
    }, []);

    // Stop the worker (and with it every decoded image) when the viewer goes away.
    useEffect(() => {
        // Created here, not during render: a record that falls out of the cache
        // must also free its pixels inside the worker.
        parsedCacheRef.current = new ParsedFitsCache<ParsedFitsData>(PARSED_FITS_CACHE_LIMIT, (dropped) => {
            workerRef.current?.postMessage({ cmd: 'release', imageId: dropped.imageId });
        });
        return () => {
            parsedCacheRef.current?.clear();
            parsedCacheRef.current = null;
            workerRef.current?.terminate();
            workerRef.current = null;
        };
    }, []);

    // Take part in the neighbour prefetch, if asked to.
    useEffect(() => {
        if (!acceptPrefetch) return undefined;
        const handler: PrefetchHandler = async (blob, signal) => {
            if (isPictureBlob(blob)) {
                if (bitmapBlobCacheRef.current.has(blob)) return;
                const pictureBitmap = await createImageBitmap(blob);
                if (signal.aborted) {
                    pictureBitmap.close();
                    return;
                }
                bitmapBlobCacheRef.current.set(blob, pictureBitmap);
                return;
            }
            const cache = parsedCacheRef.current;
            if (!cache || cache.has(blob)) return;
            const decode = decodeFitsBlob(blob, getWorker);
            const onAbort = () => decode.cancel();
            signal.addEventListener('abort', onAbort);
            try {
                const parsed = await decode.promise;
                if (signal.aborted) return;
                parsedCacheRef.current?.set(blob, parsed);
            } catch {
                // A cancelled or failed prefetch is not an error; the picture loads normally when selected.
            } finally {
                signal.removeEventListener('abort', onAbort);
            }
        };
        prefetchHandler = handler;
        return () => {
            if (prefetchHandler === handler) prefetchHandler = null;
        };
    }, [acceptPrefetch, getWorker]);

    useEffect(() => {
        let cancelled = false;
        let currentDecode: FitsDecode | null = null;
        const abortController = new AbortController();

        /** Records a failure for the viewer to show. */
        const fail = (message: string) => {
            setErrorMessage(message);
            setPhase('error');
            setStatus(message);
        };

        const load = async () => {
            if (!imageUrl && !imageBlob) {
                setParsedData(null);
                setBitmap(null);
                setLoadedKey(null);
                setErrorMessage(null);
                setPhase('idle');
                return;
            }

            // check caches
            const parsedKey = imageBlob ?? imageUrl;
            const cachedParsed = parsedKey ? parsedCacheRef.current?.get(parsedKey) : undefined;
            if (cachedParsed) {
                parsedRef.current = cachedParsed;
                setParsedData(cachedParsed);
                setBitmap(null);
                setLoadedKey(parsedKey);
                setErrorMessage(null);
                setPhase('ready');
                setStatus(null);
                return;
            }
            if (imageBlob && bitmapBlobCacheRef.current.has(imageBlob)) {
                setBitmap(bitmapBlobCacheRef.current.get(imageBlob)!);
                setParsedData(null);
                setLoadedKey(parsedKey);
                setErrorMessage(null);
                setPhase('ready');
                setStatus(null);
                return;
            }
            if (imageUrl && bitmapUrlCacheRef.current.has(imageUrl)) {
                setBitmap(bitmapUrlCacheRef.current.get(imageUrl)!);
                setParsedData(null);
                setLoadedKey(parsedKey);
                setErrorMessage(null);
                setPhase('ready');
                setStatus(null);
                return;
            }

            // Immediately clear stale state for non-cached loads to prevent "flashing" the old target's image
            setParsedData(null);
            setBitmap(null);
            setLoadedKey(null);
            setErrorMessage(null);
            setPhase('fetching');
            setStatus('Loading...');

            try {
                // 1. Check for standard image types (PNG/JPG) first
                let isStandardImage = false;
                if (imageBlob && imageBlob.type?.startsWith('image/')) isStandardImage = true;
                if (imageUrl && (imageUrl.startsWith('data:image/') || /\.(png|jpe?g|bmp|gif|webp)$/i.test(imageUrl))) {
                    isStandardImage = true;
                }

                if (isStandardImage) {
                    let blob: Blob;
                    if (imageBlob) {
                        blob = imageBlob;
                    } else if (imageUrl?.startsWith('data:')) {
                        const arr = imageUrl.split(',');
                        const mime = arr[0].match(/:(.*?);/)?.[1] ?? 'image/png';
                        const bstr = atob(arr[1]);
                        let n = bstr.length;
                        const u8arr = new Uint8Array(n);
                        while (n--) u8arr[n] = bstr.charCodeAt(n);
                        blob = new Blob([u8arr], { type: mime });
                    } else {
                        const resp = await fetchImageFile(imageUrl!, abortController.signal);
                        blob = await resp.blob();
                    }

                    if (!cancelled) setPhase('decoding');
                    const imgBitmap = await createImageBitmap(blob);

                    if (imageBlob) {
                        bitmapBlobCacheRef.current.set(imageBlob, imgBitmap);
                    } else if (imageUrl) {
                        const cache = bitmapUrlCacheRef.current;
                        if (cache.size >= 5) {
                            const firstKey = cache.keys().next().value;
                            if (firstKey) {
                                const oldBitmap = cache.get(firstKey);
                                if (oldBitmap) {
                                    if (oldBitmap === displayedBitmapRef.current) {
                                        // Still on screen: close it later, when it is replaced.
                                        evictedBitmapsRef.current.add(oldBitmap);
                                    } else {
                                        closeBitmapQuietly(oldBitmap); // Free VRAM
                                    }
                                }
                                cache.delete(firstKey);
                            }
                        }
                        cache.set(imageUrl, imgBitmap);
                    }

                    if (!cancelled) {
                        setBitmap(imgBitmap);
                        setParsedData(null);
                        setLoadedKey(parsedKey);
                        setPhase('ready');
                        setStatus(null);
                    }
                    return;
                }

                // 2. FITS Parsing
                // Fetch Data if not provided as blob
                let blob = imageBlob;
                if (!blob && imageUrl) {
                    const resp = await fetchImageFile(imageUrl, abortController.signal);
                    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                    blob = await resp.blob();
                }

                if (!blob) throw new Error("No data");
                if (cancelled) return;

                setPhase('parsing');
                setStatus('Parsing FITS...');
                currentDecode = decodeFitsBlob(blob, getWorker);
                const parsed = await currentDecode.promise;
                if (cancelled) return;

                if (parsedKey) parsedCacheRef.current?.set(parsedKey, parsed);
                parsedRef.current = parsed;
                setParsedData(parsed);
                setLoadedKey(parsedKey);
                setPhase('ready');
                setStatus(null);
            } catch (err) {
                if (!cancelled) {
                    console.error("FITS Load Error:", err);
                    fail(err instanceof Error ? err.message : String(err));
                }
            }
        };

        load();

        return () => {
            cancelled = true;
            abortController.abort();
            currentDecode?.cancel();
        };
    }, [imageUrl, imageBlob, setStatus, getWorker]);

    return { parsedData, parsedRef, bitmap, workerRef, phase, errorMessage, loadedKey };
};
