/**
 * @fileoverview TextInput: the one single-line entry field of the app. Every text, password and
 * search field uses it, so every field has the same height, edge, focus ring and corners.
 */

import React from 'react';
import './TextInput.css';

/** How wide a field is. `short` is 80px, `fill` takes the free space in a row, `full` is 100% of its container. */
export type TextInputWidth = 'full' | 'short' | 'fill';

/** How tall a field is. `small` is the 24px height of a small Button, for panel headers and toolbars. */
export type TextInputSize = 'small';

export interface TextInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
    /** The kind of field. Defaults to plain text. Use NumberInput for numbers. */
    type?: 'text' | 'password' | 'search' | 'number';
    /** The width of the field. Leave it out for 100% of the container. */
    width?: TextInputWidth;
    /** Centers the text, which suits a short numeric field. */
    centered?: boolean;
    /** Leave it out for the regular 38px field. `small` matches a small Button and a compact PopoverSelect. */
    size?: TextInputSize;
}

/**
 * Builds the class list of an entry field.
 * @param width The width modifier, if any.
 * @param centered True to center the text.
 * @param size The size modifier, if any.
 * @param className Extra layout classes from the caller.
 * @return The class names, joined by spaces.
 */
export function entryClassName(width: TextInputWidth | undefined, centered: boolean | undefined, className?: string, size?: TextInputSize): string {
    return [
        'entry',
        width === 'short' ? 'entry--short' : '',
        width === 'fill' ? 'entry--fill' : '',
        centered ? 'entry--centered' : '',
        size === 'small' ? 'entry--small' : '',
        className ?? '',
    ].filter(Boolean).join(' ');
}

/**
 * A single-line text field. It is a native input, so labels, `aria-label`, focus and `disabled` work as usual.
 * Pass a layout-only `className` when the field needs a position; colors and sizes come from the component.
 * @param props See {@link TextInputProps}. All other props go to the native input.
 * @return The input element.
 */
export const TextInput = React.forwardRef<HTMLInputElement, TextInputProps>(({
    type = 'text', width, centered, size, className, ...nativeProps
}, ref) => (
    <input {...nativeProps} ref={ref} type={type} className={entryClassName(width, centered, className, size)} />
));
TextInput.displayName = 'TextInput';
