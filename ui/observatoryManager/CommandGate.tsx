/**
 * @file CommandGate.tsx
 * @description Wraps equipment controls that send commands to a device. When
 * the app may not command the device, the wrapper tells the user why: hovering
 * anywhere on the controls shows the reason, and screen readers read it as the
 * description of the group. A disabled button does not always receive hover in
 * Chromium, so the wrapper (not the button) carries the `title`, and the
 * stylesheet lets mouse events pass through the disabled controls to it.
 */

import React, { useId } from 'react';
import { InfoButton } from '../common/components/InfoButton';

/** Properties of {@link CommandGate}. */
export interface CommandGateProps {
    /** True when the app may not command the device. */
    disabled: boolean;
    /** The reason shown on hover and read by screen readers. */
    reason: string;
    /** The controls to wrap. Leave out when {@link renderChildren} is given. */
    children?: React.ReactNode;
    /** Extra class names for the wrapper, for layout. */
    className?: string;
    /** Draws the controls, given the id of the hidden reason text so inputs can point at it. */
    renderChildren?: (reasonId: string | undefined) => React.ReactNode;
}

/**
 * Puts a hover reason and an accessible description around command controls.
 * Renders only its children (no wrapper) while the controls are enabled.
 * @param props See {@link CommandGateProps}.
 * @return The wrapped controls.
 */
export const CommandGate: React.FC<CommandGateProps> = ({ disabled, reason, children, className, renderChildren }) => {
    const reasonId = `command-reason-${useId().replace(/:/g, '')}`;
    if (!disabled) {
        return <>{renderChildren ? renderChildren(undefined) : children}</>;
    }
    return (
        <div
            className={`command-gate ${className ?? ''}`.trim()}
            title={reason}
            role="group"
            aria-describedby={reasonId}
            data-testid="command-gate"
            data-unavailable="true"
        >
            {renderChildren ? renderChildren(reasonId) : children}
            <span id={reasonId} className="command-gate__reason">{reason}</span>
        </div>
    );
};

/** Properties of {@link WhyUnavailableButton}. */
export interface WhyUnavailableButtonProps {
    /** True when the controls of the panel are greyed. The button is drawn only then. */
    disabled: boolean;
    /** The reason shown on hover, on focus and on click. */
    reason: string;
}

/**
 * The "Why unavailable?" info button for a panel header. It can be reached from
 * the keyboard and by touch, which the hover title on the gate cannot.
 * @param props See {@link WhyUnavailableButtonProps}.
 * @return The info button, or nothing while the controls are available.
 */
export const WhyUnavailableButton: React.FC<WhyUnavailableButtonProps> = ({ disabled, reason }) => {
    if (!disabled) {
        return null;
    }
    return <InfoButton label="Why unavailable?" text={reason} />;
};
