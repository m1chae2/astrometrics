/**
 * @fileoverview Slider: the one range slider of the app. It is a native range input, so it keeps
 * arrow-key control; its color is the theme accent (set once in `common/styles/index.css`).
 */

import React from 'react';

export type SliderProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'>;

/**
 * A range slider. Give it an `aria-label` or a label element, a `min`, a `max` and a `step`.
 * @param props See {@link SliderProps}. All props go to the native input.
 * @return The input element.
 */
export const Slider = React.forwardRef<HTMLInputElement, SliderProps>((props, ref) => (
    <input {...props} ref={ref} type="range" />
));
Slider.displayName = 'Slider';
