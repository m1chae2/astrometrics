/**
 * @file useNeighborPrefetch.ts
 * @description Gets the pictures of the targets just above and just below the
 * selected one ready while the user looks at the selected one, so stepping
 * through the list feels instant.
 *
 * Only those two neighbors are ever fetched. Loading every picture at start-up
 * would take gigabytes of memory.
 */
import { useEffect, useSyncExternalStore } from 'react';
import { fetchProcessedImage } from '../../common/services/imaging/imageService';
import { prefetchDecode } from '../../common/fitsViewer/hooks/useFitsLoader';
import {
  cacheTargetImage,
  cacheTargetPreview,
  getCachedTargetImage,
  getTargetImageCacheVersion,
  subscribeToTargetImageCache,
} from './targetImageCache';

/** One row of the target list, as far as the prefetch cares. */
export interface PrefetchCandidate {
  /** The target's id. */
  id: string;
  /** Whether the target has a processed image to fetch. */
  hasImage: boolean;
}

/** Longest wait, in milliseconds, for the browser to be idle before prefetching anyway. */
const IDLE_TIMEOUT_MS = 2000;

/** Wait, in milliseconds, used where the browser has no idle callback. */
const IDLE_FALLBACK_DELAY_MS = 300;

/**
 * Runs a function when the browser is idle, or after a short delay where idle
 * callbacks do not exist.
 *
 * @param callback The work to run.
 * @return A function that cancels the wait.
 */
function runWhenIdle(callback: () => void): () => void {
  if (typeof window.requestIdleCallback === 'function') {
    const handle = window.requestIdleCallback(callback, { timeout: IDLE_TIMEOUT_MS });
    return () => window.cancelIdleCallback(handle);
  }
  const handle = window.setTimeout(callback, IDLE_FALLBACK_DELAY_MS);
  return () => window.clearTimeout(handle);
}

/**
 * Fetches one target's picture into the picture cache and has the viewer decode it.
 *
 * @param targetId The target to prepare.
 * @param signal Aborted when the user moves on.
 */
async function prefetchTargetPicture(targetId: string, signal: AbortSignal): Promise<void> {
  let blob = getCachedTargetImage(targetId)?.blob ?? null;
  if (!blob) {
    blob = await fetchProcessedImage(targetId, signal, (preview) => cacheTargetPreview(targetId, preview));
    if (!blob || blob.size === 0 || signal.aborted) return;
    cacheTargetImage(targetId, blob);
  }
  if (signal.aborted) return;
  await prefetchDecode(blob, signal);
}

/**
 * Prefetches the pictures of the two targets next to the selected one.
 *
 * Waits until the selected picture is shown (`ready`) and the browser is idle.
 * Does nothing while the window is hidden, and starts when it is shown again.
 * Moving the selection cancels any prefetch under way.
 *
 * @param candidates The target list in the order shown (filtered and sorted).
 * @param selectedId The selected target, or an empty value for none.
 * @param ready Whether the selected picture has finished loading.
 */
export function useNeighborPrefetch(
  candidates: readonly PrefetchCandidate[],
  selectedId: string | undefined,
  ready: boolean
): void {
  // A cleared cache (the library changed) means the neighbors must be fetched again.
  const cacheVersion = useSyncExternalStore(subscribeToTargetImageCache, getTargetImageCacheVersion);

  const index = selectedId ? candidates.findIndex((candidate) => candidate.id === selectedId) : -1;
  const above = index > 0 && candidates[index - 1].hasImage ? candidates[index - 1].id : '';
  const below = index >= 0 && index < candidates.length - 1 && candidates[index + 1].hasImage
    ? candidates[index + 1].id
    : '';

  useEffect(() => {
    if (!ready || (!above && !below)) return undefined;

    let controller = new AbortController();
    let cancelWait: (() => void) | null = null;

    const start = () => {
      cancelWait?.();
      cancelWait = null;
      if (document.hidden) return;
      const run = controller;
      cancelWait = runWhenIdle(() => {
        cancelWait = null;
        void (async () => {
          // One after the other, so the neighbors never compete with each other.
          for (const targetId of [below, above]) {
            if (!targetId || run.signal.aborted || document.hidden) continue;
            try {
              await prefetchTargetPicture(targetId, run.signal);
            } catch {
              // A prefetch that fails is not shown to the user; the picture loads normally when selected.
            }
          }
        })();
      });
    };

    const onVisibilityChange = () => {
      if (document.hidden) {
        cancelWait?.();
        cancelWait = null;
        controller.abort();
      } else {
        controller = new AbortController();
        start();
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    start();

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      cancelWait?.();
      controller.abort();
    };
  }, [ready, above, below, cacheVersion]);
}
