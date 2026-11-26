/**
 * @file useArrivedFromImage.ts
 * @description Tells a screen whether the person came to it from a star in the Image Processing picture, so it can
 * offer a "Back to image" button. It reads the saved view (see imageViewStore) and listens for changes to it.
 */
import { useEffect, useState } from 'react';
import { IMAGE_VIEW_CHANGED_EVENT, loadImageView } from '../utils/imageViewStore';

/**
 * Reports the star the person opened from the Image Processing picture.
 *
 * @returns The star's id, or null when the person did not come from the picture (or has gone back already).
 */
export function useArrivedFromImage(): string | null {
  const [star, setStar] = useState<string | null>(() => loadImageView()?.arrivedFromStar ?? null);
  useEffect(() => {
    const refresh = (): void => setStar(loadImageView()?.arrivedFromStar ?? null);
    refresh();
    window.addEventListener(IMAGE_VIEW_CHANGED_EVENT, refresh);
    return () => window.removeEventListener(IMAGE_VIEW_CHANGED_EVENT, refresh);
  }, []);
  return star;
}
