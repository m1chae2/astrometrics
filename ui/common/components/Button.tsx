/**
 * @fileoverview Button: the one button of the app. Every screen uses it, so every button has the
 * same look, the same sizes and the same keyboard and screen-reader behavior.
 */

import React from 'react';
import './Button.css';

/** The fill of a button. `default` is the plain blue button; `ghost` has no fill. */
export type ButtonVariant = 'default' | 'primary' | 'secondary' | 'danger' | 'ghost';

/** The size of a button. `regular` is 36px tall, `small` is 24px tall, `square` is a 36px icon button. */
export type ButtonSize = 'regular' | 'small' | 'square';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    /** The fill. Leave it out for the plain blue button. */
    variant?: ButtonVariant;
    /** The size. Leave it out for the regular size. */
    size?: ButtonSize;
    /** Draws the button as switched on, without saying anything to screen readers. */
    active?: boolean;
    /**
     * For a button that switches something on or off: true while it is on. Sets `aria-pressed`
     * and draws the button as switched on.
     */
    pressed?: boolean;
    /** Makes the button fill the width of its container. */
    fullWidth?: boolean;
    /** Marks a hardware command button (telescope, guiding, INDI): sized to share a row. */
    control?: boolean;
    /** Shows that a command was sent and has not finished. Sets `aria-busy`. */
    pending?: boolean;
}

/**
 * Builds the class list of a button from its props.
 * @param props The variant, size and state flags.
 * @return The class names, joined by spaces.
 */
export function buttonClassName({
    variant = 'default', size = 'regular', active = false, fullWidth = false, control = false,
    pending = false, className = '',
}: Pick<ButtonProps, 'variant' | 'size' | 'active' | 'fullWidth' | 'control' | 'pending' | 'className'>): string {
    const classes: string[] = [];
    if (variant === 'ghost') {
        classes.push('btn-ghost');
        if (size === 'square') classes.push('btn-ghost--square');
        if (active) classes.push('btn-ghost--active');
    } else {
        classes.push('btn');
        if (variant !== 'default') classes.push(`btn--${variant}`);
        if (size === 'small') classes.push('btn--tiny');
        if (size === 'square') classes.push('btn--square');
        if (control) classes.push('btn--control');
        if (active) classes.push('btn--active');
        if (fullWidth) classes.push('btn--full-width');
        if (pending) classes.push('btn--pending');
    }
    if (className) classes.push(className);
    return classes.join(' ');
}

/**
 * A button. It is a native button, so it keeps keyboard focus, Enter and Space, and `disabled`.
 * It defaults to `type="button"`, so inside a form it does not submit; pass `type="submit"` when it should.
 * @param props See {@link ButtonProps}. All other props go to the native button.
 * @return The button element.
 */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({
    variant, size, active, pressed, fullWidth, control, pending, className, type = 'button', ...nativeProps
}, ref) => (
    <button
        {...nativeProps}
        ref={ref}
        type={type}
        className={buttonClassName({
            variant, size, active: active || pressed === true, fullWidth, control, pending, className,
        })}
        aria-pressed={pressed === undefined ? nativeProps['aria-pressed'] : pressed}
        aria-busy={pending ? true : nativeProps['aria-busy']}
    />
));
Button.displayName = 'Button';
