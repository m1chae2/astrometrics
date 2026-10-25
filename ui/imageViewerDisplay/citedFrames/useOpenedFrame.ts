/**
 * @file useOpenedFrame.ts
 * @description Opens one cited frame in the Image Viewer. It asks the backend to draw the
 * FITS file (`images:convert_fits`, the call the viewer already uses for a FITS path) and
 * keeps the picture so the viewer's center panel can show it in place of the target image.
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchImageByPath } from '../../common/services/imaging/imageService';
import { dataUrlToBlob } from '../../common/utils/dataUrl';

/** The frame opened from the cited list, as the viewer needs it. */
export interface OpenedFrame {
  /** File path of the frame. */
  path: string;
  /** Object URL of the drawn picture. */
  imageUrl: string | null;
  /** The drawn picture. */
  imageBlob: Blob | null;
  /** True while the picture is being drawn. */
  loading: boolean;
  /** Error text when the picture could not be drawn. */
  error: string | null;
}

/**
 * Keeps the frame the user opened from the cited list.
 *
 * @returns The opened frame (or null), a function that opens a path, and a function that closes it.
 */
export function useOpenedFrame(): {
  openedFrame: OpenedFrame | null;
  openFrame: (path: string) => void;
  closeFrame: () => void;
} {
  const [openedFrame, setOpenedFrame] = useState<OpenedFrame | null>(null);
  const objectUrl = openedFrame?.imageUrl ?? null;

  // Release the picture's object URL when it is replaced or the viewer goes away.
  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectUrl]);

  const openFrame = useCallback((path: string) => {
    setOpenedFrame({ path, imageUrl: null, imageBlob: null, loading: true, error: null });
    void fetchImageByPath(path).then((rendered) => {
      if (!rendered?.imageData) {
        setOpenedFrame((current) => (current?.path === path
          ? { path, imageUrl: null, imageBlob: null, loading: false, error: 'Could not open this frame.' }
          : current));
        return;
      }
      const blob = dataUrlToBlob(rendered.imageData);
      const url = URL.createObjectURL(blob);
      setOpenedFrame((current) => {
        if (current?.path !== path) {
          URL.revokeObjectURL(url);
          return current;
        }
        return { path, imageUrl: url, imageBlob: blob, loading: false, error: null };
      });
    });
  }, []);

  const closeFrame = useCallback(() => setOpenedFrame(null), []);
  return { openedFrame, openFrame, closeFrame };
}
