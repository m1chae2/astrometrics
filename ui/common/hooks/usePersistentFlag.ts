/**
 * @file usePersistentFlag.ts
 * @description States that are kept in the browser's `localStorage`, so they are still the same
 * after the window refreshes: a true/false flag (`usePersistentFlag`, used for folded panels and
 * on/off choices) and a number (`usePersistentNumber`, used for a slider such as an opacity).
 * Keys follow the `astrometrics:<display>:<name>` pattern. Storage is a per-viewer convenience:
 * if it is missing, empty or throws, the value simply starts at its default.
 */
import { useCallback, useState } from 'react';

/** Extra choices for a saved value. */
export interface PersistentValueOptions {
  /**
   * An older key the same choice was saved under. When the key has nothing saved, the value is
   * read from this key once, saved under the key, and the old key is removed.
   */
  legacyKey?: string;
}

/**
 * Reads the saved text of a key, moving it from `legacyKey` first when only the old key has it.
 * @param {string} key - The `localStorage` key.
 * @param {string | undefined} legacyKey - An older key for the same choice, if any.
 * @returns {string | null} The saved text, or `null` when nothing is saved or storage fails.
 */
function readSaved(key: string, legacyKey: string | undefined): string | null {
  try {
    const saved = localStorage.getItem(key);
    if (saved !== null || !legacyKey) return saved;
    const old = localStorage.getItem(legacyKey);
    if (old !== null) {
      localStorage.setItem(key, old);
      localStorage.removeItem(legacyKey);
    }
    return old;
  } catch {
    // Storage is unavailable; the caller uses its default.
    return null;
  }
}

/**
 * Saves the text of a key, ignoring a storage failure.
 * @param {string} key - The `localStorage` key.
 * @param {string} text - The text to save.
 */
function writeSaved(key: string, text: string): void {
  try {
    localStorage.setItem(key, text);
  } catch {
    // Storage is unavailable; the value still works for this session.
  }
}

/**
 * Reads a saved flag.
 * @param {string} key - The `localStorage` key.
 * @param {boolean} defaultValue - The value to use when nothing usable is saved.
 * @param {string | undefined} legacyKey - An older key for the same choice, if any.
 * @returns {boolean} The saved value, or `defaultValue`.
 */
function readFlag(key: string, defaultValue: boolean, legacyKey?: string): boolean {
  const saved = readSaved(key, legacyKey);
  if (saved === 'true') return true;
  if (saved === 'false') return false;
  return defaultValue;
}

/**
 * A boolean state that is saved to `localStorage` each time it changes.
 * @param {string} key - The `localStorage` key.
 * @param {boolean} defaultValue - The starting value when nothing is saved.
 * @param {PersistentValueOptions} [options] - An older key to move the saved value from.
 * @returns {[boolean, (value: boolean) => void]} The value and a setter that also saves it.
 */
export function usePersistentFlag(
  key: string,
  defaultValue: boolean,
  options: PersistentValueOptions = {},
): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => readFlag(key, defaultValue, options.legacyKey));
  const update = useCallback((next: boolean) => {
    setValue(next);
    writeSaved(key, String(next));
  }, [key]);
  return [value, update];
}

/** The choices for a saved number. */
export interface PersistentNumberOptions extends PersistentValueOptions {
  /** The smallest allowed value. A smaller saved or set value is raised to it. */
  min?: number;
  /** The largest allowed value. A larger saved or set value is lowered to it. */
  max?: number;
}

/**
 * Keeps a number inside its allowed range.
 * @param {number} value - The number.
 * @param {PersistentNumberOptions} options - The range.
 * @returns {number} The number, raised to `min` or lowered to `max` when outside.
 */
function clampNumber(value: number, options: PersistentNumberOptions): number {
  let result = value;
  if (options.min !== undefined) result = Math.max(options.min, result);
  if (options.max !== undefined) result = Math.min(options.max, result);
  return result;
}

/**
 * Reads a saved number.
 * @param {string} key - The `localStorage` key.
 * @param {number} defaultValue - The value to use when nothing usable is saved.
 * @param {PersistentNumberOptions} options - The range and an older key, if any.
 * @returns {number} The saved value kept inside the range, or `defaultValue`.
 */
function readNumber(key: string, defaultValue: number, options: PersistentNumberOptions): number {
  const saved = readSaved(key, options.legacyKey);
  if (saved === null || saved.trim() === '') return defaultValue;
  const parsed = Number(saved);
  return Number.isFinite(parsed) ? clampNumber(parsed, options) : defaultValue;
}

/**
 * A number state that is saved to `localStorage` each time it changes. A value set outside the
 * range is kept inside it before it is stored.
 * @param {string} key - The `localStorage` key.
 * @param {number} defaultValue - The starting value when nothing usable is saved.
 * @param {PersistentNumberOptions} [options] - The allowed range and an older key, if any.
 * @returns {[number, (value: number) => void]} The value and a setter that also saves it.
 */
export function usePersistentNumber(
  key: string,
  defaultValue: number,
  options: PersistentNumberOptions = {},
): [number, (value: number) => void] {
  const [value, setValue] = useState<number>(() => readNumber(key, defaultValue, options));
  const { min, max } = options;
  const update = useCallback((next: number) => {
    const kept = clampNumber(next, { min, max });
    setValue(kept);
    writeSaved(key, String(kept));
  }, [key, min, max]);
  return [value, update];
}
