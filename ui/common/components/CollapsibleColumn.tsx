/**
 * @file CollapsibleColumn.tsx
 * @description A display's left column that folds to a thin strip and opens again. Image
 * Processing, the Image Viewer and the Astronomy Manager all use it, so the fold looks and works
 * the same on every screen. With `side="right"` it is a column on the right edge instead, such
 * as the Command Console's Console panel or Image Processing's Frame Analysis, and folds to a
 * strip on that edge. With `side="bottom"` it is a block under the viewer, such as Image
 * Processing's File Browser, and folds to a bar across the bottom. Screens remember the fold
 * with `usePersistentFlag`.
 */

import React from 'react';
import { SectionPanel } from './SectionPanel';
import './collapsibleColumn.css';

export interface CollapsibleColumnProps {
    /** Whether the column is folded to its strip. */
    collapsed: boolean;
    /** Called with `false` when the user clicks the strip (or presses Enter on it). */
    onCollapsedChange: (collapsed: boolean) => void;
    /** The word running down the strip, such as "Filter Targets" or "Targets". */
    stripTitle: string;
    /** The selected item shown under the title on the strip, such as "M 31". */
    selectedLabel?: string;
    /**
     * A second selected item shown after the first, such as the star picked in the target's
     * star list ("HD 338506"). The strip then reads "Albireo · HD 338506" under its title.
     */
    selectedDetail?: string;
    /** Hover text of the strip when nothing is selected. */
    showLabel: string;
    /**
     * Which edge the column sits on. `left` (the default) narrows the display's left grid column
     * to the strip. `right` draws the strip at a fixed width on the right edge and leaves the
     * left column alone; a display with a right grid column narrows that column to the strip.
     * `bottom` draws a bar across the full width, for a block under a viewer.
     */
    side?: 'left' | 'right' | 'bottom';
    /** A layout class for the strip's outer box. */
    className?: string;
    /** The open column. The screen puts the fold button (a panel title) in it. */
    children: React.ReactNode;
}

/**
 * Shows the open column, or the thin strip when `collapsed` is true. While a strip is on screen,
 * the display's left grid column narrows to the strip's width (see `collapsibleColumn.css`), so
 * the rest of the screen takes the space.
 *
 * The strip's button reads "<title>, <selected>, <detail>" to screen readers and as its hover
 * text, so the selected items are never lost when the column is folded. A long note is cut
 * with an ellipsis on the strip; the hover text keeps it whole.
 *
 * @param props See `CollapsibleColumnProps`.
 * @returns The open column, or the strip.
 */
export const CollapsibleColumn: React.FC<CollapsibleColumnProps> = ({
    collapsed,
    onCollapsedChange,
    stripTitle,
    selectedLabel,
    selectedDetail,
    showLabel,
    side = 'left',
    className = '',
    children,
}) => {
    if (!collapsed) {
        return <>{children}</>;
    }
    const selectedParts = [selectedLabel, selectedDetail].filter((part): part is string => !!part);
    const stripNote = selectedParts.length > 0 ? selectedParts.join(' · ') : undefined;
    const fullStripLabel = [stripTitle, ...selectedParts].join(', ');
    const stripClass = side === 'left' ? 'collapsible-column--collapsed' : `collapsible-column--collapsed-${side}`;
    return (
        <div className={`${stripClass} ${side === 'bottom' ? '' : 'panel-group'} ${className}`}>
            <SectionPanel
                title={stripTitle}
                collapsedNote={stripNote}
                collapsed={side === 'bottom' ? 'down' : 'right'}
                onTitleClick={() => onCollapsedChange(false)}
                titleButtonLabel={stripNote ? fullStripLabel : showLabel}
                collapsedAriaLabel={stripNote ? fullStripLabel : undefined}
            >
                {null}
            </SectionPanel>
        </div>
    );
};
