import { useState, useRef, useEffect } from 'react';
import { fetchProcessedImage } from '../../common/services/imagingService';
import { reportError } from '../../common/utils/reportError';
import { on as onEvent } from '../../common/utils/eventBus';

/** Result of the useTargetImage hook. */
export interface UseTargetImageResult {
    /** Object URL of the processed image. */
    imageUrl: string | null;
    /** Raw Blob of the processed image data. */
    imageBlob: Blob | null;
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
    const [loading, setLoading] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);
    const [refreshCount, setRefreshCount] = useState<number>(0);

    const fetchController = useRef<AbortController | null>(null);
    const cacheRef = useRef<Map<string, { objectUrl: string; blob: Blob }>>(
        new Map()
    );

    const onLoadedRef = useRef(onImageLoaded);
    useEffect(() => {
        onLoadedRef.current = onImageLoaded;
    }, [onImageLoaded]);

    // Setup global cache invalidation listener.
    useEffect(() => {
        // Captured here rather than read in the cleanup: the ref's contents are
        // mutated but the Map itself is never reassigned, so this is the same
        // object either way, and holding it directly is what lets the cleanup
        // revoke the object URLs it actually created.
        const cacheAtMount = cacheRef.current;

        const detach = onEvent('targetsUpdated', () => {
            try {
                const cache = cacheRef.current;
                cache.forEach((entry) => {
                    try {
                        URL.revokeObjectURL(entry.objectUrl);
                    } catch {
                        // Ignore revocation failures.
                    }
                });
                cache.clear();
                // Trigger re-fetch
                setRefreshCount((prev) => prev + 1);
            } catch {
                // Ignore cache clear failures.
            }
        });
        return () => {
            try {
                detach();
            } catch {
                // Ignore.
            }
            try {
                cacheAtMount.forEach((entry) => {
                    try {
                        URL.revokeObjectURL(entry.objectUrl);
                    } catch {
                        // Ignore errors revoking object URL
                    }
                });
                cacheAtMount.clear();
            } catch {
                // Ignore errors clearing cache
            }
        };
    }, []);

    // Fetch the image whenever targetId changes or cache is invalidated.
    useEffect(() => {
        if (!targetId) return;

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

        // Prefer session cache if available.
        const cached = cacheRef.current.get(targetId);
        if (cached && cached.objectUrl) {
            setImageUrl(cached.objectUrl);
            setImageBlob(cached.blob);
            setLoading(false);
            onLoadedRef.current?.(targetId);
            return;
        }

        fetchProcessedImage(targetId, controller.signal)
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
                const url = URL.createObjectURL(blob);
                try {
                    cacheRef.current.set(targetId, { objectUrl: url, blob });
                } catch {
                    // Ignore cache set failures.
                }

                if (!controller.signal.aborted) {
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
    }, [targetId, refreshCount]);

    return { imageUrl, imageBlob, loading, error };
}
