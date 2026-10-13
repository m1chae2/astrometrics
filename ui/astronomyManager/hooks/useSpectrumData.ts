import { useState, useRef, useEffect, useCallback } from 'react';
import { fetchAstronomyData } from '../../common/services/astronomyService';
import { reportError } from '../../common/utils/reportError';

import { Spectrum, PhotometryResult } from '../../common/types/backendTypes';

/** Represents parsed astronomy data, extending the base Spectrum with processed arrays. */
export interface ParsedAstronomyData extends Spectrum {
    /** Array of wavelength values in Angstroms. */
    wavelength: number[];
    /** Array of intensity/flux values for the spectrum. */
    spectrumFlux: number[];
}

/** Result of the useSpectrumData hook. */
export interface UseSpectrumDataResult {
    /** The parsed astronomy data, or null if not loaded. */
    astronomyData: ParsedAstronomyData | null;
    /** Whether the data is currently loading. */
    loading: boolean;
    /** Error message if fetching or parsing failed. */
    error: string | null;
    /** Replaces the loaded star, and its cached copy, with a newer version from the backend. */
    replaceAstronomyData: (updatedStar: Spectrum) => void;
}

/**
 * Custom hook to fetch and parse astronomy data.
 * @param pendingId The ID of the object to load.
 * @param onLoaded Optional callback triggered when loading is complete.
 */
export function useSpectrumData(
    pendingId: string,
    onLoaded?: (id: string) => void
): UseSpectrumDataResult {
    const [astronomyData, setAstronomyData] = useState<ParsedAstronomyData | null>(null);
    const [loading, setLoading] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);

    const cacheRef = useRef<Map<string, ParsedAstronomyData>>(new Map());
    const onLoadedRef = useRef(onLoaded);
    // The id the currently shown star was requested under. `pendingId` is
    // cleared once a star loads, so this is what its cached copy is keyed by.
    const loadedKeyRef = useRef<string>('');

    useEffect(() => {
        onLoadedRef.current = onLoaded;
    }, [onLoaded]);

    useEffect(() => {
        if (!pendingId) return;

        let cancelled = false;
        const controller = new AbortController();

        // Check cache for existing data.
        const cached = cacheRef.current.get(pendingId);
        if (cached) {
            loadedKeyRef.current = pendingId;
            setAstronomyData(cached);
            onLoadedRef.current?.(pendingId);
            return;
        }

        setLoading(true);
        setError(null);
        setAstronomyData(null);

        (async () => {
            try {
                const fetched = (await fetchAstronomyData(pendingId, controller.signal)) as Spectrum | null;
                if (cancelled || !fetched) return;

                // REQ: BKD-7.1: Use canonical plotData from backend to eliminate client-side guessing
                const plotData = fetched.plotData || { wavelengths: [], intensities: [] };

                const result: ParsedAstronomyData = {
                    ...fetched,
                    wavelength: plotData.wavelengths,
                    spectrumFlux: plotData.intensities
                };

                cacheRef.current.set(pendingId, result);
                if (!cancelled) {
                    loadedKeyRef.current = pendingId;
                    setAstronomyData(result);
                    onLoadedRef.current?.(pendingId);
                }
            } catch (err: unknown) {
                if (!cancelled) {
                    const msg = err instanceof Error ? err.message : String(err);
                    setError(msg || 'Failed to fetch astronomy data');
                    // Always report errors from this hook so they can be surfaced via the global handler.
                    reportError(err instanceof Error ? err : new Error(msg), 'astronomy-hook');
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();

        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [pendingId]);

    const replaceAstronomyData = useCallback((updatedStar: Spectrum) => {
        const plotData = updatedStar.plotData || { wavelengths: [], intensities: [] };
        const replacement: ParsedAstronomyData = {
            ...updatedStar,
            wavelength: plotData.wavelengths,
            spectrumFlux: plotData.intensities,
        };
        if (loadedKeyRef.current) cacheRef.current.set(loadedKeyRef.current, replacement);
        setAstronomyData(replacement);
    }, []);

    return { astronomyData, loading, error, replaceAstronomyData };
}
