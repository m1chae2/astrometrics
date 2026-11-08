import React from 'react';
import '../styles/panels.css';
import { Skeleton, UpdatingBar, SkeletonProps } from './Skeleton';

export interface SectionPanelProps {
    title?: string;
    children: React.ReactNode;
    className?: string;
    headerContent?: React.ReactNode;
    id?: string;
    style?: React.CSSProperties;
    fullHeight?: boolean;
    /** True while the panel has no data yet: `skeleton` shows instead of the children. */
    loading?: boolean;
    /** True while data already shown is refreshed: the children stay and a thin bar shows at the panel's top edge. */
    refreshing?: boolean;
    /** The placeholder to show while `loading`. Required when `loading` can be true. */
    skeleton?: SkeletonProps;
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
    fullHeight = false,
    loading = false,
    refreshing = false,
    skeleton
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
            {refreshing && !loading && <UpdatingBar label={title ? `Updating ${title}` : 'Updating'} />}
            <div className="panel__content">
                {loading && skeleton ? <Skeleton {...skeleton} /> : children}
            </div>
        </div>
    );
};
