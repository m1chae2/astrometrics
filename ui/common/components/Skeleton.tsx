/**
 * @fileoverview Skeleton, UpdatingBar and PanelBody: the one loading style for every panel.
 *
 * A Skeleton is a set of grey shapes in the layout of the content that is coming, with a slow,
 * faint pulse. The `block` variant (a picture or canvas area) draws no shapes: the area stays empty and only a thin
 * moving bar runs along its top edge. It shows while a panel has no data yet, so nothing jumps when the data arrives.
 * When a panel already shows data and only refreshes it, keep the content and show an
 * UpdatingBar instead. "Loaded, nothing here" is a different state: use EmptyState for that.
 */

import React from 'react';
import './Skeleton.css';

export type SkeletonVariant = 'rows' | 'list' | 'chart' | 'keyValue' | 'block' | 'line';

export interface SkeletonProps {
    /** Shape of the coming content. */
    variant: SkeletonVariant;
    /** Hidden text that tells a screen reader what is loading, for example "Loading guiding for this night". Required. */
    label: string;
    /** How many rows, items or key/value lines to draw. Default depends on the variant. */
    count?: number;
    /** For `rows`: how many columns each row has. Default 3. */
    columns?: number;
    /** For `list`: lines per item (a title plus detail lines). Default 2. */
    lines?: number;
    /** Layout class from the parent (margin, flex). */
    className?: string;
}

const DEFAULT_COUNT: Record<SkeletonVariant, number> = {
    rows: 5,
    list: 4,
    chart: 1,
    keyValue: 4,
    block: 1,
    line: 1,
};

/**
 * Draws a list of grey bars.
 *
 * @param count How many bars.
 * @param build Makes one bar's element from its index.
 * @return The array of elements.
 */
function repeat(count: number, build: (index: number) => React.ReactNode): React.ReactNode[] {
    return Array.from({ length: Math.max(0, Math.floor(count)) }, (_, index) => build(index));
}

/**
 * Grey placeholder shapes with a slow, faint pulse. Respects `prefers-reduced-motion`.
 *
 * @param props See {@link SkeletonProps}.
 * @return The placeholder element, a `role="status"` region marked busy.
 */
export const Skeleton: React.FC<SkeletonProps> = ({
    variant,
    label,
    count,
    columns = 3,
    lines = 2,
    className = '',
}) => {
    const total = count ?? DEFAULT_COUNT[variant];
    let shapes: React.ReactNode;
    if (variant === 'rows') {
        shapes = (
            <>
                <div className="skeleton__row">
                    {repeat(columns, (column) => (
                        <span key={column} className="skeleton__bar skeleton__bar--header" />
                    ))}
                </div>
                {repeat(total, (row) => (
                    <div key={row} className="skeleton__row">
                        {repeat(columns, (column) => (
                            <span key={column} className="skeleton__bar" />
                        ))}
                    </div>
                ))}
            </>
        );
    } else if (variant === 'list') {
        shapes = repeat(total, (item) => (
            <div key={item} className="skeleton__item">
                <span className="skeleton__bar skeleton__bar--wide" />
                {repeat(Math.max(0, lines - 1), (line) => (
                    <span key={line} className="skeleton__bar skeleton__bar--narrow" />
                ))}
            </div>
        ));
    } else if (variant === 'keyValue') {
        shapes = repeat(total, (line) => (
            <div key={line} className="skeleton__pair">
                <span className="skeleton__bar" />
                <span className="skeleton__bar" />
            </div>
        ));
    } else if (variant === 'chart') {
        shapes = (
            <>
                <span className="skeleton__bar skeleton__bar--chart" />
                <span className="skeleton__bar skeleton__bar--axis" />
            </>
        );
    } else if (variant === 'line') {
        shapes = repeat(total, (line) => <span key={line} className="skeleton__bar" />);
    } else {
        shapes = <span className="updating-bar" aria-hidden="true" />;
    }
    return (
        <div className={`skeleton skeleton--${variant} ${className}`.trim()} role="status">
            <span className="skeleton__label">{label}</span>
            {shapes}
        </div>
    );
};

export interface UpdatingBarProps {
    /** Accessible name, for example "Updating guiding". */
    label: string;
    /** True to draw the bar in the normal flow (under a status line) instead of along a panel's top edge. */
    inline?: boolean;
}

/**
 * A thin moving bar along the top edge of the nearest positioned parent (or in the flow, with `inline`). It tells the user
 * that data already on screen is being refreshed, without blanking it.
 *
 * @param props See {@link UpdatingBarProps}.
 * @return The bar element.
 */
export const UpdatingBar: React.FC<UpdatingBarProps> = ({ label, inline = false }) => (
    <div className={inline ? 'updating-bar updating-bar--inline' : 'updating-bar'} role="progressbar" aria-label={label} aria-busy="true" />
);

export interface PanelBodyProps {
    /** True while there is no data yet: the skeleton shows instead of the children. */
    loading: boolean;
    /** True while data already shown is being refreshed: the children stay, and the updating bar shows. */
    refreshing?: boolean;
    /** The skeleton to show while `loading`. */
    skeleton: SkeletonProps;
    /** Text for the updating bar. Default "Updating". */
    updatingLabel?: string;
    /** Layout class from the parent. */
    className?: string;
    children: React.ReactNode;
}

/**
 * The one loading pattern for a panel's content: skeleton on first load, content plus a thin
 * updating bar on refresh.
 *
 * @param props See {@link PanelBodyProps}.
 * @return The panel content area.
 */
export const PanelBody: React.FC<PanelBodyProps> = ({
    loading,
    refreshing = false,
    skeleton,
    updatingLabel = 'Updating',
    className = '',
    children,
}) => (
    <div className={`panel-body ${className}`.trim()}>
        {loading ? (
            <Skeleton {...skeleton} />
        ) : (
            <>
                {refreshing && <UpdatingBar label={updatingLabel} />}
                {children}
            </>
        )}
    </div>
);
