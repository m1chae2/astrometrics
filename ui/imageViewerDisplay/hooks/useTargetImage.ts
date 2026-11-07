import { useState, useRef, useEffect, useSyncExternalStore } from 'react';
import { fetchProcessedImage } from '../../common/services/imaging/imageService';
import { reportError } from '../../common/utils/reportError';
import {
    cacheTargetImage,
    cacheTargetPreview,
    getCachedTargetPreview,
    getCachedTargetImage,
    getTargetImageCacheVersion,
    setShownTargetImage,
    subscribeToTargetImageCache,
} from './targetImageCache';

/** Result of the useTargetImage hook. */
export interface UseTargetImageResult {
    /** Object URL of the processed image. */
    imageUrl: string | null;
    /** Raw Blob of the processed image data. */
    imageBlob: Blob | null;
    /**
     * Small JPEG preview of a stretched FITS picture, available before the picture itself (at once when kept from an earlier visit or a prefetch).
     * Null when there is none. It stays until the target changes; the viewer stops drawing it once the picture is ready.
     */
    previewBlob: Blob | null;
    /** Whether the image is currently being fetched. */
    loading: boolean;
    /** Error message if image fetching failed. */
    error: string | null;
}

/**
 * Custom hook to fetch and cache processed target images.
 * @param targetId The ID of the target to load an image for.
 * @param onImageLoaded Optional callback triggered when the image is successfully loaded.
 */
export function useTargetImage(
    targetId: string,
    onImageLoaded?: (target: string) => void
): UseTargetImageResult {
    const [imageUrl, setImageUrl] = useState<string | null>(null);
    const [imageBlob, setImageBlob] = useState<Blob | null>(null);
    const [previewBlob, setPreviewBlob] = useState<Blob | null>(null);
    // Seeded from whether a target is already selected at mount (e.g.
    // restored from localStorage) rather than always starting false. The app
    // boot readiness check (see ImageViewerDisplay.tsx, ui/common/utils/
    // appBootReadiness.ts) gates the splash screen on `!loading`; if this
    // started false unconditionally, that check would read "ready" on the
    // very first render, before the fetch effect below even runs, and the
    // splash would dismiss before the real image fetch (which can take
    // several seconds) has started.
    const [loading, setLoading] = useState<boolean>(() => Boolean(targetId));
    const [error, setError] = useState<string | null>(null);

    const fetchController = useRef<AbortController | null>(null);
    // The pictures are kept outside this component (see targetImageCache.ts),
    // so they survive the display being paused while hidden. The version goes
    // up when the library changed and the pictures must be read again.
    const cacheVersion = useSyncExternalStore(subscribeToTargetImageCache, getTargetImageCacheVersion);

    const onLoadedRef = useRef(onImageLoaded);
    useEffect(() => {
        onLoadedRef.current = onImageLoaded;
    }, [onImageLoaded]);

    // Fetch the image whenever targetId changes or cache is invalidated.
    useEffect(() => {
        if (!targetId) return;

        // The picture of this target is in use: the cache must not free it.
        setShownTargetImage(targetId);

        // Abort any outstanding fetch.
        try {
            fetchController.current?.abort();
        } catch {
            // Ignore.
        }
        const controller = new AbortController();
        fetchController.current = controller;

        setLoading(true);
        setError(null);
        setImageUrl(null);
        setImageBlob(null);
        // A kept preview is shown at once, whether or not the full picture is kept.
        setPreviewBlob(getCachedTargetPreview(targetId) ?? null);

        // Prefer session cache if available.
        const cached = getCachedTargetImage(targetId);
        if (cached && cached.objectUrl) {
            setImageUrl(cached.objectUrl);
            setImageBlob(cached.blob);
            setLoading(false);
            onLoadedRef.current?.(targetId);
            return;
        }

        // The preview stays until the target changes; the viewer stops drawing it once the full picture is ready.
        fetchProcessedImage(targetId, controller.signal, (preview) => {
            cacheTargetPreview(targetId, preview);
            if (!controller.signal.aborted) setPreviewBlob(preview);
        })
            .then((blob) => {
                if (!blob) {
                    // No processed image for this target — clear any stale state.
                    if (!controller.signal.aborted) {
                        setImageUrl(null);
                        setImageBlob(null);
                    }
                    onLoadedRef.current?.(targetId);
                    return;
                }
                if (blob.size === 0) {
                    throw new Error('Received empty image data');
                }
                if (!controller.signal.aborted) {
                    const url = cacheTargetImage(targetId, blob).objectUrl;
                    setImageUrl(url);
                    setImageBlob(blob);
                    onLoadedRef.current?.(targetId);
                }
            })
            .catch((err: unknown) => {
                if (err instanceof Error && err.name === 'AbortError') return;
                // Error already reported by backendApi
                setError('Failed to fetch processed image.');
            })
            .finally(() => {
                if (!controller.signal.aborted) {
                    setLoading(false);
                }
                if (fetchController.current === controller) {
                    fetchController.current = null;
                }
            });

        return () => {
            try {
                controller.abort();
            } catch {
                // Ignore.
            }
        };
    }, [targetId, cacheVersion]);

    return { imageUrl, imageBlob, previewBlob, loading, error };
}
