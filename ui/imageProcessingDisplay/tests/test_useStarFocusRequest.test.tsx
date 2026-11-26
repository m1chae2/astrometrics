/**
 * @file test_useStarFocusRequest.test.tsx
 * @description Checks the request that shows one star on its target's main stack: it waits for the target the
 * star belongs to (also when that is another target than the one on screen), shows the main stack, selects the
 * star and centers and zooms on it, and leaves a short note when the stack does not name the star.
 */
import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
  findStarForRecord, STAR_FOCUS_SCALE, starFocusView, useStarFocusRequest, type StarFocusInputs,
} from '../hooks/useStarFocusRequest';
import type { AstrometryOverlayStar } from '../../common/services/astronomyService';

/** A named star of the NGC 7023 stack. */
const STAR: AstrometryOverlayStar = {
  id: 'det-7', name: 'HD 200775', gaiaSourceId: '123', libraryId: 'Gaia DR3 123', x: 400, y: 300, referenceWidth: 800, referenceHeight: 600,
};

/** The screen's inputs, with the M 31 target on screen. */
function inputs(overrides: Partial<StarFocusInputs> = {}): StarFocusInputs {
  return {
    selectedTarget: 'M 31',
    isLoadingFiles: false,
    mainStackPath: '/stacks/M31.fits',
    isStackShown: false,
    hasChosenFile: true,
    showMainStack: vi.fn(),
    identification: { isLoaded: false, running: false, hasResult: false, stars: [], start: vi.fn() },
    onFound: vi.fn(),
    ...overrides,
  };
}

describe('star focus helpers', () => {
  it('finds the star by the library record id it carries', () => {
    expect(findStarForRecord([STAR], 'Gaia DR3 123')).toBe(STAR);
    expect(findStarForRecord([{ ...STAR, libraryId: 'rec-1' }], 'rec-1')).not.toBeNull();
    expect(findStarForRecord([STAR], 'HD 200775')).toBeNull();
    expect(findStarForRecord([{ ...STAR, libraryId: null }], 'Gaia DR3 123')).toBeNull();
  });

  it('centers the star and zooms in screen pixels per stack pixel', () => {
    expect(starFocusView(STAR, 'k')).toEqual({
      key: 'k', view: { x: 0.5, y: 0.5, zoomOverFit: 1, turn: 0 }, scale: STAR_FOCUS_SCALE, pictureWidth: 800, pickStar: true,
    });
    expect(starFocusView({ ...STAR, referenceWidth: null }, 'k')).toBeNull();
  });
});

describe('useStarFocusRequest', () => {
  it('waits for the switched target, shows its main stack, then selects and centers the star', () => {
    const showMainStack = vi.fn();
    const onFound = vi.fn();
    const { result, rerender } = renderHook((props: StarFocusInputs) => useStarFocusRequest(props), {
      initialProps: inputs({ showMainStack, onFound }),
    });
    act(() => result.current.request({ targetId: 'NGC 7023', recordId: 'Gaia DR3 123', starName: 'HD 200775' }));
    // Still on M 31: nothing happens yet.
    expect(showMainStack).not.toHaveBeenCalled();
    rerender(inputs({ selectedTarget: 'NGC 7023', isLoadingFiles: true, mainStackPath: null, showMainStack, onFound }));
    expect(showMainStack).not.toHaveBeenCalled();
    rerender(inputs({ selectedTarget: 'NGC 7023', mainStackPath: '/stacks/NGC7023.fits', showMainStack, onFound }));
    expect(showMainStack).toHaveBeenCalledTimes(1);
    rerender(inputs({
      selectedTarget: 'NGC 7023', mainStackPath: '/stacks/NGC7023.fits', isStackShown: true, showMainStack, onFound,
      identification: { isLoaded: true, running: false, hasResult: true, stars: [STAR], start: vi.fn() },
    }));
    expect(onFound).toHaveBeenCalledTimes(1);
    expect(onFound.mock.calls[0][0]).toBe(STAR);
    expect(onFound.mock.calls[0][1]).toMatchObject({ view: { x: 0.5, y: 0.5 }, scale: STAR_FOCUS_SCALE });
    expect(result.current.note).toBeNull();
  });

  it('shows the stack and a note when the stack does not name the star', () => {
    const onFound = vi.fn();
    const { result, rerender } = renderHook((props: StarFocusInputs) => useStarFocusRequest(props), {
      initialProps: inputs({ onFound }),
    });
    act(() => result.current.request({ targetId: 'M 31', recordId: 'Vega', starName: 'Vega' }));
    rerender(inputs({
      isStackShown: true, onFound,
      identification: { isLoaded: true, running: false, hasResult: true, stars: [STAR], start: vi.fn() },
    }));
    expect(onFound).not.toHaveBeenCalled();
    expect(result.current.note).toBe('Vega is not among the named stars of this stack.');
  });

  it('says so when the target has no stack', () => {
    const { result } = renderHook(() => useStarFocusRequest(inputs({ mainStackPath: null })));
    act(() => result.current.request({ targetId: 'M 31', recordId: 'Vega', starName: 'Vega' }));
    expect(result.current.note).toBe('M 31 has no stack, so Vega cannot be shown on it.');
  });
});
