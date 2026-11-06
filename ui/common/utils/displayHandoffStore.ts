/**
 * @file displayHandoffStore.ts
 * @description The one place where a "show this in that display" request waits
 * until the display can act on it.
 *
 * Why it exists: the app keeps every display mounted and hides the ones you are
 * not looking at. A hidden display can be paused (React's `Activity`), and a
 * paused display has no running effects, so a window event sent to it is lost.
 * A hand-off instead goes into this store. The display reads it when it is
 * shown, whether it was hidden, paused or not yet loaded when the request was
 * made.
 *
 * How it works:
 * - A sender calls `postHandoff(display, kind, payload)`.
 * - The receiving display calls `useHandoffConsumer(display, kind, handler)`
 *   (or `usePendingHandoff` when it must wait before acting). Each request is
 *   handed over once and then removed.
 * - Most kinds keep only the newest request ("latest wins"). A kind that must
 *   not lose items, such as agent terminal output, is posted with
 *   `queue: true` and keeps an ordered buffer with a size limit.
 * - A part of the app that is always running (not inside a display) can watch
 *   every post of a kind with `observeHandoffs`.
 */

import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react';

/** One waiting request for a display. */
export interface Handoff<Payload = unknown> {
  /** Unique number that identifies this request (used to remove it). */
  id: number;
  /** The display that should act on the request, such as `'Planetarium'`. */
  display: string;
  /** What the request asks for, such as `'showOnSky'`. */
  kind: string;
  /** The data that goes with the request. */
  payload: Payload;
  /** Optional name of the panel inside the display that the request is for. */
  element?: string;
}

/** Extra choices for `postHandoff`. */
export interface PostHandoffOptions {
  /** Keep every request in order (up to `queueLimit`) instead of only the newest. */
  queue?: boolean;
  /** Most requests a queue keeps; the oldest are dropped first. Default 200. */
  queueLimit?: number;
  /** Optional name of the panel inside the display that the request is for. */
  element?: string;
}

/** Default size limit of a queue, so a display that never opens cannot grow it forever. */
const DEFAULT_QUEUE_LIMIT = 200;

/** A shared empty list, so "nothing waiting" is the same value every time. */
const NO_HANDOFFS: readonly Handoff[] = Object.freeze([]);

/** The waiting requests, by `display + kind`. Each list is replaced, never changed in place. */
const waiting = new Map<string, readonly Handoff[]>();
/** Functions to call when the waiting requests change (React uses these). */
const changeListeners = new Set<() => void>();
/** Functions to call for each new post, by kind. */
const observersByKind = new Map<string, Set<(handoff: Handoff) => void>>();
let nextHandoffId = 1;

/**
 * Builds the key a display and kind are stored under.
 *
 * @param display The display name.
 * @param kind The kind of request.
 * @return The storage key.
 */
function keyOf(display: string, kind: string): string {
  return `${display}\u0000${kind}`;
}

/** Tells every watcher that something changed. */
function notifyChange(): void {
  changeListeners.forEach((listener) => listener());
}

/**
 * Lets React watch for changes.
 *
 * @param listener Called after any post or removal.
 * @return A function that stops watching.
 */
function subscribe(listener: () => void): () => void {
  changeListeners.add(listener);
  return () => {
    changeListeners.delete(listener);
  };
}

/**
 * Puts a request in the store for a display.
 *
 * @param display The display that should act on it.
 * @param kind What the request asks for.
 * @param payload The data that goes with it.
 * @param options Queue behavior and the panel inside the display.
 * @return The new request's id.
 */
export function postHandoff<Payload>(
  display: string,
  kind: string,
  payload: Payload,
  options: PostHandoffOptions = {}
): number {
  const handoff: Handoff<Payload> = {
    id: nextHandoffId++,
    display,
    kind,
    payload,
    element: options.element,
  };
  const key = keyOf(display, kind);
  if (options.queue) {
    const limit = options.queueLimit ?? DEFAULT_QUEUE_LIMIT;
    const previous = waiting.get(key) ?? NO_HANDOFFS;
    waiting.set(key, [...previous, handoff as Handoff].slice(-limit));
  } else {
    waiting.set(key, [handoff as Handoff]);
  }
  observersByKind.get(kind)?.forEach((observer) => {
    try {
      observer(handoff as Handoff);
    } catch (error) {
      console.warn(`[displayHandoffStore] observer of "${kind}" failed:`, error);
    }
  });
  notifyChange();
  return handoff.id;
}

/**
 * Returns the requests now waiting for a display, oldest first. Does not remove them.
 *
 * @param display The display name.
 * @param kind The kind of request.
 * @return The waiting requests (an empty shared list when there are none).
 */
export function peekHandoffs<Payload = unknown>(
  display: string,
  kind: string
): readonly Handoff<Payload>[] {
  return (waiting.get(keyOf(display, kind)) ?? NO_HANDOFFS) as readonly Handoff<Payload>[];
}

/**
 * Removes requests once they have been handled.
 *
 * @param display The display name.
 * @param kind The kind of request.
 * @param ids The ids of the requests that were handled.
 */
export function consumeHandoffs(display: string, kind: string, ids: readonly number[]): void {
  const key = keyOf(display, kind);
  const current = waiting.get(key);
  if (!current || current.length === 0) return;
  const remaining = current.filter((handoff) => !ids.includes(handoff.id));
  if (remaining.length === current.length) return;
  if (remaining.length === 0) waiting.delete(key);
  else waiting.set(key, remaining);
  notifyChange();
}

/**
 * Removes one request once it has been handled.
 *
 * @param handoff The request that was handled.
 */
export function consumeHandoff(handoff: Handoff): void {
  consumeHandoffs(handoff.display, handoff.kind, [handoff.id]);
}

/**
 * Calls a function for every future post of one kind, whichever display it is for.
 * For parts of the app that are always running (providers and module-level state),
 * not for displays: a display that can be paused must use `useHandoffConsumer`.
 * Watching does not remove the request.
 *
 * @param kind The kind of request to watch.
 * @param observer Called with each new request.
 * @return A function that stops watching.
 */
export function observeHandoffs(kind: string, observer: (handoff: Handoff) => void): () => void {
  let observers = observersByKind.get(kind);
  if (!observers) {
    observers = new Set();
    observersByKind.set(kind, observers);
  }
  observers.add(observer);
  return () => {
    observers?.delete(observer);
    // Another set may have taken the kind's place since; only remove our own, empty one.
    if (observers?.size === 0 && observersByKind.get(kind) === observers) observersByKind.delete(kind);
  };
}

/**
 * Follows the requests waiting for a display. Re-renders when they change.
 *
 * @param display The display name.
 * @param kind The kind of request.
 * @return The waiting requests, oldest first.
 */
export function usePendingHandoffs<Payload = unknown>(
  display: string,
  kind: string
): readonly Handoff<Payload>[] {
  return useSyncExternalStore(
    subscribe,
    () => peekHandoffs<Payload>(display, kind),
    () => peekHandoffs<Payload>(display, kind)
  );
}

/**
 * Follows the newest request waiting for a display. Use it when the display
 * must wait before acting (for example until its lists have loaded); call
 * `consumeHandoff` when done.
 *
 * @param display The display name.
 * @param kind The kind of request.
 * @return The newest waiting request, or null.
 */
export function usePendingHandoff<Payload = unknown>(
  display: string,
  kind: string
): Handoff<Payload> | null {
  const pending = usePendingHandoffs<Payload>(display, kind);
  return pending.length > 0 ? pending[pending.length - 1] : null;
}

/**
 * Hands every waiting request of one kind to a function, once each, in order,
 * as soon as the display is running. A request posted while the display was
 * hidden or paused is handed over when it is shown again.
 *
 * @param display The display name.
 * @param kind The kind of request.
 * @param handler Called with each request's payload and the request itself.
 */
export function useHandoffConsumer<Payload = unknown>(
  display: string,
  kind: string,
  handler: (payload: Payload, handoff: Handoff<Payload>) => void
): void {
  const pending = usePendingHandoffs<Payload>(display, kind);
  const handlerRef = useRef(handler);
  useEffect(() => {
    handlerRef.current = handler;
  });
  const deliver = useCallback(() => {
    // Read the store again: a repeated effect run (React development mode runs
    // each effect twice) must not hand the same request over twice.
    const current = peekHandoffs<Payload>(display, kind);
    if (current.length === 0) return;
    consumeHandoffs(
      display,
      kind,
      current.map((handoff) => handoff.id)
    );
    current.forEach((handoff) => handlerRef.current(handoff.payload, handoff));
  }, [display, kind]);
  useEffect(() => {
    if (pending.length > 0) deliver();
  }, [pending, deliver]);
}

/** Empties the waiting requests (the watchers stay). For tests only. */
export function resetHandoffsForTests(): void {
  waiting.clear();
  notifyChange();
}
