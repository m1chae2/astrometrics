/**
 * @file usePersistentFlag.ts
 * @description A true/false state that is kept in the browser's `localStorage`, so it is
 * still the same after the window refreshes. Used for the folded state of the Image
 * Processing panels. Storage is a per-viewer convenience: if it is missing, empty or
 * throws, the flag simply starts at its default.
 */
import { useCallback, useState } from 'react';

/**
 * Reads a saved flag.
 * @param {string} key - The `localStorage` key.
 * @param {boolean} defaultValue - The value to use when nothing usable is saved.
 * @returns {boolean} The saved value, or `defaultValue`.
 */
function readFlag(key: string, defaultValue: boolean): boolean {
  try {
    const saved = localStorage.getItem(key);
    if (saved === 'true') return true;
    if (saved === 'false') return false;
  } catch {
    // Storage is unavailable; fall through to the default.
  }
  return defaultValue;
}

/**
 * A boolean state that is saved to `localStorage` each time it changes.
 * @param {string} key - The `localStorage` key.
 * @param {boolean} defaultValue - The starting value when nothing is saved.
 * @returns {[boolean, (value: boolean) => void]} The value and a setter that also saves it.
 */
export function usePersistentFlag(key: string, defaultValue: boolean): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => readFlag(key, defaultValue));
  const update = useCallback((next: boolean) => {
    setValue(next);
    try {
      localStorage.setItem(key, String(next));
    } catch {
      // Storage is unavailable; the flag still works for this session.
    }
  }, [key]);
  return [value, update];
}
