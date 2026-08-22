import { useState, useEffect, useRef } from 'react';

// Type definitions for jsfitsio (ambient)
interface FITS {
    HDUs: HDU[];
}
interface HDU {
    header: Header;
    data: Data;
}
interface Header {
    get(key: string): string | number | undefined;
    cards: Record<string, unknown>;
}
interface Data {
    width: number;
    height: number;
    getFrame(): Float32Array;
}
declare const window: any;

export interface ParsedFitsData {
    w: number;
    h: number;
    channels: number;
    raw: Float32Array;
    min: number;
    max: number;
    crval1?: number;
    crval2?: number;
    cdelt1?: number;
    cdelt2?: number;
    roworder?: string;
}

/**
 * Hook to fetch and parse FITS data from a URL or Blob.
 * Now uses a Web Worker for parsing to avoid blocking the main thread.
 */
export const useFitsLoader = (
    imageUrl: string | null,
    imageBlob: Blob | null,
    setStatus: (s: string | null) => void
) => {
    const [parsedData, setParsedData] = useState<ParsedFitsData | null>(null);
    const [bitmap, setBitmap] = useState<ImageBitmap | null>(null);
    const parsedRef = useRef<ParsedFitsData | null>(null);

    // Caches
    const blobCacheRef = useRef<WeakMap<Blob, ParsedFitsData>>(new WeakMap());
    const urlCacheRef = useRef<Map<string, ParsedFitsData>>(new Map());
    const bitmapBlobCacheRef = useRef<WeakMap<Blob, ImageBitmap>>(new WeakMap());
    const bitmapUrlCacheRef = useRef<Map<string, ImageBitmap>>(new Map());

    useEffect(() => {
        let cancelled = false;
        let worker: Worker | null = null;
        const abortController = new AbortController();

        const load = async () => {
            if (!imageUrl && !imageBlob) {
                setParsedData(null);
                setBitmap(null);
                return;
            }

            // check caches
            if (imageBlob && blobCacheRef.current.has(imageBlob)) {
                setParsedData(blobCacheRef.current.get(imageBlob)!);
                setBitmap(null);
                setStatus(null);
                return;
            }
            if (imageUrl && urlCacheRef.current.has(imageUrl)) {
                setParsedData(urlCacheRef.current.get(imageUrl)!);
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

                // Offload Heavy Parsing to Worker with main-thread fallback
                let workerSpawned = false;
                try {
                    worker = new Worker(new URL('../fitsWorker.ts', import.meta.url), { type: 'module' });
                    workerSpawned = true;
                } catch (e) {
                    console.warn("Failed to spawn Web Worker, falling back to main-thread FITS parsing:", e);
                }

                if (workerSpawned && worker) {
                    worker.onerror = (e) => {
                        console.error("Worker error:", e);
                        if (!cancelled) setStatus("Worker Error");
                    };

                    worker.onmessage = (ev) => {
                        if (cancelled) return;
                        const { cmd, result, error } = ev.data;
                        if (error) {
                            console.error("Worker parse error:", error);
                            setStatus(`Error: ${error}`);
                            return;
                        }
                        if (cmd === 'parseComplete' && result) {
                            result.crval1 = headerInfo['CRVAL1'] !== undefined ? Number(headerInfo['CRVAL1']) : undefined;
                            result.crval2 = headerInfo['CRVAL2'] !== undefined ? Number(headerInfo['CRVAL2']) : undefined;
                            result.cdelt1 = headerInfo['CDELT1'] !== undefined ? Number(headerInfo['CDELT1']) : undefined;
                            result.cdelt2 = headerInfo['CDELT2'] !== undefined ? Number(headerInfo['CDELT2']) : undefined;
                            if (result.cdelt1 === undefined && headerInfo['SCALE'] !== undefined) {
                                result.cdelt1 = -Number(headerInfo['SCALE']) / 3600.0;
                            }
                            if (result.cdelt2 === undefined && headerInfo['SCALE'] !== undefined) {
                                result.cdelt2 = Number(headerInfo['SCALE']) / 3600.0;
                            }
                            result.roworder = headerInfo['ROWORDER'] !== undefined ? String(headerInfo['ROWORDER']).trim() : undefined;
                            // result.raw is the Float32Array
                            if (imageBlob) {
                                blobCacheRef.current.set(imageBlob, result);
                            } else if (imageUrl) {
                                const cache = urlCacheRef.current;
                                if (cache.size >= 5) {
                                    const firstKey = cache.keys().next().value;
                                    if (firstKey) cache.delete(firstKey);
                                }
                                cache.set(imageUrl, result);
                            }

                            parsedRef.current = result;
                            setParsedData(result);
                            setStatus(null);
                        }
                    };

                    // Send data to worker
                    worker.postMessage({
                        cmd: 'parse',
                        pw: naxis1,
                        ph: naxis2,
                        channels: naxis3,
                        rawBuffer: arrayBuffer,
                        bitpix,
                        bzero,
                        bscale,
                        dataOffset: offset
                    }, [arrayBuffer]);
                } else {
                    // Synchronous Main-Thread FITS Parsing Fallback
                    const channels = naxis3 || 1;
                    const bytesPerPixel = Math.abs(bitpix) / 8;
                    const totalElements = naxis1 * naxis2 * channels;
                    const floatData = new Float32Array(totalElements);
                    const dataView = new DataView(arrayBuffer);

                    let min = Infinity;
                    let max = -Infinity;
                    const littleEndian = false; // FITS is Big Endian

                    for (let i = 0; i < totalElements; i++) {
                        const byteOffset = offset + i * bytesPerPixel;
                        if (byteOffset + bytesPerPixel > dataView.byteLength) break;

                        let val = 0;
                        if (bitpix === -32) {
                            val = dataView.getFloat32(byteOffset, littleEndian);
                        } else if (bitpix === -64) {
                            val = dataView.getFloat64(byteOffset, littleEndian);
                        } else if (bitpix === 16) {
                            val = dataView.getInt16(byteOffset, littleEndian);
                        } else if (bitpix === 32) {
                            val = dataView.getInt32(byteOffset, littleEndian);
                        } else if (bitpix === 8) {
                            val = dataView.getUint8(byteOffset);
                        }

                        const physicalVal = (bzero || 0) + (bscale || 1) * val;
                        floatData[i] = physicalVal;

                        if (physicalVal < min) min = physicalVal;
                        if (physicalVal > max) max = physicalVal;
                    }

                    if (min === Infinity) {
                        min = 0;
                        max = 65535;
                    }

                    const result = {
                        w: naxis1,
                        h: naxis2,
                        channels,
                        min,
                        max,
                        raw: floatData,
                        crval1: headerInfo['CRVAL1'] !== undefined ? Number(headerInfo['CRVAL1']) : undefined,
                        crval2: headerInfo['CRVAL2'] !== undefined ? Number(headerInfo['CRVAL2']) : undefined,
                        cdelt1: headerInfo['CDELT1'] !== undefined ? Number(headerInfo['CDELT1']) : (headerInfo['SCALE'] !== undefined ? -Number(headerInfo['SCALE']) / 3600.0 : undefined),
                        cdelt2: headerInfo['CDELT2'] !== undefined ? Number(headerInfo['CDELT2']) : (headerInfo['SCALE'] !== undefined ? Number(headerInfo['SCALE']) / 3600.0 : undefined),
                        roworder: headerInfo['ROWORDER'] !== undefined ? String(headerInfo['ROWORDER']).trim() : undefined,
                    };

                    if (imageBlob) {
                        blobCacheRef.current.set(imageBlob, result);
                    } else if (imageUrl) {
                        const cache = urlCacheRef.current;
                        if (cache.size >= 5) {
                            const firstKey = cache.keys().next().value;
                            if (firstKey) cache.delete(firstKey);
                        }
                        cache.set(imageUrl, result);
                    }

                    if (!cancelled) {
                        parsedRef.current = result;
                        setParsedData(result);
                        setStatus(null);
                    }
                }

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
            if (worker) worker.terminate();
        };
    }, [imageUrl, imageBlob, setStatus]);

    return { parsedData, parsedRef, bitmap };
};
