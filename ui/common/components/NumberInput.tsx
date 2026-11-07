/**
 * @fileoverview NumberInput: the one number field of the app. It is a TextInput of type number, so
 * it has the same look; `min`, `max` and `step` go to the native input.
 */

import React from 'react';
import { TextInput, TextInputProps } from './TextInput';

export type NumberInputProps = Omit<TextInputProps, 'type'>;

/**
 * A number field with the app's spinner arrows.
 * @param props See {@link NumberInputProps}. All other props go to the native input.
 * @return The input element.
 */
export const NumberInput = React.forwardRef<HTMLInputElement, NumberInputProps>((props, ref) => (
    <TextInput {...props} ref={ref} type="number" />
));
NumberInput.displayName = 'NumberInput';
