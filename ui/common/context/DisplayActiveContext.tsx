/**
 * @file DisplayActiveContext.tsx
 * @description Tells a component whether the display it lives in is on screen.
 * Every visited display stays mounted and is only hidden with CSS, so timers and
 * pollers inside a hidden display keep running. Pollers read this to pause
 * while their display is hidden and refresh once when it is shown again.
 */

import { createContext, useContext } from 'react';

/** True when the display is on screen. Components outside any display count as active. */
export const DisplayActiveContext = createContext<boolean>(true);

/**
 * Reports whether the display this component belongs to is currently on screen.
 *
 * @return `true` while the display is visible (or when not inside a display).
 */
export const useIsDisplayActive = (): boolean => useContext(DisplayActiveContext);
