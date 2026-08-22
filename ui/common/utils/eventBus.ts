/**
 * @fileoverview A simple, typed event bus for cross-component communication.
 * This module allows disparate parts of the application to emit and listen
 * for events without direct coupling.
 */

/** Type for event handler functions. */
type Handler = (payload?: unknown) => void;

/** Map storing event names to their registered sets of handlers. */
const handlers: Map<string, Set<Handler>> = new Map();

/**
 * Registers a listener for the specified event.
 * @param event The name of the event to listen for.
 * @param handler The callback function to execute when the event is emitted.
 * @return A function that unregisters the listener when called (detacher).
 */
export function on(event: string, handler: Handler): () => void {
  let handlersSet = handlers.get(event);
  if (!handlersSet) {
    handlersSet = new Set();
    handlers.set(event, handlersSet);
  }
  handlersSet.add(handler);
  return (): void => off(event, handler);
}

/**
 * Unregisters a listener for the specified event.
 * @param event The name of the event.
 * @param handler The callback function to remove.
 */
export function off(event: string, handler: Handler): void {
  const handlersSet = handlers.get(event);
  if (!handlersSet) return;
  handlersSet.delete(handler);
  if (handlersSet.size === 0) handlers.delete(event);
}

/**
 * Emits an event, triggering all registered handlers.
 * @param event The name of the event to emit.
 * @param payload Optional data to pass to the handlers.
 */
export function emit(event: string, payload?: unknown): void {
  const handlersSet = handlers.get(event);
  if (!handlersSet) return;
  for (const handler of Array.from(handlersSet)) {
    try {
      handler(payload);
    } catch {
      // ignore handler errors to prevent crashing the emitter
    }
  }
}
