/**
 * @file runWhenIdle.ts
 * @description Runs a piece of work when the browser has nothing else to do,
 * so it does not slow down what the user is looking at. Where the browser has
 * no idle callback (for example in tests), the work runs after a short delay.
 */

/** How long to wait for the browser to be idle. */
export interface RunWhenIdleOptions {
  /** Longest wait, in milliseconds, before the work runs even if the browser is still busy. */
  timeoutMs: number;
  /** Wait, in milliseconds, used where the browser has no idle callback. */
  fallbackDelayMs: number;
}

/**
 * Runs a function when the browser is idle, or after a short delay where idle
 * callbacks do not exist.
 *
 * @param callback The work to run.
 * @param options The longest wait, and the delay used without idle callbacks.
 * @return A function that cancels the wait.
 */
export function runWhenIdle(callback: () => void, options: RunWhenIdleOptions): () => void {
  if (typeof window.requestIdleCallback === 'function') {
    const handle = window.requestIdleCallback(callback, { timeout: options.timeoutMs });
    return () => window.cancelIdleCallback(handle);
  }
  const handle = window.setTimeout(callback, options.fallbackDelayMs);
  return () => window.clearTimeout(handle);
}
