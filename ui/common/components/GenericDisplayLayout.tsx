import React from 'react';
import '../styles/panels.css';
import '../styles/layout.css';

export interface GenericDisplayLayoutProps {
    leftPanel?: React.ReactNode;
    centerPanel?: React.ReactNode;
    rightPanel?: React.ReactNode;
    className?: string;
}

/**
 * GenericDisplayLayout Component
 * Standardizes the top-level 2 or 3 column layout across display views.
 */
export const GenericDisplayLayout: React.FC<GenericDisplayLayoutProps> = ({
    leftPanel,
    centerPanel,
    rightPanel,
    className = ''
}) => {
    // Determine layout mode based on prop presence
    const layoutClass = rightPanel ? 'layout--3-col' : 'layout--2-col';

    return (
        <div className={`layout ${layoutClass} ${className}`}>
            <div className="layout__panel layout__panel--left">
                {leftPanel}
            </div>
            <div className="layout__panel layout__panel--center">
                {centerPanel}
            </div>
            {rightPanel && (
                <div className="layout__panel layout__panel--right">
                    {rightPanel}
                </div>
            )}
        </div>
    );
};
