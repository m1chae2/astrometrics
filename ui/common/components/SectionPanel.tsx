import React from 'react';
import '../styles/panels.css';

export interface SectionPanelProps {
    title?: string;
    children: React.ReactNode;
    className?: string;
    headerContent?: React.ReactNode;
    id?: string;
    style?: React.CSSProperties;
    fullHeight?: boolean;
}

/**
 * SectionPanel component.
 *
 * A generic container for UI sections. Provides a consistent style
 * with a title/header bar and a content area.
 */
export const SectionPanel: React.FC<SectionPanelProps> = ({
    title,
    children,
    className = '',
    headerContent,
    id,
    style,
    fullHeight = false
}) => {
    const fullHeightClass = fullHeight ? 'panel--full-height' : '';
    return (
        <div id={id} className={`panel ${fullHeightClass} ${className}`} style={style}>
            {(title || headerContent) && (
                <div className="panel__header">
                    <span>{title}</span>
                    {headerContent}
                </div>
            )}
            <div className="panel__content">
                {children}
            </div>
        </div>
    );
};
