/**
 * @file targetsUpdatedVersion.ts
 * @description Counts the `targetsUpdated` events (the library changed: a target
 * was added, removed or restacked).
 *
 * A display that is paused while hidden (React `Activity`) hears no events, so a
 * cache it keeps would stay out of date. Such a display compares the count it
 * last saw with `getTargetsUpdatedVersion()` when it starts running again, and
 * drops its cache when the count is higher. The listener is registered when this
 * file is first imported, which happens at start-up.
 */

import { on as onEvent } from './eventBus';

let version = 0;

onEvent('targetsUpdated', () => {
  version += 1;
});

/**
 * Returns how many `targetsUpdated` events the app has seen.
 *
 * @return The count.
 */
export function getTargetsUpdatedVersion(): number {
  return version;
}
