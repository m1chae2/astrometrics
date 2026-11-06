/**
 * @fileoverview Tests for the manual-slew direction pad: spoken names, keyboard control, a move that never
 * outlives the pad or the pointer press, and a slew-rate slider that keeps the value it is given.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DirectionalControl } from '../panels/DirectionalControl';

/** Renders the pad with spies. */
function renderPad(extra: Partial<React.ComponentProps<typeof DirectionalControl>> = {}) {
  const handlers = { onStop: vi.fn(), onStartMove: vi.fn(), onStopMove: vi.fn(), onSetSlewRate: vi.fn() };
  const view = render(<DirectionalControl {...handlers} {...extra} />);
  return { ...handlers, ...view };
}

describe('DirectionalControl', () => {
  /** Each arrow has a spoken name. */
  it('names every direction button', () => {
    renderPad();
    for (const name of ['north', 'south', 'east', 'west', 'northeast', 'northwest', 'southeast', 'southwest']) {
      expect(screen.getByRole('button', { name: `Move ${name}` })).toBeTruthy();
    }
  });

  /** Holding Space starts the move; releasing it stops. */
  it('starts on a key press and stops on key release', () => {
    const { onStartMove, onStopMove } = renderPad();
    const north = screen.getByRole('button', { name: 'Move north' });
    fireEvent.keyDown(north, { key: ' ' });
    expect(onStartMove).toHaveBeenCalledWith('N');
    fireEvent.keyUp(north, { key: ' ' });
    expect(onStopMove).toHaveBeenCalledWith('N');
  });

  /** Enter works like Space. */
  it('also works with Enter', () => {
    const { onStartMove, onStopMove } = renderPad();
    const east = screen.getByRole('button', { name: 'Move east' });
    fireEvent.keyDown(east, { key: 'Enter' });
    fireEvent.keyUp(east, { key: 'Enter' });
    expect(onStartMove).toHaveBeenCalledWith('E');
    expect(onStopMove).toHaveBeenCalledWith('E');
  });

  /** Leaving the button with the key held (focus lost) stops the move. */
  it('stops when the button loses focus', () => {
    const { onStopMove } = renderPad();
    const south = screen.getByRole('button', { name: 'Move south' });
    fireEvent.keyDown(south, { key: ' ' });
    fireEvent.blur(south);
    expect(onStopMove).toHaveBeenCalledWith('S');
  });

  /** Releasing the pointer anywhere in the window stops the move. */
  it('stops when the pointer is released anywhere', () => {
    const { onStopMove } = renderPad();
    fireEvent.mouseDown(screen.getByRole('button', { name: 'Move west' }));
    fireEvent.pointerUp(window);
    expect(onStopMove).toHaveBeenCalledWith('W');
  });

  /** The window losing focus stops the move. */
  it('stops when the window loses focus', () => {
    const { onStopMove } = renderPad();
    fireEvent.mouseDown(screen.getByRole('button', { name: 'Move north' }));
    fireEvent.blur(window);
    expect(onStopMove).toHaveBeenCalledWith('N');
  });

  /** Taking the pad off the screen (tab or display switch) while held stops the mount. */
  it('stops the move when the pad is removed', () => {
    const { onStopMove, unmount } = renderPad();
    fireEvent.mouseDown(screen.getByRole('button', { name: 'Move north' }));
    unmount();
    expect(onStopMove).toHaveBeenCalledWith('N');
  });

  /** No move, no stop call on removal. */
  it('sends no stop when nothing was moving', () => {
    const { onStopMove, unmount } = renderPad();
    unmount();
    expect(onStopMove).not.toHaveBeenCalled();
  });

  /** The slider shows the rate it is given and is tied to its label. */
  it('shows the given slew rate and is labelled', () => {
    renderPad({ slewRate: 3 });
    const slider = screen.getByRole('slider', { name: /Slew speed/ }) as HTMLInputElement;
    expect(slider.value).toBe('3');
  });
});
