import { useState, useEffect, useRef } from 'react';
import { ParsedFitsCache, PARSED_FITS_CACHE_LIMIT } from '../parsedFitsCache';

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
 * Hook to fetch and parse FITS data from a URL or Blob.
 *
 * Decoding happens in one long-lived Web Worker per viewer, which also keeps the
 * decoded pixels and draws them (see `useCanvasDrawer`). The worker is returned
 * so the drawer can talk to the same one.
 */
export const useFitsLoader = (
    imageUrl: string | null,
    imageBlob: Blob | null,
    setStatus: (s: string | null) => void
) => {
    const [parsedData, setParsedData] = useState<ParsedFitsData | null>(null);
    const [bitmap, setBitmap] = useState<ImageBitmap | null>(null);
    const parsedRef = useRef<ParsedFitsData | null>(null);
    const workerRef = useRef<Worker | null>(null);

    // Caches
    const parsedCacheRef = useRef<ParsedFitsCache<ParsedFitsData> | null>(null);
    const bitmapBlobCacheRef = useRef<WeakMap<Blob, ImageBitmap>>(new WeakMap());
    const bitmapUrlCacheRef = useRef<Map<string, ImageBitmap>>(new Map());

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

    useEffect(() => {
        let cancelled = false;
        let activeWorker: Worker | null = null;
        // Id of a parse still in flight; if this load is cancelled first, the worker must drop the result.
        let pendingImageId: number | null = null;
        let removeMessageListener: (() => void) | null = null;
        const abortController = new AbortController();

        const load = async () => {
            if (!imageUrl && !imageBlob) {
                setParsedData(null);
                setBitmap(null);
                return;
            }

            // check caches
            const parsedKey = imageBlob ?? imageUrl;
            const cachedParsed = parsedKey ? parsedCacheRef.current?.get(parsedKey) : undefined;
            if (cachedParsed) {
                parsedRef.current = cachedParsed;
                setParsedData(cachedParsed);
                setBitmap(null);
                setStatus(null);
                return;
            }
            if (imageBlob && bitmapBlobCacheRef.current.has(imageBlob)) {
                setBitmap(bitmapBlobCacheRef.current.get(imageBlob)!);
                setParsedData(null);
                setStatus(null);
                return;
            }
            if (imageUrl && bitmapUrlCacheRef.current.has(imageUrl)) {
                setBitmap(bitmapUrlCacheRef.current.get(imageUrl)!);
                setParsedData(null);
                setStatus(null);
                return;
            }

            // Immediately clear stale state for non-cached loads to prevent "flashing" the old target's image
            setParsedData(null);
            setBitmap(null);
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
                        const resp = await fetch(imageUrl!, { signal: abortController.signal });
                        blob = await resp.blob();
                    }

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
                                    try {
                                        oldBitmap.close(); // Free VRAM
                                    } catch {
                                        // Ignore errors closing old bitmap
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
                        setStatus(null);
                    }
                    return;
                }

                // 2. FITS Parsing
                setStatus('Parsing FITS...');

                // Fetch Data if not provided as blob
                let blob = imageBlob;
                if (!blob && imageUrl) {
                    const resp = await fetch(imageUrl, { signal: abortController.signal });
                    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                    blob = await resp.blob();
                }

                if (!blob) throw new Error("No data");

                const arrayBuffer = await blob.arrayBuffer();
                const uint8data = new Uint8Array(arrayBuffer);

                // Quick Header Parse (Main Thread - Fast enough for 2880 bytes)
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

                // Decode in the worker, which keeps the pixels for drawing.
                if (!workerRef.current) {
                    try {
                        workerRef.current = new Worker(new URL('../fitsWorker.ts', import.meta.url), { type: 'module' });
                    } catch (e) {
                        console.error("Failed to spawn the FITS Web Worker:", e);
                        if (!cancelled) setStatus("Worker Error");
                        return;
                    }
                }
                const worker = workerRef.current;
                const imageId = nextImageId++;
                pendingImageId = imageId;
                activeWorker = worker;

                worker.onerror = (e) => {
                    console.error("Worker error:", e);
                    if (!cancelled) setStatus("Worker Error");
                };

                const handleMessage = (ev: MessageEvent) => {
                    if (ev.data.requestId !== imageId) return;
                    worker.removeEventListener('message', handleMessage);
                    if (cancelled) return;
                    const { cmd, result, error } = ev.data;
                    if (error) {
                        console.error("Worker parse error:", error);
                        setStatus(`Error: ${error}`);
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

                        const parsedKey = imageBlob ?? imageUrl;
                        if (parsedKey) parsedCacheRef.current?.set(parsedKey, parsed);
                        pendingImageId = null;

                        parsedRef.current = parsed;
                        setParsedData(parsed);
                        setStatus(null);
                    }
                };
                worker.addEventListener('message', handleMessage);
                removeMessageListener = () => worker.removeEventListener('message', handleMessage);

                // Send data to worker
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

            } catch (err) {
                if (!cancelled) {
                    console.error("FITS Load Error:", err);
                    setStatus(err instanceof Error ? err.message : String(err));
                }
            }
        };

        load();

        return () => {
            cancelled = true;
            abortController.abort();
            removeMessageListener?.();
            if (activeWorker && pendingImageId !== null) {
                activeWorker.postMessage({ cmd: 'release', imageId: pendingImageId });
            }
        };
    }, [imageUrl, imageBlob, setStatus]);

    return { parsedData, parsedRef, bitmap, workerRef };
};
