/**
 * @fileoverview Checkbox: a native checkbox with its label. Every checkbox in the app uses it, so
 * every box has the same size, spacing, accent color and keyboard behavior.
 */

import React from 'react';
import './Checkbox.css';

export interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
    /**
     * The visible label. The box sits inside the label, so a click on the text toggles it. Leave it
     * out for a box with no text, such as a table row selector, and then pass `aria-label`.
     */
    label?: React.ReactNode;
    /** A layout-only class. It goes on the label when there is one, otherwise on the box. */
    className?: string;
    /** "regular" (the default), or "compact": a smaller box and the toolbar's small text, for toolbars. */
    size?: 'regular' | 'compact';
}

/**
 * A checkbox. It is a native input, so Space toggles it and `disabled` works as usual.
 * @param props See {@link CheckboxProps}. All other props go to the native input.
 * @return The label with the box inside it, or the bare box when there is no label.
 */
export const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(({
    label, className, disabled, size = 'regular', ...nativeProps
}, ref) => {
    const sizeClass = size === 'compact' ? ' checkbox--compact' : '';
    if (label === undefined || label === null) {
        return (
            <input
                {...nativeProps}
                ref={ref}
                type="checkbox"
                disabled={disabled}
                className={`checkbox__input${size === 'compact' ? ' checkbox__input--compact' : ''}${className ? ` ${className}` : ''}`}
            />
        );
    }
    return (
        <label className={`checkbox${sizeClass}${disabled ? ' checkbox--disabled' : ''}${className ? ` ${className}` : ''}`}>
            <input {...nativeProps} ref={ref} type="checkbox" disabled={disabled} className="checkbox__input" />
            <span className="checkbox__label">{label}</span>
        </label>
    );
});
Checkbox.displayName = 'Checkbox';
