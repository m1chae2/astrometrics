import React, { useEffect, useRef, useState } from 'react';

export interface PopoverRenderProps {
    isOpen: boolean;
    toggle: () => void;
    close: () => void;
}

export interface PopoverProps {
    /** Renders the button (or other control) that opens/closes the popover. */
    trigger: (props: PopoverRenderProps) => React.ReactNode;
    /** Renders the popover's menu content. Only called while open. */
    children: (props: PopoverRenderProps) => React.ReactNode;
    className?: string;
}

/**
 * Wraps a trigger control and a dropdown menu with the open/close state and
 * click-outside-to-close behavior every bespoke toolbar popover in this app
 * was reimplementing on its own. Callers own all visual styling (className
 * on the container, plus whatever they render for the trigger and menu) --
 * this component only owns the open/closed mechanics.
 */
export const Popover: React.FC<PopoverProps> = ({ trigger, children, className = '' }) => {
    const [isOpen, setIsOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!isOpen) return;
        const handleClickOutside = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [isOpen]);

    const renderProps: PopoverRenderProps = {
        isOpen,
        toggle: () => setIsOpen((prev) => !prev),
        close: () => setIsOpen(false),
    };

    return (
        <div className={className} ref={containerRef}>
            {trigger(renderProps)}
            {isOpen && children(renderProps)}
        </div>
    );
};
