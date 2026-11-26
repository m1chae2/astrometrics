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

/**
 * How the field is drawn. `boxed` (the default) has the usual edge and fill. `inline` has no
 * edge, fill or padding and takes the text color and font of where it sits, for a field that
 * is part of a line of text, such as the Command Console's ">>>" entry line.
 */
export type TextInputAppearance = 'boxed' | 'inline';

export interface TextInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
    /** The kind of field. Defaults to plain text. Use NumberInput for numbers. */
    type?: 'text' | 'password' | 'search' | 'number';
    /** The width of the field. Leave it out for 100% of the container. */
    width?: TextInputWidth;
    /** Centers the text, which suits a short numeric field. */
    centered?: boolean;
    /** Leave it out for the regular 38px field. `small` matches a small Button and a compact PopoverSelect. */
    size?: TextInputSize;
    /** `inline` draws the field with no edge or fill. Leave it out for the usual boxed field. */
    appearance?: TextInputAppearance;
}

/**
 * Builds the class list of an entry field.
 * @param width The width modifier, if any.
 * @param centered True to center the text.
 * @param size The size modifier, if any.
 * @param className Extra layout classes from the caller.
 * @param appearance The appearance modifier, if any.
 * @return The class names, joined by spaces.
 */
export function entryClassName(width: TextInputWidth | undefined, centered: boolean | undefined, className?: string, size?: TextInputSize, appearance?: TextInputAppearance): string {
    return [
        'entry',
        width === 'short' ? 'entry--short' : '',
        width === 'fill' ? 'entry--fill' : '',
        centered ? 'entry--centered' : '',
        size === 'small' ? 'entry--small' : '',
        appearance === 'inline' ? 'entry--inline' : '',
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
    type = 'text', width, centered, size, appearance, className, ...nativeProps
}, ref) => (
    <input {...nativeProps} ref={ref} type={type} className={entryClassName(width, centered, className, size, appearance)} />
));
TextInput.displayName = 'TextInput';
