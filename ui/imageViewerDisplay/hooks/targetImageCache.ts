/**
 * @file targetImageCache.ts
 * @description Keeps the Image Viewer's pictures (as Blob object URLs) outside
 * any React component.
 *
 * Why: the Image Viewer can be paused while hidden (React `Activity`), and a
 * paused display runs the cleanup of all its effects. If the picture's URL were
 * freed in such a cleanup, the picture would be broken when the display is shown
 * again. Here the URLs belong to the app, not to the display. A URL is freed
 * only when the picture is replaced, pushed out of the small cache, or the
 * library says its data changed (`targetsUpdated`).
 */

import { on as onEvent } from '../../common/utils/eventBus';

/** One picture the viewer can show. */
export interface CachedTargetImage {
  /** Object URL of the picture. */
  objectUrl: string;
  /** The picture data. */
  blob: Blob;
}

/** Most pictures kept at once. Older ones are freed first. */
const MAX_CACHED_IMAGES = 12;

/**
 * Most previews kept at once. A preview is a small JPEG (about 100 KB), so many
 * more fit than full pictures, and one outlives its full picture.
 */
const MAX_CACHED_PREVIEWS = 50;

/** Order is use order (a read moves an entry to the end), so the first key is the least recently used. */
const cache = new Map<string, CachedTargetImage>();
/** Small JPEG previews by target id, oldest first. They need no object URL. */
const previews = new Map<string, Blob>();
/** The target whose picture is on screen. Its entry is never pushed out of the cache. */
let shownTargetId: string | null = null;
let version = 0;
const versionListeners = new Set<() => void>();

/**
 * Frees one picture's object URL, ignoring failures.
 *
 * @param entry The picture to free.
 */
function release(entry: CachedTargetImage): void {
  try {
    URL.revokeObjectURL(entry.objectUrl);
  } catch {
    // Ignore revocation failures.
  }
}

/**
 * Returns the cached picture of a target, if there is one.
 *
 * @param targetId The target's id.
 * @return The picture, or undefined.
 */
export function getCachedTargetImage(targetId: string): CachedTargetImage | undefined {
  const entry = cache.get(targetId);
  if (entry) {
    // A read makes the entry the newest, so the least recently used one is freed first.
    cache.delete(targetId);
    cache.set(targetId, entry);
  }
  return entry;
}

/**
 * Says which target's picture is on screen, so its object URL is never freed while it is shown.
 *
 * @param targetId The shown target's id, or null when none is shown.
 */
export function setShownTargetImage(targetId: string | null): void {
  shownTargetId = targetId;
}

/**
 * Keeps a picture for a target. A picture already kept for the target is freed.
 *
 * @param targetId The target's id.
 * @param blob The picture data.
 * @return The kept picture, with its new object URL.
 */
export function cacheTargetImage(targetId: string, blob: Blob): CachedTargetImage {
  const previous = cache.get(targetId);
  if (previous) {
    release(previous);
    cache.delete(targetId);
  }
  const entry: CachedTargetImage = { objectUrl: URL.createObjectURL(blob), blob };
  cache.set(targetId, entry);
  while (cache.size > MAX_CACHED_IMAGES) {
    // The oldest entry that is not the picture on screen.
    const oldestKey = [...cache.keys()].find((key) => key !== shownTargetId);
    if (oldestKey === undefined) break;
    const oldest = cache.get(oldestKey);
    if (oldest) release(oldest);
    cache.delete(oldestKey);
  }
  return entry;
}

/**
 * Returns the cached preview of a target, if there is one.
 *
 * @param targetId The target's id.
 * @return The small JPEG preview, or undefined.
 */
export function getCachedTargetPreview(targetId: string): Blob | undefined {
  return previews.get(targetId);
}

/**
 * Keeps a preview for a target. A preview already kept for the target is replaced.
 *
 * @param targetId The target's id.
 * @param preview The small JPEG preview.
 */
export function cacheTargetPreview(targetId: string, preview: Blob): void {
  previews.delete(targetId);
  previews.set(targetId, preview);
  while (previews.size > MAX_CACHED_PREVIEWS) {
    previews.delete(previews.keys().next().value as string);
  }
}

/** Frees every picture and tells the viewers to read their pictures again. */
export function clearTargetImageCache(): void {
  cache.forEach(release);
  cache.clear();
  previews.clear();
  version += 1;
  versionListeners.forEach((listener) => listener());
}

/**
 * Lets React watch the cache version, which changes when the cache is cleared.
 *
 * @param listener Called after a clear.
 * @return A function that stops watching.
 */
export function subscribeToTargetImageCache(listener: () => void): () => void {
  versionListeners.add(listener);
  return () => {
    versionListeners.delete(listener);
  };
}

/**
 * Returns the cache version.
 *
 * @return A number that goes up each time the cache is cleared.
 */
export function getTargetImageCacheVersion(): number {
  return version;
}

// The library changed (a stack finished, a target was added or removed): the
// pictures are out of date. This listener lives as long as the app does, so it
// also runs while the Image Viewer is paused.
onEvent('targetsUpdated', clearTargetImageCache);
