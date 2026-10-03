/**
 * @module parsedFitsCache
 * @fileoverview A small "least recently used" cache of decoded FITS images.
 *
 * The decoded pixels live inside the FITS worker, not on the main thread (see
 * `fitsWorker.ts`). What the main thread keeps is a short record per image:
 * the image's size and range plus the id the worker filed the pixels under.
 * When a record is pushed out of this cache, the owner is told so it can
 * release the pixels held by the worker. That keeps the worker's memory use
 * bounded.
 */

/** How many decoded images to keep before the oldest is dropped. */
export const PARSED_FITS_CACHE_LIMIT = 5;

/**
 * A cache with a fixed size that drops its least recently used entry when full.
 */
export class ParsedFitsCache<Entry> {
  private readonly entries = new Map<string | Blob, Entry>();

  /**
   * @param limit - Most entries to keep.
   * @param onEvict - Called with each entry that is dropped to make room, or on `clear()`.
   */
  constructor(
    private readonly limit: number,
    private readonly onEvict: (entry: Entry) => void,
  ) {}

  /**
   * Looks up an entry and marks it as the most recently used.
   *
   * @param key - The image's URL or `Blob`.
   * @returns The entry, or `undefined` when it is not cached.
   */
  get(key: string | Blob): Entry | undefined {
    const entry = this.entries.get(key);
    if (entry === undefined) return undefined;
    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry;
  }

  /**
   * Stores an entry as the most recently used, dropping the oldest ones if the cache is over its limit.
   *
   * @param key - The image's URL or `Blob`.
   * @param entry - The record to keep.
   */
  set(key: string | Blob, entry: Entry): void {
    const previous = this.entries.get(key);
    if (previous !== undefined && previous !== entry) this.onEvict(previous);
    this.entries.delete(key);
    this.entries.set(key, entry);
    while (this.entries.size > this.limit) {
      const oldestKey = this.entries.keys().next().value as string | Blob;
      const oldestEntry = this.entries.get(oldestKey) as Entry;
      this.entries.delete(oldestKey);
      this.onEvict(oldestEntry);
    }
  }

  /** Drops every entry, telling `onEvict` about each one. */
  clear(): void {
    const dropped = [...this.entries.values()];
    this.entries.clear();
    dropped.forEach((entry) => this.onEvict(entry));
  }
}
