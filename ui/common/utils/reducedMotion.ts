/**
 * @file reducedMotion.ts
 * @description Says whether the person asked the computer to show less motion (the system's
 * "reduce motion" setting). Screens check it before a smooth scroll or a sliding panel, and
 * jump straight to the end instead when it is set.
 */

/**
 * Says whether the person asked the computer to show less motion.
 *
 * @returns True when `prefers-reduced-motion: reduce` matches; false when it does not, or when
 * the page cannot ask (no `window.matchMedia`, as in some tests).
 */
export function prefersReducedMotion(): boolean {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
