/**
 * @fileoverview Tests that polling stops while a display is hidden.
 * Hidden displays stay mounted, so their pollers used to keep calling the
 * backend. The shared poll tick must pause while hidden and refresh once as
 * soon as the display is shown again.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import React from 'react';
import { usePollTick } from '../common/hooks/usePollTick';
import { DisplayActiveContext } from '../common/context/DisplayActiveContext';

describe('usePollTick in a hidden display', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const wrapperFor = (active: boolean) => ({ children }: { children: React.ReactNode }) => (
    <DisplayActiveContext.Provider value={active}>{children}</DisplayActiveContext.Provider>
  );

  it('keeps ticking in an active display', () => {
    const { result } = renderHook(() => usePollTick(1000), { wrapper: wrapperFor(true) });

    act(() => vi.advanceTimersByTime(3000));

    expect(result.current).toBe(3);
  });

  it('does not tick while hidden', () => {
    const { result } = renderHook(() => usePollTick(1000), { wrapper: wrapperFor(false) });

    act(() => vi.advanceTimersByTime(5000));

    expect(result.current).toBe(0);
  });

  it('refreshes once when the display is shown again, then resumes ticking', () => {
    let active = false;
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <DisplayActiveContext.Provider value={active}>{children}</DisplayActiveContext.Provider>
    );
    const { result, rerender } = renderHook(() => usePollTick(1000), { wrapper });
    act(() => vi.advanceTimersByTime(5000));
    expect(result.current).toBe(0);

    active = true;
    rerender();
    expect(result.current).toBe(1);

    act(() => vi.advanceTimersByTime(2000));
    expect(result.current).toBe(3);
  });
});
