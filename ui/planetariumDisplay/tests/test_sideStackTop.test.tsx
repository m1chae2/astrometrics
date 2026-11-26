/**
 * @fileoverview Tests that the Planetarium's card column keeps the top-left inset when the
 * toolbar is off to the right (a wide window), and moves just below the toolbar when the toolbar
 * reaches over it (a narrow window, where it wraps).
 */

import React, { useRef } from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { useSideStackTop } from '../hooks/useSideStackTop';

/**
 * Gives an element a fixed on-screen box.
 * @param element The element.
 * @param box Its left, top, right and bottom edges.
 */
function placeAt(element: Element, box: { left: number; top: number; right: number; bottom: number }): void {
  element.getBoundingClientRect = () => ({ ...box, x: box.left, y: box.top, width: box.right - box.left, height: box.bottom - box.top, toJSON: () => box }) as DOMRect;
}

/** A sky view with the toolbar at the given left edge and the column at the left inset. */
function Harness({ toolbarLeft }: { toolbarLeft: number }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const columnRef = useRef<HTMLDivElement | null>(null);
  const top = useSideStackTop(containerRef, columnRef);
  const setContainer = (element: HTMLDivElement | null) => {
    if (element) placeAt(element, { left: 0, top: 0, right: 1000, bottom: 800 });
    containerRef.current = element;
  };
  const setToolbar = (element: HTMLDivElement | null) => {
    if (element) placeAt(element, { left: toolbarLeft, top: 16, right: 984, bottom: 76 });
  };
  const setColumn = (element: HTMLDivElement | null) => {
    if (element) placeAt(element, { left: 16, top: 16, right: 336, bottom: 400 });
    columnRef.current = element;
  };
  return (
    <div ref={setContainer}>
      <div ref={setToolbar} className="planetarium-toolbar" />
      <div ref={setColumn} />
      <span data-testid="top">{top === null ? 'inset' : String(top)}</span>
    </div>
  );
}

describe('useSideStackTop', () => {
  /** Wide window: the toolbar is right of the column, so the column keeps its inset. */
  it('keeps the top inset when the toolbar is off to the right', () => {
    render(<Harness toolbarLeft={500} />);
    expect(screen.getByTestId('top').textContent).toBe('inset');
  });

  /** Narrow window: the toolbar reaches over the column, which moves below it. */
  it('moves below the toolbar when the toolbar reaches over it', () => {
    render(<Harness toolbarLeft={100} />);
    expect(screen.getByTestId('top').textContent).toBe('84');
  });
});
