/**
 * @module useSideStackTop
 * @fileoverview Keeps the Planetarium's left card column clear of the toolbar.
 *
 * The column sits at the top-left of the sky with the same inset from the top
 * as from the left. The toolbar sits at the top-right. In a narrow window the
 * toolbar reaches across to the left (it wraps onto more rows), so the column
 * then starts just below the toolbar instead, still at the left inset.
 */

import { RefObject, useLayoutEffect, useState } from 'react';

/** Space kept between the toolbar's bottom edge and the column, in pixels (the `--space-2` token). */
const GAP_BELOW_TOOLBAR_PX = 8;

/**
 * Works out where the card column's top must be so it does not overlap the toolbar.
 *
 * @param {RefObject<HTMLElement | null>} containerRef The sky view that holds both.
 * @param {RefObject<HTMLElement | null>} columnRef The card column.
 * @returns {number | null} The column's top in pixels when the toolbar is in its way, or null to keep
 *   the stylesheet's inset.
 */
export function useSideStackTop(
  containerRef: RefObject<HTMLElement | null>,
  columnRef: RefObject<HTMLElement | null>,
): number | null {
  const [top, setTop] = useState<number | null>(null);

  useLayoutEffect(() => {
    const container = containerRef.current;
    const column = columnRef.current;
    if (!container || !column) return undefined;

    const measure = () => {
      const toolbar = container.querySelector<HTMLElement>('.planetarium-toolbar');
      if (!toolbar) {
        setTop(null);
        return;
      }
      const containerRect = container.getBoundingClientRect();
      const toolbarRect = toolbar.getBoundingClientRect();
      const columnRect = column.getBoundingClientRect();
      // The toolbar is in the way when its left edge comes in over the column.
      const overlapsSideways = toolbarRect.left < columnRect.right && toolbarRect.right > columnRect.left;
      setTop(overlapsSideways ? toolbarRect.bottom - containerRect.top + GAP_BELOW_TOOLBAR_PX : null);
    };

    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    const toolbar = container.querySelector<HTMLElement>('.planetarium-toolbar');
    if (toolbar) observer.observe(toolbar);
    return () => observer.disconnect();
  }, [containerRef, columnRef]);

  return top;
}
