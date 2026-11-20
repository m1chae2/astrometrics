import React from 'react';
import '../styles/panels.css';
import { Button } from './Button';
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
    /** When set, the title becomes a button that calls this to fold the panel away. */
    onTitleClick?: () => void;
    /** Hover text for the title button. */
    titleButtonLabel?: string;
    /**
     * Folds the panel away. `down` leaves only the title row; `right` leaves a thin rail with the
     * title running down it. Clicking the title (`onTitleClick`) opens the panel again.
     */
    collapsed?: 'down' | 'right';
    /** A short muted note beside the title while the panel is folded down, such as a count. */
    collapsedNote?: string;
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
    skeleton,
    onTitleClick,
    titleButtonLabel,
    collapsed,
    collapsedNote
}) => {
    const fullHeightClass = fullHeight ? 'panel--full-height' : '';
    if (collapsed) {
        return (
            <div id={id} className={`panel panel--collapsed panel--collapsed-${collapsed} ${className}`} style={style}>
                <div className="panel__header">
                    <Button variant="ghost" size="small" onClick={onTitleClick} title={titleButtonLabel} aria-expanded={false}>
                        {title}
                    </Button>
                    {collapsed === 'down' && collapsedNote && <span className="panel__collapsed-note">{collapsedNote}</span>}
                </div>
            </div>
        );
    }
    return (
        <div id={id} className={`panel ${fullHeightClass} ${className}`} style={style}>
            {(title || headerContent) && (
                <div className="panel__header">
                    {onTitleClick ? (
                        <Button variant="ghost" size="small" onClick={onTitleClick} title={titleButtonLabel} aria-expanded={true}>
                            {title}
                        </Button>
                    ) : (
                        <span>{title}</span>
                    )}
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
