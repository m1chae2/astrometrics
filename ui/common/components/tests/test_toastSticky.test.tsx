/**
 * @fileoverview Tests that a toast the user must act on (sent with `notify`) stays on screen until it is
 * closed, that a normal toast still goes away by itself, and that an action button on a toast works.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { ToastProvider } from '../ToastProvider';
import { emitToast } from '../../utils/emitToast';

describe('ToastProvider sticky and action toasts', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** A normal toast leaves after its short timeout. */
  it('removes a normal toast after three seconds', () => {
    render(<ToastProvider><div /></ToastProvider>);
    act(() => emitToast('Saved a thing', 'info', 'test-normal'));
    expect(screen.getByText('Saved a thing')).toBeTruthy();
    act(() => { vi.advanceTimersByTime(3500); });
    expect(screen.queryByText('Saved a thing')).toBeNull();
  });

  /** A toast sent with notify stays until closed, and has a close button. */
  it('keeps a notify toast until the user closes it', () => {
    render(<ToastProvider><div /></ToastProvider>);
    act(() => emitToast('Guiding lost. Check the guide star and the guider.', 'warning', 'test-sticky', { notify: true }));
    act(() => { vi.advanceTimersByTime(60000); });
    expect(screen.getByText(/Guiding lost/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
    expect(screen.queryByText(/Guiding lost/)).toBeNull();
  });

  /** An action button runs its function and closes the toast. */
  it('runs the action of a toast and closes it', () => {
    const run = vi.fn();
    render(<ToastProvider><div /></ToastProvider>);
    act(() => emitToast('Frames for M 13 are downloaded', 'info', 'test-action', { action: { label: 'Open', run } }));
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    expect(run).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/M 13 are downloaded/)).toBeNull();
  });
});
