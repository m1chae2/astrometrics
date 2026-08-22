import React from 'react';
import '../styles/panels.css';

export interface ControlLayoutBaseProps {
    children?: React.ReactNode;
    className?: string;
    style?: React.CSSProperties;
}

/**
 * ControlContainer Component
 * A flex row container for holding columns of controls.
 */
export const ControlContainer: React.FC<ControlLayoutBaseProps> = ({ children, className = '', style }) => (
    <div className={`controls ${className}`} style={style}>
        {children}
    </div>
);

/**
 * ControlColumn Component
 * A vertical column for stacking parameter rows.
 */
export const ControlColumn: React.FC<ControlLayoutBaseProps> = ({ children, className = '', style }) => (
    <div className={`controls__column ${className}`} style={style}>
        {children}
    </div>
);

/**
 * SectionLabel Component
 * A small uppercase label for subdividing control sections.
 */
export const SectionLabel: React.FC<ControlLayoutBaseProps> = ({ children, className = '', style }) => (
    <div className={`controls__section-label ${className}`} style={style}>
        {children}
    </div>
);

export interface ParameterRowProps extends ControlLayoutBaseProps {
    label: string;
    htmlFor?: string;
}

/**
 * ParameterRow Component
 * Renders a label on the left and a child control (input/select) on the right.
 */
export const ParameterRow: React.FC<ParameterRowProps> = ({ label, children, className = '', htmlFor, style }) => (
    <div className={`parameter ${className}`} style={style}>
        <label className="parameter__label" htmlFor={htmlFor}>{label}</label>
        {children}
    </div>
);

/**
 * VerticalDivider Component
 * A simple vertical line for visually separating columns.
 */
export const VerticalDivider: React.FC<{ className?: string }> = ({ className = '' }) => (
    <div className={`vertical-divider ${className}`} />
);
