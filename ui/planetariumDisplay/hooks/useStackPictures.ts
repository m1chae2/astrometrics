/**
 * @module useStackPictures
 * @fileoverview The Planetarium's Images layer: each target's processed stack
 * picture, laid on the sky. This hook keeps the layer's switch and opacity
 * (remembered for this viewer), asks the backend where each picture lies on
 * the sky, and loads only the pictures the sky map asks for (the ones in
 * view), each as a smaller copy unless the view is zoomed in on it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend, loadStaticMounts, resolveImageSrc } from '../../common/services/backendApi';
import { usePersistentFlag, usePersistentNumber } from '../../common/hooks/usePersistentFlag';
import { useIsDisplayActive } from '../../common/context/DisplayActiveContext';
import type { StackSkyPicture } from '../../common/types/planetariumTypes';

/** Longest side, in pixels, of the smaller copy drawn at a wide view. */
export const OVERVIEW_PICTURE_SIZE_PX = 1024;

/** Most full-size pictures kept at once; each is several tens of megabytes decoded. */
const MAXIMUM_FULL_PICTURES = 4;

/** How often a failed picture list is asked for again, in milliseconds (a minute). */
const PICTURE_LIST_RETRY_INTERVAL_MS = 60000;

/** Default opacity of the pictures, from 0 to 1. */
export const DEFAULT_PICTURE_OPACITY = 0.8;

/** Lowest opacity the slider can set, so the pictures never vanish completely. */
const MINIMUM_PICTURE_OPACITY = 0.05;

const OPACITY_STORAGE_KEY = 'astrometrics:planetarium:imagesOpacity';

/** Something the sky map can draw a picture from. */
export type PictureDrawable = HTMLImageElement | HTMLCanvasElement | ImageBitmap;

/** What the sky map needs to draw the Images layer. */
export interface StackPictureLayer {
  /** Whether the Images layer is on. */
  showImages: boolean;
  setShowImages: (value: boolean) => void;
  /** The pictures' opacity, from 0 to 1. */
  opacity: number;
  setOpacity: (value: number) => void;
  /** The placed pictures; empty while the layer is off or the list is not loaded. */
  pictures: StackSkyPicture[];
  /**
   * The loaded copy of a picture to draw, or null while it loads. Asking starts the load.
   * `full` asks for the full-size picture (a zoomed-in view); otherwise the smaller copy.
   */
  getDrawable: (picture: StackSkyPicture, full: boolean) => PictureDrawable | null;
  /** Goes up by one each time a picture finishes loading, so the sky map draws again. */
  loadedVersion: number;
}

/**
 * Loads a picture file as an image.
 *
 * @param {string} url The picture's address.
 * @returns {Promise<HTMLImageElement>} The decoded image.
 */
function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Could not load the picture ${url}`));
    image.src = url;
  });
}

/**
 * Makes the smaller copy of a picture, so a wide view does not keep every full picture.
 *
 * @param {HTMLImageElement} image The full picture.
 * @returns {PictureDrawable} A copy whose longest side is at most OVERVIEW_PICTURE_SIZE_PX.
 */
function overviewCopy(image: HTMLImageElement): PictureDrawable {
  const scale = Math.min(1, OVERVIEW_PICTURE_SIZE_PX / Math.max(image.naturalWidth, image.naturalHeight));
  if (scale >= 1) return image;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  canvas.getContext('2d')?.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas;
}

/**
 * Runs the Images layer of the Planetarium.
 *
 * The picture list is asked for only while the layer is on, and quietly: a
 * failure (such as a backend that does not have the request yet) shows no
 * error; the layer simply has no pictures and asks again a minute later.
 *
 * @func useStackPictures
 * @returns {StackPictureLayer} The switch, the opacity, the pictures and their loaded copies.
 */
export const useStackPictures = (): StackPictureLayer => {
  const [showImages, setShowImages] = usePersistentFlag('astrometrics:planetarium:showImages', false);
  const [opacity, setOpacity] = usePersistentNumber(
    OPACITY_STORAGE_KEY, DEFAULT_PICTURE_OPACITY, { min: MINIMUM_PICTURE_OPACITY, max: 1 });

  const isDisplayActive = useIsDisplayActive();
  const query = useQuery({
    queryKey: ['stackPictures'],
    queryFn: async () => {
      const data = await callBackend('planetarium:get_stack_pictures', {}, { silent: true });
      return Array.isArray(data) ? data : [];
    },
    enabled: showImages && isDisplayActive,
    staleTime: 5 * 60 * 1000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (state) => (state.state.status === 'error' ? PICTURE_LIST_RETRY_INTERVAL_MS : false),
  });
  const pictures = showImages ? (query.data ?? []) : [];

  const overviews = useRef(new Map<string, PictureDrawable>());
  const fulls = useRef(new Map<string, HTMLImageElement>());
  const loading = useRef(new Set<string>());
  const failed = useRef(new Set<string>());
  const [loadedVersion, setLoadedVersion] = useState(0);
  const isMounted = useRef(true);
  useEffect(() => {
    isMounted.current = true;
    return () => { isMounted.current = false; };
  }, []);

  const getDrawable = useCallback((picture: StackSkyPicture, full: boolean): PictureDrawable | null => {
    const path = picture.picturePath;
    const fullImage = fulls.current.get(path);
    if (fullImage) {
      // Keep the most recently used full pictures at the end of the list.
      fulls.current.delete(path);
      fulls.current.set(path, fullImage);
      if (full) return fullImage;
    }
    const overview = overviews.current.get(path) ?? null;
    const wanted = full && !fullImage;
    if ((overview && !wanted) || loading.current.has(path) || failed.current.has(path)) {
      return overview ?? fullImage ?? null;
    }
    loading.current.add(path);
    void (async () => {
      try {
        await loadStaticMounts();
        const url = resolveImageSrc(path);
        if (!url) {
          failed.current.add(path);
          return;
        }
        const image = await loadImage(url);
        if (!overviews.current.has(path)) overviews.current.set(path, overviewCopy(image));
        if (wanted) {
          fulls.current.set(path, image);
          while (fulls.current.size > MAXIMUM_FULL_PICTURES) {
            const oldest = fulls.current.keys().next().value as string;
            fulls.current.delete(oldest);
          }
        }
        if (isMounted.current) setLoadedVersion((version) => version + 1);
      } catch {
        // A picture that cannot be loaded is simply not drawn.
        failed.current.add(path);
      } finally {
        loading.current.delete(path);
      }
    })();
    return overview ?? null;
  }, []);

  return { showImages, setShowImages, opacity, setOpacity, pictures, getDrawable, loadedVersion };
};
