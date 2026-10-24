/**
 * @fileoverview One panel of Observation Execution: a landmark region (so a
 * screen reader can jump to it) around the shared `SectionPanel`.
 *
 * The region keeps the column's height rules in one place. A panel with
 * `grow` shares the free height of its column and scrolls inside itself when
 * its content is long. A panel without it takes only the height it needs.
 */

import React from 'react';
import { SectionPanel } from '../common/components/SectionPanel';

export interface ExecutionPanelProps {
    /** The panel's title, drawn in the panel header. */
    title?: string;
    /** The region's accessible name. Defaults to the title. */
    label?: string;
    /** Extra header content, such as the Example data badge or a button. */
    headerContent?: React.ReactNode;
    /** True when the panel shows example data (sets `data-example`). */
    example?: boolean;
    /** True when the panel should share the free height of its column. */
    grow?: boolean;
    /** Extra class names for the region. */
    className?: string;
    /** The panel's content. */
    children: React.ReactNode;
}

/**
 * A panel in the shared display style.
 * @param props The title, label, grow flag and content.
 * @return The panel.
 */
export const ExecutionPanel: React.FC<ExecutionPanelProps> = ({
    title,
    label,
    headerContent,
    example = false,
    grow = false,
    className = '',
    children,
}) => (
    <section
        className={`execution-region ${grow ? 'execution-region--grow' : ''} ${className}`}
        aria-label={label ?? title}
        data-example={example ? 'true' : undefined}
    >
        <SectionPanel title={title} headerContent={headerContent}>
            {children}
        </SectionPanel>
    </section>
);
