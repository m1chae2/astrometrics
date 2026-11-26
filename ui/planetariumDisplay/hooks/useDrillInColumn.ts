/**
 * @file useDrillInColumn.ts
 * @description The two levels of the Planetarium's left column. Level 1 is the target (or
 * spectral class) list; level 2 is the star list of the picked entry. This hook keeps which
 * level each browse mode shows, slides between them, moves the keyboard focus so it never stays
 * on a list that has slid away, and handles the keys (arrow keys, Enter, Escape).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { prefersReducedMotion } from '../../common/utils/reducedMotion';

/** The two ways to browse the left column. */
export type BrowseMode = 'target' | 'spectralClass';

// Longest wait, in milliseconds, for the column's slide to end before the
// work held back for it runs anyway (the slide itself takes 240 ms).
const SLIDE_END_FALLBACK_MS = 400;

/** What `useDrillInColumn` gives back. */
export interface DrillInColumn {
  /** True when the current browse mode shows level 2 (the star list). */
  isDrilledIn: boolean;
  /** The level 1 pane. */
  levelOnePaneRef: React.RefObject<HTMLDivElement | null>;
  /** The level 2 pane. */
  levelTwoPaneRef: React.RefObject<HTMLDivElement | null>;
  /** The Back button on level 2. */
  backButtonRef: React.RefObject<HTMLButtonElement | null>;
  /** Shows level 1 or level 2 for one browse mode, with no change of focus. */
  setModeDrilledIn: (mode: BrowseMode, drilledIn: boolean) => void;
  /** Slides one browse mode to level 2 after a pick; the focus goes to level 2 unless a key already chose where. */
  openLevelTwo: (mode: BrowseMode) => void;
  /** True while an arrow key in a level 1 list makes the pick (such a pick stays on level 1). */
  isArrowKeyPick: () => boolean;
  /** Runs work once the column's slide has ended. */
  runAfterSlide: (work: () => void) => void;
  /** The Back button's click. */
  handleBackToLevelOne: (event: React.MouseEvent<HTMLButtonElement>) => void;
  /** Key presses on level 1 (arrow keys and Enter). */
  handleLevelOneKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void;
  /** Key presses on level 2 (Escape). */
  handleLevelTwoKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void;
}

/**
 * Keeps the left column's level for each browse mode and handles the slide, the focus and the keys.
 *
 * @param navigationMode The browse mode shown now.
 * @returns The level, the pane refs and the handlers (see `DrillInColumn`).
 */
export const useDrillInColumn = (navigationMode: BrowseMode): DrillInColumn => {
  // Each browse mode remembers its own level.
  const [drilledInModes, setDrilledInModes] = useState<Record<BrowseMode, boolean>>({
    target: false,
    spectralClass: false,
  });
  const isDrilledIn = drilledInModes[navigationMode];
  const levelOnePaneRef = useRef<HTMLDivElement>(null);
  const levelTwoPaneRef = useRef<HTMLDivElement>(null);
  const backButtonRef = useRef<HTMLButtonElement>(null);
  // Where to put the focus after the next level change, so it never stays on
  // a list that has just slid away. A keyboard action focuses a control (the
  // Back button or the picked row); a mouse pick focuses the level 2 box
  // itself, which shows no focus ring but still hears Escape.
  const pendingFocusRef = useRef<'back' | 'list' | 'levelTwo' | null>(null);
  // True during an arrow-key press in a level 1 list; see handleLevelOneKeyDown.
  const isArrowKeyPickRef = useRef<boolean>(false);

  useEffect(() => {
    const focusTarget = pendingFocusRef.current;
    if (!focusTarget) return;
    pendingFocusRef.current = null;
    // preventScroll: the pane is still sliding in, and the browser would
    // otherwise scroll the clipped column sideways to show the focused control.
    if (focusTarget === 'back') {
      backButtonRef.current?.focus({ preventScroll: true });
    } else if (focusTarget === 'levelTwo') {
      levelTwoPaneRef.current?.focus({ preventScroll: true });
    } else {
      levelOnePaneRef.current?.querySelector<HTMLInputElement>('input[type="radio"]:checked')?.focus({ preventScroll: true });
    }
  }, [isDrilledIn, navigationMode]);

  /**
   * Turns level 2 on or off for one browse mode.
   *
   * @param mode The browse mode.
   * @param drilledIn True to show the star list, false to show the list.
   */
  const setModeDrilledIn = useCallback((mode: BrowseMode, drilledIn: boolean) => {
    setDrilledInModes((previous) => (previous[mode] === drilledIn ? previous : { ...previous, [mode]: drilledIn }));
  }, []);

  /**
   * Slides one browse mode to level 2 after a pick from its list.
   *
   * @param mode The browse mode.
   */
  const openLevelTwo = useCallback((mode: BrowseMode) => {
    if (pendingFocusRef.current === null) pendingFocusRef.current = 'levelTwo';
    setModeDrilledIn(mode, true);
  }, [setModeDrilledIn]);

  /**
   * Tells whether the pick being made now came from an arrow key.
   *
   * @returns True during an arrow-key press in a level 1 list.
   */
  const isArrowKeyPick = useCallback(() => isArrowKeyPickRef.current, []);

  /**
   * Runs `work` once the column's slide has ended, so the slide gets the main
   * thread to itself; heavy work (centering the sky map) waits for it. With
   * reduced motion there is no slide, so it runs on the next frame.
   *
   * @param work The work to run.
   */
  const runAfterSlide = useCallback((work: () => void) => {
    const pane = levelTwoPaneRef.current;
    if (!pane || prefersReducedMotion()) {
      window.requestAnimationFrame(() => work());
      return;
    }
    let hasRun = false;
    const runOnce = () => {
      if (hasRun) return;
      hasRun = true;
      pane.removeEventListener('transitionend', handleTransitionEnd);
      window.clearTimeout(fallbackTimer);
      work();
    };
    // Only the pane's own slide counts; a transition inside it (a row's
    // hover color, say) also sends a transitionend that bubbles up here.
    const handleTransitionEnd = (event: TransitionEvent) => {
      if (event.target === pane && event.propertyName === 'transform') runOnce();
    };
    const fallbackTimer = window.setTimeout(runOnce, SLIDE_END_FALLBACK_MS);
    pane.addEventListener('transitionend', handleTransitionEnd);
  }, []);

  // The panes get `will-change: transform` only while they slide, so the
  // browser keeps them on their own layer for the slide and frees it after.
  const hasRenderedDrillRef = useRef<boolean>(false);
  useEffect(() => {
    if (!hasRenderedDrillRef.current) {
      hasRenderedDrillRef.current = true;
      return undefined;
    }
    const panes = [levelOnePaneRef.current, levelTwoPaneRef.current].filter(
      (pane): pane is HTMLDivElement => pane !== null,
    );
    panes.forEach((pane) => { pane.style.willChange = 'transform'; });
    const clearWillChange = () => panes.forEach((pane) => { pane.style.willChange = ''; });
    const timer = window.setTimeout(clearWillChange, SLIDE_END_FALLBACK_MS);
    return () => {
      window.clearTimeout(timer);
      clearWillChange();
    };
  }, [isDrilledIn]);

  /**
   * Slides back to level 1. The picked target (or class) stays highlighted.
   *
   * @param fromKeyboard True when a key started it; the focus then goes to the picked row.
   */
  const backToLevelOne = useCallback((fromKeyboard: boolean) => {
    pendingFocusRef.current = fromKeyboard ? 'list' : null;
    setModeDrilledIn(navigationMode, false);
  }, [navigationMode, setModeDrilledIn]);

  /**
   * The Back button. A click made with Enter or Space has `detail` 0.
   *
   * @param event The click.
   */
  const handleBackToLevelOne = useCallback((event: React.MouseEvent<HTMLButtonElement>) => {
    backToLevelOne(event.detail === 0);
  }, [backToLevelOne]);

  /**
   * Escape anywhere on level 2 slides back to level 1.
   *
   * @param event The key press.
   */
  const handleLevelTwoKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    event.preventDefault();
    backToLevelOne(true);
  }, [backToLevelOne]);

  /**
   * Keyboard use on level 1. Arrow keys move through the list and pick each
   * row (the sky map follows), but do not slide to level 2, so the list can be
   * browsed. Enter opens the picked row's stars.
   *
   * @param event The key press.
   */
  const handleLevelOneKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key.startsWith('Arrow')) {
      // The browser picks the next radio button during this key press; the
      // flag tells the pick handlers it came from an arrow key.
      isArrowKeyPickRef.current = true;
      window.setTimeout(() => { isArrowKeyPickRef.current = false; }, 0);
      return;
    }
    const isOnRadio = event.target instanceof HTMLInputElement && event.target.type === 'radio';
    if (event.key === 'Enter' && isOnRadio) {
      event.preventDefault();
      pendingFocusRef.current = 'back';
      setModeDrilledIn(navigationMode, true);
    }
  }, [navigationMode, setModeDrilledIn]);

  return {
    isDrilledIn,
    levelOnePaneRef,
    levelTwoPaneRef,
    backButtonRef,
    setModeDrilledIn,
    openLevelTwo,
    isArrowKeyPick,
    runAfterSlide,
    handleBackToLevelOne,
    handleLevelOneKeyDown,
    handleLevelTwoKeyDown,
  };
};
